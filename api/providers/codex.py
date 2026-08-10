"""Codex subscription quota from the locally authenticated Codex CLI account."""

from __future__ import annotations

import base64
import json
import os
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, ClassVar

import requests

from api.providers.base import (
    FetchError,
    Provider,
    ProviderQuota,
    QuotaMetric,
    QuotaWindow,
    build_session,
)


_TIMESTAMP = re.compile(r'^\{"timestamp":"([^"]+)"')


@dataclass
class _SessionUsage:
    size: int
    mtime_ns: int
    offset: int
    task_start: datetime | None = None
    longest_task_seconds: int = 0
    last_total: int = 0
    last_cached: int = 0
    peak_total: int = 0
    daily: dict[str, int] = field(default_factory=dict)


class CodexProvider(Provider):
    id = "codex"
    name = "Codex"
    supports_subscription_quota = True
    official_api_hosts = {"chatgpt.com"}
    credential_fields = {
        "HOME": {
            "label": "Codex 目录（可选）",
            "secret": False,
            "optional": True,
            "directory": True,
            "hint": "默认读取 %USERPROFILE%\\.codex\\auth.json",
        }
    }
    _session_cache: ClassVar[dict[Path, _SessionUsage]] = {}
    _session_cache_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(config)
        self._session = build_session()

    def close(self) -> None:
        self._session.close()

    def _home(self) -> Path:
        configured = str(self.config_get("CODEX_HOME", "")).strip()
        if configured:
            path = Path(configured).expanduser()
            # 旧版设置页允许手工输入，部分用户会直接填 auth.json。
            # Provider 其余逻辑都以 Codex 根目录为基准，因此在这里兼容旧值。
            return path.parent if path.name.lower() == "auth.json" else path
        return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()

    def _credentials(self) -> tuple[str, str | None, dict[str, Any]]:
        path = self._home() / "auth.json"
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("AUTH_FILE_TOO_LARGE")
        payload = json.loads(path.read_text(encoding="utf-8"))
        tokens = payload.get("tokens") if isinstance(payload, dict) else None
        if not isinstance(tokens, dict):
            raise ValueError("MISSING_OAUTH")
        access_token = str(tokens.get("access_token") or tokens.get("accessToken") or "").strip()
        account_id = str(tokens.get("account_id") or tokens.get("accountId") or "").strip()
        if not access_token:
            raise ValueError("MISSING_OAUTH")
        claims = self._jwt_claims(str(tokens.get("id_token") or tokens.get("idToken") or ""))
        return access_token, account_id or None, claims

    @staticmethod
    def _jwt_claims(token: str) -> dict[str, Any]:
        try:
            segment = token.split(".")[1]
            segment += "=" * (-len(segment) % 4)
            value = json.loads(base64.urlsafe_b64decode(segment).decode("utf-8"))
            return value if isinstance(value, dict) else {}
        except (IndexError, UnicodeDecodeError, ValueError):
            return {}

    def is_configured(self) -> bool:
        try:
            self._credentials()
            return True
        except (OSError, ValueError, json.JSONDecodeError):
            return False

    @staticmethod
    def _window_title(seconds: int, label: str = "") -> str:
        if seconds > 0 and seconds % 604800 == 0:
            weeks = seconds // 604800
            duration = "每周额度" if weeks == 1 else f"{weeks} 周额度"
        elif seconds > 0 and seconds % 86400 == 0:
            days = seconds // 86400
            duration = "每日额度" if days == 1 else f"{days} 天额度"
        elif seconds > 0 and seconds % 3600 == 0:
            duration = f"{seconds // 3600} 小时额度"
        else:
            duration = "订阅额度"
        # 窗口位置并不代表固定时长；名称必须跟随接口时长，避免额度策略变化后误报。
        return f"{label} · {duration}" if label else duration

    @classmethod
    def _window(cls, identifier: str, value: Any, label: str = "") -> QuotaWindow | None:
        if not isinstance(value, dict):
            return None
        try:
            used = max(0.0, min(100.0, float(value["used_percent"])))
            reset = datetime.fromtimestamp(int(value["reset_at"]), timezone.utc)
            seconds = int(value.get("limit_window_seconds") or 0)
        except (KeyError, TypeError, ValueError, OSError):
            return None
        return QuotaWindow(
            identifier,
            cls._window_title(seconds, label),
            used,
            resets_at=reset,
            window_minutes=seconds // 60 if seconds > 0 else None,
        )

    @staticmethod
    def _parse_timestamp(value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @classmethod
    def _scan_session_file(
        cls, path: Path, previous: _SessionUsage | None
    ) -> _SessionUsage | None:
        try:
            stat = path.stat()
        except OSError:
            return previous
        if previous and previous.size == stat.st_size and previous.mtime_ns == stat.st_mtime_ns:
            return previous
        can_append = bool(previous and stat.st_size > previous.size)
        usage = (
            _SessionUsage(
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                offset=previous.offset,
                task_start=previous.task_start,
                longest_task_seconds=previous.longest_task_seconds,
                last_total=previous.last_total,
                last_cached=previous.last_cached,
                peak_total=previous.peak_total,
                daily=dict(previous.daily),
            )
            if can_append and previous
            else _SessionUsage(stat.st_size, stat.st_mtime_ns, 0)
        )
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                if can_append:
                    handle.seek(usage.offset)
                while line := handle.readline():
                    match = _TIMESTAMP.match(line)
                    observed_at = cls._parse_timestamp(match.group(1)) if match else None
                    is_session_meta = '"session_meta"' in line
                    is_event_msg = '"event_msg"' in line
                    has_task_boundary = any(
                        marker in line
                        for marker in ('"task_started"', '"task_complete"', '"turn_aborted"')
                    )
                    has_token_count = '"token_count"' in line
                    # Session records can contain large prompts; only deserialize the
                    # small task-boundary and token-count events needed for local statistics.
                    if not is_session_meta and not (
                        is_event_msg and (has_task_boundary or has_token_count)
                    ):
                        continue
                    try:
                        payload = json.loads(line)
                        record_type = payload.get("type") if isinstance(payload, dict) else None
                        event = payload.get("payload") if isinstance(payload, dict) else None
                        event_type = event.get("type") if isinstance(event, dict) else None
                    except (AttributeError, json.JSONDecodeError):
                        continue
                    if observed_at is not None:
                        if record_type == "session_meta" and usage.task_start is None:
                            # Older Codex logs may not emit task_started for the first task.
                            usage.task_start = observed_at
                        elif record_type == "event_msg" and event_type == "task_started":
                            usage.task_start = observed_at
                        elif record_type == "event_msg" and event_type in {
                            "task_complete",
                            "turn_aborted",
                        }:
                            if usage.task_start is not None and observed_at >= usage.task_start:
                                task_seconds = int(
                                    (observed_at - usage.task_start).total_seconds()
                                )
                                # A session file can contain tasks resumed days apart. Only an
                                # individual task's active boundary may contribute to this metric.
                                usage.longest_task_seconds = max(
                                    usage.longest_task_seconds, task_seconds
                                )
                            usage.task_start = None
                    if record_type != "event_msg" or event_type != "token_count":
                        continue
                    try:
                        info = event.get("info") if isinstance(event, dict) else None
                        total_usage = (
                            info.get("total_token_usage") if isinstance(info, dict) else None
                        )
                        total = int(total_usage.get("total_tokens") or 0)
                        cached = int(total_usage.get("cached_input_tokens") or 0)
                    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                        continue
                    delta_total = total - usage.last_total if total >= usage.last_total else total
                    delta_cached = (
                        cached - usage.last_cached
                        if cached >= usage.last_cached
                        else cached
                    )
                    # Codex 客户端把缓存输入单列计入本地活动；这里沿用相同口径，
                    # 否则累计与单聊天峰值会显著低于客户端统计页。
                    delta = delta_total + delta_cached
                    usage.last_total = total
                    usage.last_cached = cached
                    usage.peak_total = max(usage.peak_total, total + cached)
                    if delta <= 0 or observed_at is None:
                        continue
                    usage_day = observed_at.astimezone().date().isoformat()
                    usage.daily[usage_day] = usage.daily.get(usage_day, 0) + delta
                usage.offset = handle.tell()
        except OSError:
            return previous
        usage.size = stat.st_size
        usage.mtime_ns = stat.st_mtime_ns
        return usage

    @staticmethod
    def _compact_tokens(value: int) -> str:
        denominator, suffix = (100_000_000, "亿") if value >= 100_000_000 else (10_000, "万")
        text = f"{value / denominator:.2f}".rstrip("0").rstrip(".")
        return f"{text}{suffix}"

    @staticmethod
    def _duration_text(seconds: int) -> str:
        hours, remainder = divmod(max(0, seconds), 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}时 {minutes}分"
        return f"{minutes}分 {seconds}秒"

    @staticmethod
    def _streaks(active_days: set[date]) -> tuple[int, int]:
        current = 0
        cursor = datetime.now().astimezone().date()
        while cursor in active_days:
            current += 1
            cursor -= timedelta(days=1)
        longest = running = 0
        previous: date | None = None
        for active_day in sorted(active_days):
            running = running + 1 if previous and active_day == previous + timedelta(days=1) else 1
            longest = max(longest, running)
            previous = active_day
        return current, longest

    def _local_activity(self) -> tuple[tuple[tuple[str, int], ...], tuple[QuotaMetric, ...]]:
        sessions = self._home() / "sessions"
        try:
            paths = [path for path in sessions.rglob("*.jsonl") if path.is_file()]
        except OSError:
            return (), ()
        with self._session_cache_lock:
            current_paths = set(paths)
            for cached_path in set(self._session_cache) - current_paths:
                self._session_cache.pop(cached_path, None)
            usages: list[_SessionUsage] = []
            for path in paths:
                scanned = self._scan_session_file(path, self._session_cache.get(path))
                if scanned is None:
                    continue
                self._session_cache[path] = scanned
                usages.append(scanned)
        daily: dict[str, int] = {}
        longest_seconds = 0
        for usage in usages:
            for usage_day, tokens in usage.daily.items():
                daily[usage_day] = daily.get(usage_day, 0) + tokens
            longest_seconds = max(longest_seconds, usage.longest_task_seconds)
        active_days = {date.fromisoformat(value) for value, tokens in daily.items() if tokens > 0}
        current_streak, longest_streak = self._streaks(active_days)
        total_tokens = sum(daily.values())
        peak_tokens = max((usage.peak_total for usage in usages), default=0)
        statistics = (
            QuotaMetric("累计 Token 数", self._compact_tokens(total_tokens)),
            QuotaMetric("峰值 Token 数", self._compact_tokens(peak_tokens)),
            QuotaMetric("最长聊天时长", self._duration_text(longest_seconds)),
            QuotaMetric("当前连续天数", f"{current_streak} 天"),
            QuotaMetric("最长连续天数", f"{longest_streak} 天"),
        )
        return tuple(sorted(daily.items())), statistics

    @staticmethod
    def _local_only_quota(
        activity: tuple[tuple[str, int], ...], statistics: tuple[QuotaMetric, ...]
    ) -> ProviderQuota | None:
        return ProviderQuota(activity=activity, statistics=statistics) if activity else None

    def fetch_quota(self) -> tuple[ProviderQuota | None, FetchError | None]:
        activity, statistics = self._local_activity()
        try:
            access_token, account_id, claims = self._credentials()
        except (OSError, ValueError, json.JSONDecodeError):
            return None, FetchError(
                "NOT_CONFIGURED", "Codex 订阅额度", "未找到可用的 Codex CLI 登录信息"
            )
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": "TokenMeter",
        }
        if account_id:
            headers["ChatGPT-Account-Id"] = account_id
        try:
            response = self._session.get(
                "https://chatgpt.com/backend-api/wham/usage",
                headers=headers,
                timeout=(5, 20),
            )
        except requests.Timeout:
            return self._local_only_quota(activity, statistics), FetchError(
                "NETWORK_TIMEOUT", "Codex 订阅额度", "连接 Codex 额度服务超时"
            )
        except requests.RequestException:
            return self._local_only_quota(activity, statistics), FetchError(
                "NETWORK_ERROR", "Codex 订阅额度", "无法连接 Codex 额度服务"
            )
        if response.status_code in (401, 403):
            return self._local_only_quota(activity, statistics), FetchError(
                "AUTH_EXPIRED", "Codex 订阅额度", "Codex 登录已过期，请运行 codex 重新登录"
            )
        if response.status_code == 429:
            return self._local_only_quota(activity, statistics), FetchError(
                "RATE_LIMITED", "Codex 订阅额度", "Codex 额度查询过于频繁"
            )
        if not response.ok:
            return self._local_only_quota(activity, statistics), FetchError(
                "SERVER_ERROR",
                "Codex 订阅额度",
                f"Codex 额度服务返回 HTTP {response.status_code}",
            )
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError
        except (requests.JSONDecodeError, ValueError):
            return self._local_only_quota(activity, statistics), FetchError(
                "INVALID_RESPONSE", "Codex 订阅额度", "Codex 额度数据结构已变化"
            )

        rate_limit = payload.get("rate_limit") or {}
        windows = [
            self._window("codex-primary", rate_limit.get("primary_window")),
            self._window("codex-secondary", rate_limit.get("secondary_window")),
        ]
        for index, item in enumerate(payload.get("additional_rate_limits") or []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("limit_name") or item.get("metered_feature") or f"专项额度 {index + 1}")
            extra = item.get("rate_limit") or {}
            windows.extend(
                (
                    self._window(
                        f"codex-extra-{index}-primary", extra.get("primary_window"), name
                    ),
                    self._window(
                        f"codex-extra-{index}-secondary", extra.get("secondary_window"), name
                    ),
                )
            )
        metrics: list[QuotaMetric] = []
        credits = payload.get("credits")
        if isinstance(credits, dict) and credits.get("has_credits"):
            value = "不限量" if credits.get("unlimited") else str(credits.get("balance") or "0")
            metrics.append(QuotaMetric("可用 Credits", value))
        email = str(claims.get("email") or "")
        return ProviderQuota(
            windows=tuple(window for window in windows if window is not None),
            metrics=tuple(metrics),
            activity=activity,
            statistics=statistics,
            account_label=email,
            plan=str(payload.get("plan_type") or ""),
        ), None


__all__ = ["CodexProvider"]
