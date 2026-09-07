"""GLM Coding Plan percentages from the first-party usage plugin endpoint."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import requests

from api.http import is_https_url
from api.providers.base import (
    FetchError,
    Provider,
    ProviderQuota,
    QuotaMetric,
    QuotaWindow,
    build_session,
)

_QUOTA_TITLES = {"TOKENS_LIMIT": "套餐额度", "TIME_LIMIT": "工具调用额度"}


class ZaiProvider(Provider):
    id = "zai"
    name = "GLM / Z.ai Coding Plan"
    default_base = "https://api.z.ai"
    official_api_hosts = {"api.z.ai", "open.bigmodel.cn", "dev.bigmodel.cn"}
    supports_subscription_quota = True
    support_description = "仅显示官方接口返回的套餐与工具额度比例；未确认的窗口和计量单位不作推算"
    dashboard_url = "https://z.ai/manage-apikey/subscription"
    credential_fields = {
        "API_KEY": {
            "label": "Coding Plan API Key",
            "secret": True,
            "hint": "个人 Coding Plan 原始 API Key，不要添加 Bearer 前缀",
        },
        "BASE": {
            "label": "API 根地址",
            "secret": False,
            "hint": "国际 https://api.z.ai；国内 https://open.bigmodel.cn",
        },
    }

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(config)
        self._session = build_session()

    def close(self) -> None:
        self._session.close()

    @staticmethod
    def _error(code: str) -> FetchError:
        messages = {
            "NOT_CONFIGURED": "尚未配置 GLM Coding Plan API Key",
            "INVALID_CONFIG": "GLM API 根地址须为不含路径和查询参数的 HTTPS 地址",
            "AUTH_EXPIRED": "GLM API Key 无效或无权查询此套餐，请检查凭据与站点",
            "RATE_LIMITED": "GLM 额度查询过于频繁，请稍后重试",
            "NETWORK_TIMEOUT": "连接 GLM 额度服务超时",
            "NETWORK_ERROR": "无法连接 GLM 额度服务",
            "SERVER_ERROR": "GLM 额度服务暂时异常",
            "INVALID_RESPONSE": "GLM 额度数据结构已变化，请查看官方控制台",
            "API_ERROR": "GLM 额度接口返回业务错误，请检查套餐与凭据",
            "QUOTA_UNAVAILABLE": "GLM 未返回受支持的额度类型；新套餐可能需要适配，请查看官方控制台",
        }
        return FetchError(code, "GLM Coding Plan 额度", messages[code])

    def fetch_quota(self) -> tuple[ProviderQuota | None, FetchError | None]:
        key = str(self.config_get("ZAI_API_KEY", "") or "").strip()
        if not key:
            return None, self._error("NOT_CONFIGURED")
        base = str(self.config_get("ZAI_BASE", "") or self.default_base).strip().rstrip("/")
        try:
            parsed = urlsplit(base)
            valid_base = is_https_url(base) and not (parsed.path or parsed.query or parsed.fragment)
        except ValueError:
            valid_base = False
        if not valid_base:
            return None, self._error("INVALID_CONFIG")
        try:
            response = self._session.get(
                f"{base}/api/monitor/usage/quota/limit",
                # 第一方查询插件直接传原始 Key；不能沿用其他供应商的 Bearer 拼接规则。
                headers={"Authorization": key, "Accept": "application/json"},
                timeout=(3, 10),
                allow_redirects=False,
            )
        except requests.Timeout:
            return None, self._error("NETWORK_TIMEOUT")
        except requests.RequestException:
            return None, self._error("NETWORK_ERROR")
        status = response.status_code
        if status != 200:
            code = {401: "AUTH_EXPIRED", 403: "AUTH_EXPIRED", 429: "RATE_LIMITED"}.get(
                status, "SERVER_ERROR" if status >= 500 else "INVALID_RESPONSE"
            )
            return None, self._error(code)
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Invalid envelope")
            if "success" in payload and type(payload["success"]) is not bool:
                raise ValueError("Invalid status")
            if "code" in payload and type(payload["code"]) is not int:
                raise ValueError("Invalid code")
            if (
                payload.get("success") is False
                or "error" in payload
                or payload.get("code", 200) not in (0, 200)
            ):
                return None, self._error("API_ERROR")
            quota = self._parse_quota(payload.get("data"))
        except (ValueError, TypeError, OverflowError):
            return None, self._error("INVALID_RESPONSE")
        if not quota.windows:
            return None, self._error("QUOTA_UNAVAILABLE")
        return quota, None

    @staticmethod
    def _parse_quota(data: Any) -> ProviderQuota:
        if not isinstance(data, dict) or not isinstance(data.get("limits"), list):
            raise ValueError("Invalid limits")
        entries = data["limits"]
        if any(not isinstance(entry, dict) for entry in entries):
            raise ValueError("Invalid limit entry")
        supported = [entry for entry in entries if entry.get("type") in _QUOTA_TITLES]
        counts = Counter(entry["type"] for entry in supported)
        seen: Counter[str] = Counter()
        windows = []
        for entry in supported:
            kind = entry["type"]
            percentage = entry.get("percentage")
            if (
                type(percentage) not in (int, float)
                or not math.isfinite(percentage)
                or not 0 <= percentage <= 100
            ):
                raise ValueError("Invalid percentage")
            # 官方脚本只对 TIME_LIMIT 证明 usage 是总配额；零总配额不生成虚假的百分比窗口。
            if kind == "TIME_LIMIT" and "usage" in entry:
                total = entry["usage"]
                if type(total) not in (int, float) or not math.isfinite(total) or total < 0:
                    raise ValueError("Invalid entitlement")
                if total == 0:
                    continue
            seen[kind] += 1
            title = _QUOTA_TITLES[kind]
            if counts[kind] > 1:
                title = f"{title} {seen[kind]}"
            # 新套餐可能包含多种窗口；第一方缺少 unit/reset 合同，不把条目顺序当作时长。
            windows.append(QuotaWindow(f"{kind.lower()}_{seen[kind]}", title, float(percentage)))
        metrics = ()
        if len(supported) != len(entries):
            metrics = (
                QuotaMetric("支持范围", "部分额度类型暂不支持", "请在官方控制台查看完整套餐额度"),
            )
        return ProviderQuota(windows=tuple(windows), metrics=metrics, plan="GLM Coding Plan")


__all__ = ["ZaiProvider"]
