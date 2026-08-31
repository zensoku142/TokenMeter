"""Codex subscription quota from the locally authenticated Codex CLI account."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, ClassVar

import requests

from api.http import HttpsSession
from api.providers.base import (
    FetchError,
    Provider,
    ProviderQuota,
    QuotaMetric,
    QuotaWindow,
    build_session,
)

_TIMESTAMP = re.compile(r'^\{"timestamp":"([^"]+)"')
_MAX_SESSION_LINE_BYTES = 1024 * 1024
_ActivityRows = tuple[tuple[str, int], ...]
_ActivityData = tuple[_ActivityRows, _ActivityRows, tuple[QuotaMetric, ...]]


@dataclass
class _SessionUsage:
    size: int
    mtime_ns: int
    offset: int
    task_start: datetime | None = None
    longest_task_seconds: int = 0
    last_total: int = 0
    peak_total: int = 0
    daily: dict[str, int] = field(default_factory=dict)
    file_id: tuple[int, int] | None = None


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
    _subscription_cache: ClassVar[dict[str, tuple[datetime, datetime]]] = {}
    _subscription_cache_ttl = timedelta(hours=6)
    _activity_cache: ClassVar[dict[str, tuple[float, _ActivityData]]] = {}
    _activity_cache_ttl_seconds = 60 * 60

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(config)
        self._session = build_session()
        # 账号统计与套餐日期属于可选慢数据，禁用自动重试，避免弱网时
        # 两个辅助请求把已经成功的额度刷新拖住一分钟以上。
        self._metadata_session = HttpsSession()
        self._activity_data_source = ""
        self._weekly_activity_data_source = ""
        self._statistics_data_source = ""

    def close(self) -> None:
        self._session.close()
        self._metadata_session.close()

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

    def snapshot_identity(self) -> str:
        try:
            access_token, account_id, claims = self._credentials()
        except (OSError, ValueError, json.JSONDecodeError):
            return ""
        identity = (
            account_id
            or str(claims.get("sub") or "").strip()
            or str(claims.get("email") or "").strip().lower()
            or f"token:{access_token}"
        )
        # SQLite 只保存不可逆指纹，避免把账号 ID、邮箱或访问令牌写入缓存。
        return hashlib.sha256(f"codex:{identity}".encode()).hexdigest()

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
        file_id = (stat.st_dev, stat.st_ino)
        if previous and previous.file_id == file_id and previous.size == stat.st_size and previous.mtime_ns == stat.st_mtime_ns:
            return previous
        # 同路径被原子替换时，文件大小增长也不能沿用旧偏移。
        can_append = bool(previous and previous.file_id == file_id and stat.st_size > previous.size)
        usage = (
            _SessionUsage(
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                offset=previous.offset,
                task_start=previous.task_start,
                longest_task_seconds=previous.longest_task_seconds,
                last_total=previous.last_total,
                peak_total=previous.peak_total,
                daily=dict(previous.daily),
            )
            if can_append and previous
            else _SessionUsage(stat.st_size, stat.st_mtime_ns, 0)
        )
        try:
            with path.open("rb") as handle:
                if can_append:
                    handle.seek(usage.offset)
                while True:
                    line_start = handle.tell()
                    raw = handle.readline(_MAX_SESSION_LINE_BYTES + 1)
                    if not raw:
                        break
                    oversized = len(raw) > _MAX_SESSION_LINE_BYTES
                    if oversized:
                        # Prompt/工具输出可能包含巨型单行；分块跳过，不能按行无界分配内存。
                        while raw and not raw.endswith(b"\n"):
                            raw = handle.readline(_MAX_SESSION_LINE_BYTES + 1)
                    if not raw.endswith(b"\n"):
                        # 写入方尚未提交尾行；保留起点，追加后重读整条事件（含 UTF-8 分段）。
                        handle.seek(line_start)
                        break
                    if oversized:
                        continue
                    line = raw.decode("utf-8", errors="replace")
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
                        if not isinstance(total_usage, dict):
                            continue
                        total = int(total_usage.get("total_tokens") or 0)
                    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                        continue
                    delta_total = total - usage.last_total if total >= usage.last_total else total
                    # total_tokens 已包含缓存输入；再次叠加 cached_input_tokens 会重复计数。
                    delta = delta_total
                    usage.last_total = total
                    usage.peak_total = max(usage.peak_total, total)
                    if delta <= 0 or observed_at is None:
                        continue
                    usage_day = observed_at.astimezone().date().isoformat()
                    usage.daily[usage_day] = usage.daily.get(usage_day, 0) + delta
                usage.offset = handle.tell()
        except OSError:
            return previous
        usage.size = stat.st_size
        usage.mtime_ns = stat.st_mtime_ns
        usage.file_id = file_id
        return usage

    @staticmethod
    def _compact_tokens(value: int) -> str:
        denominator, suffix = (100_000_000, "亿") if value >= 100_000_000 else (10_000, "万")
        # Codex 账号页保留 1 位小数并去尾零；采用相同显示口径避免
        # 原始值相同却因 26.08 亿/26.1 亿的精度差异看起来不一致。
        text = f"{value / denominator:.1f}".rstrip("0").rstrip(".")
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
        today_only = getattr(self, "_local_today_only", False)
        today = datetime.now().astimezone().date()
        day_start = datetime.combine(today, datetime.min.time()).astimezone().timestamp()
        try:
            paths = [
                path for path in sessions.rglob("*.jsonl")
                if path.is_file() and (not today_only or path.stat().st_mtime >= day_start)
            ]
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
                if today_only and usage_day != today.isoformat():
                    continue
                daily[usage_day] = daily.get(usage_day, 0) + tokens
            longest_seconds = max(longest_seconds, usage.longest_task_seconds)
        if today_only:
            # 按最后修改时间选活跃文件，保留跨午夜续聊；当天补值不再计算被丢弃的全量统计。
            return tuple(sorted(daily.items())), ()
        active_days = {date.fromisoformat(value) for value, tokens in daily.items() if tokens > 0}
        current_streak, longest_streak = self._streaks(active_days)
        total_tokens = sum(daily.values())
        peak_tokens = max((usage.peak_total for usage in usages), default=0)
        detail = "服务端统计暂不可用，当前显示本机 Codex 会话日志估算"
        statistics = (
            QuotaMetric("累计 Token 数", self._compact_tokens(total_tokens), detail, total_tokens, "tokens"),
            QuotaMetric("峰值 Token 数", self._compact_tokens(peak_tokens), detail, peak_tokens, "tokens"),
            QuotaMetric("最长任务时长", self._duration_text(longest_seconds), detail, longest_seconds, "seconds"),
            QuotaMetric("当前连续天数", f"{current_streak} 天", detail, current_streak, "days"),
            QuotaMetric("最长连续天数", f"{longest_streak} 天", detail, longest_streak, "days"),
        )
        return tuple(sorted(daily.items())), statistics

    def _activity_cache_key(
        self, account_id: str | None, claims: Mapping[str, Any]
    ) -> str:
        if account_id:
            return f"account:{account_id}"
        email = str(claims.get("email") or "").strip().lower()
        if email:
            return f"email:{email}"
        return f"home:{self._home().resolve(strict=False)}"

    def _activity_snapshot(
        self,
        cache_key: str,
        headers: dict[str, str] | None = None,
    ) -> _ActivityData:
        now = time.monotonic()
        with self._session_cache_lock:
            cached = self._activity_cache.get(cache_key)
        if cached and (
            now - cached[0] < self._activity_cache_ttl_seconds or headers is None
        ):
            activity, weekly_activity, statistics = cached[1]
            self._activity_data_source = "cache" if activity else ""
            self._weekly_activity_data_source = "cache" if weekly_activity else ""
            self._statistics_data_source = "cache" if statistics else ""
            return cached[1]

        # 额度窗口仍按用户配置刷新；账号统计接口独立限频为一小时。
        # 本机会话只补近 7 天图的当天值，不能污染官方热力图和底部统计。
        today = datetime.now().astimezone().date().isoformat()
        profile_activity = self._profile_activity(headers) if headers is not None else None
        local_today_tokens = 0
        if headers is None or (
            profile_activity is not None and today not in dict(profile_activity[0])
        ) or (profile_activity is None and cached is None):
            # 官方已有当天数据时不访问会话树；官方失败且有缓存时同样无需本地扫描。
            self._local_today_only = True
            local_rows, _local_statistics = self._local_activity()
            local_today_tokens = dict(local_rows).get(today, 0)
        if headers is None:
            weekly_activity = ((today, local_today_tokens),) if local_today_tokens else ()
            self._activity_data_source = ""
            self._weekly_activity_data_source = "local" if weekly_activity else ""
            self._statistics_data_source = ""
            return (), weekly_activity, ()
        else:
            # 统计接口暂时不可用时优先展示最后一次官方结果；从未成功过时
            # 只允许近 7 天图显示本机当天估算，其他统计保持空白。
            if profile_activity is not None:
                activity, statistics = profile_activity
                weekly = dict(activity)
                merged_local_today = local_today_tokens > 0 and today not in weekly
                if merged_local_today:
                    weekly[today] = local_today_tokens
                result = activity, tuple(sorted(weekly.items())), statistics
                self._activity_data_source = "interface"
                self._weekly_activity_data_source = (
                    "mixed" if merged_local_today else "interface"
                )
                self._statistics_data_source = "interface"
            elif cached is not None:
                result = cached[1]
                activity, weekly_activity, statistics = result
                self._activity_data_source = "cache" if activity else ""
                self._weekly_activity_data_source = "cache" if weekly_activity else ""
                self._statistics_data_source = "cache" if statistics else ""
            else:
                weekly_activity = (
                    ((today, local_today_tokens),) if local_today_tokens else ()
                )
                result = (), weekly_activity, ()
                self._activity_data_source = ""
                self._weekly_activity_data_source = (
                    "local" if weekly_activity else ""
                )
                self._statistics_data_source = ""
        with self._session_cache_lock:
            self._activity_cache[cache_key] = (time.monotonic(), result)
        return result

    def _local_only_quota(self, cache_key: str) -> ProviderQuota | None:
        activity, weekly_activity, statistics = self._activity_snapshot(cache_key)
        return (
            ProviderQuota(
                activity=activity,
                statistics=statistics,
                activity_source=self._activity_data_source,
                weekly_activity=weekly_activity,
                weekly_activity_source=self._weekly_activity_data_source,
                statistics_source=self._statistics_data_source,
            )
            if activity or weekly_activity or statistics
            else None
        )

    @staticmethod
    def _nonnegative_int(value: Any) -> int | None:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return None

    def _profile_activity(
        self, headers: dict[str, str]
    ) -> tuple[tuple[tuple[str, int], ...], tuple[QuotaMetric, ...]] | None:
        try:
            response = self._metadata_session.get(
                "https://chatgpt.com/backend-api/wham/profiles/me",
                headers=headers,
                timeout=(3, 5),
            )
        except requests.RequestException:
            return None
        if not response.ok:
            return None
        try:
            payload = response.json()
        except (requests.JSONDecodeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        metadata = payload.get("metadata")
        if isinstance(metadata, dict) and str(metadata.get("stats_error") or "").strip():
            return None
        stats = payload.get("stats")
        if not isinstance(stats, dict):
            return None

        daily: dict[str, int] = {}
        buckets = stats.get("daily_usage_buckets")
        if isinstance(buckets, list):
            for bucket in buckets:
                if not isinstance(bucket, dict):
                    continue
                usage_day = str(bucket.get("start_date") or "").strip()
                try:
                    date.fromisoformat(usage_day)
                except ValueError:
                    continue
                tokens = self._nonnegative_int(bucket.get("tokens"))
                if tokens is None:
                    continue
                daily[usage_day] = daily.get(usage_day, 0) + tokens

        total_tokens = self._nonnegative_int(stats.get("lifetime_tokens"))
        peak_tokens = self._nonnegative_int(stats.get("peak_daily_tokens"))
        longest_seconds = self._nonnegative_int(stats.get("longest_running_turn_sec"))
        current_streak = self._nonnegative_int(stats.get("current_streak_days"))
        longest_streak = self._nonnegative_int(stats.get("longest_streak_days"))
        if not daily and all(
            value is None
            for value in (
                total_tokens,
                peak_tokens,
                longest_seconds,
                current_streak,
                longest_streak,
            )
        ):
            return None

        detail = "来自 Codex 账号统计"
        statistics = (
            QuotaMetric(
                "累计 Token 数",
                "--" if total_tokens is None else self._compact_tokens(total_tokens),
                detail,
                total_tokens, "tokens",
            ),
            QuotaMetric(
                "峰值 Token 数",
                "--" if peak_tokens is None else self._compact_tokens(peak_tokens),
                detail,
                peak_tokens, "tokens",
            ),
            QuotaMetric(
                "最长任务时长",
                "--" if longest_seconds is None else self._duration_text(longest_seconds),
                detail,
                longest_seconds, "seconds",
            ),
            QuotaMetric(
                "当前连续天数",
                "--" if current_streak is None else f"{current_streak} 天",
                detail,
                current_streak, "days",
            ),
            QuotaMetric(
                "最长连续天数",
                "--" if longest_streak is None else f"{longest_streak} 天",
                detail,
                longest_streak, "days",
            ),
        )
        return tuple(sorted(daily.items())), statistics

    def _subscription_active_until(
        self, headers: dict[str, str], account_id: str
    ) -> datetime | None:
        now = datetime.now(timezone.utc)
        with self._session_cache_lock:
            cached = self._subscription_cache.get(account_id)
        if cached:
            cached_at, active_until = cached
            active_now = datetime.now(active_until.tzinfo)
            if now - cached_at < self._subscription_cache_ttl and active_until > active_now:
                return active_until
        try:
            response = self._metadata_session.get(
                "https://chatgpt.com/backend-api/subscriptions",
                headers=headers,
                params={"account_id": account_id},
                timeout=(3, 5),
            )
        except requests.RequestException:
            return None
        if not response.ok:
            return None
        try:
            payload = response.json()
        except (requests.JSONDecodeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        active_until = self._parse_timestamp(str(payload.get("active_until") or ""))
        if active_until is not None:
            with self._session_cache_lock:
                self._subscription_cache[account_id] = (now, active_until)
        return active_until

    def fetch_quota(self) -> tuple[ProviderQuota | None, FetchError | None]:
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
            "originator": "Codex Desktop",
        }
        if account_id:
            headers["ChatGPT-Account-Id"] = account_id
        activity_cache_key = self._activity_cache_key(account_id, claims)
        try:
            response = self._session.get(
                "https://chatgpt.com/backend-api/wham/usage",
                headers=headers,
                timeout=(3, 10),
            )
        except requests.Timeout:
            return self._local_only_quota(activity_cache_key), FetchError(
                "NETWORK_TIMEOUT", "Codex 订阅额度", "连接 Codex 额度服务超时"
            )
        except requests.RequestException:
            return self._local_only_quota(activity_cache_key), FetchError(
                "NETWORK_ERROR", "Codex 订阅额度", "无法连接 Codex 额度服务"
            )
        if response.status_code in (401, 403):
            return self._local_only_quota(activity_cache_key), FetchError(
                "AUTH_EXPIRED", "Codex 订阅额度", "Codex 登录已过期，请运行 codex 重新登录"
            )
        if response.status_code == 429:
            return self._local_only_quota(activity_cache_key), FetchError(
                "RATE_LIMITED", "Codex 订阅额度", "Codex 额度查询过于频繁"
            )
        if not response.ok:
            return self._local_only_quota(activity_cache_key), FetchError(
                "SERVER_ERROR",
                "Codex 订阅额度",
                f"Codex 额度服务返回 HTTP {response.status_code}",
            )
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError
        except (requests.JSONDecodeError, ValueError):
            return self._local_only_quota(activity_cache_key), FetchError(
                "INVALID_RESPONSE", "Codex 订阅额度", "Codex 额度数据结构已变化"
            )

        activity, weekly_activity, statistics = self._activity_snapshot(
            activity_cache_key, headers
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
        subscription_account_id = account_id or str(payload.get("account_id") or "").strip()
        active_until = (
            self._subscription_active_until(headers, subscription_account_id)
            if subscription_account_id
            else None
        )
        return ProviderQuota(
            windows=tuple(window for window in windows if window is not None),
            metrics=tuple(metrics),
            activity=activity,
            statistics=statistics,
            account_label=email,
            plan=str(payload.get("plan_type") or ""),
            account_plan_active_until=active_until,
            activity_source=self._activity_data_source,
            weekly_activity=weekly_activity,
            weekly_activity_source=self._weekly_activity_data_source,
            statistics_source=self._statistics_data_source,
        ), None


__all__ = ["CodexProvider"]
