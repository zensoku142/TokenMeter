"""Kimi Code subscription quotas; distinct from Moonshot API account balance."""

from __future__ import annotations

from datetime import datetime
from decimal import DecimalException
from typing import Any

from api.providers.api_balance import _amount
from api.providers.base import FetchError, ProviderQuota, QuotaWindow
from api.providers.quota_api import _QuotaAPIProvider


class KimiProvider(_QuotaAPIProvider):
    id = "kimi"
    name = "Kimi Coding"
    default_base = "https://api.kimi.com/coding/v1"
    official_api_hosts = {"api.kimi.com"}
    dashboard_url = "https://www.kimi.com/code/console"
    support_description = "读取 Kimi Coding 周额度与接口实际返回的短周期额度、重置时间；不代表 Moonshot API 余额或 Token 数。"
    credential_fields = {
        "API_KEY": {
            "label": "Coding API Key",
            "secret": True,
            "hint": "Kimi Code 控制台的 API Key，与 Moonshot 开放平台密钥不同",
        },
        "BASE": {
            "label": "API 地址",
            "secret": False,
            "hint": "默认 https://api.kimi.com/coding/v1",
        },
    }

    def fetch_quota(self) -> tuple[ProviderQuota | None, FetchError | None]:
        source = "Kimi Coding 订阅额度"
        payload, error = self._request_data("/usages", source)
        if error:
            return None, error
        try:
            if payload is None:
                raise ValueError("Missing payload")
            windows = []
            # 第一方 Kimi CLI 将顶层 usage 定义为周额度；不根据 limit=100 推断 Token 数。
            if "usage" in payload:
                windows.append(self._window(payload["usage"], "weekly", "周额度", 7 * 24 * 60))
            entries = payload.get("limits", [])
            if not isinstance(entries, list):
                raise ValueError("Invalid limits")
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict) or not isinstance(entry.get("window"), dict):
                    raise ValueError("Invalid window")
                window = entry["window"]
                duration = _amount(window, "duration", nonnegative=True)
                scale = {
                    "TIME_UNIT_SECOND": 1 / 60,
                    "TIME_UNIT_MINUTE": 1,
                    "TIME_UNIT_HOUR": 60,
                    "TIME_UNIT_DAY": 1440,
                }.get(window.get("timeUnit"))
                if duration <= 0 or scale is None:
                    raise ValueError("Unknown window unit")
                minutes = float(duration) * scale
                if not minutes.is_integer() or not 1 <= minutes <= 525600:
                    raise ValueError("Invalid window duration")
                title = "5 小时额度" if minutes == 300 else f"{int(minutes)} 分钟额度"
                detail = entry.get("detail", entry)
                windows.append(self._window(detail, f"limit_{index}", title, int(minutes)))
            if not windows:
                return None, self._error("QUOTA_UNAVAILABLE", source, "未返回可量化的订阅额度")
            return ProviderQuota(windows=tuple(windows), plan="Kimi Coding"), None
        except (KeyError, TypeError, ValueError, OverflowError, DecimalException):
            return None, self._invalid_response(source)

    @staticmethod
    def _window(data: Any, window_id: str, title: str, minutes: int) -> QuotaWindow:
        if not isinstance(data, dict):
            raise ValueError("Invalid quota")
        total = _amount(data, "limit", nonnegative=True)
        if total <= 0:
            raise ValueError("Unknown entitlement")
        remaining = _amount(data, "remaining", nonnegative=True) if "remaining" in data else None
        if remaining is not None and remaining > total:
            raise ValueError("Invalid remaining quota")
        if "used" in data:
            used = _amount(data, "used", nonnegative=True)
        elif remaining is not None:
            used = total - remaining
        else:
            raise ValueError("Unknown usage")
        reset = None
        for key in ("resetTime", "reset_at", "resetAt", "reset_time"):
            if key not in data or data[key] is None:
                continue
            if not isinstance(data[key], str):
                raise ValueError("Invalid reset")
            # Python 支持纳秒 RFC3339 的截断；要求时区，避免把 UTC 重置误当本地时间。
            reset = datetime.fromisoformat(data[key].replace("Z", "+00:00"))
            if reset.tzinfo is None:
                raise ValueError("Missing reset timezone")
            break
        return QuotaWindow(window_id, title, float(min(100, used / total * 100)), reset, minutes)
