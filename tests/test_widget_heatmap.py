import os
from datetime import date, timedelta
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from ui.activity import activity_levels, calendar_position, compact_tokens, normalize_activity
from ui.qt_heatmap import ActivityTooltip, TokenActivityHeatmap
from ui.qt_theme import configure_theme, current_theme

APP = QApplication.instance() or QApplication([])
configure_theme(APP, "dark")


def test_activity_calendar_covers_a_full_year_and_complete_weeks():
    period, days = normalize_activity([], date(2026, 7, 3))

    assert period.start == date(2025, 7, 4)
    assert period.end == date(2026, 7, 3)
    assert period.grid_start.weekday() == 0
    assert period.grid_end.weekday() == 6
    assert len(days) == period.week_count * 7
    assert period.week_count in (52, 53)


def test_calendar_position_uses_week_columns_and_weekday_rows():
    period, _days = normalize_activity([], date(2026, 7, 3))

    assert calendar_position(period.grid_start, period.grid_start) == (0, 0)
    assert calendar_position(period.grid_start + timedelta(days=6), period.grid_start) == (0, 6)
    assert calendar_position(period.grid_start + timedelta(days=7), period.grid_start) == (1, 0)


def test_missing_dates_are_zero_and_source_values_are_aggregated():
    period, days = normalize_activity(
        [
            {"date": "2026-07-03", "tokens": 10, "cost_cny": ".1"},
            {"date": "2026-07-03", "tokens": 15, "cost_cny": ".2"},
        ],
        date(2026, 7, 3),
    )
    by_date = {day.date: day for day in days}

    assert by_date[period.start].token_count == 0
    assert by_date[date(2026, 7, 3)].token_count == 25
    assert by_date[date(2026, 7, 3)].amount == 0.3


def test_levels_preserve_large_usage_differences():
    _period, days = normalize_activity(
        [
            {"date": "2026-07-01", "tokens": 10},
            {"date": "2026-07-02", "tokens": 100},
            {"date": "2026-07-03", "tokens": 1_000_000},
        ],
        date(2026, 7, 3),
    )
    levels = activity_levels(days)

    assert levels[date(2026, 7, 1)] == 1
    assert levels[date(2026, 7, 2)] == 1
    assert levels[date(2026, 7, 3)] == 5


def test_levels_renormalize_when_visible_maximum_changes():
    maximum_day = date(2026, 7, 3)
    _period, days_with_100_max = normalize_activity(
        [{"date": maximum_day.isoformat(), "tokens": 100}],
        maximum_day,
    )
    _period, days_with_10_max = normalize_activity(
        [{"date": maximum_day.isoformat(), "tokens": 10}],
        maximum_day,
    )

    assert activity_levels(days_with_100_max)[maximum_day] == 5
    assert activity_levels(days_with_10_max)[maximum_day] == 5
    assert date(2026, 7, 2) not in activity_levels(days_with_10_max)


def test_levels_keep_similar_usage_in_the_same_color_band():
    end = date(2026, 7, 3)
    rows = [
        {
            "date": (end - timedelta(days=index)).isoformat(),
            "tokens": value,
        }
        for index, value in enumerate((100, 99, 98, 97, 96))
    ]
    _period, days = normalize_activity(rows, end)

    levels = activity_levels(days)

    assert [levels[end - timedelta(days=index)] for index in range(5)] == [5] * 5


def test_levels_do_not_saturate_when_a_small_usage_day_is_added():
    end = date(2026, 7, 3)
    rows = [
        {"date": (end - timedelta(days=index)).isoformat(), "tokens": value}
        for index, value in enumerate(
            (100_000_000, 65_000_000, 45_000_000, 25_000_000, 5_000_000)
        )
    ]
    _period, days = normalize_activity(rows, end)
    levels = activity_levels(days)
    small_day = end - timedelta(days=5)
    _period, days_with_small_usage = normalize_activity(
        rows + [{"date": small_day.isoformat(), "tokens": 1}], end
    )
    updated_levels = activity_levels(days_with_small_usage)

    assert [levels[end - timedelta(days=index)] for index in range(5)] == [5, 4, 3, 2, 1]
    assert all(updated_levels[day] == level for day, level in levels.items())
    assert updated_levels[small_day] == 1


def test_levels_use_equal_twenty_percent_bands_and_leave_zero_uncolored():
    end = date(2026, 7, 3)
    values = (0, 1, 20, 21, 40, 41, 60, 61, 80, 81, 100)
    rows = [
        {"date": (end - timedelta(days=index)).isoformat(), "tokens": value}
        for index, value in enumerate(values)
    ]
    _period, days = normalize_activity(rows, end)

    levels = activity_levels(days)

    assert end not in levels
    assert [levels.get(end - timedelta(days=index), 0) for index in range(len(values))] == [
        0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5,
    ]
    assert activity_levels([]) == {}


def test_token_values_use_wan_units():
    assert compact_tokens(0) == "0万"
    assert compact_tokens(1_500) == "0.15万"
    assert compact_tokens(1_500_000) == "150万"

    parent = TokenActivityHeatmap()
    tooltip = ActivityTooltip(parent)
    day = normalize_activity(
        [{"date": "2026-07-03", "tokens": 1_500_000}],
        date(2026, 7, 3),
    )[1][-3]
    tooltip.show_day(day, parent.rect().center())

    assert "Token 使用量：150万" in tooltip._body.text()
    parent.close()


def test_heatmap_renders_complete_calendar_grid():
    heatmap = TokenActivityHeatmap()
    heatmap.set_activity([], date(2026, 7, 3))
    heatmap.resize(heatmap.minimumSize())
    heatmap.show()
    APP.processEvents()
    heatmap.grab()

    assert len(heatmap._hits) >= 365
    assert heatmap.minimumWidth() >= 560
    heatmap.close()


def test_activity_tooltip_keeps_an_opaque_themed_surface():
    controller = configure_theme(APP, "light")
    end = date(2026, 7, 3)
    heatmap = TokenActivityHeatmap()
    heatmap.set_activity([{"date": end.isoformat(), "tokens": 100}], end)
    heatmap.resize(heatmap.minimumSize())
    heatmap.show()
    APP.processEvents()
    heatmap.grab()
    day = next(item for item in heatmap.days if item.date == end)

    try:
        for mode in ("light", "dark"):
            controller.set_mode(mode)
            heatmap._tooltip.show_day(day, heatmap.rect().center())
            APP.processEvents()
            image = heatmap._tooltip.grab().toImage()
            sample = image.pixelColor(image.width() - 8, image.height() - 8)

            assert sample == QColor(current_theme().elevated)
            assert sample.alpha() == 255
    finally:
        controller.set_mode("dark")
        heatmap.close()


def test_existing_heatmap_repaints_in_place_without_rebinding_activity():
    controller = configure_theme(APP, "dark")
    end = date(2026, 7, 3)
    heatmap = TokenActivityHeatmap()
    heatmap.set_activity([{"date": end.isoformat(), "tokens": 100}], end)
    heatmap.resize(heatmap.minimumSize())
    heatmap.show()
    APP.processEvents()
    heatmap_identity = id(heatmap)

    week, weekday = calendar_position(end, heatmap.period.grid_start)
    horizontal_step = max(
        heatmap.CELL + heatmap.MIN_HORIZONTAL_GAP,
        (heatmap.width() - heatmap.LEFT - 12) // heatmap.period.week_count,
    )
    center = (
        heatmap.LEFT + week * horizontal_step + heatmap.CELL // 2,
        heatmap.TOP + weekday * (heatmap.CELL + heatmap.GAP) + heatmap.CELL // 2,
    )
    dark_color = heatmap.grab().toImage().pixelColor(*center)
    assert dark_color == QColor(current_theme().heat[5])

    try:
        with patch.object(heatmap, "set_activity") as set_activity:
            controller.set_mode("light")
            APP.processEvents()
            light_color = heatmap.grab().toImage().pixelColor(*center)

            assert id(heatmap) == heatmap_identity
            assert light_color == QColor(current_theme().heat[5])
            assert light_color != dark_color

            controller.set_mode("dark")
            APP.processEvents()
            assert heatmap.grab().toImage().pixelColor(*center) == dark_color
            set_activity.assert_not_called()
    finally:
        controller.set_mode("dark")
        heatmap.close()


def test_future_padding_uses_the_same_color_as_unused_days():
    heatmap = TokenActivityHeatmap()
    heatmap.set_activity([], date(2026, 7, 3))
    heatmap.resize(heatmap.minimumSize())
    heatmap.show()
    APP.processEvents()
    image = heatmap.grab().toImage()
    horizontal_step = max(
        heatmap.CELL + heatmap.MIN_HORIZONTAL_GAP,
        (heatmap.width() - heatmap.LEFT - 12) // heatmap.period.week_count,
    )

    def cell_center(day):
        week, weekday = calendar_position(day, heatmap.period.grid_start)
        return (
            heatmap.LEFT + week * horizontal_step + heatmap.CELL // 2,
            heatmap.TOP + weekday * (heatmap.CELL + heatmap.GAP) + heatmap.CELL // 2,
        )

    unused_color = image.pixelColor(*cell_center(heatmap.period.start))
    future_color = image.pixelColor(*cell_center(heatmap.period.end + timedelta(days=1)))

    assert future_color == unused_color
    heatmap.close()
