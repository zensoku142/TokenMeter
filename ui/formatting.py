"""Lightweight display formatting shared by the ball and full panel."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from math import ceil

from ui.activity import compact_tokens


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


def format_plan_active_until(value: datetime | None) -> str:
    if value is None:
        return "--"
    local_value = value.astimezone() if value.tzinfo is not None else value
    return local_value.strftime("%m-%d")


def is_codex_spark_quota(title: str) -> bool:
    normalized = "".join(str(title or "").casefold().split())
    return "codex-spark" in normalized
