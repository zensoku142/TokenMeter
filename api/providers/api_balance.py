"""Official API balance and key-spend providers."""

from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import Decimal, DecimalException
from typing import Any
from urllib.parse import urlsplit

import requests

from api.http import is_https_url
from api.providers.base import (
    FetchError,
    Provider,
    ProviderBalance,
    ProviderSummary,
    build_session,
)


def _amount(data: Mapping[str, Any], field: str, *, nonnegative: bool = False) -> Decimal:
    value = data[field]
    # 缺失、布尔或非有限金额不能降级为零，否则会产生误导性的余额告警。
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("Invalid amount")
    result = Decimal(str(value))
    # 展示与缓存使用浮点值；极大 Decimal 即使有限，转换后也可能变成 Infinity。
    if not result.is_finite() or not math.isfinite(float(result)) or (nonnegative and result < 0):
        raise ValueError("Invalid amount")
    return result


class _APIKeyProvider(Provider):
    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(config)
        self._session = build_session()
        self._response_cache: dict[str, tuple[dict[str, Any] | None, FetchError | None]] = {}

    def close(self) -> None:
        self._session.close()

    def reset_refresh_cache(self) -> None:
        # 同轮重复读取共用请求结果；失败也缓存，下一轮重新尝试，避免持续重试无效凭据。
        self._response_cache.clear()

    def _base_url(self) -> str:
        return str(self.config_get(f"{self.id.upper()}_BASE", "") or self.default_base).strip().rstrip("/")

    def _credential(self) -> str:
        return str(self.config_get(f"{self.id.upper()}_API_KEY", "") or "").strip()

    def is_configured(self) -> bool:
        return bool(self._credential())

    def _error(self, code: str, source: str, message: str) -> FetchError:
        return FetchError(code, source, f"{self.name} {message}")

    def _invalid_response(self, source: str) -> FetchError:
        return self._error("INVALID_RESPONSE", source, "返回了无效数据或接口结构已变化")

    def _request_data(
        self, path: str, source: str
    ) -> tuple[dict[str, Any] | None, FetchError | None]:
        if path not in self._response_cache:
            self._response_cache[path] = self._fetch_data(path, source)
        return self._response_cache[path]

    def _fetch_data(
        self, path: str, source: str
    ) -> tuple[dict[str, Any] | None, FetchError | None]:
        key = self._credential()
        if not key:
            return None, self._error("NOT_CONFIGURED", source, "尚未配置 API Key")
        base = self._base_url()
        try:
            parsed = urlsplit(base)
            valid_base = is_https_url(base) and not parsed.query and not parsed.fragment
        except ValueError:
            valid_base = False
        if not valid_base:
            return None, self._error("INVALID_CONFIG", source, "平台地址须为不含查询参数的 HTTPS API 地址")
        try:
            response = self._session.get(
                f"{base}{path}",
                headers={"Accept": "application/json", "Authorization": f"Bearer {key}"},
                timeout=20,
                # API Key 不能随服务端重定向转发；地址变更应由用户在配置中明确更新。
                allow_redirects=False,
            )
        except requests.Timeout:
            return None, self._error("NETWORK_TIMEOUT", source, "请求超时")
        except requests.RequestException:
            return None, self._error("NETWORK_ERROR", source, "连接失败，请检查网络和平台地址")
        status = response.status_code
        if status in {401, 403}:
            return None, self._error("AUTH_EXPIRED", source, "API Key 无效或权限不足，请检查凭据")
        if status == 429:
            return None, self._error("RATE_LIMITED", source, "请求过于频繁，请稍后重试")
        if status >= 500:
            return None, self._error("SERVER_ERROR", source, "服务暂时不可用")
        if not 200 <= status < 300:
            return None, self._error("HTTP_ERROR", source, "请求失败，请检查平台 API 地址")
        try:
            payload = response.json()
        except (TypeError, ValueError):
            return None, self._invalid_response(source)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            return None, self._invalid_response(source)
        if "error" in payload:
            return None, self._error("API_ERROR", source, "接口返回错误")
        return payload, None


class OpenRouterProvider(_APIKeyProvider):
    id = "openrouter"
    name = "OpenRouter"
    default_currency = "USD"
    default_base = "https://openrouter.ai/api/v1"
    official_api_hosts = {"openrouter.ai"}
    supports_cost = True
    support_description = "读取当前 API Key 的剩余支出限额及 UTC 当日、本月、累计费用；不限额时余额显示未知。"
    balance_label = "密钥剩余额度"
    balance_description = "当前 API Key 的支出限额；不代表账户余额"
    dashboard_url = "https://openrouter.ai/settings/keys"
    credential_fields = {
        "API_KEY": {
            "label": "API Key",
            "secret": True,
            "hint": "普通 API Key，用于读取该 Key 的费用（美元）",
        },
        "BASE": {
            "label": "API 地址",
            "secret": False,
            "hint": "默认 https://openrouter.ai/api/v1",
        },
    }

    def fetch_balance(self) -> tuple[ProviderBalance | None, FetchError | None]:
        source = "API Key 额度与费用"
        payload, error = self._request_data("/key", source)
        if error:
            return None, error
        if payload is None:
            return None, self._invalid_response(source)
        try:
            data = payload["data"]
            # limit_remaining 仅表示密钥限额，null 表示不限额，不能替换成零或账户余额。
            remaining = None if data["limit_remaining"] is None else _amount(data, "limit_remaining")
            return ProviderBalance("USD", remaining), None
        except (KeyError, TypeError, ValueError, DecimalException):
            return None, self._invalid_response(source)

    def fetch_summary(self) -> tuple[ProviderSummary | None, FetchError | None]:
        source = "API Key 额度与费用"
        payload, error = self._request_data("/key", source)
        if error:
            return None, error
        if payload is None:
            return None, self._invalid_response(source)
        try:
            data = payload["data"]
            # 官方只提供当前 UTC 日/月和累计费用，没有 Token 总数或逐日历史。
            return ProviderSummary(
                month_tokens=None,
                today_cost=_amount(data, "usage_daily", nonnegative=True),
                month_cost=_amount(data, "usage_monthly", nonnegative=True),
                total_cost=_amount(data, "usage", nonnegative=True),
            ), None
        except (KeyError, TypeError, ValueError, DecimalException):
            return None, self._invalid_response(source)


class MoonshotProvider(_APIKeyProvider):
    id = "moonshot"
    name = "Moonshot / Kimi API"
    default_currency = "CNY"
    default_base = "https://api.moonshot.cn/v1"
    official_api_hosts = {"api.moonshot.cn", "api.moonshot.ai"}
    support_description = "读取 Kimi API 账户可用余额；国内站为人民币，国际站为美元，密钥须与站点对应。"
    dashboard_url = "https://platform.kimi.com"
    credential_fields = {
        "API_KEY": {
            "label": "API Key",
            "secret": True,
            "hint": "Kimi 开放平台 API Key，不是 Kimi 网页订阅凭据",
        },
        "BASE": {
            "label": "API 地址",
            "secret": False,
            "hint": "国内 https://api.moonshot.cn/v1；国际 https://api.moonshot.ai/v1",
        },
    }

    def fetch_balance(self) -> tuple[ProviderBalance | None, FetchError | None]:
        source = "账户余额"
        payload, error = self._request_data("/users/me/balance", source)
        if error:
            return None, error
        if payload is None:
            return None, self._invalid_response(source)
        try:
            if type(payload.get("code")) is not int or type(payload.get("status")) is not bool:
                raise ValueError("Invalid result status")
            if payload["code"] != 0 or not payload["status"]:
                return None, self._error("API_ERROR", source, "余额接口返回错误")
            # 现金为负时可用余额有特殊规则，直接采用官方 available_balance，不能自行加总。
            amount = _amount(payload["data"], "available_balance")
            currency = "USD" if urlsplit(self._base_url()).hostname == "api.moonshot.ai" else "CNY"
            return ProviderBalance(currency, amount), None
        except (KeyError, TypeError, ValueError, DecimalException):
            return None, self._invalid_response(source)
