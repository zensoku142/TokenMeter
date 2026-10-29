"""Pure data preparation for the long-range Token activity calendar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable


ACTIVITY_DAYS = 365


@dataclass(frozen=True)
class TokenActivityDay:
    date: date
    token_count: int
    amount: float | None = None
    request_count: int | None = None
    has_source_data: bool = False


@dataclass(frozen=True)
class ActivityRange:
    start: date
    end: date
    grid_start: date
    grid_end: date
    week_count: int


def activity_range(today: date | None = None) -> ActivityRange:
    current = today or date.today()
    start = current - timedelta(days=ACTIVITY_DAYS - 1)
    grid_start = start - timedelta(days=start.weekday())
    grid_end = current + timedelta(days=6 - current.weekday())
    weeks = (grid_end - grid_start).days // 7 + 1
    return ActivityRange(start, current, grid_start, grid_end, weeks)


def calendar_position(current: date, grid_start: date) -> tuple[int, int]:
    return (current - grid_start).days // 7, current.weekday()


def normalize_activity(
    rows: Iterable[dict[str, Any]], today: date | None = None
) -> tuple[ActivityRange, list[TokenActivityDay]]:
    period = activity_range(today)
    aggregated: dict[date, dict[str, Any]] = {}
    for row in rows:
        try:
            current = date.fromisoformat(str(row["date"]))
        except (KeyError, TypeError, ValueError):
            continue
        if not period.start <= current <= period.end:
            continue
        item = aggregated.setdefault(
            current,
            {"tokens": 0, "amount": None, "request_count": None},
        )
        item["tokens"] += max(0, int(row.get("tokens", 0) or 0))
        if row.get("cost_cny") is not None:
            item["amount"] = Decimal(str(item["amount"] or 0)) + Decimal(
                str(row["cost_cny"])
            )
        if row.get("request_count") is not None:
            item["request_count"] = int(item["request_count"] or 0) + int(
                row["request_count"]
            )

    days: list[TokenActivityDay] = []
    current = period.grid_start
    while current <= period.grid_end:
        values = aggregated.get(current)
        days.append(
            TokenActivityDay(
                date=current,
                token_count=int(values["tokens"]) if values else 0,
                amount=float(values["amount"]) if values and values["amount"] is not None else None,
                request_count=values["request_count"] if values else None,
                has_source_data=values is not None,
            )
        )
        current += timedelta(days=1)
    return period, days


def activity_levels(days: Iterable[TokenActivityDay]) -> dict[date, int]:
    days = list(days)
    values = sorted(day.token_count for day in days if day.token_count > 0)
    if not values:
        return {}

    # 非零用量的 75% 分位向下取整，最多用其两倍作为色阶上限，避免异常峰值压平普通日期。
    scale_max = min(values[-1], values[(len(values) - 1) * 3 // 4] * 2)
    # 向上取整让非零用量至少落入第一档，超过上限的用量显示最高档。
    return {
        day.date: min(5, (day.token_count * 5 + scale_max - 1) // scale_max)
        for day in days
        if day.token_count > 0
    }


def compact_tokens(value: int) -> str:
    """把整数 token 数量压缩为中文可读短文本。

    默认以"万"为单位（< 1 万也以 "X 万" 输出，保持单位一致）；
    当数值 ≥ 1 亿时切换为"亿"。保留最多 2 位小数，末尾 0 会
    被去掉。
    """
    if value is None:
        return "--"
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return str(value)
    if amount == 0:
        return "0万"

    def _fmt(denominator: int) -> str:
        scaled = amount / denominator
        text = f"{scaled:.2f}".rstrip("0").rstrip(".")
        # 若因四舍五入退化为 0，直接返回 "0" 以便外层补单位。
        return "0" if text.lstrip("-") == "0" else text

    abs_amount = abs(amount)
    if abs_amount >= 100_000_000:
        yi = _fmt(100_000_000)
        return f"{yi}亿"
    wan = _fmt(10_000)
    return f"{wan}万"
