from datetime import datetime, timezone

import pytest

from api.deepseek_pricing import (
    BEIJING_TIMEZONE,
    normalize_offpeak_dates,
    parse_offpeak_dates,
    pricing_state,
)


DEFAULTS = {
    "DEEPSEEK_PEAK_PERIOD_1_START": "09:00",
    "DEEPSEEK_PEAK_PERIOD_1_END": "12:00",
    "DEEPSEEK_PEAK_PERIOD_2_START": "14:00",
    "DEEPSEEK_PEAK_PERIOD_2_END": "18:00",
}


@pytest.mark.parametrize(
    ("clock", "is_peak", "label", "next_boundary"),
    (
        ("08:59", False, "空闲 1× · 09:00 进入峰时", "09:00"),
        ("09:00", True, "峰时 2× · 12:00 结束", "12:00"),
        ("11:59", True, "峰时 2× · 12:00 结束", "12:00"),
        ("12:00", False, "空闲 1× · 14:00 进入峰时", "14:00"),
        ("13:59", False, "空闲 1× · 14:00 进入峰时", "14:00"),
        ("14:00", True, "峰时 2× · 18:00 结束", "18:00"),
        ("17:59", True, "峰时 2× · 18:00 结束", "18:00"),
        ("18:00", False, "空闲 1× · 明日 09:00 进入峰时", "09:00"),
    ),
)
def test_default_peak_pricing_boundaries(clock, is_peak, label, next_boundary):
    hour, minute = (int(part) for part in clock.split(":"))
    now = datetime(2026, 7, 15, hour, minute, tzinfo=BEIJING_TIMEZONE)

    state = pricing_state(DEFAULTS, now)

    assert state.is_peak is is_peak
    assert state.label == label
    assert state.next_boundary.strftime("%H:%M") == next_boundary
    assert "北京时间高峰日：周一、周二、周三、周四、周五" in state.tooltip
    assert "高峰时段：09:00–12:00、14:00–18:00" in state.tooltip
    assert "本提示不参与账单计算" in state.tooltip


def test_peak_pricing_converts_aware_times_to_beijing_and_supports_custom_periods():
    values = {
        "DEEPSEEK_PEAK_PERIOD_1_START": "08:30",
        "DEEPSEEK_PEAK_PERIOD_1_END": "10:00",
        "DEEPSEEK_PEAK_PERIOD_2_START": "16:00",
        "DEEPSEEK_PEAK_PERIOD_2_END": "19:30",
    }

    state = pricing_state(values, datetime(2026, 7, 15, 0, 30, tzinfo=timezone.utc))

    assert state.is_peak
    assert state.label == "峰时 2× · 10:00 结束"


def test_weekends_including_adjusted_work_weekends_are_offpeak():
    adjusted_sunday = pricing_state(
        DEFAULTS, datetime(2026, 9, 20, 10, 0, tzinfo=BEIJING_TIMEZONE)
    )

    assert not adjusted_sunday.is_peak
    assert adjusted_sunday.label == "空闲 1× · 明日 09:00 进入峰时"
    assert adjusted_sunday.next_boundary == datetime(
        2026, 9, 21, 9, 0, tzinfo=BEIJING_TIMEZONE
    )


def test_weekday_checkboxes_can_explicitly_enable_a_weekend_day():
    values = {**DEFAULTS, "DEEPSEEK_PEAK_WEEKDAYS": [6]}

    state = pricing_state(values, datetime(2026, 9, 20, 10, 0, tzinfo=BEIJING_TIMEZONE))

    assert state.is_peak
    assert "北京时间高峰日：周日" in state.tooltip


def test_configured_holiday_range_is_offpeak_and_skipped_for_next_boundary():
    values = {**DEFAULTS, "DEEPSEEK_OFFPEAK_DATES": "2026-10-01..2026-10-07"}

    state = pricing_state(values, datetime(2026, 10, 1, 10, 0, tzinfo=BEIJING_TIMEZONE))

    assert not state.is_peak
    assert state.label == "空闲 1× · 10月8日 09:00 进入峰时"
    assert state.next_boundary == datetime(2026, 10, 8, 9, 0, tzinfo=BEIJING_TIMEZONE)
    assert "法定节假日全天为空闲时段" in state.tooltip


def test_offpeak_date_parser_accepts_ranges_and_normalizes_duplicates():
    parsed = parse_offpeak_dates("2026-05-01 至 2026-05-03，2026-05-02")

    assert len(parsed) == 3
    assert normalize_offpeak_dates("2026-05-01 至 2026-05-03，2026-05-02") == (
        "2026-05-01..2026-05-03"
    )
