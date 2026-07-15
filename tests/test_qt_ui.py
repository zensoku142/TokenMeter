import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = str(Path.cwd() / ".test-appdata")

import pyqtgraph as pg
import pytest
from PySide6.QtCore import QDate, QEvent, QPoint, QPointF, QSize, QTime, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from app_update import CheckResult, ReleaseAsset, ReleaseInfo, SemVer
import config_manager
from data.store import PerProviderData, TokenData
from ui.geometry import WorkArea
from ui.qt_ball import FloatingUsageBall
from ui.qt_panel import (
    ANNUAL_ACTIVITY_SECTION_HEIGHT,
    ANNUAL_PANEL_HEIGHT,
    ACTIVITY_SECTION_HEIGHT,
    HEADER_HEIGHT,
    PANEL_HEIGHT,
    PANEL_MAX_WIDTH,
    PANEL_MIN_WIDTH,
    MinuteDateEdit,
    STATISTICS_SECTION_HEIGHT,
    STATUS_SECTION_HEIGHT,
    TOP_SECTION_HEIGHT,
    MainPanel,
    MinuteUsageChart,
    StatisticsCard,
    TrendCard,
    format_money_axis,
    format_token_axis,
)
from ui.qt_settings import SettingsWindow
from ui.qt_theme import configure_theme, current_theme
from ui.qt_update import AppUpdateController, UpdatePromptDialog
from ui.qt_widget import FloatingWidget


APP = QApplication.instance() or QApplication([])
configure_theme(APP, "dark")


def sample_data() -> TokenData:
    rows = [
        {
            "date": (date.today() - timedelta(days=offset)).isoformat(),
            "tokens": (offset + 1) * 10_000_000,
            "cost_cny": offset / 10,
        }
        for offset in range(7)
    ]
    return TokenData(
        status="ok",
        last_success_at=datetime.now(),
        total_cost_cny=12.34,
        daily_usage=rows,
    )


def sample_release(version: str = "1.3.4") -> ReleaseInfo:
    return ReleaseInfo(
        version=version,
        semver=SemVer.parse(version),
        tag_name=f"v{version}",
        published_at="2026-07-07T08:00:00Z",
        body="Bug fixes",
        is_prerelease=False,
        app_asset=ReleaseAsset(
            name=f"TokenSpider-v{version}-windows-x64.exe",
            download_url=f"https://github.com/zensoku142/TokenSpider/releases/download/v{version}/TokenSpider-v{version}-windows-x64.exe",
            size=10,
        ),
        updater_asset=ReleaseAsset(
            name=f"TokenSpiderUpdater-v{version}-windows-x64.exe",
            download_url=f"https://github.com/zensoku142/TokenSpider/releases/download/v{version}/TokenSpiderUpdater-v{version}-windows-x64.exe",
            size=5,
        ),
        checksum_asset=ReleaseAsset(
            name="SHA256SUMS.txt",
            download_url=f"https://github.com/zensoku142/TokenSpider/releases/download/v{version}/SHA256SUMS.txt",
            size=2,
        ),
    )


def test_token_axis_uses_readable_units():
    assert format_token_axis(0) == "0万"
    assert format_token_axis(1_500) == "0.15万"
    assert format_token_axis(60_000_000) == "6000万"


def test_panel_token_values_use_readable_units():
    data = sample_data()
    data.today_tokens = 1_500_000
    data.balance_tokens = 250_000_000
    data.monthly_usage_tokens = 60_000_000
    panel = MainPanel()
    panel.update_data(data)

    assert panel.today_card.detail.text() == "150万"
    assert panel.balance_card.detail.text() == "约 2.5亿"
    assert panel.month_card.detail.text() == "6000万"
    statistics = [label.text() for label in panel.statistics._values]
    assert "6000万" in statistics
    assert "2.8亿" in statistics
    assert panel.activity_summary.text().endswith("2.8亿")
    panel.close()


def test_trend_uses_exactly_seven_cost_bars_with_hover_tooltip():
    trend = TrendCard()
    trend.set_rows(sample_data().daily_usage, date.today())
    trend.resize(480, TOP_SECTION_HEIGHT)
    trend.show()
    APP.processEvents()

    assert trend.title.text() == "近 7 天使用金额"
    assert trend._values == [0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0]
    bar_items = [
        item
        for item in trend.plot.getPlotItem().items
        if isinstance(item, pg.BarGraphItem)
    ]
    assert bar_items == [trend._series]
    assert trend._series.opts["x"] == list(range(7))
    assert trend._series.opts["height"] == trend._values
    assert trend._series.opts["width"] == trend.BAR_WIDTH
    x_min, x_max = trend.plot.getViewBox().viewRange()[0]
    assert x_min == -0.5
    assert x_max == 6.5
    assert (date.today() - timedelta(days=6)).isoformat() in trend.tooltip_text(0)
    assert "使用金额：¥0.60" in trend.tooltip_text(0)

    scene_point = trend.plot.getViewBox().mapViewToScene(QPointF(0, 0.3))
    with patch("ui.qt_panel.QToolTip.showText") as show_tooltip:
        trend._on_mouse_moved((scene_point,))

    assert trend._hover_index == 0
    assert len(trend._series.opts["brushes"]) == 7
    assert show_tooltip.call_count == 1
    assert show_tooltip.call_args.args[1] == trend.tooltip_text(0)
    trend.close()


def test_money_axis_and_zero_cost_range_remain_readable():
    assert format_money_axis(0) == "¥0.00"
    assert format_money_axis(0.006) == "¥0.0060"
    trend = TrendCard()
    trend.set_rows([], date.today())

    assert trend._values == [0.0] * 7
    assert trend.plot.getViewBox().viewRange()[1][1] >= 0.01
    trend.close()


def test_minute_chart_tooltip_legend_and_navigator_preserve_raw_series():
    chart = MinuteUsageChart()
    rows = [
        {"minute": 600, "token_type": "PROMPT_CACHE_HIT_TOKEN", "token_amount": 80},
        {"minute": 600, "token_type": "PROMPT_CACHE_MISS_TOKEN", "token_amount": 20},
        {"minute": 600, "token_type": "RESPONSE_TOKEN", "token_amount": 10},
    ]
    rows.extend(
        {"minute": minute, "token_type": "RESPONSE_TOKEN", "token_amount": 1}
        for minute in range(601, 625)
    )
    chart.set_rows(rows, "recorded", cost_rows=[{"minute": 600, "cost_cny": Decimal(".24")}])
    chart.show()
    APP.processEvents()

    initial_left, initial_right = chart.plot.getViewBox().viewRange()[0]
    assert initial_right - initial_left == pytest.approx(24)
    assert chart._minute_at_x(initial_right - 0.01) == 624
    assert chart._nav_bars.zValue() > chart.region.zValue()
    assert chart._nav_handles.zValue() > chart._nav_bars.zValue()
    nav_left, nav_right = chart.navigator.getViewBox().viewRange()[0]
    assert nav_left == pytest.approx(-0.5)
    assert nav_right == pytest.approx(24.5)

    tooltip = chart.tooltip_text(600)
    assert "10:00" in tooltip
    assert "输入（命中缓存）　80" in tooltip
    assert "输入（未命中缓存）　20" in tooltip
    assert "输出　10" in tooltip
    assert "总计 110" in tooltip
    assert "缓存命中率　80.0%" in tooltip
    assert "本分钟消耗金额　¥0.24" in tooltip
    assert chart._display_bucket_indexes[0] == 600
    assert chart._bars["RESPONSE_TOKEN"].opts["height"][0] == 10
    assert chart._bars["PROMPT_CACHE_MISS_TOKEN"].opts["y0"][0] == 10
    assert chart._bars["PROMPT_CACHE_HIT_TOKEN"].opts["y0"][0] == 30
    chart._show_hover(600, QPoint(120, 50))
    assert chart.hover_tooltip.isVisible()
    assert chart.hover_tooltip.time_label.text() == "10:00"
    assert chart.hover_tooltip.cost_label.text() == "¥0.24"
    assert chart._hover_line.isVisible()
    assert chart._hover_bar.isVisible()
    chart.set_series_visible("RESPONSE_TOKEN", False)
    assert not chart._bars["RESPONSE_TOKEN"].isVisible()
    assert chart._bars["PROMPT_CACHE_MISS_TOKEN"].opts["y0"][0] == 0
    assert chart._bars["PROMPT_CACHE_HIT_TOKEN"].opts["y0"][0] == 20
    assert chart.tooltip_text(600) == tooltip
    chart.region.setRegion((0.5, 12.5))
    APP.processEvents()
    left, right = chart.plot.getViewBox().viewRange()[0]
    assert left == pytest.approx(0.5)
    assert right == pytest.approx(12.5)
    chart.close()


def test_minute_chart_aggregates_configured_time_buckets_and_costs():
    chart = MinuteUsageChart()
    rows = [
        {
            "minute": 600,
            "token_type": "PROMPT_CACHE_HIT_TOKEN",
            "token_amount": 80,
        },
        {
            "minute": 604,
            "token_type": "PROMPT_CACHE_MISS_TOKEN",
            "token_amount": 20,
        },
        {"minute": 604, "token_type": "RESPONSE_TOKEN", "token_amount": 10},
        {"minute": 605, "token_type": "RESPONSE_TOKEN", "token_amount": 7},
        {"minute": 1439, "token_type": "RESPONSE_TOKEN", "token_amount": 3},
    ]
    chart.set_rows(
        rows,
        "recorded",
        cost_rows=[
            {"minute": 600, "cost_cny": Decimal(".10")},
            {"minute": 604, "cost_cny": Decimal(".14")},
            {"minute": 605, "cost_cny": Decimal(".02")},
            {"minute": 1439, "cost_cny": Decimal("0")},
        ],
        interval_minutes=5,
    )

    first_bucket = chart._bucket_index_for_minute(600)
    next_bucket = chart._bucket_index_for_minute(605)
    assert len(chart._bucket_starts) == 288
    assert chart._bucket_centers[first_bucket] == 602
    assert chart._values["PROMPT_CACHE_HIT_TOKEN"][first_bucket] == 80
    assert chart._values["PROMPT_CACHE_MISS_TOKEN"][first_bucket] == 20
    assert chart._values["RESPONSE_TOKEN"][first_bucket] == 10
    assert chart._values["RESPONSE_TOKEN"][next_bucket] == 7
    assert sum(sum(series) for series in chart._values.values()) == 120
    assert "10:00–10:04　总计 110" in chart.tooltip_text(604)
    assert "本时段消耗金额　¥0.24" in chart.tooltip_text(600)
    assert "10:05–10:09　总计 7" in chart.tooltip_text(605)
    assert "23:55–23:59" in chart.tooltip_text(1439)
    assert "本时段消耗金额　¥0.00" in chart.tooltip_text(1439)
    assert chart.summary_text().endswith("峰值 10:00–10:04")
    first_display = chart._bucket_display_positions[first_bucket]
    assert chart._bars["RESPONSE_TOKEN"].opts["height"][first_display] == 10

    chart._show_hover(604, QPoint(120, 50))
    assert chart.hover_tooltip.time_label.text() == "10:00–10:04"
    assert chart.hover_tooltip.cost_name.text() == "本时段消耗金额"
    assert chart.hover_tooltip.cost_label.text() == "¥0.24"
    chart.close()


def test_minute_chart_arbitrary_interval_aligns_to_midnight_and_clips_last_bucket():
    chart = MinuteUsageChart()
    chart.set_rows(
        [
            {"minute": 59, "token_type": "RESPONSE_TOKEN", "token_amount": 2},
            {"minute": 60, "token_type": "RESPONSE_TOKEN", "token_amount": 3},
            {"minute": 1439, "token_type": "RESPONSE_TOKEN", "token_amount": 4},
        ],
        "recorded",
        interval_minutes=7,
    )

    assert len(chart._bucket_starts) == 206
    assert "00:56–01:02　总计 5" in chart.tooltip_text(59)
    assert "00:56–01:02　总计 5" in chart.tooltip_text(60)
    assert "23:55–23:59　总计 4" in chart.tooltip_text(1439)
    with pytest.raises(ValueError, match="1 到 60"):
        chart.set_rows([], "empty", interval_minutes=61)
    chart.close()


def test_minute_chart_switches_between_bar_and_line_rendering():
    chart = MinuteUsageChart()
    rows = [
        {
            "minute": 600,
            "token_type": "PROMPT_CACHE_HIT_TOKEN",
            "token_amount": 80,
        },
        {"minute": 604, "token_type": "RESPONSE_TOKEN", "token_amount": 10},
        {"minute": 605, "token_type": "RESPONSE_TOKEN", "token_amount": 7},
    ]
    chart.set_rows(rows, "recorded", interval_minutes=5, chart_type="line")

    assert not chart._bars
    assert set(chart._lines) == {key for key, _label in chart.SERIES}
    assert chart._nav_bars is None
    assert chart._nav_line is not None
    hit_line = chart._lines["PROMPT_CACHE_HIT_TOKEN"]
    assert chart._display_bucket_indexes == [120, 121]
    assert list(hit_line.xData) == pytest.approx(
        [index / 8 for index in range(9)]
    )
    assert list(hit_line.yData) == sorted(hit_line.yData, reverse=True)
    hit_point_x, hit_point_y = chart._line_points[
        "PROMPT_CACHE_HIT_TOKEN"
    ].getData()
    assert list(hit_point_x) == [0, 1]
    assert list(hit_point_y) == [80, 0]
    assert chart._line_points["PROMPT_CACHE_HIT_TOKEN"].opts["size"] == 4
    assert chart._line_points["PROMPT_CACHE_HIT_TOKEN"].opts["antialias"] is True
    assert hit_line.opts["antialias"] is True
    assert hit_line.opts["pen"].capStyle() == Qt.PenCapStyle.RoundCap
    assert hit_line.opts["pen"].joinStyle() == Qt.PenJoinStyle.RoundJoin
    assert (
        chart._line_points["PROMPT_CACHE_HIT_TOKEN"].opts["pen"].color()
        == hit_line.opts["pen"].color()
    )
    assert chart._nav_line.opts["antialias"] is True
    assert chart._nav_line.opts["pen"].capStyle() == Qt.PenCapStyle.RoundCap
    response_point_x, response_point_y = chart._line_points[
        "RESPONSE_TOKEN"
    ].getData()
    assert list(response_point_x) == [0, 1]
    assert list(response_point_y) == [10, 7]
    chart.set_series_visible("RESPONSE_TOKEN", False)
    assert not chart._lines["RESPONSE_TOKEN"].isVisible()
    assert not chart._line_points["RESPONSE_TOKEN"].isVisible()

    chart._show_hover(600, QPoint(120, 50))
    assert chart._hover_line.isVisible()
    assert chart._hover_line.value() == 0
    assert chart._hover_bar is None
    assert chart.hover_tooltip.time_label.text() == "10:00–10:04"

    chart.set_rows(rows, "recorded", interval_minutes=5, chart_type="bar")
    assert chart._bars
    assert not chart._lines
    assert not chart._line_points
    assert chart._nav_bars is not None
    assert chart._nav_line is None
    with pytest.raises(ValueError, match="bar 或 line"):
        chart.set_rows([], "empty", chart_type="area")
    chart.close()


def test_minute_line_chart_compacts_distant_active_buckets_and_preserves_labels():
    chart = MinuteUsageChart()
    chart.set_rows(
        [
            {"minute": 60, "token_type": "RESPONSE_TOKEN", "token_amount": 2},
            {"minute": 600, "token_type": "RESPONSE_TOKEN", "token_amount": 3},
        ],
        "recorded",
        chart_type="line",
    )

    assert chart._display_bucket_indexes == [60, 600]
    assert list(chart._nav_line.xData) == pytest.approx(
        [index / 8 for index in range(9)]
    )
    assert chart.plot.getViewBox().viewRange()[0] == pytest.approx([-0.5, 1.5])
    assert chart.navigator.getViewBox().viewRange()[0] == pytest.approx([-0.5, 1.5])
    assert chart.plot.getAxis("bottom")._tickLevels == [
        [(0.0, "01:00"), (1.0, "10:00")]
    ]
    assert chart.navigator.getAxis("bottom")._tickLevels == [
        [(0.0, "01:00"), (1.0, "10:00")]
    ]
    assert chart._minute_at_x(0) == 60
    assert chart._minute_at_x(1) == 600

    chart.show()
    APP.processEvents()
    chart._on_mouse_moved(
        (chart.plot.getViewBox().mapViewToScene(QPointF(1, 2)),)
    )
    assert chart._hover_line.value() == 1
    assert chart.hover_tooltip.isVisible()
    assert chart.hover_tooltip.time_label.text() == "10:00"
    chart.close()


def test_minute_line_chart_shows_latest_24_active_buckets_and_navigates_all():
    chart = MinuteUsageChart()
    chart.set_rows(
        [
            {
                "minute": index * 10,
                "token_type": "RESPONSE_TOKEN",
                "token_amount": index + 1,
            }
            for index in range(30)
        ],
        "recorded",
        chart_type="line",
    )

    assert chart._display_bucket_indexes == [index * 10 for index in range(30)]
    assert chart.plot.getViewBox().viewRange()[0] == pytest.approx([5.5, 29.5])
    assert chart.navigator.getViewBox().viewRange()[0] == pytest.approx([-0.5, 29.5])
    chart.region.setRegion((0.5, 12.5))
    APP.processEvents()
    assert chart.plot.getViewBox().viewRange()[0] == pytest.approx([0.5, 12.5])
    assert chart._minute_at_x(1) == 10
    assert chart._minute_at_x(12) == 120
    chart.close()


def test_minute_line_chart_smoothing_is_bounded_and_preserves_original_nodes():
    x_values = [0.0, 1.0, 2.0, 3.0]
    y_values = [0, 10, 3, 20]
    smooth_x, smooth_y = MinuteUsageChart._smooth_curve_data(x_values, y_values)

    assert len(smooth_x) == len(smooth_y) == 25
    for index, expected in enumerate(y_values):
        sample_index = index * 8
        assert smooth_x[sample_index] == index
        assert smooth_y[sample_index] == expected
    for segment, (start, end) in enumerate(zip(y_values, y_values[1:])):
        segment_values = smooth_y[segment * 8 : (segment + 1) * 8 + 1]
        assert min(start, end) <= min(segment_values)
        assert max(segment_values) <= max(start, end)
        assert min(segment_values) >= 0

    assert MinuteUsageChart._smooth_curve_data([0.0], [7]) == ([0.0], [7.0])
    two_x, two_y = MinuteUsageChart._smooth_curve_data([0.0, 1.0], [2, 10])
    assert two_x == pytest.approx([index / 8 for index in range(9)])
    assert two_y == pytest.approx([2 + index for index in range(9)])


@pytest.mark.parametrize(
    ("interval_minutes", "minute", "expected_label"),
    [
        (1, 1439, "23:59"),
        (5, 1439, "23:55"),
        (7, 1439, "23:55"),
        (60, 1439, "23:00"),
    ],
)
@pytest.mark.parametrize("chart_type", ["bar", "line"])
def test_minute_chart_compact_axis_labels_use_real_bucket_times(
    interval_minutes, minute, expected_label, chart_type
):
    chart = MinuteUsageChart()
    chart.set_rows(
        [
            {"minute": 60, "token_type": "RESPONSE_TOKEN", "token_amount": 2},
            {"minute": minute, "token_type": "RESPONSE_TOKEN", "token_amount": 3},
        ],
        "recorded",
        interval_minutes=interval_minutes,
        chart_type=chart_type,
    )

    last_position = len(chart._display_bucket_indexes) - 1
    assert chart.plot.getAxis("bottom")._tickLevels[0][-1] == (
        float(last_position),
        expected_label,
    )
    assert expected_label in chart.tooltip_text(minute)
    chart.close()


def test_minute_chart_default_range_shows_about_24_configured_buckets():
    chart = MinuteUsageChart()
    chart.resize(900, 180)
    chart.set_rows(
        [
            {
                "minute": 600 + index * 5,
                "token_type": "RESPONSE_TOKEN",
                "token_amount": 1,
            }
            for index in range(25)
        ],
        "recorded",
        interval_minutes=5,
    )
    APP.processEvents()

    assert not chart._sparse_mode
    left, right = chart.plot.getViewBox().viewRange()[0]
    assert right - left == pytest.approx(24)
    assert chart._bar_width <= 0.84
    chart.close()


def test_minute_chart_uses_compact_range_for_up_to_24_nonzero_minutes():
    chart = MinuteUsageChart()
    chart.resize(900, 180)
    chart.show()

    for count in (1, 5, 12, 24):
        rows = [
            {
                "minute": 600 + index,
                "token_type": "PROMPT_CACHE_HIT_TOKEN",
                "token_amount": 100,
            }
            for index in range(count)
        ]
        chart.set_rows(rows, "recorded")
        APP.processEvents()
        left, right = chart.plot.getViewBox().viewRange()[0]
        assert chart._sparse_mode
        assert left == pytest.approx(-0.5)
        assert right == pytest.approx(max(0.5, count - 0.5))
        assert right - left == pytest.approx(max(1, count))
        nav_left, nav_right = chart.navigator.getViewBox().viewRange()[0]
        assert nav_left == pytest.approx(-0.5)
        assert nav_right == pytest.approx(max(0.5, count - 0.5))
        assert chart._minute_at_x(left + 0.01) == 600
        assert chart._minute_at_x(right - 0.01) == 600 + count - 1
        pixel_width = chart._bar_width * chart.plot.getViewBox().width() / (right - left)
        assert chart.BAR_MIN_WIDTH_PX <= pixel_width <= chart.BAR_MAX_WIDTH_PX + 0.5

    chart.close()


def test_minute_chart_hides_tooltip_when_pointer_leaves_bar():
    chart = MinuteUsageChart()
    chart.set_rows(
        [{"minute": 600, "token_type": "RESPONSE_TOKEN", "token_amount": 100}],
        "recorded",
    )
    chart.show()
    APP.processEvents()

    chart._on_mouse_moved(
        (chart.plot.getViewBox().mapViewToScene(QPointF(0, 50)),)
    )
    assert chart.hover_tooltip.isVisible()
    chart._on_mouse_moved(
        (chart.plot.getViewBox().mapViewToScene(QPointF(0, 101)),)
    )
    assert not chart.hover_tooltip.isVisible()
    chart.close()


def test_minute_chart_shows_latest_24_active_buckets_and_navigates_all():
    for count in (25, 100):
        chart = MinuteUsageChart()
        rows = [
            {
                "minute": 720 + index,
                "token_type": "PROMPT_CACHE_HIT_TOKEN",
                "token_amount": 100,
            }
            for index in range(count)
        ]
        chart.set_rows(rows, "recorded")
        APP.processEvents()

        assert not chart._sparse_mode
        left, right = chart.plot.getViewBox().viewRange()[0]
        assert right - left == pytest.approx(24)
        assert chart._minute_at_x(right - 0.01) == 720 + count - 1
        chart.region.setRegion((0.5, 12.5))
        APP.processEvents()
        left, right = chart.plot.getViewBox().viewRange()[0]
        assert left == pytest.approx(0.5)
        assert right == pytest.approx(12.5)
        chart.close()


def test_minute_chart_sparse_distant_points_use_compact_timeline():
    chart = MinuteUsageChart()
    rows = [
        {"minute": 60, "token_type": "RESPONSE_TOKEN", "token_amount": 10},
        {"minute": 600, "token_type": "RESPONSE_TOKEN", "token_amount": 20},
    ]
    chart.set_rows(rows, "recorded")

    left, right = chart.plot.getViewBox().viewRange()[0]
    assert chart._sparse_mode
    assert (left, right) == pytest.approx((-0.5, 1.5))
    assert chart._display_bucket_indexes == [60, 600]
    assert chart._nav_bars.opts["x"] == [0, 1]
    assert chart._minute_at_x(0) == 60
    assert chart._minute_at_x(1) == 600
    assert chart.plot.getAxis("bottom")._tickLevels == [
        [(0.0, "01:00"), (1.0, "10:00")]
    ]
    chart.show()
    APP.processEvents()
    chart._on_mouse_moved(
        (chart.plot.getViewBox().mapViewToScene(QPointF(1, 10)),)
    )
    assert chart.hover_tooltip.isVisible()
    assert chart.hover_tooltip.time_label.text() == "10:00"
    chart.close()


def test_minute_chart_zero_rows_keeps_empty_state_instead_of_compact_range():
    chart = MinuteUsageChart()
    chart.set_rows([], "empty")

    assert not chart.chart_container.isVisible()
    assert chart.state_label.text() == "今日暂无 Token 消耗"
    assert chart.summary_text() == "今日 0 · 缓存命中 -- · 峰值 --"
    chart.close()


def test_minute_chart_handles_zero_cache_denominator_and_panel_defaults_to_annual():
    chart = MinuteUsageChart()
    chart.set_rows(
        [{"minute": 1, "token_type": "RESPONSE_TOKEN", "token_amount": 1}],
        "recorded",
    )
    assert "缓存命中率　--" in chart.tooltip_text(1)
    assert "总计 0" in chart.tooltip_text(2)
    assert chart._minute_at_x(2.0) == 1
    hit, miss, output = chart._colors()
    assert hit.lightness() > miss.lightness() > output.lightness()
    panel = MainPanel()
    panel.update_data(sample_data())
    assert panel.activity_stack.currentIndex() == 0
    assert panel.annual_activity_button.isChecked()
    panel.minute_activity_button.click()
    assert panel.activity_stack.currentIndex() == 1
    assert panel.minute_activity_button.isChecked()
    assert not panel.minute_estimate_label.isHidden()
    assert panel.minute_estimate_label.text() == "估算"
    assert "按刷新间隔均摊" in panel.minute_estimate_label.toolTip()
    assert not panel.activity_summary.text().startswith("估算")
    assert not panel.minute_previous_button.isEnabled()
    assert not panel.minute_next_button.isEnabled()
    panel.close()
    chart.close()


def test_minute_chart_cost_tooltip_handles_missing_zero_and_plot_boundaries():
    chart = MinuteUsageChart()
    chart.resize(900, 200)
    chart.set_rows(
        [{"minute": 600, "token_type": "RESPONSE_TOKEN", "token_amount": 1}],
        "recorded",
    )
    assert "本分钟消耗金额　--" in chart.tooltip_text(600)
    chart.set_rows(
        [{"minute": 600, "token_type": "RESPONSE_TOKEN", "token_amount": 1}],
        "recorded",
        cost_rows=[{"minute": 600, "cost_cny": Decimal("0")}],
    )
    assert "本分钟消耗金额　¥0.00" in chart.tooltip_text(600)
    chart.show()
    APP.processEvents()

    chart._show_hover(600, QPoint(0, 0))
    tooltip = chart.hover_tooltip
    view_left = chart.plot.mapFromScene(
        chart.plot.getViewBox().sceneBoundingRect().topLeft()
    ).x() + 6
    assert tooltip.x() >= view_left
    assert tooltip.x() + tooltip.width() <= chart.plot.width() - 6
    assert 6 <= tooltip.y() <= chart.plot.height() - tooltip.height() - 6

    chart._show_hover(600, QPoint(chart.plot.width() - 1, chart.plot.height() - 1))
    assert tooltip.x() >= view_left
    assert tooltip.x() + tooltip.width() <= chart.plot.width() - 6
    assert 6 <= tooltip.y() <= chart.plot.height() - tooltip.height() - 6
    chart.close()


def test_minute_date_edit_uses_three_segments_and_only_date_button_opens_popup():
    picker = MinuteDateEdit()
    picker.setDateRange(QDate(2026, 7, 12), QDate(2026, 7, 14))
    picker.setDate(QDate(2026, 7, 14))
    picker.show()
    APP.processEvents()

    assert picker.size() == QSize(118, 26)
    assert picker.previous_button.size() == QSize(20, 24)
    assert picker.date_button.size() == QSize(76, 24)
    assert picker.next_button.size() == QSize(20, 24)
    assert picker.date_button.text() == "2026-07-14"
    assert picker.previous_button.isEnabled()
    assert not picker.next_button.isEnabled()

    picker.previous_button.click()
    APP.processEvents()
    assert picker.date() == QDate(2026, 7, 13)
    assert not picker.popup.isVisible()

    picker.previous_button.click()
    assert picker.date() == QDate(2026, 7, 12)
    assert not picker.previous_button.isEnabled()

    picker.next_button.click()
    picker.next_button.click()
    APP.processEvents()
    assert picker.date() == QDate(2026, 7, 14)
    assert not picker.popup.isVisible()

    picker.date_button.click()
    APP.processEvents()
    assert picker.popup.isVisible()
    assert picker.popup.windowFlags() & Qt.WindowType.Popup
    QTest.keyClick(picker.popup, Qt.Key.Key_Escape)
    APP.processEvents()
    assert not picker.popup.isVisible()
    picker.close()


def test_minute_date_edit_calendar_range_month_navigation_and_disabled_state():
    picker = MinuteDateEdit()
    picker.setDateRange(QDate(2026, 6, 30), QDate(2026, 7, 14))
    picker.setDate(QDate(2026, 7, 14))
    picker.show()
    picker.date_button.click()
    APP.processEvents()

    assert picker.popup.month_label.text() == "2026年7月"
    assert picker.popup.calendar.firstDayOfWeek() == Qt.DayOfWeek.Monday
    assert not picker.popup.calendar.isNavigationBarVisible()
    assert picker.popup.previous_month_button.isEnabled()
    assert not picker.popup.next_month_button.isEnabled()
    assert (
        picker.popup.calendar.weekdayTextFormat(Qt.DayOfWeek.Saturday).foreground().color()
        == picker.popup.calendar.weekdayTextFormat(Qt.DayOfWeek.Monday).foreground().color()
    )

    picker.popup._select_date(QDate(2026, 6, 30))
    assert picker.date() == QDate(2026, 7, 14)

    picker.setEnabled(False)
    assert not picker.popup.isVisible()
    assert not picker.previous_button.isEnabled()
    assert not picker.date_button.isEnabled()
    assert not picker.next_button.isEnabled()
    picker.close()


def test_minute_date_selection_renders_history_and_refresh_keeps_user_choice():
    panel = MainPanel()
    data = sample_data()
    data.per_provider = [PerProviderData("mimo", "小米 MiMo")]
    data.minute_usage_date = "2026-07-14"
    data.minute_usage_status = "recorded"
    data.minute_usage = [
        {"minute": 10, "token_type": "RESPONSE_TOKEN", "token_amount": 20}
    ]
    data.minute_usage_days = ["2026-07-13", "2026-07-14"]
    data.minute_usage_history = {
        "2026-07-13": [
            {"minute": 10, "token_type": "RESPONSE_TOKEN", "token_amount": 10}
        ]
    }

    with patch(
        "ui.qt_panel.config_manager.get",
        side_effect=lambda key, default=None: 3 if key == "MINUTE_USAGE_RETENTION_DAYS" else default,
    ):
        panel.update_data(data)
        panel.minute_previous_button.click()
        assert panel.minute_date_edit.date() == QDate(2026, 7, 13)
        bucket_index = panel.minute_chart._bucket_index_for_minute(10)
        assert panel.minute_chart._values["RESPONSE_TOKEN"][bucket_index] == 10

        panel.update_data(data)
        assert panel.minute_date_edit.date() == QDate(2026, 7, 13)

        next_day = sample_data()
        next_day.per_provider = [PerProviderData("mimo", "小米 MiMo")]
        next_day.minute_usage_date = "2026-07-15"
        next_day.minute_usage_status = "recorded"
        next_day.minute_usage = [
            {"minute": 10, "token_type": "RESPONSE_TOKEN", "token_amount": 30}
        ]
        next_day.minute_usage_days = ["2026-07-13", "2026-07-14", "2026-07-15"]
        next_day.minute_usage_history = data.minute_usage_history
        panel.update_data(next_day)
        assert panel.minute_date_edit.date() == QDate(2026, 7, 13)

        switched = sample_data()
        switched.per_provider = [PerProviderData("deepseek", "DeepSeek")]
        switched.minute_usage_date = "2026-07-15"
        switched.minute_usage_status = "recorded"
        panel.update_data(switched)
        assert panel.minute_date_edit.date() == QDate(2026, 7, 15)

    panel.close()


def test_minute_date_follows_latest_across_day_when_user_stayed_on_current_date():
    panel = MainPanel()
    first = sample_data()
    first.per_provider = [PerProviderData("mimo", "小米 MiMo")]
    first.minute_usage_date = "2026-07-14"
    first.minute_usage_status = "recorded"
    second = sample_data()
    second.per_provider = [PerProviderData("mimo", "小米 MiMo")]
    second.minute_usage_date = "2026-07-15"
    second.minute_usage_status = "recorded"

    with patch(
        "ui.qt_panel.config_manager.get",
        side_effect=lambda key, default=None: 3 if key == "MINUTE_USAGE_RETENTION_DAYS" else default,
    ):
        panel.update_data(first)
        panel.update_data(second)

    assert panel.minute_date_edit.date() == QDate(2026, 7, 15)
    panel.close()


def test_statistics_show_cached_historical_total_with_scope_tooltip():
    statistics = StatisticsCard()
    statistics.set_data(sample_data())
    historical_label = next(
        label for label in statistics.findChildren(QLabel)
        if label.text() == "历史使用总金额"
    )

    assert statistics._values[1].text() == "¥12.34"
    assert "本机已缓存账单" in historical_label.toolTip()
    statistics.close()


def test_panel_uses_fixed_v3_layout_budget_and_fluent_actions():
    panel = MainPanel()
    panel.minute_activity_button.click()
    panel.resize(820, 550)
    panel.update_data(sample_data())
    panel.show()
    APP.processEvents()
    panel.activity.grab()
    status_bar = panel.findChild(QWidget, "statusBar")
    buttons = panel.findChildren(QToolButton, "panelToolButton")

    assert PANEL_MIN_WIDTH == 640
    assert PANEL_MAX_WIDTH == 820
    assert PANEL_HEIGHT == 550
    assert HEADER_HEIGHT == 42
    assert TOP_SECTION_HEIGHT == 160
    assert ACTIVITY_SECTION_HEIGHT == 230
    assert STATISTICS_SECTION_HEIGHT == 76
    assert STATUS_SECTION_HEIGHT == 40
    assert panel.minimumSize().width() == PANEL_MIN_WIDTH
    assert panel.maximumSize().width() == PANEL_MAX_WIDTH
    assert panel.minimumSize().height() == PANEL_HEIGHT
    assert panel.maximumSize().height() == PANEL_HEIGHT
    assert panel.header.height() == HEADER_HEIGHT
    assert panel.top_section.height() == TOP_SECTION_HEIGHT
    assert panel.activity_card.height() == ACTIVITY_SECTION_HEIGHT
    assert panel.statistics.height() == STATISTICS_SECTION_HEIGHT
    assert status_bar.height() == STATUS_SECTION_HEIGHT
    assert panel.top_section.y() < panel.activity_card.y() < panel.statistics.y() < status_bar.y()
    assert len(panel.activity._hits) >= 365
    assert panel.activity.height() == 133
    assert len(panel.statistics._values) == 5
    assert all(
        label.alignment() & Qt.AlignmentFlag.AlignHCenter
        for label in panel.statistics._names + panel.statistics._values
    )
    assert [button.width() for button in panel.minute_legend_buttons.values()] == [64, 54, 44]
    assert panel.activity_mode_segment.size() == QSize(148, 26)
    assert panel.minute_date_edit.size() == QSize(118, 26)
    assert panel.annual_activity_button.size() == QSize(72, 22)
    assert panel.activity_summary.minimumWidth() == 200
    assert all(
        0 <= value.mapTo(panel.statistics, QPoint()).y()
        and value.mapTo(panel.statistics, QPoint()).y() + value.height()
        <= panel.statistics.height()
        for value in panel.statistics._values
    )
    assert [button.toolTip() for button in buttons] == ["设置", "刷新", "收起"]
    assert all(not button.icon().isNull() for button in buttons)
    assert all(button.iconSize().width() == 18 for button in buttons)
    assert panel.light_theme_button.size().width() == 24
    assert panel.dark_theme_button.size().width() == 24
    assert panel.light_theme_button.iconSize().width() == 14
    assert panel.theme_segment.height() == 30
    panel.close()


def test_activity_switch_keeps_compact_controls_stable_and_fills_annual_page():
    panel = MainPanel()
    panel.resize(PANEL_MAX_WIDTH, ANNUAL_PANEL_HEIGHT)
    panel.update_data(sample_data())
    panel.show()
    APP.processEvents()

    annual_segment_geometry = panel.activity_mode_segment.geometry()
    panel.minute_activity_button.click()
    APP.processEvents()
    minute_segment_geometry = panel.activity_mode_segment.geometry()

    assert minute_segment_geometry == annual_segment_geometry
    assert (
        panel.activity_header_spacer.sizePolicy().horizontalPolicy()
        == QSizePolicy.Policy.Expanding
    )
    assert panel.height() == PANEL_HEIGHT
    assert panel.activity_card.height() == ACTIVITY_SECTION_HEIGHT
    assert panel.annual_activity_button.size() == QSize(72, 22)
    assert panel.minute_activity_button.size() == QSize(72, 22)

    panel.annual_activity_button.click()
    APP.processEvents()
    assert panel.height() == ANNUAL_PANEL_HEIGHT
    assert panel.activity_card.height() == ANNUAL_ACTIVITY_SECTION_HEIGHT
    assert panel.activity_scroll.height() == panel.activity_stack.height()
    panel.close()


def test_panel_at_640px_keeps_full_heatmap_without_horizontal_scrolling():
    panel = MainPanel()
    panel.resize(PANEL_MIN_WIDTH, PANEL_HEIGHT)
    panel.update_data(sample_data())
    panel.show()
    APP.processEvents()
    panel.activity.grab()
    APP.processEvents()

    assert panel.size().width() == PANEL_MIN_WIDTH
    assert panel.activity.width() <= panel.activity_scroll.viewport().width()
    assert not panel.activity_scroll.horizontalScrollBar().isVisible()
    assert all(
        not scroll.horizontalScrollBar().isVisible()
        for scroll in panel.findChildren(QScrollArea)
    )
    panel.close()


def test_panel_ignores_legacy_layout_state_and_has_no_reorder_handles():
    saved_layout = {
        "sections": ["bottom", "top", "middle"],
        "top_cards": ["month", "today", "balance"],
        "bottom_cards": ["statistics", "trend"],
    }
    with (
        patch(
            "ui.qt_panel.config_manager.load_panel_layout_state",
            return_value=saved_layout,
        ) as load_layout,
        patch("ui.qt_panel.config_manager.save_panel_layout_state") as save_layout,
    ):
        panel = MainPanel()

    load_layout.assert_not_called()
    save_layout.assert_not_called()
    assert not hasattr(panel, "layout_state")
    assert not hasattr(panel, "_section_reorder")
    assert not hasattr(panel, "_top_card_reorder")
    assert not hasattr(panel, "_bottom_card_reorder")
    assert not panel.findChildren(QWidget, "dragHandle")
    assert all(
        not hasattr(widget, "drag_handle")
        for widget in (
            panel.top_section,
            panel.activity_card,
            panel.statistics,
            panel.today_card,
            panel.balance_card,
            panel.month_card,
            panel.trend,
        )
    )
    panel.close()


def test_panel_system_mode_selects_resolved_theme_and_explains_following():
    panel = MainPanel()

    panel.set_theme_mode("system", "light")
    assert panel.light_theme_button.isChecked()
    assert not panel.dark_theme_button.isChecked()
    assert panel.light_theme_button.property("selected") is True
    assert "跟随系统" in panel.theme_segment.toolTip()
    assert "当前为浅色主题" in panel.light_theme_button.toolTip()

    panel.set_theme_mode("system", "dark")
    assert not panel.light_theme_button.isChecked()
    assert panel.dark_theme_button.isChecked()
    assert panel.dark_theme_button.property("selected") is True
    assert "跟随系统" in panel.theme_segment.toolTip()
    assert "当前为深色主题" in panel.dark_theme_button.toolTip()
    panel.close()


def test_existing_panel_switches_light_and_dark_without_refetching_or_rebinding_data():
    controller = configure_theme(APP, "dark")
    panel = MainPanel()
    panel.update_data(sample_data())
    panel.show()
    APP.processEvents()
    panel_identity = id(panel)
    dark_background = panel.trend.plot.backgroundBrush().color()

    try:
        with (
            patch.object(panel, "update_data") as update_data,
            patch.object(panel.trend, "set_rows") as set_rows,
            patch.object(panel.activity, "set_activity") as set_activity,
            patch.object(TokenData, "fetch") as fetch,
        ):
            controller.set_mode("light")
            APP.processEvents()
            light_background = panel.trend.plot.backgroundBrush().color()

            assert id(panel) == panel_identity
            assert panel._resolved_theme == "light"
            assert current_theme().name == "light"
            assert light_background.name() == current_theme().window.lower()
            assert light_background != dark_background

            controller.set_mode("dark")
            APP.processEvents()
            assert panel._resolved_theme == "dark"
            assert panel.trend.plot.backgroundBrush().color() == dark_background
            update_data.assert_not_called()
            set_rows.assert_not_called()
            set_activity.assert_not_called()
            fetch.assert_not_called()
    finally:
        controller.set_mode("dark")
        panel.close()


def test_expanded_window_hides_ball_and_uses_compact_panel_size():
    data = sample_data()
    with patch("ui.qt_widget.TokenData.fetch", return_value=data):
        widget = FloatingWidget()
        widget._data = data
        widget._refreshing = False
        widget.toggle()
        APP.processEvents()

        assert widget.ball.isHidden()
        assert widget.panel.isVisible()
        # 小屏幕/无头测试后端会把面板限制在当前工作区内。
        assert widget.width() <= 820
        assert widget.height() == ANNUAL_PANEL_HEIGHT
        widget.panel.minute_activity_button.click()
        APP.processEvents()
        assert widget.height() == PANEL_HEIGHT
        assert widget.mask().isEmpty()

        widget.toggle()
        APP.processEvents()
        assert widget.ball.isVisible()
        assert widget.panel.isHidden()
        assert widget.mask().contains(QPoint(60, 60))
        assert not widget.mask().contains(QPoint(0, 0))
        widget._closed = True
        widget.hide()


def test_window_stays_on_top_and_compact_ball_does_not_take_focus():
    with patch("ui.qt_widget.TokenData.fetch", return_value=sample_data()):
        widget = FloatingWidget()
        flags = widget.windowFlags()

        assert flags & Qt.WindowType.Tool
        assert flags & Qt.WindowType.FramelessWindowHint
        assert flags & Qt.WindowType.WindowStaysOnTopHint
        assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
        assert flags & Qt.WindowType.NoDropShadowWindowHint
        assert widget.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert widget.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        widget.expand_panel()
        assert widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
        assert not widget.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus

        widget.collapse_panel()
        assert widget.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
        widget._closed = True
        widget.hide()


def test_compact_ball_uses_smaller_size_and_keeps_free_drag_position():
    with patch("ui.qt_widget.FloatingWidget.refresh"):
        widget = FloatingWidget()
        widget.move(420, 260)

        with (
            patch.object(widget, "_work_area", return_value=WorkArea(0, 0, 1920, 1080)),
            patch("ui.qt_widget.config_manager.save_widget_position") as save_position,
        ):
            widget._clamp_to_work_area()

        assert (widget.width(), widget.height()) == (88, 88)
        assert (widget.ball.width(), widget.ball.height()) == (88, 88)
        assert (widget.x(), widget.y()) == (420, 260)
        save_position.assert_called_once_with(420, 260)
        widget._closed = True
        widget.hide()


def test_deactivation_collapses_panel_but_ignores_visible_settings():
    with patch("ui.qt_widget.TokenData.fetch", return_value=sample_data()):
        widget = FloatingWidget()
        widget.expand_panel()

        with patch("ui.qt_widget.config_manager.get", return_value=True):
            with (
                patch.object(widget, "isActiveWindow", return_value=False),
                patch.object(widget, "_has_settings_child", return_value=True),
            ):
                widget._collapse_after_deactivation()
            assert widget._expanded

            widget._drag_started = True
            with patch.object(widget, "isActiveWindow", return_value=False):
                widget._collapse_after_deactivation()
            assert widget._expanded
            widget._drag_started = False

            with (
                patch.object(widget, "isActiveWindow", return_value=True),
                patch.object(widget, "_has_settings_child", return_value=False),
            ):
                widget._collapse_after_deactivation()
            assert widget._expanded

            with (
                patch.object(widget, "isActiveWindow", return_value=False),
                patch.object(widget, "_has_settings_child", return_value=False),
            ):
                widget._collapse_after_deactivation()
        assert not widget._expanded
        assert widget.ball.isVisible()
        widget._settings_window = None
        widget._closed = True
        widget.hide()


def test_expanded_panel_preserves_transparent_rounded_bottom_corners():
    with patch("ui.qt_widget.FloatingWidget.refresh"):
        widget = FloatingWidget()
        widget.expand_panel()
        APP.processEvents()
        image = widget.grab().toImage()
        right = image.width() - 1
        bottom = image.height() - 1

        assert image.pixelColor(0, bottom).alpha() == 0
        assert image.pixelColor(right, bottom).alpha() == 0
        widget._closed = True
        widget.hide()


def test_deactivation_keeps_panel_expanded_when_auto_collapse_is_disabled():
    with patch("ui.qt_widget.TokenData.fetch", return_value=sample_data()):
        widget = FloatingWidget()
        widget.expand_panel()

        with (
            patch("ui.qt_widget.config_manager.get", return_value=False),
            patch.object(widget, "isActiveWindow", return_value=False),
            patch.object(widget, "_has_settings_child", return_value=False),
        ):
            widget._collapse_after_deactivation()

        assert widget._expanded
        widget._closed = True
        widget.hide()


def test_escape_closes_settings_before_collapsing_panel():
    with patch("ui.qt_widget.TokenData.fetch", return_value=sample_data()):
        widget = FloatingWidget()
        widget.expand_panel()
        settings = QDialog(widget)
        widget._settings_window = settings
        settings.show()
        APP.processEvents()
        escape = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier,
        )

        widget.keyPressEvent(escape)
        assert settings.isHidden()
        assert widget._expanded

        widget.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_Escape,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        assert not widget._expanded
        assert widget.ball.isVisible()
        widget._closed = True
        widget.hide()


def test_edge_snap_uses_one_eased_animation_and_delayed_hide():
    with patch("ui.qt_widget.TokenData.fetch", return_value=sample_data()):
        widget = FloatingWidget()
        widget.move(12, 200)
        with patch.object(widget, "_work_area", return_value=WorkArea(0, 0, 1920, 1080)):
            assert widget._try_edge_snap()
            assert widget._edge_direction == "left"
            assert widget._edge_animation.duration() == 180
            assert widget._edge_hide_timer.isActive()

            widget._expanded = True
            widget._edge_animation.stop()
            before = widget.pos()
            widget._do_edge_hide()
            assert widget.pos() == before

        widget._closed = True
        widget.hide()


def test_edge_snap_accepts_ball_that_already_overlaps_screen_edge():
    with patch("ui.qt_widget.TokenData.fetch", return_value=sample_data()):
        widget = FloatingWidget()
        with patch.object(widget, "_work_area", return_value=WorkArea(0, 0, 1920, 1080)):
            # 模拟按住球体中间拖到边缘：窗口左上角会越过边缘，但球本体已经接触屏幕边界。
            widget.move(-48, 200)
            assert widget._try_edge_snap()
            assert widget._edge_direction == "left"

            widget._edge_unsnap()
            widget.move(1872, 200)
            assert widget._try_edge_snap()
            assert widget._edge_direction == "right"

        widget._closed = True
        widget.hide()


def test_edge_unsnap_clears_hover_state_before_next_snap():
    with patch("ui.qt_widget.TokenData.fetch", return_value=sample_data()):
        widget = FloatingWidget()
        widget.move(12, 200)
        with patch.object(widget, "_work_area", return_value=WorkArea(0, 0, 1920, 1080)):
            assert widget._try_edge_snap()
            widget._edge_hovering = True

            widget._edge_unsnap()
            assert widget._edge_hovering is False

            widget.move(12, 200)
            assert widget._try_edge_snap()
            widget._do_edge_hide()
            assert widget._edge_hidden is True

        widget._closed = True
        widget.hide()


def test_settings_keep_unsaved_provider_drafts_when_switching():
    values = {
        "ACTIVE_PROVIDER": "deepseek",
        "REFRESH_INTERVAL": 60_000,
        "EDGE_HIDE_ENABLED": True,
        "DEEPSEEK_AUTH": "",
    }
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=values),
        patch("ui.qt_settings.config_manager.all_config", return_value=values),
    ):
        window = SettingsWindow()
        window._provider_widgets["AUTH"].setText("draft-token")
        mimo_index = next(
            index
            for index in range(window.provider_combo.count())
            if window.provider_combo.itemData(index) == "mimo"
        )
        window.provider_combo.setCurrentIndex(mimo_index)
        window.provider_combo.setCurrentIndex(0)
        assert window._provider_widgets["AUTH"].text() == "draft-token"
        assert window._provider_widgets["AUTH"].echoMode() == QLineEdit.EchoMode.Password
        window.close()


def test_settings_exposes_deepseek_peak_pricing_and_keeps_unsaved_times():
    values = {
        **config_manager.all_config(),
        "ACTIVE_PROVIDER": "deepseek",
        "DEEPSEEK_PEAK_PRICING_ENABLED": True,
        "DEEPSEEK_PEAK_PERIOD_1_START": "09:00",
        "DEEPSEEK_PEAK_PERIOD_1_END": "12:00",
        "DEEPSEEK_PEAK_PERIOD_2_START": "14:00",
        "DEEPSEEK_PEAK_PERIOD_2_END": "18:00",
    }
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=values),
        patch("ui.qt_settings.config_manager.all_config", return_value=values),
    ):
        window = SettingsWindow()
        assert not window.deepseek_peak_pricing_card.isHidden()
        assert window.deepseek_peak_period_1_start.isEnabled()
        window.deepseek_peak_period_1_start.setTime(QTime(8, 30))

        mimo_index = window.provider_combo.findData("mimo")
        window.provider_combo.setCurrentIndex(mimo_index)
        assert window.deepseek_peak_pricing_card.isHidden()
        window.provider_combo.setCurrentIndex(window.provider_combo.findData("deepseek"))
        assert not window.deepseek_peak_pricing_card.isHidden()
        assert window.deepseek_peak_period_1_start.time().toString("HH:mm") == "08:30"

        saved = window._values()
        assert saved["DEEPSEEK_PEAK_PRICING_ENABLED"] is True
        assert saved["DEEPSEEK_PEAK_PERIOD_1_START"] == "08:30"
        window.deepseek_peak_pricing_enabled.setChecked(False)
        assert not window.deepseek_peak_period_1_start.isEnabled()
        assert window._values()["DEEPSEEK_PEAK_PRICING_ENABLED"] is False
        window.close()


def test_panel_badge_shows_peak_pricing_state():
    panel = MainPanel()
    tooltip = (
        "峰时 2× · 12:00 结束\n北京时间高峰时段：09:00–12:00、14:00–18:00\n"
        "高峰价适用所有计费项；本提示不参与账单计算。"
    )
    panel.set_pricing_state(True, True, "峰时 2× · 12:00 结束", tooltip)
    assert not panel.pricing_badge.isHidden()
    assert panel.pricing_badge.text() == "峰时 2× · 12:00 结束"
    assert panel.pricing_badge.property("pricingState") == "peak"
    assert panel.pricing_badge.toolTip() == tooltip

    panel.set_pricing_state(False)
    assert panel.pricing_badge.isHidden()
    panel.close()


def test_ball_peak_highlight_enhances_glow_without_pricing_text():
    ball = FloatingUsageBall(88)
    ball.set_values("¥4.31", "¥36.03")
    ball.show()
    APP.processEvents()
    normal_ring = ball.grab().toImage().pixelColor(ball.width() // 2, 2)

    ball.set_peak_highlight(True)
    APP.processEvents()
    peak_ring = ball.grab().toImage().pixelColor(ball.width() // 2, 2)

    assert peak_ring != normal_ring
    assert ball.toolTip() == ""
    assert ball.accessibleName() == ""
    assert (ball._today, ball._balance) == ("¥4.31", "¥36.03")
    assert (ball.width(), ball.height()) == (88, 88)
    ball.close()


def test_settings_exposes_panel_auto_collapse_toggle():
    values = {
        **config_manager.all_config(),
        "PANEL_AUTO_COLLAPSE_ON_DEACTIVATE": False,
    }
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=values),
        patch("ui.qt_settings.config_manager.all_config", return_value=values),
    ):
        window = SettingsWindow()

    assert not window.panel_auto_collapse_check.isChecked()
    window.panel_auto_collapse_check.setChecked(True)
    assert window._values()["PANEL_AUTO_COLLAPSE_ON_DEACTIVATE"] is True
    window.close()


def test_settings_exposes_minute_usage_retention_days():
    values = {
        **config_manager.all_config(),
        "MINUTE_USAGE_CHART_TYPE": "line",
        "MINUTE_USAGE_RETENTION_DAYS": 7,
    }
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=values),
        patch("ui.qt_settings.config_manager.all_config", return_value=values),
    ):
        window = SettingsWindow()

    assert window.minute_usage_interval_minutes.value() == 5
    window.minute_usage_interval_minutes.setValue(15)
    assert window._values()["MINUTE_USAGE_INTERVAL_MINUTES"] == 15
    assert window.minute_usage_chart_type.currentData() == "line"
    window.minute_usage_chart_type.setCurrentIndex(
        window.minute_usage_chart_type.findData("bar")
    )
    assert window._values()["MINUTE_USAGE_CHART_TYPE"] == "bar"
    assert window.minute_usage_retention_days.value() == 7
    window.minute_usage_retention_days.setValue(14)
    assert window._values()["MINUTE_USAGE_RETENTION_DAYS"] == 14
    window.close()


def test_settings_schedules_application_data_directory_change_after_save():
    values = config_manager.all_config()
    current = Path.cwd() / ".test-appdata" / "current"
    target = Path.cwd() / ".test-appdata" / "target"
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=values),
        patch("ui.qt_settings.config_manager.all_config", return_value=values),
        patch("ui.qt_settings.config_manager.pending_data_dir", return_value=None),
        patch("ui.qt_settings.config_manager.data_dir_migration_error", return_value=""),
        patch.object(config_manager, "CONFIG_DIR", current),
    ):
        window = SettingsWindow()
        window._selected_data_dir = target
        window.data_dir_edit.setText(str(target))
        with (
            patch(
                "ui.qt_settings.config_manager.validate_data_dir_target",
                return_value=target.resolve(),
            ),
            patch("ui.qt_settings.config_manager.save_config"),
            patch("ui.qt_settings.config_manager.schedule_data_dir_change") as schedule,
            patch("ui.qt_settings.QMessageBox.information") as information,
        ):
            window._save()

    schedule.assert_called_once_with(target.resolve())
    information.assert_called_once()
    window.close()


def test_settings_theme_selector_emits_all_modes_immediately_and_cancel_does_not_rollback():
    values = {
        **config_manager.all_config(),
        "UI_THEME": "dark",
    }
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=values),
        patch("ui.qt_settings.config_manager.all_config", return_value=values),
    ):
        window = SettingsWindow()

    requested: list[str] = []
    window.theme_requested.connect(requested.append)
    assert [
        window.theme_combo.itemData(index)
        for index in range(window.theme_combo.count())
    ] == ["system", "light", "dark"]

    for mode in ("system", "light", "dark"):
        window.theme_combo.setCurrentIndex(window.theme_combo.findData(mode))
        assert requested[-1] == mode
        assert window._values()["UI_THEME"] == mode

    window.theme_combo.setCurrentIndex(window.theme_combo.findData("light"))
    emitted_before_cancel = list(requested)
    window.reject()

    assert window.theme_combo.currentData() == "light"
    assert requested == emitted_before_cancel
    assert "取消设置不会回滚主题" in window.theme_combo.toolTip()
    window.close()


def test_settings_separates_accounts_runtime_and_updates_into_tabs():
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=config_manager.all_config()),
        patch("ui.qt_settings.config_manager.all_config", return_value=config_manager.all_config()),
    ):
        window = SettingsWindow()

    assert [window.tabs.tabText(index) for index in range(window.tabs.count())] == [
        "账户与凭据",
        "运行行为",
        "软件更新",
    ]
    assert window.tabs.widget(0) is window.scroll_area
    assert window.update_card.parent() is window.tabs.widget(2)
    assert window.test_button.parent() is window.content
    window.close()


def test_deepseek_cookie_acquisition_only_updates_its_cookie_draft():
    values = {
        **config_manager.all_config(),
        "ACTIVE_PROVIDER": "deepseek",
        "DEEPSEEK_AUTH": "existing-bearer-token",
    }
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=values),
        patch("ui.qt_settings.config_manager.all_config", return_value=values),
    ):
        window = SettingsWindow()

    window._apply_acquired_cookie("deepseek", "session=latest; user=42")
    assert window._provider_widgets["COOKIE"].toPlainText() == "session=latest; user=42"
    assert window._provider_widgets["AUTH"].text() == "existing-bearer-token"
    assert window._provider_drafts["deepseek"]["COOKIE"] == "session=latest; user=42"
    window.close()


def test_settings_window_exposes_update_controls_without_controller():
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=config_manager.all_config()),
        patch("ui.qt_settings.config_manager.all_config", return_value=config_manager.all_config()),
    ):
        window = SettingsWindow()

    assert window.current_version_label.text() == "v开发模式"
    assert window.auto_check_updates.isChecked() is True
    assert window.update_channel_combo.currentData() == "stable"
    assert window.check_updates_button.text() == "检查更新"
    assert not window.skip_update_button.isEnabled()
    assert window.update_status_label.text()
    window.close()


def test_auto_update_prompt_only_deduplicates_within_current_session():
    release = sample_release()
    result = CheckResult(
        current_version="1.3.3",
        latest_release=release,
        update_available=True,
        message=f"发现新版本 v{release.version}",
    )
    first_owner = QWidget()
    first_controller = AppUpdateController(first_owner)
    second_owner = QWidget()
    second_controller = AppUpdateController(second_owner)

    try:
        with patch("ui.qt_update.skipped_version", return_value=""):
            with patch.object(first_controller, "_prompt_for_release") as first_prompt:
                first_controller._finish_check(result, None, manual=False, parent=first_owner)
                first_controller._finish_check(result, None, manual=False, parent=first_owner)
                assert first_prompt.call_count == 1

            with patch.object(second_controller, "_prompt_for_release") as second_prompt:
                second_controller._finish_check(result, None, manual=False, parent=second_owner)
                assert second_prompt.call_count == 1
    finally:
        first_owner.close()
        second_owner.close()


def test_manual_update_check_still_allows_reprompt_for_same_version():
    release = sample_release()
    result = CheckResult(
        current_version="1.3.3",
        latest_release=release,
        update_available=True,
        message=f"发现新版本 v{release.version}",
    )
    owner = QWidget()
    controller = AppUpdateController(owner)

    try:
        with patch.object(controller, "_prompt_for_release") as prompt:
            controller._finish_check(result, None, manual=True, parent=owner)
            controller._finish_check(result, None, manual=True, parent=owner)
            assert prompt.call_count == 2
    finally:
        owner.close()


def test_settings_window_wraps_content_in_scroll_area():
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=config_manager.all_config()),
        patch("ui.qt_settings.config_manager.all_config", return_value=config_manager.all_config()),
    ):
        window = SettingsWindow()

    scroll_area = window.findChild(QScrollArea)
    assert scroll_area is window.scroll_area
    assert scroll_area.widgetResizable() is True
    assert scroll_area.widget() is window.content
    window.close()


def test_existing_settings_window_follows_light_and_dark_after_scroll_wrap():
    controller = configure_theme(APP, "dark")
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=config_manager.all_config()),
        patch("ui.qt_settings.config_manager.all_config", return_value=config_manager.all_config()),
    ):
        window = SettingsWindow()

    window.show()
    APP.processEvents()
    dark_sample = window.grab().toImage().pixelColor(12, 12)

    try:
        controller.set_mode("light")
        APP.processEvents()
        light_sample = window.grab().toImage().pixelColor(12, 12)

        assert light_sample.name() == current_theme().window.lower()
        assert light_sample != dark_sample
        assert window.theme_combo.currentData() == "light"

        controller.set_mode("dark")
        APP.processEvents()
        assert window.grab().toImage().pixelColor(12, 12) == dark_sample
        assert window.theme_combo.currentData() == "dark"
    finally:
        controller.set_mode("dark")
        window.close()


def test_open_ball_and_update_dialog_retheme_in_place():
    controller = configure_theme(APP, "dark")
    ball = FloatingUsageBall(96)
    ball.set_values("¥0.71", "¥0.47")
    update_dialog = UpdatePromptDialog(sample_release())
    ball.show()
    update_dialog.show()
    APP.processEvents()
    ball_identity = id(ball)
    dialog_identity = id(update_dialog)
    dark_ball = ball.grab().toImage().pixelColor(48, 48)
    dark_dialog = update_dialog.grab().toImage().pixelColor(12, 12)

    try:
        controller.set_mode("light")
        APP.processEvents()

        assert id(ball) == ball_identity
        assert id(update_dialog) == dialog_identity
        assert ball.grab().toImage().pixelColor(48, 48) != dark_ball
        assert update_dialog.grab().toImage().pixelColor(12, 12) != dark_dialog
    finally:
        controller.set_mode("dark")
        ball.close()
        update_dialog.close()
