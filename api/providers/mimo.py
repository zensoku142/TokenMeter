"""Xiaomi MiMo Token Plan usage provider.

MiMo does not publish a billing-history API.  The web console exposes monthly
plan quota through ``/api/v1/tokenPlan/usage``; this adapter therefore reports
plan tokens only and deliberately leaves daily usage and monetary cost absent.
"""

from __future__ import annotations

from typing import Any

import requests

import config_manager
from api.providers.base import FetchError, Provider, ProviderBalance, ProviderSummary, build_session, safe_int


class MiMoProvider(Provider):
    id = "mimo"
    name = "小米 MiMo"
    default_currency = "CNY"
    default_base = "https://platform.xiaomimimo.com"
    official_api_hosts = {"platform.xiaomimimo.com"}
    credential_fields = {
        "COOKIE": {
            "label": "Cookie",
            "secret": True,
            "multiline": True,
            "hint": "通常包含 api-platform_serviceToken、userId、api-platform_slh 和 api-platform_ph",
        },
        "API_PLATFORM_PH": {
            "label": "api-platform_ph（兼容旧配置）",
            "secret": True,
            "optional": True,
            "hint": "完整 Cookie 已包含该项时无需单独填写",
        },
        "API_KEY": {
            "label": "推理 API Key（用量查询不使用）",
            "secret": True,
            "optional": True,
            "hint": "仅保留旧配置，不会发送到控制台用量接口",
        },
        "BASE": {
            "label": "平台地址",
            "secret": False,
            "hint": "默认 https://platform.xiaomimimo.com",
        },
    }

    def __init__(self) -> None:
        self._session = build_session()
        self._usage_cache: dict[str, Any] | None = None
        self._usage_error: Exception | None = None

    def is_configured(self) -> bool:
        return bool(str(config_manager.get("MIMO_COOKIE", "")).strip())

    def _base_url(self) -> str:
        configured = str(config_manager.get("MIMO_BASE", "")).strip().rstrip("/")
        if configured in {"https://api.xiaomimimo.com", "api.xiaomimimo.com"}:
            return self.default_base
        return configured or self.default_base

    def _headers(self) -> dict[str, str]:
        cookie = str(config_manager.get("MIMO_COOKIE", "")).strip()
        ph = str(config_manager.get("MIMO_API_PLATFORM_PH", "")).strip()
        if ph and "api-platform_ph=" not in cookie:
            # api-platform_ph 本身是登录 Cookie；保留浏览器复制出的编码，不能二次解码。
            cookie = f"{cookie.rstrip('; ')}; api-platform_ph={ph}" if cookie else f"api-platform_ph={ph}"
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "cookie": cookie,
            "referer": f"{self._base_url()}/console/plan-manage",
            "x-timezone": "Asia/Shanghai",
            "user-agent": "TokenSpider/1.1",
        }

    def _get(self, path: str) -> dict[str, Any]:
        if not self.is_configured():
            raise RuntimeError("NOT_CONFIGURED")
        try:
            response = self._session.get(
                f"{self._base_url()}{path}",
                headers=self._headers(),
                timeout=(5, 15),
            )
        except requests.Timeout as exc:
            raise RuntimeError("NETWORK_TIMEOUT") from exc
        except requests.RequestException as exc:
            raise RuntimeError("NETWORK_ERROR") from exc
        if response.status_code in (401, 403):
            raise RuntimeError("AUTH_EXPIRED")
        if response.status_code == 429:
            raise RuntimeError("RATE_LIMITED")
        if response.status_code >= 500:
            raise RuntimeError("SERVER_ERROR")
        if not response.ok:
            raise RuntimeError(f"HTTP_{response.status_code}")
        try:
            payload = response.json()
        except requests.JSONDecodeError as exc:
            raise RuntimeError("INVALID_RESPONSE") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("INVALID_RESPONSE")
        if payload.get("code") in (401, "401"):
            raise RuntimeError("AUTH_EXPIRED")
        if payload.get("code") not in (0, "0", None):
            raise RuntimeError("API_ERROR")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("INVALID_RESPONSE")
        return data

    def _usage(self) -> dict[str, Any]:
        if self._usage_cache is None and self._usage_error is None:
            try:
                self._usage_cache = self._get("/api/v1/tokenPlan/usage")
            except Exception as exc:
                self._usage_error = exc
        if self._usage_error is not None:
            raise self._usage_error
        return self._usage_cache or {}

    @staticmethod
    def _item(data: dict[str, Any], group: str, name: str) -> dict[str, Any] | None:
        section = data.get(group)
        items = section.get("items", []) if isinstance(section, dict) else []
        if not isinstance(items, list):
            return None
        for item in items:
            if isinstance(item, dict) and item.get("name") == name:
                return item
        return None

    @staticmethod
    def _error(source: str, exc: Exception) -> FetchError:
        code = str(exc)
        messages = {
            "NOT_CONFIGURED": "尚未配置 MiMo Cookie",
            "AUTH_EXPIRED": "MiMo 登录状态已失效，请重新复制 Cookie",
            "NETWORK_TIMEOUT": "连接 MiMo 超时",
            "NETWORK_ERROR": "无法连接 MiMo",
            "RATE_LIMITED": "MiMo 请求过于频繁，请稍后重试",
            "SERVER_ERROR": "MiMo 服务暂时异常",
            "INVALID_RESPONSE": "MiMo 返回结构已变化",
            "API_ERROR": "MiMo 返回业务错误",
        }
        return FetchError(code, source, messages.get(code, f"MiMo 请求失败（{code}）"))

    def fetch_balance(self) -> tuple[ProviderBalance | None, FetchError | None]:
        try:
            item = self._item(self._usage(), "usage", "plan_total_token")
            if item is None:
                return None, FetchError("NO_DATA", "MiMo 套餐", "未找到套餐 Token 用量")
            used = safe_int(item.get("used"))
            limit = safe_int(item.get("limit"))
            return ProviderBalance(currency="", amount=None, token_estimate=max(0, limit - used)), None
        except Exception as exc:
            return None, self._error("MiMo 套餐", exc)

    def fetch_summary(self) -> tuple[ProviderSummary | None, FetchError | None]:
        try:
            data = self._usage()
            month_item = self._item(data, "monthUsage", "month_total_token")
            plan_item = self._item(data, "usage", "plan_total_token")
            item = month_item or plan_item
            if item is None:
                return None, FetchError("NO_DATA", "MiMo 用量", "未找到本月 Token 用量")
            used = safe_int(item.get("used"))
            remaining = 0
            if plan_item is not None:
                remaining = max(0, safe_int(plan_item.get("limit")) - safe_int(plan_item.get("used")))
            return ProviderSummary(month_cost=None, month_tokens=used, remaining_tokens=remaining), None
        except Exception as exc:
            return None, self._error("MiMo 用量", exc)

    def fetch_payloads(
        self, months: list[tuple[int, int]]
    ) -> tuple[list[dict[str, Any]], list[FetchError]]:
        # 控制台仅返回套餐/月度汇总，不提供可靠的逐日明细或人民币费用。
        return [], []


__all__ = ["MiMoProvider"]
