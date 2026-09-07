"""Lightweight display formatting shared by the ball and full panel."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from math import ceil, isfinite

from ui.activity import compact_tokens
from ui.i18n import current_language, tr

_SHANGHAI_TIMEZONE = timezone(timedelta(hours=8))


def quota_used_percent(value) -> float | None:
    # 各展示面及提醒共用校验；未知值不能参与减法或被夹成满额，超过 100% 则保留真实超用量。
    if isinstance(value, bool):
        return None
    try:
        used = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return used if isfinite(used) and used >= 0 else None


def format_quota_metric(metric) -> str:
    if metric.raw_value is None or current_language() == "zh-cn":
        return tr(metric.value)
    value = metric.raw_value
    if metric.value_kind == "tokens":
        if current_language() == "en":
            for scale, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
                if abs(value) >= scale:
                    return f"{value / scale:.1f}".rstrip("0").rstrip(".") + suffix
            return str(value)
        return tr(format_codex_tokens(value))
    if metric.value_kind == "days":
        return tr("{n} 天", n=value)
    if metric.value_kind == "seconds":
        hours, remainder = divmod(max(0, value), 3600)
        minutes, seconds = divmod(remainder, 60)
        return (
            tr("{hours}时 {minutes}分", hours=hours, minutes=minutes)
            if hours else tr("{minutes}分 {seconds}秒", minutes=minutes, seconds=seconds)
        )
    return tr(metric.value)


def _currency_prefix(currency: str) -> str:
    normalized = str(currency or "CNY").strip().upper()
    return {"CNY": "¥", "USD": "$", "EUR": "€", "GBP": "£"}.get(
        normalized, f"{normalized} "
    )


def format_money(value: float | Decimal | None, currency: str = "CNY") -> str:
    if value is None:
        return "--"
    amount = float(value)
    decimals = 4 if 0 < abs(amount) < 0.01 else 2
    return f"{_currency_prefix(currency)}{amount:.{decimals}f}"


def format_minute_money(
    value: float | Decimal | None, currency: str = "CNY"
) -> str:
    if value is None:
        return "--"
    if str(currency or "").strip().upper() == "USD":
        return f"${Decimal(str(value)):.4f}"
    return format_money(value, currency)


def format_token_axis(value: float) -> str:
    return compact_tokens(int(round(value)))


def format_codex_tokens(value: int | float) -> str:
    amount = int(round(value))
    denominator, suffix = (
        (100_000_000, "亿") if abs(amount) >= 100_000_000 else (10_000, "万")
    )
    text = f"{amount / denominator:.1f}".rstrip("0").rstrip(".")
    return f"{text or '0'}{suffix}"


def format_money_axis(value: float, currency: str = "CNY") -> str:
    absolute = abs(value)
    if absolute >= 100:
        return f"{_currency_prefix(currency)}{value:,.0f}"
    decimals = 4 if 0 < absolute < 0.01 else 2
    return f"{_currency_prefix(currency)}{value:.{decimals}f}"


def format_reset_countdown(value: datetime | None, now: datetime | None = None) -> str:
    if value is None:
        return "重置时间未知"
    current = now or datetime.now(value.tzinfo)
    if value.tzinfo is not None and current.tzinfo is None:
        current = current.replace(tzinfo=value.tzinfo)
    elif value.tzinfo is None and current.tzinfo is not None:
        current = current.replace(tzinfo=None)
    seconds = max(0, int((value - current).total_seconds()))
    if seconds <= 0:
        return "即将重置"
    minutes = max(1, ceil(seconds / 60))
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days} 天 {hours} 小时后重置"
    if hours:
        return f"{hours} 小时 {minutes} 分钟后重置"
    return f"{minutes} 分钟后重置"


def format_codex_reset_time(value: datetime | None, *, compact: bool = False) -> str:
    if value is None:
        return "重置时间未知"
    local_value = (
        value.astimezone(_SHANGHAI_TIMEZONE) if value.tzinfo is not None else value
    )
    if compact:
        return f"{local_value.month}月{local_value.day}日{local_value:%H:%M}"
    return f"{local_value.month}月{local_value.day}日 {local_value:%H:%M}重置"


def format_plan_active_until(value: datetime | None) -> str:
    if value is None:
        return "--"
    local_value = value.astimezone() if value.tzinfo is not None else value
    return local_value.strftime("%m-%d")


def is_codex_spark_quota(title: str) -> bool:
    normalized = "".join(str(title or "").casefold().split())
    return "codex-spark" in normalized
