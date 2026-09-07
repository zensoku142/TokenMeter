"""Claude Code subscription quota from read-only OAuth or a statusline snapshot."""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from api.providers.base import (
    FetchError,
    Provider,
    ProviderQuota,
    QuotaMetric,
    QuotaWindow,
    build_session,
)

_MAX_SNAPSHOT_BYTES = 16 * 1024
_SNAPSHOT_TTL = timedelta(minutes=15)
_WINDOWS = (("five_hour", "5 小时额度", 300), ("seven_day", "每周额度", 10080))


class ClaudeProvider(Provider):
    id = "claude"
    name = "Claude Code"
    supports_subscription_quota = True
    official_api_hosts = {"api.anthropic.com"}
    support_description = (
        "优先只读本机 Claude Code OAuth 登录查询订阅额度；也支持状态栏快照。"
        "OAuth 为实验性客户端接口，过期需在 Claude Code 重新登录。"
    )
    dashboard_url = "https://claude.ai/settings/usage"
    credential_fields = {
        "ACCESS_TOKEN": {
            "label": "OAuth Access Token（可选）", "secret": True, "optional": True,
            "hint": "留空读取本机 Claude Code 登录；需要 user:profile 权限，setup-token 不适用",
        },
        "CREDENTIALS_FILE": {
            "label": "CLI 凭据文件（可选）", "secret": False, "optional": True,
            "hint": "默认 ~/.claude/.credentials.json；支持 CLAUDE_CONFIG_DIR，仅读取不修改",
        },
        "STATUSLINE_FILE": {
            "label": "状态栏快照文件（可选）",
            "secret": False,
            "optional": True,
            "hint": "填写后优先使用快照；默认 ~/.claude/tokenmeter-usage.json，需配置 claude_statusline.py",
        },
    }

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(config)
        self._session = build_session()

    def close(self) -> None:
        self._session.close()

    def _explicit_snapshot(self) -> bool:
        return bool(str(self.config_get("CLAUDE_STATUSLINE_FILE", "")).strip())

    def _explicit_oauth(self) -> bool:
        return any(str(self.config_get(key, "")).strip()
                   for key in ("CLAUDE_ACCESS_TOKEN", "CLAUDE_CREDENTIALS_FILE"))

    def _credentials_path(self) -> Path:
        value = str(self.config_get("CLAUDE_CREDENTIALS_FILE", "")).strip()
        if value:
            return Path(value).expanduser()
        directory = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
        return (Path(directory).expanduser() if directory else Path.home() / ".claude") / ".credentials.json"

    def _read_oauth_credentials(self) -> dict[str, Any]:
        token = str(self.config_get("CLAUDE_ACCESS_TOKEN", "")).strip()
        if token:
            return {"accessToken": token}
        # 只读受限大小的登录文件，不扫描会话、浏览器或执行 CLI/凭据脚本。
        with self._credentials_path().open("rb") as handle:
            raw = handle.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise ValueError("INVALID_CREDENTIALS")
        payload = json.loads(raw)
        oauth = payload.get("claudeAiOauth") if isinstance(payload, dict) else None
        if not isinstance(oauth, dict):
            raise ValueError("INVALID_CREDENTIALS")
        token = oauth.get("accessToken")
        if not isinstance(token, str) or not token.strip():
            raise ValueError("INVALID_CREDENTIALS")
        return {**oauth, "accessToken": token.strip()}

    def _snapshot_path(self) -> Path:
        configured = str(self.config_get("CLAUDE_STATUSLINE_FILE", "")).strip()
        return (
            Path(configured).expanduser()
            if configured
            else Path.home() / ".claude" / "tokenmeter-usage.json"
        )

    def is_configured(self) -> bool:
        try:
            if not self._explicit_snapshot():
                try:
                    return bool(self._read_oauth_credentials())
                except FileNotFoundError:
                    if self._explicit_oauth():
                        return False
            return self._snapshot_path().is_file()
        except (OSError, ValueError, TypeError, OverflowError):
            return False

    def _read_snapshot(self) -> tuple[dict[str, Any], datetime]:
        # 有界读取防止误选会话日志；不读取 Claude 的凭据、对话或 transcript。
        with self._snapshot_path().open("rb") as handle:
            raw = handle.read(_MAX_SNAPSHOT_BYTES + 1)
        if len(raw) > _MAX_SNAPSHOT_BYTES:
            raise ValueError("快照文件过大")
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or type(payload.get("schema_version")) is not int
            or payload["schema_version"] != 1
        ):
            raise ValueError("未知快照格式")
        if not isinstance(payload.get("rate_limits"), dict):
            raise ValueError("缺少额度字段")
        observed = payload.get("observed_at")
        if not isinstance(observed, str):
            raise ValueError("缺少快照时间")
        observed_at = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("快照时间缺少时区")
        observed_at = observed_at.astimezone(timezone.utc)
        # 容忍小幅时钟偏差，但未来时间不能让快照永久保持新鲜。
        if observed_at > datetime.now(timezone.utc) + timedelta(minutes=1):
            raise ValueError("快照时间位于未来")
        return payload, observed_at

    def snapshot_identity(self) -> str:
        try:
            if not self._explicit_snapshot():
                try:
                    oauth = self._read_oauth_credentials()
                except FileNotFoundError:
                    if self._explicit_oauth():
                        return ""
                else:
                    # OAuth 与快照使用独立身份；令牌/路径变化都使旧缓存失效。
                    scope = ["oauth", oauth["accessToken"], str(self._credentials_path().resolve())]
                    return hashlib.sha256(json.dumps(scope).encode()).hexdigest()
            payload, _ = self._read_snapshot()
            scope = payload.get("account_scope")
            if not isinstance(scope, str) or not scope.strip():
                # 官方状态栏未提供账号标识；默认禁用持久缓存，避免换账号串数。
                return ""
            identity = [str(self._snapshot_path().resolve()), scope.strip()]
            return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        except (OSError, ValueError, OverflowError):
            return ""

    @staticmethod
    def _window(identifier: str, title: str, minutes: int, data: Any) -> QuotaWindow:
        if not isinstance(data, dict):
            raise ValueError("额度窗口结构无效")
        used = data.get("used_percentage")
        if (
            not isinstance(used, (int, float)) or isinstance(used, bool)
            or not math.isfinite(used) or not 0 <= used <= 100
        ):
            raise ValueError("额度百分比无效")
        reset = data.get("resets_at")
        if (
            not isinstance(reset, (int, float)) or isinstance(reset, bool)
            or not math.isfinite(reset) or reset <= 0
        ):
            raise ValueError("额度重置时间无效")
        resets_at = datetime.fromtimestamp(reset, timezone.utc)
        return QuotaWindow(
            identifier, title, float(used), resets_at=resets_at, window_minutes=minutes
        )

    def fetch_quota(self) -> tuple[ProviderQuota | None, FetchError | None]:
        if self._explicit_snapshot():
            return self._fetch_snapshot()
        try:
            oauth = self._read_oauth_credentials()
        except FileNotFoundError:
            # 仅默认登录不存在时回退旧快照；显式文件错误或已登录账号的错误不能串号回退。
            if not self._explicit_oauth():
                return self._fetch_snapshot()
            return None, self._oauth_error("NOT_CONFIGURED")
        except OSError:
            return None, self._oauth_error("FILE_ERROR")
        except (ValueError, TypeError, OverflowError):
            return None, self._oauth_error("INVALID_CREDENTIALS")
        return self._fetch_oauth(oauth)

    @staticmethod
    def _oauth_error(code: str) -> FetchError:
        messages = {
            "NOT_CONFIGURED": "未找到 Claude Code 登录，请先在 Claude Code /login 或配置状态栏快照",
            "AUTH_EXPIRED": "Claude Code OAuth 登录已过期，请在 Claude Code /login 或更新 Access Token",
            "SCOPE_REQUIRED": "此 Claude 令牌缺少 user:profile 权限；请使用 /login 登录，setup-token 不支持额度查询",
            "INVALID_CREDENTIALS": "Claude Code 登录文件格式无效，请重新登录或检查所选文件",
            "FILE_ERROR": "无法读取 Claude Code 登录文件",
            "PERMISSION_DENIED": "此 Claude 登录无权读取额度，请检查订阅与 user:profile 权限",
            "RATE_LIMITED": "Claude 额度查询过于频繁，请稍后重试",
            "NETWORK_TIMEOUT": "连接 Claude 额度服务超时",
            "NETWORK_ERROR": "无法连接 Claude 额度服务",
            "SERVER_ERROR": "Claude 额度服务暂时异常",
            "INVALID_RESPONSE": "Claude 额度数据结构已变化，请查看官方控制台",
            "QUOTA_UNAVAILABLE": "此账号未返回 Claude 订阅额度，请查看官方控制台",
        }
        return FetchError(code, "Claude Code 额度", messages[code])

    def _fetch_oauth(self, oauth: dict[str, Any]) -> tuple[ProviderQuota | None, FetchError | None]:
        try:
            expiry = oauth.get("expiresAt")
            if expiry is not None:
                if isinstance(expiry, bool) or not isinstance(expiry, (int, float)):
                    raise ValueError
                if not math.isfinite(expiry) or expiry <= 0:
                    raise ValueError
                # 刷新会改写原客户端登录状态；本程序不交换 refreshToken，也不执行 CLI。
                if expiry <= datetime.now(timezone.utc).timestamp() * 1000:
                    return None, self._oauth_error("AUTH_EXPIRED")
            scopes = oauth.get("scopes")
            if scopes is not None:
                if not isinstance(scopes, list) or not all(isinstance(item, str) for item in scopes):
                    raise ValueError
                if "user:profile" not in scopes:
                    return None, self._oauth_error("SCOPE_REQUIRED")
        except (ValueError, TypeError, OverflowError):
            return None, self._oauth_error("INVALID_CREDENTIALS")
        try:
            response = self._session.get(
                "https://api.anthropic.com/api/oauth/usage",
                headers={"Authorization": f"Bearer {oauth['accessToken']}",
                         "anthropic-beta": "oauth-2025-04-20", "Accept": "application/json",
                         "Content-Type": "application/json",
                         # 客户端额度入口沿用 CodexBar 的已知兼容 UA，无需执行本机 CLI 探测版本。
                         "User-Agent": "claude-code/2.1.0"},
                timeout=(3, 10),
                # 固定官方地址，禁止重定向携带登录令牌离开 Anthropic。
                allow_redirects=False,
            )
        except requests.Timeout:
            return None, self._oauth_error("NETWORK_TIMEOUT")
        except requests.RequestException:
            return None, self._oauth_error("NETWORK_ERROR")
        if response.status_code != 200:
            code = {401: "AUTH_EXPIRED", 403: "PERMISSION_DENIED", 429: "RATE_LIMITED"}.get(
                response.status_code, "SERVER_ERROR" if response.status_code >= 500 else "INVALID_RESPONSE"
            )
            return None, self._oauth_error(code)
        try:
            quota = self._parse_oauth(response.json(), oauth.get("subscriptionType"))
        except (ValueError, TypeError, OverflowError):
            return None, self._oauth_error("INVALID_RESPONSE")
        if not quota.windows:
            return None, self._oauth_error("QUOTA_UNAVAILABLE")
        return quota, None

    @staticmethod
    def _oauth_window(identifier: str, title: str, minutes: int, data: Any) -> QuotaWindow | None:
        if data is None:
            return None
        if not isinstance(data, dict):
            raise ValueError("INVALID_RESPONSE")
        used = data.get("utilization")
        # 未启用的窗口可为 null，未提供利用率时不能伪造 0% 已用。
        if used is None:
            return None
        if (isinstance(used, bool) or not isinstance(used, (int, float))
                or not math.isfinite(used) or not 0 <= used <= 100):
            raise ValueError("INVALID_RESPONSE")
        reset = data.get("resets_at")
        resets_at = None
        if reset not in (None, ""):
            if not isinstance(reset, str):
                raise ValueError("INVALID_RESPONSE")
            resets_at = datetime.fromisoformat(reset.replace("Z", "+00:00"))
            if resets_at.tzinfo is None:
                raise ValueError("INVALID_RESPONSE")
        return QuotaWindow(identifier, title, float(used), resets_at=resets_at, window_minutes=minutes)

    @classmethod
    def _parse_oauth(cls, payload: Any, plan: Any = None) -> ProviderQuota:
        if not isinstance(payload, dict) or payload.get("error") is not None:
            raise ValueError("INVALID_RESPONSE")
        windows = []
        for identifier, title, minutes in (*_WINDOWS,
            ("seven_day_sonnet", "Sonnet 每周额度", 10080),
            ("seven_day_opus", "Opus 每周额度", 10080),
        ):
            window = cls._oauth_window(identifier, title, minutes, payload.get(identifier))
            if window is not None:
                windows.append(window)
        for key in ("seven_day_routines", "seven_day_cowork"):
            window = cls._oauth_window("seven_day_routines", "Routines 每周额度", 10080, payload.get(key))
            if window is not None:
                windows.append(window)
                break
        limits = payload.get("limits")
        if limits is not None:
            if not isinstance(limits, list):
                raise ValueError("INVALID_RESPONSE")
            for limit in limits:
                if not isinstance(limit, dict):
                    raise ValueError("INVALID_RESPONSE")
                if limit.get("is_active") is not None and not isinstance(limit["is_active"], bool):
                    raise ValueError("INVALID_RESPONSE")
                if limit.get("kind") != "weekly_scoped" or limit.get("is_active") is False:
                    continue
                scope = limit.get("scope")
                model = scope.get("model") if isinstance(scope, dict) else None
                if not isinstance(model, dict):
                    continue
                label = model.get("display_name") or model.get("id")
                if not isinstance(label, str) or not label.strip():
                    continue
                name = label.strip()
                # All models 属于主每周窗口；仅在旧顶层字段缺失时补充，避免重复计数。
                generic = name.lower() == "all models"
                identifier = "seven_day" if generic else "weekly_scoped-" + hashlib.sha256(name.encode()).hexdigest()[:16]
                if any(window.id == identifier for window in windows):
                    continue
                window = cls._oauth_window(identifier, "每周额度" if generic else f"{name} 每周额度", 10080,
                    {"utilization": limit.get("percent"), "resets_at": limit.get("resets_at")})
                if window is not None:
                    windows.append(window)
        return ProviderQuota(windows=tuple(windows), plan=plan if isinstance(plan, str) else "")

    def _fetch_snapshot(self) -> tuple[ProviderQuota | None, FetchError | None]:
        try:
            payload, observed_at = self._read_snapshot()
            limits = payload["rate_limits"]
            # 窗口可以分别缺失；缺失不代表 0% 或无限额度。
            windows = tuple(
                self._window(identifier, title, minutes, limits[identifier])
                for identifier, title, minutes in _WINDOWS
                if identifier in limits
            )
            if not windows:
                return None, FetchError(
                    "NO_DATA", "订阅额度", "Claude 尚未提供额度；Pro/Max 会话首次响应后才会生成"
                )
            quota = ProviderQuota(
                windows=windows,
                metrics=(
                    QuotaMetric(
                        "快照时间",
                        observed_at.strftime("%H:%M"),
                        observed_at.strftime("%Y-%m-%d UTC"),
                    ),
                ),
                plan="Claude Code",
                source="local_snapshot",
            )
            if datetime.now(timezone.utc) - observed_at > _SNAPSHOT_TTL:
                # 刷新成功时间与源观测时间不同；过期快照不能作为本轮成功结果入库。
                return None, FetchError(
                    "STALE_DATA",
                    "订阅额度",
                    "Claude 状态栏超过 15 分钟未更新，请在 Claude 中继续使用后刷新",
                )
            return quota, None
        except FileNotFoundError:
            return None, FetchError(
                "NOT_CONFIGURED",
                "订阅额度",
                "未找到 Claude 状态栏快照，请先配置 claude_statusline.py",
            )
        except OSError:
            return None, FetchError("FILE_ERROR", "订阅额度", "无法读取 Claude 状态栏快照文件")
        except (ValueError, TypeError, OverflowError):
            return None, FetchError(
                "INVALID_RESPONSE", "订阅额度", "Claude 状态栏快照格式或额度字段无效"
            )


__all__ = ["ClaudeProvider"]
