"""DeepSeek provider built on the project's existing API clients."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import api.deepseek as platform_api
import api.deepseek_official as official_api
import config_manager
from api.deepseek import APIError
from api.providers.base import FetchError, Provider, ProviderBalance, ProviderSummary, _decimal, safe_int


TOKEN_TYPES = {
    "PROMPT_CACHE_HIT_TOKEN",
    "PROMPT_CACHE_MISS_TOKEN",
    "RESPONSE_TOKEN",
}


def _fetch_error(source: str, exc: Exception) -> FetchError:
    if isinstance(exc, APIError):
        return FetchError(exc.code, source, exc.message)
    if isinstance(exc, (KeyError, TypeError, ValueError, InvalidOperation)):
        return FetchError("INVALID_RESPONSE", source, "DeepSeek 返回结构已变化")
    config_manager.logger().exception("DeepSeek request failed: source=%s", source)
    return FetchError("UNKNOWN_ERROR", source, "读取 DeepSeek 数据时发生未知错误")


def _append_usage(
    by_date: dict[str, dict[str, dict[str, Any]]],
    payload: dict[str, Any],
    *,
    is_cost: bool,
) -> None:
    days = payload.get("days", [])
    if not isinstance(days, list):
        raise ValueError("days 字段不是列表")
    for day in days:
        if not isinstance(day, dict):
            continue
        usage_date = str(day.get("date", "")).strip()
        if not usage_date:
            continue
        items = day.get("data", [])
        if not isinstance(items, list):
            continue
        date_bucket = by_date.setdefault(usage_date, {})
        for item in items:
            if not isinstance(item, dict):
                continue
            model = str(item.get("model", "unknown")).strip() or "unknown"
            model_entry = date_bucket.setdefault(model, {"model": model, "usage": []})
            usages = item.get("usage", [])
            if not isinstance(usages, list):
                continue
            if is_cost:
                # DeepSeek 的费用接口沿用 Token 类型名，但 amount 表示人民币金额；
                # 归一为 cost_cny，避免下游把小数金额误算成 Token。
                cost = Decimal("0")
                for usage in usages:
                    if not isinstance(usage, dict) or usage.get("type") not in TOKEN_TYPES:
                        continue
                    try:
                        cost += _decimal(usage.get("amount"))
                    except ValueError:
                        config_manager.logger().warning("Skipped malformed DeepSeek cost row")
                model_entry["usage"].append({"type": "cost_cny", "amount": str(cost)})
                continue
            for usage in usages:
                if not isinstance(usage, dict) or usage.get("type") not in TOKEN_TYPES:
                    continue
                try:
                    amount = safe_int(usage.get("amount"))
                except (TypeError, ValueError):
                    continue
                model_entry["usage"].append({"type": usage["type"], "amount": amount})


def _merge_payloads(
    amount_payload: dict[str, Any] | None,
    cost_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    by_date: dict[str, dict[str, dict[str, Any]]] = {}
    if amount_payload is not None:
        _append_usage(by_date, amount_payload, is_cost=False)
    if cost_payload is not None:
        _append_usage(by_date, cost_payload, is_cost=True)
    days = [
        {"date": usage_date, "data": list(models.values())}
        for usage_date, models in sorted(by_date.items())
    ]
    return {"days": days, "total": []}


class DeepSeekProvider(Provider):
    id = "deepseek"
    name = "DeepSeek"
    default_currency = "CNY"
    default_base = "https://platform.deepseek.com"
    official_api_hosts = {"platform.deepseek.com", "api.deepseek.com"}
    supports_daily_usage = True
    supports_cost = True
    credential_fields = {
        "API_KEY": {
            "label": "API Key（可选）",
            "secret": True,
            "optional": True,
            "hint": "sk-xxxx，仅用于官方余额接口",
        },
        "AUTH": {
            "label": "Bearer Token",
            "secret": True,
            "hint": "登录后浏览器请求头 authorization（推荐）",
        },
        "COOKIE": {
            "label": "Cookie",
            "secret": True,
            "multiline": True,
            "hint": "登录 platform.deepseek.com 后复制，填 AUTH 可省略",
        },
        "BASE": {
            "label": "平台地址",
            "secret": False,
            "hint": "默认 https://platform.deepseek.com",
        },
    }

    def __init__(self) -> None:
        self._summary_cache: dict[str, Any] | None = None
        self._summary_error: Exception | None = None

    def _has_platform_credentials(self) -> bool:
        return bool(
            str(config_manager.get("DEEPSEEK_AUTH", "")).strip()
            or str(config_manager.get("DEEPSEEK_COOKIE", "")).strip()
        )

    def is_configured(self) -> bool:
        return self._has_platform_credentials() or bool(
            str(config_manager.get("DEEPSEEK_API_KEY", "")).strip()
        )

    def _summary(self) -> dict[str, Any]:
        # 同一轮余额和摘要共用一次平台请求，避免无 API Key 时重复拉取摘要。
        if self._summary_cache is None and self._summary_error is None:
            try:
                self._summary_cache = platform_api.get_user_summary()
            except Exception as exc:
                self._summary_error = exc
        if self._summary_error is not None:
            raise self._summary_error
        return self._summary_cache or {}

    def fetch_balance(self) -> tuple[ProviderBalance | None, FetchError | None]:
        if str(config_manager.get("DEEPSEEK_API_KEY", "")).strip():
            try:
                payload = official_api.get_balance()
                infos = payload.get("balance_infos", [])
                if not isinstance(infos, list):
                    raise ValueError("balance_infos 字段不是列表")
                for info in infos:
                    if isinstance(info, dict) and info.get("currency") == "CNY":
                        return ProviderBalance("CNY", _decimal(info.get("total_balance"))), None
            except Exception as exc:
                config_manager.logger().warning(
                    "DeepSeek official balance unavailable: code=%s",
                    getattr(exc, "code", type(exc).__name__),
                )

        if not self._has_platform_credentials():
            return None, FetchError("NOT_CONFIGURED", "账户余额", "未配置 DeepSeek 平台 Token/Cookie")
        try:
            summary = self._summary()
            wallets = summary.get("normal_wallets", [])
            if not isinstance(wallets, list):
                raise ValueError("normal_wallets 字段不是列表")
            for wallet in wallets:
                if isinstance(wallet, dict) and wallet.get("currency") == "CNY":
                    return ProviderBalance(
                        "CNY",
                        _decimal(wallet.get("balance")),
                        safe_int(wallet.get("token_estimation")),
                    ), None
            return None, FetchError("NO_DATA", "账户余额", "DeepSeek 未返回人民币余额")
        except Exception as exc:
            return None, _fetch_error("账户余额", exc)

    def fetch_summary(self) -> tuple[ProviderSummary | None, FetchError | None]:
        if not self._has_platform_credentials():
            return None, FetchError("NOT_CONFIGURED", "账户摘要", "未配置 DeepSeek 平台 Token/Cookie")
        try:
            summary = self._summary()
            monthly_costs = summary.get("monthly_costs", [])
            month_cost = Decimal("0")
            if isinstance(monthly_costs, list) and monthly_costs:
                first = monthly_costs[0]
                if isinstance(first, dict):
                    month_cost = _decimal(first.get("amount"))
            return ProviderSummary(
                month_cost=month_cost,
                month_tokens=safe_int(summary.get("monthly_token_usage")),
            ), None
        except Exception as exc:
            return None, _fetch_error("账户摘要", exc)

    def fetch_payloads(
        self, months: list[tuple[int, int]]
    ) -> tuple[list[dict[str, Any]], list[FetchError]]:
        if not self._has_platform_credentials():
            return [], [FetchError("NOT_CONFIGURED", "用量明细", "未配置 DeepSeek 平台 Token/Cookie")]
        payloads: list[dict[str, Any]] = []
        errors: list[FetchError] = []
        for month, year in dict.fromkeys(months):
            amount_data: dict[str, Any] | None = None
            cost_data: dict[str, Any] | None = None
            try:
                amount_data = platform_api.get_usage_amount(month, year)
            except Exception as exc:
                errors.append(_fetch_error("Token 明细", exc))
            try:
                cost_data = platform_api.get_usage_cost(month, year)
            except Exception as exc:
                errors.append(_fetch_error("费用明细", exc))
            if amount_data is None and cost_data is None:
                continue
            try:
                payload = _merge_payloads(amount_data, cost_data)
                payload["_month"] = (month, year)
                payload["_complete"] = amount_data is not None and cost_data is not None
                payloads.append(payload)
            except Exception as exc:
                errors.append(_fetch_error("用量解析", exc))
        return payloads, errors


__all__ = ["DeepSeekProvider"]
