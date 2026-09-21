"""DeepSeek peak-pricing schedule calculations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Mapping

BEIJING_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")
TIME_PATTERN = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
PERIOD_KEYS = (
    ("DEEPSEEK_PEAK_PERIOD_1_START", "DEEPSEEK_PEAK_PERIOD_1_END"),
    ("DEEPSEEK_PEAK_PERIOD_2_START", "DEEPSEEK_PEAK_PERIOD_2_END"),
)


@dataclass(frozen=True, slots=True)
class PricingState:
    is_peak: bool
    label: str
    tooltip: str
    next_boundary: datetime


def parse_time_text(value: object) -> time:
    """Parse the persisted minute-precision format without accepting loose variants."""
    text = str(value).strip()
    if not TIME_PATTERN.fullmatch(text):
        raise ValueError("时间必须使用 HH:mm 格式")
    hour, minute = (int(part) for part in text.split(":"))
    return time(hour, minute)


def parse_offpeak_dates(value: object) -> frozenset[date]:
    """Parse comma/newline separated dates and inclusive date ranges."""
    dates: set[date] = set()
    text = str(value or "").strip()
    if not text:
        return frozenset()
    for item in re.split(r"[,，;；\n]+", text):
        item = item.strip()
        if not item:
            continue
        parts = re.split(r"\s*(?:\.\.|~|～|至)\s*", item)
        if len(parts) not in (1, 2) or not all(DATE_PATTERN.fullmatch(part) for part in parts):
            raise ValueError("空闲日期必须使用 YYYY-MM-DD 或 YYYY-MM-DD..YYYY-MM-DD 格式")
        try:
            start = date.fromisoformat(parts[0])
            end = date.fromisoformat(parts[-1])
        except ValueError as exc:
            raise ValueError("空闲日期包含无效日期") from exc
        if start > end:
            raise ValueError("空闲日期范围的开始日期不能晚于结束日期")
        current = start
        while current <= end:
            dates.add(current)
            current += timedelta(days=1)
    return frozenset(dates)


def normalize_offpeak_dates(value: object) -> str:
    """Persist holiday dates in a stable form after validating them."""
    dates = sorted(parse_offpeak_dates(value))
    ranges: list[str] = []
    index = 0
    while index < len(dates):
        start = dates[index]
        end = start
        while index + 1 < len(dates) and dates[index + 1] == end + timedelta(days=1):
            index += 1
            end = dates[index]
        ranges.append(
            start.isoformat() if start == end else f"{start.isoformat()}..{end.isoformat()}"
        )
        index += 1
    return ",".join(ranges)


def configured_periods(values: Mapping[str, object]) -> tuple[tuple[time, time], ...]:
    periods = tuple(
        (parse_time_text(values[start_key]), parse_time_text(values[end_key]))
        for start_key, end_key in PERIOD_KEYS
    )
    first, second = periods
    # The UI presents numbered daytime periods, so keep their order explicit instead
    # of silently sorting invalid input and showing a schedule the user did not enter.
    if first[0] >= first[1] or second[0] >= second[1]:
        raise ValueError("DeepSeek 高峰时段的开始时间必须早于结束时间")
    if first[1] > second[0]:
        raise ValueError("DeepSeek 高峰时段必须按时间顺序排列且不能重叠")
    return periods


def pricing_state(
    values: Mapping[str, object], now: datetime | None = None
) -> PricingState:
    periods = configured_periods(values)
    if now is None:
        current = datetime.now(BEIJING_TIMEZONE)
    elif now.tzinfo is None:
        current = now.replace(tzinfo=BEIJING_TIMEZONE)
    else:
        current = now.astimezone(BEIJING_TIMEZONE)

    today = current.date()
    offpeak_dates = parse_offpeak_dates(values.get("DEEPSEEK_OFFPEAK_DATES", ""))
    peak_weekdays = {
        int(day) for day in values.get("DEEPSEEK_PEAK_WEEKDAYS", (0, 1, 2, 3, 4))
    }

    def is_peak_day(day: date) -> bool:
        # 按自然周匹配用户勾选项，不采用中国工作日日历对调休日的分类；
        # 因而默认未勾选的调休周末仍保持 DeepSeek 规定的空闲价格。
        return day.weekday() in peak_weekdays and day not in offpeak_dates

    boundaries = [
        (
            datetime.combine(today, start, BEIJING_TIMEZONE),
            datetime.combine(today, end, BEIJING_TIMEZONE),
        )
        for start, end in periods
    ]
    is_peak = False
    next_boundary: datetime | None = None
    if is_peak_day(today):
        for start, end in boundaries:
            if current < start:
                next_boundary = start
                break
            if start <= current < end:
                is_peak = True
                next_boundary = end
                break
    if next_boundary is None:
        next_peak_day = today + timedelta(days=1)
        while not is_peak_day(next_peak_day):
            next_peak_day += timedelta(days=1)
        next_boundary = datetime.combine(next_peak_day, periods[0][0], BEIJING_TIMEZONE)

    boundary_text = next_boundary.strftime("%H:%M")
    if is_peak:
        label = f"峰时 2× · {boundary_text} 结束"
    else:
        if next_boundary.date() == today:
            day_prefix = ""
        elif next_boundary.date() == today + timedelta(days=1):
            day_prefix = "明日 "
        else:
            day_prefix = next_boundary.strftime("%m月%d日 ").lstrip("0").replace("月0", "月")
        label = f"空闲 1× · {day_prefix}{boundary_text} 进入峰时"
    schedule = "、".join(
        f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}" for start, end in periods
    )
    weekday_names = "、".join(
        name
        for index, name in enumerate(
            ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
        )
        if index in peak_weekdays
    )
    tooltip = (
        f"{label}\n北京时间高峰日：{weekday_names}；高峰时段：{schedule}\n"
        "未勾选的日期及法定节假日全天为空闲时段；本提示不参与账单计算。"
    )
    return PricingState(is_peak, label, tooltip, next_boundary)


__all__ = [
    "BEIJING_TIMEZONE",
    "PricingState",
    "configured_periods",
    "normalize_offpeak_dates",
    "parse_offpeak_dates",
    "parse_time_text",
    "pricing_state",
]
