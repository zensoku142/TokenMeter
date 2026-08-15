"""Provider-neutral aggregation with isolated per-provider snapshots."""

from __future__ import annotations

import copy
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import api.deepseek as ds  # noqa: F401  # 兼容 v1.0 中对 data.store.ds 的测试和扩展引用。
from api.providers import active_providers
from api.providers.base import FetchError, ModelUsage, QuotaMetric, QuotaWindow
from config import runtime as config_manager
from data import history

TOKEN_TYPES = {
    "PROMPT_CACHE_HIT_TOKEN",
    "PROMPT_CACHE_MISS_TOKEN",
    "RESPONSE_TOKEN",
}
ACTIVITY_DAYS = 365
HISTORY_SYNC_BATCH_SIZE = 2
_TRANSIENT_QUOTA_ERROR_CODES = {
    "INVALID_RESPONSE",
    "NETWORK_ERROR",
    "NETWORK_TIMEOUT",
    "RATE_LIMITED",
    "SERVER_ERROR",
    "UNKNOWN_ERROR",
}


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
) -> tuple[int | None, Decimal | None, list[dict[str, Any]]]:
    month_tokens = 0
    month_cost = Decimal("0")
    found_tokens = False
    found_cost = False
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
                        found_cost = True
                    elif usage_type in TOKEN_TYPES:
                        month_tokens += int(amount)
                        found_tokens = True
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
    return (
        month_tokens if found_tokens else None,
        month_cost if found_cost else None,
        per_model,
    )


def token_breakdown_for_day(
    payloads: list[dict[str, Any]], usage_day: date
) -> dict[str, int] | None:
    """从已确认的按日接口响应中聚合三类 Token 累计值。"""
    totals = {token_type: 0 for token_type in TOKEN_TYPES}
    found_day = False
    for payload in payloads:
        for day in payload.get("days", []) or []:
            if not isinstance(day, dict) or str(day.get("date", "")) != usage_day.isoformat():
                continue
            found_day = True
            for item in day.get("data", []) or []:
                if not isinstance(item, dict):
                    continue
                for usage in item.get("usage", []) or []:
                    if not isinstance(usage, dict):
                        continue
                    token_type = str(usage.get("type", ""))
                    if token_type not in totals:
                        continue
                    totals[token_type] += _safe_int(usage.get("amount"))
    return totals if found_day else None


def cost_breakdown_for_day(
    payloads: list[dict[str, Any]], usage_day: date
) -> Decimal | None:
    """从已确认的按日接口响应中聚合当天累计费用。"""
    total = Decimal("0")
    found_cost = False
    for payload in payloads:
        for day in payload.get("days", []) or []:
            if not isinstance(day, dict) or str(day.get("date", "")) != usage_day.isoformat():
                continue
            for item in day.get("data", []) or []:
                if not isinstance(item, dict):
                    continue
                for usage in item.get("usage", []) or []:
                    if not isinstance(usage, dict) or usage.get("type") != "cost_cny":
                        continue
                    try:
                        amount = _decimal(usage.get("amount"))
                    except ValueError:
                        config_manager.logger().warning("Skipped malformed minute cost amount")
                        continue
                    if not amount.is_finite():
                        config_manager.logger().warning("Skipped non-finite minute cost amount")
                        continue
                    total += amount
                    found_cost = True
    return total if found_cost else None


def provider_observed_at(provider_id: str, observed_at: datetime) -> datetime:
    """将刷新时刻转换为提供商估算日界所使用的时区。"""
    if provider_id == "mimo":
        try:
            return observed_at.astimezone(ZoneInfo("Asia/Shanghai"))
        except ZoneInfoNotFoundError:
            # Windows 打包环境可能没有 IANA 时区数据库；MiMo 的平台日界固定为 UTC+8。
            return observed_at.astimezone(timezone(timedelta(hours=8), "Asia/Shanghai"))
    # DeepSeek 未返回账单时区；按已确认的产品约定使用运行设备本地时区。
    return observed_at.astimezone()


def provider_usage_day(provider_id: str, observed_at: datetime) -> date:
    """按提供商已确认或用户选择的估算日界返回自然日。"""
    return provider_observed_at(provider_id, observed_at).date()


def _load_minute_history(
    provider_id: str, current_day: date
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[str],
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
]:
    """Load retained minute rows once after the current refresh has been saved."""
    current_key = current_day.isoformat()
    minute_rows = history.minute_usage_for_day(provider_id, current_day)
    minute_cost_rows = history.minute_cost_usage_for_day(provider_id, current_day)
    minute_days = history.minute_usage_dates(provider_id)
    minute_history: dict[str, list[dict[str, Any]]] = {}
    minute_cost_history: dict[str, list[dict[str, Any]]] = {}
    for usage_date in minute_days:
        usage_day = date.fromisoformat(usage_date)
        # 当前日已单独读取供悬浮面板使用；历史映射复用同一只读列表，
        # 避免重复查询并同时保留原有字段结构。
        minute_history[usage_date] = (
            minute_rows
            if usage_date == current_key
            else history.minute_usage_for_day(provider_id, usage_day)
        )
        minute_cost_history[usage_date] = (
            minute_cost_rows
            if usage_date == current_key
            else history.minute_cost_usage_for_day(provider_id, usage_day)
        )
    return (
        minute_rows,
        minute_cost_rows,
        minute_days,
        minute_history,
        minute_cost_history,
    )


@dataclass
class PerProviderData:
    provider_id: str
    provider_name: str
    currency: str = "CNY"
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
    quota_windows: list[QuotaWindow] = field(default_factory=list)
    quota_metrics: list[QuotaMetric] = field(default_factory=list)
    quota_statistics: list[QuotaMetric] = field(default_factory=list)
    account_label: str = ""
    account_plan: str = ""
    account_plan_active_until: datetime | None = None
    quota_source: str = ""
    activity_source: str = ""
    weekly_activity_source: str = ""
    statistics_source: str = ""
    errors: list[FetchError] = field(default_factory=list)
    status: str = "loading"
    is_stale: bool = False


@dataclass
class TokenData:
    currency: str = "CNY"
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
    quota_windows: list[QuotaWindow] = field(default_factory=list)
    quota_metrics: list[QuotaMetric] = field(default_factory=list)
    quota_statistics: list[QuotaMetric] = field(default_factory=list)
    account_label: str = ""
    account_plan: str = ""
    account_plan_active_until: datetime | None = None
    quota_source: str = ""
    activity_source: str = ""
    weekly_activity_source: str = ""
    statistics_source: str = ""
    status: str = "loading"
    last_success_at: datetime | None = None
    last_attempt_at: datetime | None = None
    errors: list[FetchError] = field(default_factory=list)
    is_stale: bool = False
    last_updated: str = ""
    daily_usage: list[dict[str, Any]] = field(default_factory=list)
    weekly_usage: list[dict[str, Any]] = field(default_factory=list)
    minute_usage: list[dict[str, Any]] = field(default_factory=list)
    minute_usage_status: str = "unavailable"
    minute_usage_date: str = ""
    minute_usage_days: list[str] = field(default_factory=list)
    minute_usage_history: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    minute_cost_usage: list[dict[str, Any]] = field(default_factory=list)
    minute_cost_usage_history: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    minute_usage_source: str = ""

    _last_snapshot: ClassVar["TokenData | None"] = None
    _provider_snapshots: ClassVar[dict[str, "TokenData"]] = {}
    _cache_lock: ClassVar[threading.Lock] = threading.Lock()

    @staticmethod
    def _copy_for_refresh(snapshot: "TokenData") -> "TokenData":
        # 分钟历史会在本轮落盘后重新读取；复制旧历史只会抬高刷新峰值，
        # 且这些旧行不会参与额度失败回退等兼容逻辑。
        memo = {
            id(snapshot.minute_usage): [],
            id(snapshot.minute_usage_days): [],
            id(snapshot.minute_usage_history): {},
            id(snapshot.minute_cost_usage): [],
            id(snapshot.minute_cost_usage_history): {},
        }
        return copy.deepcopy(snapshot, memo)

    @staticmethod
    def _copy_for_cache(data: "TokenData") -> "TokenData":
        # 刷新完成后的分钟行不会再修改；内部快照只隔离其余可变状态，
        # 避免为长保留期常驻一份完全相同的高容量历史副本。
        memo = {
            id(data.minute_usage): data.minute_usage,
            id(data.minute_cost_usage): data.minute_cost_usage,
        }
        # 外层映射仍由 deepcopy 隔离，只有已完成的行列表共享，避免调用方
        # 增删日期时改变缓存，同时把主要内存占用限制为单份。
        for rows in data.minute_usage_history.values():
            memo[id(rows)] = rows
        for rows in data.minute_cost_usage_history.values():
            memo[id(rows)] = rows
        return copy.deepcopy(data, memo)

    @classmethod
    def test_connection(cls, config: Mapping[str, Any]) -> "TokenData":
        """Test one provider from an isolated configuration snapshot.

        The test deliberately avoids history and snapshot writes so a settings draft
        cannot affect the normal refresh path even when both operations overlap.
        """
        providers = list(active_providers(config))
        if not providers:
            return cls(
                status="error",
                errors=[FetchError("NOT_CONFIGURED", "平台", "没有可用的数据平台")],
                last_attempt_at=datetime.now(),
            )
        provider = providers[0]
        try:
            reset_cache = getattr(provider, "reset_refresh_cache", None)
            if reset_cache is not None:
                reset_cache()
            return cls._test_connection_with_provider(provider)
        finally:
            close = getattr(provider, "close", None)
            if close is not None:
                close()

    @classmethod
    def _test_connection_with_provider(cls, provider) -> "TokenData":
        per = PerProviderData(provider.id, provider.name, status="loading")
        successes = 0
        if not provider.is_configured():
            per.status = "not_configured"
            per.errors.append(
                FetchError("NOT_CONFIGURED", provider.name, f"尚未配置 {provider.name} 凭据")
            )
        else:
            for source, fetcher in (
                ("订阅额度", provider.fetch_quota),
                ("账户余额", provider.fetch_balance),
                ("账户摘要", provider.fetch_summary),
            ):
                try:
                    value, error = fetcher()
                except Exception as exc:
                    config_manager.logger().exception(
                        "Connection test failed for %s: %s", provider.id, source
                    )
                    value, error = None, FetchError("UNKNOWN_ERROR", source, str(exc))
                if value is not None:
                    successes += 1
                if error:
                    per.errors.append(error)
            try:
                payloads, errors = provider.fetch_payloads(
                    months_for_week(provider_usage_day(provider.id, datetime.now().astimezone()))
                )
            except Exception as exc:
                config_manager.logger().exception(
                    "Connection test payload fetch failed for %s", provider.id
                )
                payloads, errors = [], [FetchError("UNKNOWN_ERROR", "用量明细", str(exc))]
            if payloads:
                successes += 1
            per.errors.extend(errors)
            per.status = "partial" if successes and per.errors else "ok" if successes else "error"

        return cls(
            per_provider=[per],
            status=per.status,
            errors=list(per.errors),
            last_attempt_at=datetime.now(),
        )

    @classmethod
    def _base_snapshot(cls, provider_id: str = "") -> "TokenData":
        with cls._cache_lock:
            snapshot = cls._provider_snapshots.get(provider_id) if provider_id else cls._last_snapshot
            return cls._copy_for_refresh(snapshot) if snapshot else cls()

    @classmethod
    def cached_snapshot(cls, provider_id: str) -> "TokenData | None":
        """Return an isolated successful snapshot for one provider, if available."""
        with cls._cache_lock:
            snapshot = cls._provider_snapshots.get(provider_id)
            return copy.deepcopy(snapshot) if snapshot else None

    @classmethod
    def persisted_snapshot(
        cls, config: Mapping[str, Any] | None = None
    ) -> "TokenData | None":
        """Load the selected account's disk snapshot without making a network call."""
        providers = list(active_providers(config))
        if not providers:
            return None
        provider = providers[0]
        try:
            if not getattr(provider, "supports_subscription_quota", False):
                return None
            snapshot = cls._load_persisted_quota_snapshot(provider)
            if snapshot is not None:
                with cls._cache_lock:
                    cls._provider_snapshots[provider.id] = cls._copy_for_cache(snapshot)
            return snapshot
        except Exception:
            config_manager.logger().exception(
                "Persisted quota snapshot preload failed for %s", provider.id
            )
            return None
        finally:
            close = getattr(provider, "close", None)
            if close is not None:
                close()

    @staticmethod
    def _snapshot_identity(provider) -> str:
        identity_getter = getattr(provider, "snapshot_identity", None)
        if not callable(identity_getter):
            return ""
        try:
            return str(identity_getter() or "").strip()
        except Exception:
            config_manager.logger().warning(
                "Provider snapshot identity failed: provider=%s", provider.id
            )
            return ""

    @classmethod
    def _load_persisted_quota_snapshot(cls, provider) -> "TokenData | None":
        account_key = cls._snapshot_identity(provider)
        record = history.load_provider_quota_snapshot(provider.id, account_key)
        if record is None:
            return None
        payload, saved_at = record
        try:
            snapshot_version = int(payload.get("version") or 0)
        except (TypeError, ValueError):
            snapshot_version = 0

        def parsed_datetime(value: Any) -> datetime | None:
            if not value:
                return None
            return datetime.fromisoformat(str(value))

        def parsed_metrics(key: str) -> list[QuotaMetric]:
            result: list[QuotaMetric] = []
            values = payload.get(key)
            if not isinstance(values, list):
                return result
            for item in values:
                if not isinstance(item, dict):
                    continue
                result.append(
                    QuotaMetric(
                        str(item.get("title") or ""),
                        str(item.get("value") or ""),
                        str(item.get("detail") or ""),
                    )
                )
            return result

        try:
            windows: list[QuotaWindow] = []
            window_values = payload.get("windows")
            if isinstance(window_values, list):
                for item in window_values:
                    if not isinstance(item, dict):
                        continue
                    minutes = item.get("window_minutes")
                    windows.append(
                        QuotaWindow(
                            str(item.get("id") or ""),
                            str(item.get("title") or ""),
                            max(0.0, min(100.0, float(item.get("used_percent") or 0))),
                            resets_at=parsed_datetime(item.get("resets_at")),
                            window_minutes=None if minutes is None else int(minutes),
                            detail=str(item.get("detail") or ""),
                        )
                    )
            metrics = parsed_metrics("metrics")
            statistics = parsed_metrics("statistics")

            def parsed_activity(key: str) -> list[dict[str, Any]]:
                result: list[dict[str, Any]] = []
                activity_values = payload.get(key)
                if not isinstance(activity_values, list):
                    return result
                for item in activity_values:
                    if not isinstance(item, list) or len(item) != 2:
                        continue
                    usage_day = date.fromisoformat(str(item[0])).isoformat()
                    result.append(
                        {
                            "date": usage_day,
                            "tokens": max(0, int(item[1])),
                            "cost_cny": 0,
                        }
                    )
                return result

            daily_usage = parsed_activity("activity")
            weekly_usage = parsed_activity("weekly_activity")
            if snapshot_version < 3:
                official_statistics = bool(statistics) and all(
                    metric.detail == "来自 Codex 账号统计"
                    for metric in statistics
                )
                if official_statistics:
                    # 旧缓存只有统计项的 detail 能证明接口来源。热力图删除
                    # 当天这个历史上可能被估算合并的位置；近 7 天仍可保留
                    # 当前自然日的独立值，不让它进入官方活动序列。
                    today_key = date.today().isoformat()
                    legacy_weekly = weekly_usage or list(daily_usage)
                    daily_usage = [
                        item for item in daily_usage if item["date"] != today_key
                    ]
                    weekly_by_date = {
                        str(item["date"]): dict(item) for item in daily_usage
                    }
                    for item in legacy_weekly:
                        if item["date"] == today_key:
                            weekly_by_date[today_key] = dict(item)
                    weekly_usage = [
                        weekly_by_date[usage_day]
                        for usage_day in sorted(weekly_by_date)
                    ]
                else:
                    # 本机估算或来源不明的旧快照不能冒充官方缓存。
                    statistics = []
                    daily_usage = []
                    weekly_usage = []
            active_until = parsed_datetime(payload.get("account_plan_active_until"))
        except (TypeError, ValueError):
            config_manager.logger().warning(
                "Skipped invalid persisted quota snapshot: provider=%s", provider.id
            )
            return None

        currency = str(
            payload.get("currency")
            or getattr(provider, "default_currency", "CNY")
            or "CNY"
        ).upper()
        plan = str(payload.get("account_plan") or "")
        per = PerProviderData(
            provider.id,
            provider.name,
            currency=currency,
            quota_windows=windows,
            quota_metrics=metrics,
            quota_statistics=statistics,
            account_plan=plan,
            account_plan_active_until=active_until,
            quota_source="cache",
            activity_source="cache" if daily_usage else "",
            weekly_activity_source="cache" if weekly_usage else "",
            statistics_source="cache" if statistics else "",
            status="ok",
            is_stale=True,
        )
        return cls(
            currency=currency,
            per_provider=[per],
            quota_windows=list(windows),
            quota_metrics=list(metrics),
            quota_statistics=list(statistics),
            account_plan=plan,
            account_plan_active_until=active_until,
            quota_source="cache",
            activity_source="cache" if daily_usage else "",
            weekly_activity_source="cache" if weekly_usage else "",
            statistics_source="cache" if statistics else "",
            status="ok",
            last_success_at=saved_at,
            is_stale=True,
            last_updated=saved_at.strftime("%H:%M:%S"),
            daily_usage=daily_usage,
            weekly_usage=weekly_usage,
        )

    @classmethod
    def _save_persisted_quota_snapshot(cls, provider, data: "TokenData") -> None:
        account_key = cls._snapshot_identity(provider)
        if not account_key or not data.per_provider or data.last_success_at is None:
            return
        per = data.per_provider[0]

        def metric_payload(metric: QuotaMetric) -> dict[str, str]:
            return {
                "title": metric.title,
                "value": metric.value,
                "detail": metric.detail,
            }

        payload: dict[str, Any] = {
            "version": 3,
            "currency": per.currency,
            "windows": [
                {
                    "id": window.id,
                    "title": window.title,
                    "used_percent": window.used_percent,
                    "resets_at": (
                        window.resets_at.isoformat() if window.resets_at else None
                    ),
                    "window_minutes": window.window_minutes,
                    "detail": window.detail,
                }
                for window in per.quota_windows
            ],
            "metrics": [metric_payload(metric) for metric in per.quota_metrics],
            "statistics": [
                metric_payload(metric) for metric in per.quota_statistics
            ],
            "activity": [
                [str(item.get("date") or ""), int(item.get("tokens") or 0)]
                for item in data.daily_usage
                if item.get("date")
            ],
            "weekly_activity": [
                [str(item.get("date") or ""), int(item.get("tokens") or 0)]
                for item in data.weekly_usage
                if item.get("date")
            ],
            "account_plan": per.account_plan,
            "account_plan_active_until": (
                per.account_plan_active_until.isoformat()
                if per.account_plan_active_until
                else None
            ),
        }
        history.save_provider_quota_snapshot(
            provider.id, account_key, payload, data.last_success_at
        )

    @classmethod
    def fetch(
        cls,
        today: date | None = None,
        lightweight: bool = False,
        config: Mapping[str, Any] | None = None,
    ) -> "TokenData":
        # Background workers must use the configuration captured when they were
        # created. Otherwise a queued task can silently switch providers before
        # it starts running.
        providers = (
            list(active_providers(config))
            if config is not None
            else list(active_providers())
        )
        if not providers:
            return cls(
                status="error",
                errors=[FetchError("NOT_CONFIGURED", "平台", "没有可用的数据平台")],
                last_attempt_at=datetime.now(),
            )
        provider = providers[0]
        try:
            reset_cache = getattr(provider, "reset_refresh_cache", None)
            if reset_cache is not None:
                reset_cache()
            return cls._fetch_with_provider(provider, today, lightweight)
        finally:
            close = getattr(provider, "close", None)
            if close is not None:
                close()

    @classmethod
    def _fetch_with_provider(
        cls, provider, today: date | None = None, lightweight: bool = False
    ) -> "TokenData":
        observed_at = provider_observed_at(provider.id, datetime.now().astimezone())
        current_day = today or observed_at.date()
        cached = cls._base_snapshot(provider.id)
        if (
            not cached.per_provider
            and getattr(provider, "supports_subscription_quota", False)
        ):
            try:
                persisted = cls._load_persisted_quota_snapshot(provider)
            except Exception:
                config_manager.logger().exception(
                    "Persisted quota snapshot read failed for %s", provider.id
                )
            else:
                if persisted is not None:
                    cached = persisted
        data = cached
        data.status = "loading"
        data.errors = []
        data.last_attempt_at = datetime.now()
        previous_per = data.per_provider[0] if data.per_provider else None
        per = (
            copy.deepcopy(previous_per)
            if previous_per
            else PerProviderData(provider.id, provider.name)
        )
        per.provider_id = provider.id
        per.provider_name = provider.name
        per.currency = str(getattr(provider, "default_currency", "CNY") or "CNY").upper()
        per.quota_windows = []
        per.quota_metrics = []
        per.quota_statistics = []
        per.account_label = ""
        per.account_plan = ""
        per.account_plan_active_until = None
        per.quota_source = ""
        per.activity_source = ""
        per.weekly_activity_source = ""
        per.statistics_source = ""
        per.errors = []
        per.status = "loading"
        per.is_stale = False
        successes = 0
        kept_cached_quota = False
        quota_refresh_failed = False
        quota_refresh_succeeded = False
        minute_rows: list[dict[str, Any]] = []
        minute_cost_rows: list[dict[str, Any]] = []
        minute_status = "unavailable"
        minute_days: list[str] = []
        minute_history: dict[str, list[dict[str, Any]]] = {}
        minute_cost_history: dict[str, list[dict[str, Any]]] = {}
        retention_days = 3
        supports_estimated_minutes = bool(
            getattr(provider, "supports_estimated_minute_usage", False)
        )
        supports_exact_minutes = bool(
            getattr(provider, "supports_exact_minute_usage", False)
        )
        supports_minute_usage = supports_estimated_minutes or supports_exact_minutes
        if supports_minute_usage:
            try:
                # 每次启动/刷新均按设置的保留天数清理；失败不能影响原有账单刷新。
                retention_days = int(provider.config_get("MINUTE_USAGE_RETENTION_DAYS", 3))
                history.clear_expired_minute_usage(
                    provider.id, current_day, retention_days
                )
                minute_status = "empty"
            except Exception:
                config_manager.logger().exception("Minute usage cleanup failed for %s", provider.id)
                per.errors.append(FetchError("LOCAL_STORAGE", "分时缓存", "分钟缓存清理失败"))
                minute_status = "storage_error"

        if not provider.is_configured():
            # 删除或切换凭据后不能继续展示旧账号数据，否则会造成“仍已登录”的错觉。
            per = PerProviderData(
                provider.id,
                provider.name,
                currency=str(getattr(provider, "default_currency", "CNY") or "CNY").upper(),
                status="not_configured",
            )
            per.errors.append(FetchError("NOT_CONFIGURED", provider.name, f"尚未配置 {provider.name} 凭据"))
            data.daily_usage = []
            data.weekly_usage = []
            data.last_success_at = None
            data.last_updated = ""
            if supports_exact_minutes:
                minute_status = "unavailable"
        else:
            try:
                quota, quota_error = provider.fetch_quota()
            except Exception as exc:
                config_manager.logger().exception("Quota fetch failed for %s", provider.id)
                quota, quota_error = None, FetchError(
                    "UNKNOWN_ERROR", "订阅额度", str(exc)
                )
            quota_refresh_failed = bool(
                quota_error
                and getattr(provider, "supports_subscription_quota", False)
            )
            quota_refresh_succeeded = bool(
                quota is not None
                and quota_error is None
                and getattr(provider, "supports_subscription_quota", False)
            )
            kept_cached_quota = bool(
                quota_error
                and quota_error.code in _TRANSIENT_QUOTA_ERROR_CODES
                and previous_per
                and (
                    previous_per.quota_windows
                    or previous_per.quota_metrics
                    or previous_per.quota_statistics
                    or previous_per.account_plan
                    or previous_per.account_plan_active_until
                    or data.daily_usage
                    or data.weekly_usage
                )
            )
            if kept_cached_quota and previous_per is not None:
                # 暂时性远程失败必须保留整份最后成功快照，避免额度、统计和
                # 活动图分别回退到不同时间点的数据。
                per.quota_windows = copy.deepcopy(previous_per.quota_windows)
                per.quota_metrics = copy.deepcopy(previous_per.quota_metrics)
                per.quota_statistics = copy.deepcopy(previous_per.quota_statistics)
                per.account_label = previous_per.account_label
                per.account_plan = previous_per.account_plan
                per.account_plan_active_until = previous_per.account_plan_active_until
                per.quota_source = "cache"
                per.activity_source = "cache" if data.daily_usage else ""
                per.weekly_activity_source = "cache" if data.weekly_usage else ""
                per.statistics_source = (
                    "cache" if previous_per.quota_statistics else ""
                )
            if quota is not None:
                if not kept_cached_quota:
                    per.quota_windows = list(quota.windows)
                    per.quota_metrics = list(quota.metrics)
                    per.account_label = quota.account_label
                    per.account_plan = quota.plan
                    per.account_plan_active_until = quota.account_plan_active_until
                    per.quota_source = "interface" if quota_error is None else ""

                    if quota.activity_source or quota.activity:
                        data.daily_usage = [
                            {"date": usage_day, "tokens": tokens, "cost_cny": 0}
                            for usage_day, tokens in quota.activity
                        ]
                        per.activity_source = quota.activity_source or "interface"
                    elif data.daily_usage:
                        # 额度接口与账号统计接口相互独立；后者失败不能清空
                        # 跨重启恢复出的最后一份官方热力图。
                        per.activity_source = "cache"
                    else:
                        data.daily_usage = []
                        per.activity_source = ""

                    if quota.statistics_source or quota.statistics:
                        per.quota_statistics = list(quota.statistics)
                        per.statistics_source = (
                            quota.statistics_source or "interface"
                        )
                    elif previous_per and previous_per.quota_statistics:
                        per.quota_statistics = copy.deepcopy(
                            previous_per.quota_statistics
                        )
                        per.statistics_source = "cache"
                    else:
                        per.quota_statistics = []
                        per.statistics_source = ""

                    weekly_activity = quota.weekly_activity or quota.activity
                    weekly_usage = [
                        {"date": usage_day, "tokens": tokens, "cost_cny": 0}
                        for usage_day, tokens in weekly_activity
                    ]
                    if quota.weekly_activity_source == "local" and data.weekly_usage:
                        # 统计接口刚好离线时保留缓存历史，只覆盖本机明确提供的
                        # 当天估算；这样不会用本机日志重算任何历史日期。
                        merged_weekly = {
                            str(item.get("date") or ""): dict(item)
                            for item in data.weekly_usage
                            if item.get("date")
                        }
                        for item in weekly_usage:
                            merged_weekly[str(item["date"])] = item
                        data.weekly_usage = [
                            merged_weekly[usage_day]
                            for usage_day in sorted(merged_weekly)
                        ]
                        per.weekly_activity_source = "cache_mixed"
                    elif quota.weekly_activity_source or weekly_activity:
                        data.weekly_usage = weekly_usage
                        per.weekly_activity_source = (
                            quota.weekly_activity_source
                            or per.activity_source
                            or "interface"
                        )
                    elif data.weekly_usage:
                        per.weekly_activity_source = "cache"
                    else:
                        data.weekly_usage = []
                        per.weekly_activity_source = ""
                successes += 1
            elif kept_cached_quota:
                # 没有本地活动时也继续展示上一份完整额度。
                successes += 1
            if quota_error:
                if kept_cached_quota:
                    # 静默降级只影响界面；日志仍保留失败证据用于诊断。
                    config_manager.logger().warning(
                        "Fetch failed: provider=%s source=%s code=%s; retained cached quota",
                        provider.id,
                        quota_error.source,
                        quota_error.code,
                    )
                else:
                    per.errors.append(quota_error)

            try:
                balance, balance_error = provider.fetch_balance()
            except Exception as exc:
                config_manager.logger().exception("Balance fetch failed for %s", provider.id)
                balance, balance_error = None, FetchError("UNKNOWN_ERROR", "账户余额", str(exc))
            if balance is not None:
                per.balance_cny = float(balance.amount) if balance.amount is not None else None
                per.balance_tokens = int(balance.token_estimate)
                per.currency = str(balance.currency or per.currency).upper()
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
                if summary.today_cost is not None:
                    per.today_cost_cny = float(summary.today_cost)
                if summary.today_tokens is not None:
                    per.today_tokens = int(summary.today_tokens)
                if summary.total_cost is not None:
                    per.total_cost_cny = float(summary.total_cost)
                successes += 1
            if summary_error:
                per.errors.append(summary_error)

            if lightweight:
                # 轻量采集只请求当前月；非当前 Provider 的分钟采样不能每分钟
                # 重复历史补同步，收起状态的 MiMo 继续复用同一路径。
                request_months = [(current_day.month, current_day.year)]
            else:
                current_month = (current_day.month, current_day.year)
                request_months = [current_month]
                try:
                    unsynced = history.unsynced_months(
                        months_for_activity(current_day), provider.id
                    )
                    for month in unsynced:
                        if month in request_months:
                            continue
                        request_months.append(month)
                        if len(request_months) >= 1 + HISTORY_SYNC_BATCH_SIZE:
                            break
                except Exception:
                    config_manager.logger().exception("History sync state read failed for %s", provider.id)
                    per.errors.append(FetchError("LOCAL_STORAGE", "历史缓存", "本地同步状态读取失败"))

            try:
                fetched_payloads, payload_errors = provider.fetch_payloads(request_months)
            except Exception as exc:
                config_manager.logger().exception("Payload fetch failed for %s", provider.id)
                fetched_payloads, payload_errors = [], [FetchError("UNKNOWN_ERROR", "用量明细", str(exc))]
            per.errors.extend(payload_errors)
            payloads = list(fetched_payloads)
            for month, year in months_for_week(current_day):
                if (month, year) in request_months:
                    continue
                try:
                    cached_payload = history.provider_monthly_payload(
                        provider.id, year, month
                    )
                except Exception:
                    config_manager.logger().exception(
                        "Historical month cache read failed for %s", provider.id
                    )
                    per.errors.append(
                        FetchError("LOCAL_STORAGE", "历史缓存", "本地历史月份读取失败")
                    )
                    continue
                if cached_payload is not None:
                    # 完整历史月份只从 SQLite 参与本轮聚合，不再发送网络请求。
                    payloads.append(cached_payload)
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
                # 当前月日明细与今日/周统计同源；摘要接口偶尔会返回占位 0，
                # 因此仅要明细中确实包含该指标，就用逐日累计覆盖摘要值。
                if month_tokens is not None:
                    per.monthly_usage_tokens = month_tokens
                if month_cost is not None:
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
                    history.save_usage(
                        fetched_payloads,
                        fetched_payloads,
                        completed,
                        provider.id,
                    )
                except Exception:
                    config_manager.logger().exception("History save failed for %s", provider.id)
                    per.errors.append(FetchError("LOCAL_STORAGE", "历史缓存", "本地历史保存失败"))

                if supports_exact_minutes:
                    exact_usage = provider.exact_minute_usage()
                    if exact_usage is None:
                        minute_status = "failed"
                    else:
                        retained_from = current_day - timedelta(days=max(1, retention_days) - 1)
                        exact_dates = {
                            usage_date
                            for usage_date in exact_usage.usage_dates
                            if retained_from <= usage_date <= current_day
                        }
                        complete_months = set(exact_usage.complete_months)
                        retained_day = retained_from
                        while retained_day <= current_day:
                            if (retained_day.month, retained_day.year) in complete_months:
                                exact_dates.add(retained_day)
                            retained_day += timedelta(days=1)
                        # A successful response is authoritative for today even when
                        # a provider snapshot predates the complete-month marker.
                        exact_dates.add(current_day)
                        try:
                            minute_status = history.replace_exact_minute_usage(
                                provider.id,
                                exact_dates,
                                [
                                    row
                                    for row in exact_usage.token_rows
                                    if row.get("usage_date") in exact_dates
                                ],
                                [
                                    row
                                    for row in exact_usage.cost_rows
                                    if row.get("usage_date") in exact_dates
                                ],
                                current_day,
                                retention_days,
                            )
                        except Exception:
                            config_manager.logger().exception(
                                "Exact minute usage save failed for %s", provider.id
                            )
                            per.errors.append(
                                FetchError("LOCAL_STORAGE", "分时缓存", "分钟明细保存失败")
                            )
                            minute_status = "storage_error"
                elif supports_estimated_minutes:
                    token_totals = token_breakdown_for_day(payloads, current_day)
                    if token_totals is None:
                        minute_status = "empty"
                    else:
                        try:
                            cost_total = cost_breakdown_for_day(payloads, current_day)
                        except Exception:
                            config_manager.logger().exception(
                                "Minute cost aggregation failed for %s", provider.id
                            )
                            cost_total = None
                        try:
                            minute_status = history.save_estimated_minute_usage(
                                provider.id,
                                current_day,
                                token_totals,
                                observed_at,
                                retention_days,
                                cost_cny=cost_total,
                            )
                        except Exception:
                            config_manager.logger().exception(
                                "Minute usage save failed for %s", provider.id
                            )
                            per.errors.append(
                                FetchError("LOCAL_STORAGE", "分时缓存", "分钟缓存保存失败")
                            )
                            minute_status = "storage_error"
            elif supports_minute_usage:
                minute_status = "failed" if payload_errors else "empty"

            try:
                if provider.supports_daily_usage:
                    data.daily_usage = history.recent_daily(371, provider.id)
                if provider.supports_cost and per.total_cost_cny is None:
                    per.total_cost_cny = float(history.total_cost(provider.id))
            except Exception:
                config_manager.logger().exception("History read failed for %s", provider.id)
                per.errors.append(FetchError("LOCAL_STORAGE", "历史缓存", "本地历史读取失败"))

            if successes:
                per.status = "partial" if per.errors else "ok"
                per.is_stale = bool(per.errors) or kept_cached_quota
            else:
                per.status = "error"
                per.is_stale = previous_per is not None

        if supports_minute_usage and not (
            supports_exact_minutes and per.status == "not_configured"
        ):
            try:
                (
                    minute_rows,
                    minute_cost_rows,
                    minute_days,
                    minute_history,
                    minute_cost_history,
                ) = _load_minute_history(provider.id, current_day)
            except Exception:
                config_manager.logger().exception(
                    "Minute usage history read failed for %s", provider.id
                )
                per.errors.append(
                    FetchError("LOCAL_STORAGE", "分时缓存", "分钟缓存读取失败")
                )
                minute_status = "storage_error"
            if successes:
                # 分时读取发生在远程聚合之后，需在这里同步最终状态。
                per.status = "partial" if per.errors else "ok"
                per.is_stale = bool(per.errors) or kept_cached_quota

        data.per_provider = [per]
        data.currency = per.currency
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
        data.quota_windows = list(per.quota_windows)
        data.quota_metrics = list(per.quota_metrics)
        data.quota_statistics = list(per.quota_statistics)
        data.account_label = per.account_label
        data.account_plan = per.account_plan
        data.account_plan_active_until = per.account_plan_active_until
        data.quota_source = per.quota_source
        data.activity_source = per.activity_source
        data.weekly_activity_source = per.weekly_activity_source
        data.statistics_source = per.statistics_source
        data.errors = list(per.errors)
        data.minute_usage = minute_rows
        data.minute_usage_status = minute_status
        data.minute_usage_date = current_day.isoformat()
        data.minute_usage_days = minute_days
        data.minute_usage_history = minute_history
        data.minute_cost_usage = minute_cost_rows
        data.minute_cost_usage_history = minute_cost_history
        data.minute_usage_source = (
            "provider" if supports_exact_minutes else "estimated"
            if supports_estimated_minutes
            else ""
        )

        if successes:
            if not quota_refresh_failed:
                data.last_success_at = datetime.now()
                data.last_updated = data.last_success_at.strftime("%H:%M:%S")
            data.status = "partial" if per.errors else "ok"
            data.is_stale = per.is_stale
            with cls._cache_lock:
                cls._provider_snapshots[provider.id] = cls._copy_for_cache(data)
            if quota_refresh_succeeded:
                try:
                    cls._save_persisted_quota_snapshot(provider, data)
                except Exception:
                    # 缓存落盘失败不能把一次成功的远程刷新降级为界面错误。
                    config_manager.logger().exception(
                        "Persisted quota snapshot write failed for %s", provider.id
                    )
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
