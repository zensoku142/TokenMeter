"""Copilot quota from the entitlement endpoint used by the official VS Code client."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal, DecimalException, InvalidOperation
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


def _number(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError("INVALID_RESPONSE")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("INVALID_RESPONSE") from None
    if not number.is_finite():
        raise ValueError("INVALID_RESPONSE")
    return number


def _reset_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("INVALID_RESPONSE")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    # 旧接口仅给出账期日期；GitHub 账期按 UTC 解释，避免本机时区偏移。
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


class CopilotProvider(Provider):
    id = "copilot"
    name = "GitHub Copilot"
    default_currency = "USD"
    supports_subscription_quota = True
    official_api_hosts = {"api.github.com"}
    dashboard_url = "https://github.com/settings/copilot"
    support_description = "实验性订阅额度：读取 GitHub 客户端接口；支持 AI credits 与旧版请求额度，接口可能变化。"
    credential_fields = {
        "TOKEN": {
            "label": "GitHub Token",
            "secret": True,
            "hint": "需要可访问 Copilot 额度的本人 GitHub 登录令牌；普通 PAT 可能无权限",
        },
    }

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(config)
        self._session = build_session()

    def close(self) -> None:
        self._session.close()

    def _error(self, code: str) -> FetchError:
        messages = {
            "NOT_CONFIGURED": "尚未配置 GitHub Copilot 登录令牌",
            "AUTH_EXPIRED": "GitHub 登录已失效，请更新登录令牌",
            "PERMISSION_DENIED": "此令牌无权读取 Copilot 额度，请检查账号订阅与令牌权限",
            "RATE_LIMITED": "GitHub 额度查询过于频繁，请稍后重试",
            "NETWORK_TIMEOUT": "连接 GitHub 额度服务超时",
            "NETWORK_ERROR": "无法连接 GitHub 额度服务",
            "SERVER_ERROR": "GitHub 额度服务暂时异常",
            "INVALID_RESPONSE": "GitHub 额度数据结构已变化，请查看官方控制台",
            "QUOTA_UNAVAILABLE": "此账号未返回可用额度，请查看 GitHub Copilot 控制台",
        }
        return FetchError(code, "GitHub Copilot 额度", messages[code])

    def fetch_quota(self) -> tuple[ProviderQuota | None, FetchError | None]:
        token = str(self.config_get("COPILOT_TOKEN", "")).strip()
        if not token:
            return None, self._error("NOT_CONFIGURED")
        try:
            response = self._session.get(
                "https://api.github.com/copilot_internal/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "User-Agent": "TokenMeter",
                },
                timeout=(3, 10),
                # 此地址固定在 GitHub；重定向不应将用户登录令牌带到其他服务。
                allow_redirects=False,
            )
        except requests.Timeout:
            return None, self._error("NETWORK_TIMEOUT")
        except requests.RequestException:
            return None, self._error("NETWORK_ERROR")
        status = response.status_code
        if status != 200:
            code = {401: "AUTH_EXPIRED", 403: "PERMISSION_DENIED", 429: "RATE_LIMITED"}.get(
                status, "SERVER_ERROR" if status >= 500 else "INVALID_RESPONSE"
            )
            return None, self._error(code)
        try:
            quota = self._parse_quota(response.json())
        except (ValueError, TypeError, OverflowError, DecimalException):
            return None, self._error("INVALID_RESPONSE")
        if not quota.windows and not quota.metrics:
            return None, self._error("QUOTA_UNAVAILABLE")
        return quota, None

    @staticmethod
    def _parse_quota(payload: Any) -> ProviderQuota:
        if not isinstance(payload, dict):
            raise ValueError("INVALID_RESPONSE")
        snapshots = payload.get("quota_snapshots")
        monthly = payload.get("monthly_quotas")
        limited = payload.get("limited_user_quotas")
        snapshots = {} if snapshots is None else snapshots
        monthly = {} if monthly is None else monthly
        limited = {} if limited is None else limited
        if not all(isinstance(value, dict) for value in (snapshots, monthly, limited)):
            raise ValueError("INVALID_RESPONSE")
        billing = payload.get("token_based_billing", False)
        if not isinstance(billing, bool):
            raise ValueError("INVALID_RESPONSE")
        windows: list[QuotaWindow] = []
        metrics: list[QuotaMetric] = []
        # 高级额度最先显示；既不硬编码套餐上限，也不把 AI credits 当作请求次数。
        for key, title in (
            ("premium_interactions", "AI credits" if billing else "高级请求额度"),
            ("chat", "聊天额度"),
            ("completions", "代码补全额度"),
        ):
            legacy_snapshot = None
            if key != "premium_interactions" and monthly.get(key) is not None and limited.get(key) is not None:
                allocated = _number(monthly[key])
                if allocated < 0:
                    raise ValueError("INVALID_RESPONSE")
                if allocated > 0:
                    # 官方客户端仍兼容 Free SKU 的次数响应；新接口缺某一项时保留该项旧额度。
                    remaining = max(Decimal(0), min(allocated, _number(limited[key])))
                    legacy_snapshot = {"percent_remaining": remaining / allocated * 100}
            snapshot = snapshots.get(key)
            if snapshot is None:
                snapshot = legacy_snapshot
            if snapshot is None:
                continue
            if not isinstance(snapshot, dict):
                raise ValueError("INVALID_RESPONSE")
            unlimited = snapshot.get("unlimited", False)
            if not isinstance(unlimited, bool):
                raise ValueError("INVALID_RESPONSE")
            if unlimited:
                # 无上限不等于 0% 已用，独立显示文本，避免制造百分比。
                metrics.append(QuotaMetric(title, "不限量", "以平台实际策略为准", value_kind="unlimited"))
                continue
            entitlement = snapshot.get("entitlement")
            if entitlement is not None:
                allocated = _number(entitlement)
                if allocated < 0:
                    raise ValueError("INVALID_RESPONSE")
                if allocated == 0:
                    if legacy_snapshot is None:
                        continue
                    snapshot = legacy_snapshot
            remaining = _number(snapshot.get("percent_remaining"))
            detail = "AI credits" if billing else "平台请求额度"
            resets_at = (
                _reset_time(snapshot.get("quota_reset_at"))
                or _reset_time(payload.get("quota_reset_date_utc"))
                or _reset_time(payload.get("quota_reset_date"))
                or _reset_time(payload.get("limited_user_reset_date"))
            )
            windows.append(QuotaWindow(
                f"copilot-{key}", title,
                # 先限制剩余百分比再取补数，避免极大但有限的响应值在减法中溢出。
                float(100 - max(Decimal(0), min(Decimal(100), remaining))),
                resets_at=resets_at, detail=detail,
            ))
        plan = payload.get("copilot_plan")
        plan = "" if plan is None else plan
        if not isinstance(plan, str):
            raise ValueError("INVALID_RESPONSE")
        return ProviderQuota(windows=tuple(windows), metrics=tuple(metrics), plan=plan)


__all__ = ["CopilotProvider"]
