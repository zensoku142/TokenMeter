"""Read ElevenLabs subscription character credits without generating audio."""

from __future__ import annotations

from decimal import DecimalException

from api.providers.api_balance import _amount
from api.providers.base import FetchError, ProviderQuota, QuotaMetric, QuotaWindow
from api.providers.quota_api import _QuotaAPIProvider, _unix_time


class ElevenLabsProvider(_QuotaAPIProvider):
    id = "elevenlabs"
    name = "ElevenLabs"
    default_base = "https://api.elevenlabs.io/v1"
    official_api_hosts = {"api.elevenlabs.io"}
    credential_header = "xi-api-key"
    dashboard_url = "https://elevenlabs.io/app/subscription"
    support_description = (
        "读取当前订阅字符积分已用量、上限和重置时间；积分不是 LLM Token，超额计费不计作免费余额。"
    )
    credential_fields = {
        "API_KEY": {
            "label": "API Key",
            "secret": True,
            "hint": "具有 User 读取权限的 ElevenLabs API Key",
        },
        "BASE": {"label": "API 地址", "secret": False, "hint": "默认 https://api.elevenlabs.io/v1"},
    }

    def fetch_quota(self) -> tuple[ProviderQuota | None, FetchError | None]:
        source = "ElevenLabs 订阅额度"
        payload, error = self._request_data("/user/subscription", source)
        if error:
            return None, error
        try:
            if payload is None:
                raise ValueError("Missing subscription")
            used = _amount(payload, "character_count", nonnegative=True)
            total = _amount(payload, "character_limit", nonnegative=True)
            if used != used.to_integral_value() or total != total.to_integral_value():
                raise ValueError("Invalid character count")
            reset = _unix_time(payload, "next_character_count_reset_unix")
            plan = payload.get("tier", "ElevenLabs")
            if not isinstance(plan, str):
                raise ValueError("Invalid tier")
            metrics = (QuotaMetric("字符积分", f"{used:,} / {total:,}", "已用 / 订阅上限"),)
            # 零订阅限额不能伪造 0% 的正常进度；保留明确的无额度状态。
            if total == 0:
                return ProviderQuota(
                    metrics=metrics + (QuotaMetric("订阅额度", "无可用订阅额度"),), plan=plan
                ), None
            # 官方允许按量超额，进度封顶但原始已用积分保留；不能把扩展上限当免费额度。
            window = QuotaWindow(
                "subscription", "订阅字符积分", float(min(100, used / total * 100)), reset
            )
            return ProviderQuota(windows=(window,), metrics=metrics, plan=plan), None
        except (KeyError, TypeError, ValueError, OverflowError, OSError, DecimalException):
            return None, self._invalid_response(source)
