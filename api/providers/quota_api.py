"""Shared transport for subscription APIs that return a top-level JSON object."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import requests

from api.http import is_https_url
from api.providers.api_balance import _amount, _APIKeyProvider
from api.providers.base import FetchError


def _unix_time(
    data: Mapping[str, Any], field: str, *, milliseconds: bool = False
) -> datetime | None:
    if field not in data or data[field] is None:
        return None
    value = _amount(data, field, nonnegative=True)
    # 单位由各第一方接口合同指定，不能按数值大小猜测秒或毫秒。
    return datetime.fromtimestamp(float(value / (1000 if milliseconds else 1)), UTC)


class _QuotaAPIProvider(_APIKeyProvider):
    supports_subscription_quota = True
    credential_header = "Authorization"

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
        if not valid_base or any(ord(char) < 32 for char in key):
            return None, self._error("INVALID_CONFIG", source, "请检查 HTTPS API 地址和凭据格式")
        credential = f"Bearer {key}" if self.credential_header == "Authorization" else key
        try:
            response = self._session.get(
                f"{base}{path}",
                headers={"Accept": "application/json", self.credential_header: credential},
                timeout=20,
                # 用户配置的密钥仅发送到选定地址，不能跟随跨站或降级重定向。
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
        if not isinstance(payload, dict):
            return None, self._invalid_response(source)
        if "error" in payload:
            return None, self._error("API_ERROR", source, "额度接口返回错误")
        return payload, None
