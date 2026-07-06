"""Provider-neutral aggregation with isolated per-provider snapshots."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

import api.deepseek as ds  # 兼容 v1.0 中对 data.store.ds 的测试和扩展引用。
import config_manager
from api.providers import active_providers
from api.providers.base import FetchError, ModelUsage
from data import history

TOKEN_TYPES = {
    "PROMPT_CACHE_HIT_TOKEN",
    "PROMPT_CACHE_MISS_TOKEN",
    "RESPONSE_TOKEN",
}
ACTIVITY_DAYS = 365
HISTORY_SYNC_BATCH_SIZE = 2


def top_model_stats(
    stats: dict[str, ModelUsage], limit: int = 3
) -> list[ModelUsage]:
    models = sorted(stats.values(), key=lambda value: value.tokens, reverse=True)
    if len(models) <= limit:
        return copy.deepcopy(models)
    shown = copy.deepcopy(models[: limit - 1])
    other = ModelUsage("其他")
    for model in models[limit - 1 :]:
        other.tokens += model.tokens
        other.cost_cny += model.cost_cny
    return shown + [other]


def _decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"无效数值：{value!r}") from None


def _safe_int(value: Any) -> int:
    try:
        return int(_decimal(value))
    except ValueError:
        return 0


def sum_usage_amount(item: dict[str, Any], allowed_types: set[str] = TOKEN_TYPES) -> Decimal:
    total = Decimal("0")
    usages = item.get("usage", [])
    if not isinstance(usages, list):
        return total
    for usage in usages:
        if not isinstance(usage, dict) or usage.get("type") not in allowed_types:
            continue
        try:
            total += _decimal(usage.get("amount"))
        except ValueError:
            config_manager.logger().warning("Skipped malformed usage amount")
    return total


def months_for_week(today: date) -> list[tuple[int, int]]:
    week_start = today - timedelta(days=today.weekday())
    months = [(today.month, today.year)]
    if (week_start.year, week_start.month) != (today.year, today.month):
        months.insert(0, (week_start.month, week_start.year))
    return months


def months_for_activity(today: date) -> list[tuple[int, int]]:
    earliest = today - timedelta(days=ACTIVITY_DAYS - 1)
    current = today.replace(day=1)
    first = earliest.replace(day=1)
    months: list[tuple[int, int]] = []
    while current >= first:
        months.append((current.month, current.year))
        current = (current - timedelta(days=1)).replace(day=1)
    return months


# 新实现内部仍使用带下划线名称；保留公开别名以兼容 v1.0 调用方。
_months_for_week = months_for_week
_months_for_activity = months_for_activity


def _sum_from_payloads(
    payloads: list[dict[str, Any]], today: date
) -> tuple[int, int, Decimal, Decimal]:
    today_tokens = 0
    week_tokens = 0
    today_cost = Decimal("0")
    week_cost = Decimal("0")
    week_start = today - timedelta(days=today.weekday())
    for payload in payloads:
        days = payload.get("days", [])
        if not isinstance(days, list):
            continue
        for day in days:
            if not isinstance(day, dict):
                continue
            try:
                usage_date = date.fromisoformat(str(day.get("date", "")))
            except ValueError:
                continue
            items = day.get("data", [])
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                usages = item.get("usage", [])
                if not isinstance(usages, list):
                    continue
                for usage in usages:
                    if not isinstance(usage, dict):
                        continue
                    usage_type = str(usage.get("type", ""))
                    try:
                        amount = _decimal(usage.get("amount"))
                    except ValueError:
                        config_manager.logger().warning("Skipped malformed provider usage")
                        continue
                    if usage_type == "cost_cny":
                        if usage_date == today:
                            today_cost += amount
                        if week_start <= usage_date <= today:
                            week_cost += amount
                    elif usage_type in TOKEN_TYPES:
                        if usage_date == today:
                            today_tokens += int(amount)
                        if week_start <= usage_date <= today:
                            week_tokens += int(amount)
    return today_tokens, week_tokens, today_cost, week_cost


def _monthly_totals_from_payloads(
    payloads: list[dict[str, Any]], today: date
) -> tuple[int, Decimal, list[dict[str, Any]]]:
    month_tokens = 0
    month_cost = Decimal("0")
    models: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        days = payload.get("days", [])
        if not isinstance(days, list):
            continue
        for day in days:
            if not isinstance(day, dict):
                continue
            try:
                usage_date = date.fromisoformat(str(day.get("date", "")))
            except ValueError:
                continue
            if (usage_date.year, usage_date.month) != (today.year, today.month):
                continue
            for item in day.get("data", []) or []:
                if not isinstance(item, dict):
                    continue
                model = str(item.get("model", "unknown")).strip() or "unknown"
                slot = models.setdefault(model, {"model": model, "usage": []})
                for usage in item.get("usage", []) or []:
                    if not isinstance(usage, dict):
                        continue
                    try:
                        amount = _decimal(usage.get("amount"))
                    except ValueError:
                        continue
                    usage_type = str(usage.get("type", ""))
                    if usage_type == "cost_cny":
                        month_cost += amount
                    elif usage_type in TOKEN_TYPES:
                        month_tokens += int(amount)
                    else:
                        continue
                    slot["usage"].append(copy.deepcopy(usage))
    per_model = sorted(
        models.values(),
        key=lambda row: sum(
            _safe_int(usage.get("amount"))
            for usage in row["usage"]
            if usage.get("type") in TOKEN_TYPES
        ),
        reverse=True,
    )
    return month_tokens, month_cost, per_model


@dataclass
class PerProviderData:
    provider_id: str
    provider_name: str
    balance_cny: float | None = None
    balance_tokens: int | None = None
    monthly_usage_tokens: int | None = None
    monthly_cost_cny: float | None = None
    today_tokens: int | None = None
    today_cost_cny: float | None = None
    weekly_tokens: int | None = None
    weekly_cost_cny: float | None = None
    total_cost_cny: float | None = None
    per_model: list[dict[str, Any]] = field(default_factory=list)
    errors: list[FetchError] = field(default_factory=list)
    status: str = "loading"
    is_stale: bool = False


@dataclass
class TokenData:
    balance_cny: float | None = None
    balance_tokens: int | None = 0
    monthly_usage_tokens: int | None = 0
    monthly_cost_cny: float | None = None
    today_tokens: int | None = 0
    today_cost_cny: float | None = None
    weekly_tokens: int | None = 0
    weekly_cost_cny: float | None = None
    total_cost_cny: float | None = None
    per_model_amount: list[dict[str, Any]] = field(default_factory=list)
    per_model_cost: list[dict[str, Any]] = field(default_factory=list)
    model_stats: dict[str, ModelUsage] = field(default_factory=dict)
    per_provider: list[PerProviderData] = field(default_factory=list)
    status: str = "loading"
    last_success_at: datetime | None = None
    last_attempt_at: datetime | None = None
    errors: list[FetchError] = field(default_factory=list)
    is_stale: bool = False
    last_updated: str = ""
    daily_usage: list[dict[str, Any]] = field(default_factory=list)

    _last_snapshot: ClassVar["TokenData | None"] = None
    _provider_snapshots: ClassVar[dict[str, "TokenData"]] = {}
    _cache_lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def _base_snapshot(cls, provider_id: str = "") -> "TokenData":
        with cls._cache_lock:
            snapshot = cls._provider_snapshots.get(provider_id) if provider_id else cls._last_snapshot
            return copy.deepcopy(snapshot) if snapshot else cls()

    @classmethod
    def fetch(cls, today: date | None = None) -> "TokenData":
        current_day = today or date.today()
        providers = list(active_providers())
        if not providers:
            return cls(
                status="error",
                errors=[FetchError("NOT_CONFIGURED", "平台", "没有可用的数据平台")],
                last_attempt_at=datetime.now(),
            )

        provider = providers[0]
        cached = cls._base_snapshot(provider.id)
        data = cached
        data.status = "loading"
        data.errors = []
        data.last_attempt_at = datetime.now()
        previous_per = data.per_provider[0] if data.per_provider else None
        per = copy.deepcopy(previous_per) if previous_per else PerProviderData(provider.id, provider.name)
        per.provider_id = provider.id
        per.provider_name = provider.name
        per.errors = []
        per.status = "loading"
        per.is_stale = False
        successes = 0

        if not provider.is_configured():
            # 删除或切换凭据后不能继续展示旧账号数据，否则会造成“仍已登录”的错觉。
            per = PerProviderData(provider.id, provider.name, status="not_configured")
            per.errors.append(FetchError("NOT_CONFIGURED", provider.name, f"尚未配置 {provider.name} 凭据"))
            data.daily_usage = []
            data.last_success_at = None
            data.last_updated = ""
        else:
            try:
                balance, balance_error = provider.fetch_balance()
            except Exception as exc:
                config_manager.logger().exception("Balance fetch failed for %s", provider.id)
                balance, balance_error = None, FetchError("UNKNOWN_ERROR", "账户余额", str(exc))
            if balance is not None:
                per.balance_cny = float(balance.amount) if balance.amount is not None else None
                per.balance_tokens = int(balance.token_estimate)
                successes += 1
            if balance_error:
                per.errors.append(balance_error)

            try:
                summary, summary_error = provider.fetch_summary()
            except Exception as exc:
                config_manager.logger().exception("Summary fetch failed for %s", provider.id)
                summary, summary_error = None, FetchError("UNKNOWN_ERROR", "账户摘要", str(exc))
            if summary is not None:
                per.monthly_cost_cny = (
                    float(summary.month_cost) if summary.month_cost is not None else None
                )
                per.monthly_usage_tokens = int(summary.month_tokens)
                if summary.remaining_tokens and not per.balance_tokens:
                    per.balance_tokens = int(summary.remaining_tokens)
                successes += 1
            if summary_error:
                per.errors.append(summary_error)

            request_months = months_for_week(current_day)
            try:
                for month in history.unsynced_months(
                    months_for_activity(current_day), provider.id
                ):
                    if month in request_months:
                        continue
                    request_months.append(month)
                    if len(request_months) >= len(months_for_week(current_day)) + HISTORY_SYNC_BATCH_SIZE:
                        break
            except Exception:
                config_manager.logger().exception("History sync state read failed for %s", provider.id)
                per.errors.append(FetchError("LOCAL_STORAGE", "历史缓存", "本地同步状态读取失败"))

            try:
                payloads, payload_errors = provider.fetch_payloads(request_months)
            except Exception as exc:
                config_manager.logger().exception("Payload fetch failed for %s", provider.id)
                payloads, payload_errors = [], [FetchError("UNKNOWN_ERROR", "用量明细", str(exc))]
            per.errors.extend(payload_errors)
            if payloads:
                today_tokens, week_tokens, today_cost, week_cost = _sum_from_payloads(
                    payloads, current_day
                )
                per.today_tokens = today_tokens
                per.weekly_tokens = week_tokens
                per.today_cost_cny = float(today_cost)
                per.weekly_cost_cny = float(week_cost)
                month_tokens, month_cost, per_model = _monthly_totals_from_payloads(
                    payloads, current_day
                )
                if per.monthly_usage_tokens is None:
                    per.monthly_usage_tokens = month_tokens
                if per.monthly_cost_cny is None:
                    per.monthly_cost_cny = float(month_cost)
                per.per_model = per_model
                successes += 1
                completed = [
                    tuple(payload["_month"])
                    for payload in payloads
                    if payload.get("_month")
                    and payload.get("_complete")
                    and tuple(payload["_month"]) != (current_day.month, current_day.year)
                ]
                try:
                    history.save_usage(payloads, payloads, completed, provider.id)
                except Exception:
                    config_manager.logger().exception("History save failed for %s", provider.id)
                    per.errors.append(FetchError("LOCAL_STORAGE", "历史缓存", "本地历史保存失败"))

            try:
                data.daily_usage = (
                    history.recent_daily(371, provider.id)
                    if provider.supports_daily_usage
                    else []
                )
                per.total_cost_cny = (
                    float(history.total_cost(provider.id))
                    if provider.supports_cost
                    else None
                )
            except Exception:
                config_manager.logger().exception("History read failed for %s", provider.id)
                per.errors.append(FetchError("LOCAL_STORAGE", "历史缓存", "本地历史读取失败"))

            if successes:
                per.status = "partial" if per.errors else "ok"
                per.is_stale = bool(per.errors)
            else:
                per.status = "error"
                per.is_stale = previous_per is not None

        data.per_provider = [per]
        data.balance_cny = per.balance_cny
        data.balance_tokens = per.balance_tokens
        data.monthly_usage_tokens = per.monthly_usage_tokens
        data.monthly_cost_cny = per.monthly_cost_cny
        data.today_tokens = per.today_tokens
        data.today_cost_cny = per.today_cost_cny
        data.weekly_tokens = per.weekly_tokens
        data.weekly_cost_cny = per.weekly_cost_cny
        data.total_cost_cny = per.total_cost_cny
        data.per_model_amount = copy.deepcopy(per.per_model)
        data.per_model_cost = copy.deepcopy(per.per_model)
        data.errors = list(per.errors)

        if successes:
            data.last_success_at = datetime.now()
            data.last_updated = data.last_success_at.strftime("%H:%M:%S")
            data.status = "partial" if per.errors else "ok"
            data.is_stale = bool(per.errors)
            with cls._cache_lock:
                cls._provider_snapshots[provider.id] = copy.deepcopy(data)
                cls._last_snapshot = copy.deepcopy(data)
        else:
            data.status = "error" if per.status != "not_configured" else "not_configured"
            data.is_stale = per.is_stale

        for error in data.errors:
            config_manager.logger().warning(
                "Fetch failed: provider=%s source=%s code=%s",
                provider.id,
                error.source,
                error.code,
            )
        return data

    @property
    def display_message(self) -> str:
        if self.status == "loading":
            return "正在刷新…"
        if self.errors:
            suffix = f"，显示 {self.last_updated} 的缓存" if self.is_stale and self.last_updated else ""
            return f"{self.errors[0].message}{suffix}"
        return f"更新于 {self.last_updated}" if self.last_updated else "等待首次刷新"
