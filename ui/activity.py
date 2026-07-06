"""Pure data preparation for the long-range Token activity calendar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from math import ceil, log1p
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
    values = sorted(day.token_count for day in days if day.token_count > 0)
    if not values:
        return {}
    # Cap the scale at the 95th percentile so a single spike does not flatten normal days.
    cap_index = max(0, ceil(len(values) * 0.95) - 1)
    scale_max = max(1, values[cap_index])
    denominator = log1p(scale_max)
    return {
        day.date: min(4, max(1, ceil(log1p(min(day.token_count, scale_max)) / denominator * 4)))
        for day in days
        if day.token_count > 0
    }


def compact_tokens(value: int) -> str:
    # 保持 v1.0 的“万 Token”统一口径，避免同一面板在数值变化时切换单位。
    scaled = value / 10_000
    decimals = 4 if value and abs(value) < 100 else 2
    text = f"{scaled:.{decimals}f}".rstrip("0").rstrip(".")
    return f"{text}万"
