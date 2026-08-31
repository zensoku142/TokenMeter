import json
import math
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = str(Path.cwd() / ".test-appdata")

import pyqtgraph as pg
import pytest
from PySide6.QtCore import QDate, QEvent, QPoint, QPointF, QRectF, QSize, Qt, QTime
from PySide6.QtGui import QColor, QEnterEvent, QKeyEvent, QPainterPath
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QDialog,
    QFrame,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from config import runtime as config_manager
from config.defaults import DEFAULT_CONFIG
from api.providers.base import QuotaMetric, QuotaWindow
from updater.client import CheckResult, ReleaseAsset, ReleaseInfo, SemVer
from data.store import PerProviderData, TokenData
from ui.geometry import WorkArea
from ui.qt_ball import CodexLiquidMotion, FloatingUsageBall, LiquidSurfaceState
from ui.qt_panel import (
    ACTIVITY_SECTION_HEIGHT,
    ANNUAL_ACTIVITY_SECTION_HEIGHT,
    ANNUAL_PANEL_HEIGHT,
    HEADER_HEIGHT,
    PANEL_HEIGHT,
    PANEL_MAX_WIDTH,
    PANEL_MIN_WIDTH,
    STATISTICS_SECTION_HEIGHT,
    STATUS_SECTION_HEIGHT,
    TOP_SECTION_HEIGHT,
    MainPanel,
    MinuteDateEdit,
    MinuteUsageChart,
    StatisticsCard,
    TrendCard,
    format_codex_reset_time,
    format_codex_tokens,
    format_money,
    format_money_axis,
    format_reset_countdown,
    format_token_axis,
)
from ui.qt_settings import SettingsWindow
from ui.qt_theme import DARK_THEME, LIGHT_THEME, configure_theme, current_theme
from ui.qt_update import AppUpdateController, UpdatePromptDialog
from ui.qt_widget import FloatingWidget
from ui.vpet_host import VPetHost, usage_message

APP = QApplication.instance() or QApplication([])
configure_theme(APP, "dark")


@pytest.fixture(autouse=True)
def isolate_ui_settings_state():
    # 自动保存可能在失焦或关闭时执行；UI 测试使用内存配置，不能读写真实凭据和启动项。
    values = DEFAULT_CONFIG.copy()

    def persist(changes):
        updated = config_manager.validate_config({**values, **changes})
        values.clear()
        values.update(updated)
        return values.copy()

    with (
        patch.object(config_manager, "_config", values),
        patch.object(config_manager, "load_config", side_effect=lambda: values.copy()),
        patch.object(config_manager, "save_config", side_effect=persist),
        patch("ui.qt_settings.sync_autostart"),
    ):
        yield
        # 既有测试保留部分隐藏窗口；清除其防抖任务，防止下个用例的事件循环误保存旧草稿。
        for window in APP.allWidgets():
            if isinstance(window, SettingsWindow):
                window._autosave_ready = False
                window._save_pending = False
                window._save_timer.stop()
                window._appearance_save_timer.stop()


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
        setup_asset=ReleaseAsset(
            name=f"TokenMeter-Setup-v{version}-x64.exe",
            download_url=f"https://github.com/zensoku142/TokenMeter/releases/download/v{version}/TokenMeter-Setup-v{version}-x64.exe",
            size=15,
        ),
        checksum_asset=ReleaseAsset(
            name="SHA256SUMS.txt",
            download_url=f"https://github.com/zensoku142/TokenMeter/releases/download/v{version}/SHA256SUMS.txt",
            size=2,
        ),
    )


def test_token_axis_uses_readable_units():
    assert format_token_axis(0) == "0万"
    assert format_token_axis(1_500) == "0.15万"
    assert format_token_axis(60_000_000) == "6000万"
    assert format_codex_tokens(2_607_632_527) == "26.1亿"
    assert format_codex_tokens(202_936_827) == "2亿"


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


def test_panel_keeps_cached_values_visible_while_refreshing():
    data = sample_data()
    data.today_cost_cny = 1.25
    panel = MainPanel()

    panel.update_data(data, loading=False, refreshing=True)

    assert panel.today_card.value.text() == "¥1.25"
    assert panel.status_text.text() == "正在更新"
    panel.close()


def test_trend_uses_exactly_seven_cost_bars_with_hover_tooltip():
    trend = TrendCard()
    trend.set_rows(sample_data().daily_usage, date.today())
    trend.resize(480, TOP_SECTION_HEIGHT)
    trend.show()
    APP.processEvents()

    assert trend.title.text() == "近 7 天使用金额"
    assert trend.amount_button.text() == "金额趋势"
    assert trend.model_button.text() == "模型使用"
    assert trend.amount_button.width() == trend.model_button.width()
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
    trend._on_mouse_moved((scene_point,))

    assert trend._hover_index == 0
    assert len(trend._series.opts["brushes"]) == 7
    assert trend.hover_tooltip.isVisible()
    assert trend.hover_tooltip.date_label.text() == (date.today() - timedelta(days=6)).isoformat()
    assert trend.plot.toolTip() == ""
    trend.close()


def test_nayuto_trend_groups_all_models_with_stable_order_colors_and_tooltip():
    current = date(2026, 8, 15)
    model_rows = [
        {
            "date": (current - timedelta(days=offset)).isoformat(),
            "models": [
                {
                    "model": "model-b",
                    "cache_hit_tokens": 2,
                    "cache_miss_tokens": 3,
                    "output_tokens": 5,
                    "total_tokens": 10,
                    "cost_cny": Decimal("0.0202"),
                },
                {
                    "model": "model-a",
                    "cache_hit_tokens": 40 - offset,
                    "cache_miss_tokens": 20,
                    "output_tokens": 10,
                    "total_tokens": 70 - offset,
                    "cost_cny": Decimal("0.1001"),
                },
            ],
        }
        for offset in range(6, -1, -1)
    ]
    trend = TrendCard()
    trend.set_rows(
        [{"date": current.isoformat(), "cost_cny": Decimal("0.1203"), "tokens": 80}],
        today=current,
        currency="USD",
        model_rows=model_rows,
        model_usage_enabled=True,
        provider_id="nayuto",
    )
    trend.resize(540, TOP_SECTION_HEIGHT)
    trend.show()
    trend.model_button.click()
    APP.processEvents()

    assert trend.title.text() == "近 7 天各模型 Token 使用量"
    assert trend._model_order == ["model-a", "model-b"]
    assert len(trend._values) == 14
    assert len(trend._bar_positions) == 14
    assert trend._bar_positions[2] - trend._bar_positions[0] == pytest.approx(1.0)
    assert trend._series.opts["width"] < trend.BAR_WIDTH
    first_color = trend._series.opts["brushes"][0].color()
    assert trend._series.opts["brushes"][2].color() == first_color

    tooltip = trend.tooltip_text(0)
    expected_order = [
        "08/09　总计",
        "模型　model-a",
        "输入（命中缓存）",
        "输入（未命中缓存）",
        "输出",
        "缓存命中率",
        "当日消耗金额　$0.1001",
    ]
    assert all(text in tooltip for text in expected_order)
    assert [tooltip.index(text) for text in expected_order] == sorted(
        tooltip.index(text) for text in expected_order
    )
    scene_point = trend.plot.getViewBox().mapViewToScene(
        QPointF(trend._bar_positions[0], trend._values[0] / 2)
    )
    trend._on_mouse_moved((scene_point,))
    assert trend.hover_tooltip.isVisible()
    assert trend.hover_tooltip.model_label.text() == "model-a"
    assert trend.hover_tooltip.cost_label.text() == "$0.1001"
    assert trend.plot.toolTip() == ""

    trend.amount_button.click()
    assert trend.title.text() == "近 7 天使用金额"
    trend.model_button.click()
    assert trend._model_order == ["model-a", "model-b"]
    trend.close()


def test_nayuto_model_trend_keeps_seven_dates_and_uses_own_empty_state():
    current = date(2026, 8, 15)
    trend = TrendCard()
    trend.set_rows(
        [],
        today=current,
        currency="USD",
        model_rows=[],
        model_usage_enabled=True,
        provider_id="nayuto",
    )
    trend.model_button.click()

    assert trend._dates == [current - timedelta(days=offset) for offset in range(6, -1, -1)]
    assert trend._model_order == []
    assert trend._values == []
    assert not trend.empty_label.isHidden()
    assert "暂无模型用量" in trend.empty_label.text()
    assert trend.plot.toolTip() == ""
    trend.close()


def test_nayuto_single_model_trend_uses_current_theme_accent():
    controller = configure_theme(APP, "dark")
    controller.set_appearance("dark", "#D14C2F", 82)
    current = date(2026, 8, 15)
    trend = TrendCard()
    try:
        trend.set_rows(
            [],
            today=current,
            currency="USD",
            model_rows=[
                {
                    "date": current.isoformat(),
                    "models": [
                        {
                            "model": "gpt-5.6-terra",
                            "cache_hit_tokens": 10,
                            "cache_miss_tokens": 20,
                            "output_tokens": 5,
                            "total_tokens": 35,
                            "cost_cny": Decimal("0.2833"),
                        }
                    ],
                }
            ],
            model_usage_enabled=True,
            provider_id="nayuto",
        )
        trend.model_button.click()

        assert trend._model_order == ["gpt-5.6-terra"]
        assert {
            brush.color().name().upper() for brush in trend._series.opts["brushes"]
        } == {"#D14C2F"}
        assert "#d14c2f" in trend._legend_labels["gpt-5.6-terra"].styleSheet().lower()
    finally:
        controller.set_appearance("dark", DARK_THEME.accent, 100)
        trend.close()


def test_custom_accent_updates_trend_and_minute_bar_and_line_series():
    controller = configure_theme(APP, "dark")
    controller.set_appearance("dark", "#D14C2F", 82)
    trend = TrendCard()
    minute = MinuteUsageChart()
    rows = [
        {
            "minute": 600,
            "token_type": "PROMPT_CACHE_MISS_TOKEN",
            "token_amount": 10,
        }
    ]
    try:
        trend.set_rows(sample_data().daily_usage, date.today())
        assert trend._series.opts["brush"].color().name().upper() == "#D14C2F"

        minute.set_rows(rows, "recorded", chart_type="bar")
        bar = minute._bars["PROMPT_CACHE_MISS_TOKEN"]
        assert bar.opts["brush"].color().name().upper() == "#D14C2F"

        minute.set_rows(rows, "recorded", chart_type="line")
        line = minute._lines["PROMPT_CACHE_MISS_TOKEN"]
        assert line.opts["pen"].color().name().upper() == "#D14C2F"
    finally:
        controller.set_appearance("dark", DARK_THEME.accent, 100)
        minute.close()
        trend.close()


def test_synced_accent_keeps_chart_and_heatmap_colors_when_switching_modes():
    accent = "#E88298"
    controller = configure_theme(
        APP, "light", light_accent=accent, dark_accent=accent, sync_accent=True,
    )
    panel = MainPanel()
    panel.update_data(sample_data())
    panel.show()
    try:
        for mode in ("light", "dark", "light"):
            controller.set_mode(mode)
            APP.processEvents()
            assert panel.trend._series.opts["brush"].color() == QColor(accent)
            rendered = panel.activity.grab().toImage()
            rect, _day = max(panel.activity._hits, key=lambda hit: hit[1].token_count)
            assert rendered.pixelColor(rect.center().toPoint()) == QColor(accent)
    finally:
        panel.close()
        panel.deleteLater()
        configure_theme(APP, "dark")


def test_chart_canvases_inherit_translucent_panel_background():
    controller = configure_theme(APP, "light")
    controller.set_appearance("light", "#E986A1", 70)
    trend = TrendCard()
    minute = MinuteUsageChart()
    try:
        trend.refresh_theme()
        minute.refresh_theme()

        for plot in (trend.plot, minute.plot, minute.navigator):
            assert plot.backgroundBrush().style() == Qt.BrushStyle.NoBrush
            assert plot.palette().base().color().alpha() == 0
            assert plot.viewport().palette().base().color().alpha() == 0
    finally:
        controller.set_appearance("light", LIGHT_THEME.accent, 100)
        controller.set_mode("dark")
        minute.close()
        trend.close()


def test_money_axis_and_zero_cost_range_remain_readable():
    assert format_money_axis(0) == "¥0.00"
    assert format_money_axis(0.006) == "¥0.0060"
    trend = TrendCard()
    trend.set_rows([], date.today())

    assert trend._values == [0.0] * 7
    assert trend.plot.getViewBox().viewRange()[1][1] >= 0.01
    trend.close()


def test_money_format_uses_provider_native_currency():
    assert format_money(1.25, "USD") == "$1.25"
    assert format_money_axis(0.006, "USD") == "$0.0060"


def test_panel_quick_switches_provider_and_renders_subscription_quota():
    panel = MainPanel()
    data = sample_data()
    reset = datetime(2026, 8, 20, 3, 58, tzinfo=timezone.utc)
    data.quota_windows = [
        QuotaWindow("codex-weekly", "每周额度", 25, resets_at=reset),
    ]
    data.quota_metrics = [QuotaMetric("可用 Credits", "12.5")]
    data.quota_statistics = [
        QuotaMetric("累计 Token 数", "21.7亿", "来自 Codex 账号统计"),
        QuotaMetric("峰值 Token 数", "1.1亿"),
        QuotaMetric("最长任务时长", "52分 35秒"),
        QuotaMetric("当前连续天数", "0 天"),
        QuotaMetric("最长连续天数", "27 天"),
    ]
    data.account_plan = "pro"
    data.account_label = "a@example.com"
    data.weekly_usage = [dict(row) for row in data.daily_usage]
    data.weekly_usage[0]["tokens"] = 99_000_000
    data.quota_source = "interface"
    data.weekly_activity_source = "mixed"
    data.activity_source = "interface"
    data.statistics_source = "interface"
    data.per_provider = [
        PerProviderData(
            "codex",
            "Codex",
            quota_windows=list(data.quota_windows),
            quota_metrics=list(data.quota_metrics),
            quota_statistics=list(data.quota_statistics),
            account_plan="pro",
            account_label="a@example.com",
        )
    ]
    selected_providers: list[str] = []
    panel.provider_selected.connect(selected_providers.append)

    panel.update_data(data)

    assert panel.provider_quick_combo.currentData() == "codex"
    assert panel.provider_quick_combo.size() == QSize(132, 28)
    assert panel.provider_quick_combo.view().objectName() == "headerProviderView"
    assert panel.provider_quick_combo.view().minimumWidth() == 132
    panel.show()
    combo_animation_enabled = APP.isEffectEnabled(Qt.UIEffect.UI_AnimateCombo)
    panel.provider_quick_combo.showPopup()
    APP.processEvents()
    popup = panel.provider_quick_combo.view().window()
    assert popup.size() == QSize(132, 208)
    assert popup.minimumSize() == QSize(132, 208)
    assert popup.maximumSize() == QSize(132, 208)
    assert popup.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert popup.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert popup.frameShape() == QFrame.Shape.NoFrame
    assert panel.provider_quick_combo.view().frameShape() == QFrame.Shape.NoFrame
    assert not popup.mask().contains(QPoint(0, 0))
    assert popup.mask().contains(popup.rect().center())
    assert APP.isEffectEnabled(Qt.UIEffect.UI_AnimateCombo) == combo_animation_enabled
    panel.provider_quick_combo.hidePopup()
    assert panel.today_card.title_label.text() == "每周额度"
    assert panel.today_card.value.text() == "已用 25%"
    assert not panel.today_card.detail.isHidden()
    assert "剩余 75%" in panel.today_card.detail.text()
    assert "8月20日 11:58重置" in panel.today_card.detail.text()
    assert panel.balance_card.title_label.text() == "可用 Credits"
    assert panel.balance_card.value.text() == "12.5"
    assert panel.month_card.title_label.text() == "订阅套餐"
    assert panel.month_card.value.text() == "pro"
    assert panel.month_card.detail.isHidden()
    assert not panel.trend.isHidden()
    assert panel.trend.title.text() == "近 7 天 Token 使用量"
    assert panel.trend._values[-1] == 99_000_000
    today_activity = next(day for day in panel.activity.days if day.date == date.today())
    assert today_activity.token_count == 10_000_000
    assert "Token：" in panel.trend.tooltip_text(0)
    assert "金额" not in panel.trend.tooltip_text(0)
    assert not panel.activity_card.isHidden()
    assert panel.activity_mode_segment.isHidden()
    assert panel.trend.title.toolTip().endswith("当天 Token 为本机会话日志估算")
    assert panel.trend.plot.toolTip() == ""
    assert panel.activity_summary.toolTip() == "来自 Codex 账号统计，不含本机估算"
    assert panel.status_text.text() == (
        "额度/热力图/底部统计：接口数据 · 近 7 天：接口 + 今日本机估算"
    )
    assert [label.text() for label in panel.statistics._values] == [
        "21.7亿",
        "1.1亿",
        "52分 35秒",
        "0 天",
        "27 天",
    ]

    mimo_index = panel.provider_quick_combo.findData("mimo")
    panel.provider_quick_combo.setCurrentIndex(mimo_index)
    panel.provider_quick_combo.activated.emit(mimo_index)

    assert selected_providers == ["mimo"]
    panel.close()


def test_cursor_uses_existing_quota_panel_positions_and_empty_activity_states():
    reset = datetime.now(timezone.utc) + timedelta(days=12)
    window = QuotaWindow("cursor-monthly", "每月额度", 42, resets_at=reset)
    metrics = [
        QuotaMetric("套餐用量", "$8.40 / $20.00"),
        QuotaMetric("额外消费", "$2.10 / $50.00"),
    ]
    statistics = [
        QuotaMetric("套餐", "Pro"),
        QuotaMetric("Bonus", "$0.00"),
        QuotaMetric("Auto", "$6.20"),
        QuotaMetric("指定模型", "$2.20"),
        QuotaMetric("账期", "08-01 — 09-01"),
    ]
    data = TokenData(
        currency="USD",
        status="ok",
        last_success_at=datetime.now(),
        quota_windows=[window],
        quota_metrics=metrics,
        quota_statistics=statistics,
        quota_source="interface",
        statistics_source="interface",
        per_provider=[
            PerProviderData(
                "cursor",
                "Cursor",
                currency="USD",
                quota_windows=[window],
                quota_metrics=metrics,
                quota_statistics=statistics,
                status="ok",
            )
        ],
    )
    panel = MainPanel()
    panel.update_data(data)

    assert panel.provider_quick_combo.count() == 5
    assert panel.provider_quick_combo.currentData() == "cursor"
    assert panel.provider_quick_combo.size() == QSize(132, 28)
    assert panel.today_card.title_label.text() == "每月额度"
    assert panel.today_card.value.text() == "已用 42%"
    assert "剩余 58%" in panel.today_card.detail.text()
    assert panel.balance_card.title_label.text() == "套餐用量"
    assert panel.balance_card.value.text() == "$8.40 / $20.00"
    assert panel.month_card.title_label.text() == "额外消费"
    assert panel.month_card.value.text() == "$2.10 / $50.00"
    assert panel.statistics.title.text() == "Cursor 使用统计"
    assert [label.text() for label in panel.statistics._names] == [
        "套餐",
        "Bonus",
        "Auto",
        "指定模型",
        "账期",
    ]
    assert panel.statistics._values[0].toolTip() == "来自 Cursor 账号统计"
    assert panel.trend._values == [0.0] * 7
    assert panel.activity_summary.text() == "暂无 Token 活动"
    assert all(day.token_count == 0 for day in panel.activity.days)
    assert not panel.trend.isHidden()
    assert not panel.activity_card.isHidden()
    assert panel.activity_mode_segment.isHidden()
    panel.close()


def test_cursor_real_daily_tokens_fill_existing_trend_and_activity_regions():
    today = date.today()
    yesterday = today - timedelta(days=1)
    window = QuotaWindow("cursor-monthly", "每月额度", 33)
    rows = [
        {"date": yesterday.isoformat(), "tokens": 12_000, "cost_cny": 0},
        {"date": today.isoformat(), "tokens": 34_000, "cost_cny": 0},
    ]
    data = TokenData(
        status="ok",
        quota_windows=[window],
        daily_usage=rows,
        weekly_usage=list(rows),
        quota_source="interface",
        activity_source="interface",
        weekly_activity_source="interface",
        statistics_source="interface",
        per_provider=[
            PerProviderData(
                "cursor",
                "Cursor",
                quota_windows=[window],
                status="ok",
            )
        ],
    )
    panel = MainPanel()
    panel.update_data(data)

    assert panel.trend._values[-2:] == [12_000, 34_000]
    today_activity = next(day for day in panel.activity.days if day.date == today)
    assert today_activity.token_count == 34_000
    assert panel.activity_summary.text().endswith("4.6万")
    assert panel.trend.title.toolTip() == "来自 Cursor 账号统计"
    assert panel.activity_summary.toolTip() == "来自 Cursor 账号统计，不含本机估算"
    assert not panel.trend.isHidden()
    assert not panel.activity_card.isHidden()
    assert panel.activity_mode_segment.isHidden()
    panel.close()


def test_codex_cached_panel_does_not_claim_background_refresh_is_data_update():
    panel = MainPanel()
    data = sample_data()
    data.per_provider = [PerProviderData("codex", "Codex")]
    data.quota_windows = [QuotaWindow("codex-weekly", "每周额度", 25)]
    data.quota_source = "cache"
    data.weekly_activity_source = "cache"
    data.activity_source = "cache"
    data.statistics_source = "cache"

    panel.update_data(data, refreshing=True)

    assert "正在更新" not in panel.status_text.text()
    assert panel.status_text.text() == "额度/近 7 天/热力图/底部统计：缓存数据"
    assert panel.updated_text.text().startswith("缓存保存于")
    panel.close()


def test_codex_source_summary_marks_cached_data():
    data = TokenData(
        quota_source="interface",
        weekly_activity_source="cache",
        activity_source="cache",
        statistics_source="cache",
    )

    assert MainPanel.codex_source_summary(data) == (
        "额度：接口数据 · 近 7 天/热力图/底部统计：缓存数据"
    )
    assert MainPanel.codex_source_summary(data, loading=True).startswith(
        "正在更新 · 当前显示"
    )

    unavailable = TokenData(
        status="ok",
        quota_source="interface",
        weekly_activity_source="local",
    )
    assert MainPanel.codex_source_summary(unavailable) == (
        "额度：接口数据 · 近 7 天：今日本机估算 · 热力图/底部统计：暂无数据"
    )
    unavailable.weekly_activity_source = "cache_mixed"
    assert "近 7 天：缓存 + 今日本机估算" in MainPanel.codex_source_summary(
        unavailable
    )


def test_subscription_expiry_replaces_codex_placeholder_without_email():
    panel = MainPanel()
    data = sample_data()
    active_until = datetime(2026, 8, 11, 6, 17, tzinfo=timezone.utc)
    data.quota_windows = [QuotaWindow("codex-weekly", "每周额度", 25)]
    data.account_plan = "plus"
    data.account_label = "a@example.com"
    data.account_plan_active_until = active_until
    data.per_provider = [
        PerProviderData(
            "codex",
            "Codex",
            quota_windows=list(data.quota_windows),
            account_plan="plus",
            account_label="a@example.com",
            account_plan_active_until=active_until,
        )
    ]

    panel.update_data(data)

    local_until = active_until.astimezone()
    assert panel.balance_card.title_label.text() == "订阅套餐"
    assert panel.balance_card.value.text() == "plus"
    assert panel.balance_card.detail.isHidden()
    assert panel.month_card.title_label.text() == "套餐到期"
    assert panel.month_card.value.text() == local_until.strftime("%m-%d")
    assert panel.month_card.detail.isHidden()
    assert "a@example.com" not in panel.balance_card.value.toolTip()
    assert "a@example.com" not in panel.month_card.value.toolTip()
    panel.close()


def test_header_does_not_start_drag_from_provider_selector_events():
    panel = MainPanel()
    panel.show()
    APP.processEvents()
    pressed: list[QPoint] = []
    dragged: list[QPoint] = []
    released: list[QPoint] = []
    panel.header.pressed.connect(pressed.append)
    panel.header.dragged.connect(dragged.append)
    panel.header.released.connect(released.append)

    combo_point = panel.provider_quick_combo.mapTo(
        panel.header, panel.provider_quick_combo.rect().center()
    )

    def mouse_event(event_point: QPoint, *, pressed_button: bool) -> Mock:
        event = Mock()
        event.button.return_value = (
            Qt.MouseButton.LeftButton if pressed_button else Qt.MouseButton.NoButton
        )
        event.buttons.return_value = (
            Qt.MouseButton.LeftButton if pressed_button else Qt.MouseButton.NoButton
        )
        event.position.return_value = QPointF(event_point)
        event.globalPosition.return_value = QPointF(panel.header.mapToGlobal(event_point))
        return event

    panel.header.mousePressEvent(mouse_event(combo_point, pressed_button=True))
    panel.header.mouseMoveEvent(
        mouse_event(combo_point + QPoint(12, 0), pressed_button=True)
    )
    panel.header.mouseReleaseEvent(mouse_event(combo_point, pressed_button=True))

    assert pressed == []
    assert dragged == []
    assert released == []

    free_point = QPoint(430, panel.header.height() // 2)
    panel.header.mousePressEvent(mouse_event(free_point, pressed_button=True))
    panel.header.mouseMoveEvent(
        mouse_event(free_point + QPoint(12, 0), pressed_button=True)
    )
    panel.header.mouseReleaseEvent(mouse_event(free_point, pressed_button=True))

    assert len(pressed) == 1
    assert len(dragged) == 1
    assert len(released) == 1
    panel.close()


def test_codex_spark_quota_is_hidden_without_hiding_other_subscription_details():
    panel = MainPanel()
    data = sample_data()
    active_until = datetime(2026, 8, 11, 6, 17, tzinfo=timezone.utc)
    data.quota_windows = [
        QuotaWindow("codex-primary", "每周额度", 7),
        QuotaWindow("codex-extra-0-primary", "GPT-5.3-Codex-Spark", 0),
    ]
    data.account_plan = "prolite"
    data.account_plan_active_until = active_until
    data.per_provider = [
        PerProviderData(
            "codex",
            "Codex",
            quota_windows=list(data.quota_windows),
            account_plan="prolite",
            account_plan_active_until=active_until,
        )
    ]

    panel.update_data(data)

    assert panel.today_card.title_label.text() == "每周额度"
    assert panel.balance_card.title_label.text() == "订阅套餐"
    assert panel.balance_card.value.text() == "prolite"
    assert panel.month_card.title_label.text() == "套餐到期"
    assert panel.month_card.value.text() == active_until.astimezone().strftime("%m-%d")
    assert all(
        "Spark" not in card.title_label.text()
        for card in (panel.today_card, panel.balance_card, panel.month_card)
    )
    panel.close()


def test_reset_countdown_is_timezone_safe_and_readable():
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    assert format_reset_countdown(now + timedelta(days=1, hours=2), now) == "1 天 2 小时后重置"
    assert format_reset_countdown(now + timedelta(minutes=45), now) == "45 分钟后重置"


def test_codex_reset_time_uses_shanghai_month_day_hour_and_minute():
    reset = datetime(2026, 8, 20, 3, 58, tzinfo=timezone.utc)

    assert format_codex_reset_time(reset) == "8月20日 11:58重置"
    assert format_codex_reset_time(reset, compact=True) == "8月20日11:58"
    assert format_codex_reset_time(None) == "重置时间未知"


def test_codex_ball_never_falls_back_to_currency_when_quota_is_unavailable():
    data = TokenData(
        status="partial",
        per_provider=[PerProviderData("codex", "Codex", status="partial")],
    )
    with patch("ui.qt_widget.FloatingWidget.refresh"):
        widget = FloatingWidget()
        widget._data = data
        widget._refreshing = False
        widget._apply_update()

    assert widget.ball._quota_mode
    assert widget.ball._quota_remaining is None
    assert widget.ball._quota_title == "周额度"
    assert widget.ball._quota_reset_text == "额度暂不可用"
    assert "¥" not in widget.ball.toolTip()
    widget._closed = True
    widget.hide()


def test_codex_ball_uses_remaining_quota_and_compact_reset_time():
    reset = datetime(2026, 8, 20, 3, 58, tzinfo=timezone.utc)
    window = QuotaWindow("codex-weekly", "每周额度", 25, resets_at=reset)
    data = TokenData(
        status="ok",
        quota_windows=[window],
        per_provider=[
            PerProviderData("codex", "Codex", quota_windows=[window], status="ok")
        ],
    )
    with patch("ui.qt_widget.FloatingWidget.refresh"):
        widget = FloatingWidget()
        widget._data = data
        widget._refreshing = False
        widget._apply_update()

    assert widget.ball._quota_mode
    assert widget.ball._quota_remaining == 75
    assert widget.ball._quota_title == "周额度"
    assert widget.ball._quota_reset_text == "8月20日11:58"
    assert widget.ball.accessibleDescription() == "75%"

    widget._data = sample_data()
    widget._apply_update()
    assert not widget.ball._quota_mode
    assert widget.ball.toolTip() == ""
    widget._closed = True
    widget.hide()


def test_cursor_ball_reuses_quota_mode_for_success_and_unavailable_states():
    reset = datetime.now(timezone.utc) + timedelta(days=12)
    window = QuotaWindow("cursor-monthly", "每月额度", 42, resets_at=reset)
    data = TokenData(
        status="ok",
        quota_windows=[window],
        per_provider=[
            PerProviderData("cursor", "Cursor", quota_windows=[window], status="ok")
        ],
    )
    with patch("ui.qt_widget.FloatingWidget.refresh"):
        widget = FloatingWidget()
        widget._data = data
        widget._refreshing = False
        widget._apply_update()

    assert widget.ball._quota_mode
    assert widget.ball._quota_remaining == 58
    assert widget.ball._quota_title == "每月额度"

    widget._data = TokenData(
        status="partial",
        per_provider=[PerProviderData("cursor", "Cursor", status="partial")],
    )
    widget._apply_update()
    assert widget.ball._quota_mode
    assert widget.ball._quota_remaining is None
    assert widget.ball._quota_title == "每月额度"
    assert widget.ball._quota_reset_text == "额度暂不可用"
    assert "$" not in widget.ball.toolTip()
    widget._closed = True
    widget.hide()


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


def test_minute_chart_aggregates_all_models_in_one_and_multi_minute_buckets():
    chart = MinuteUsageChart()
    rows = [
        {"minute": 600, "token_type": "RESPONSE_TOKEN", "token_amount": 10},
        {"minute": 604, "token_type": "RESPONSE_TOKEN", "token_amount": 8},
        {"minute": 605, "token_type": "RESPONSE_TOKEN", "token_amount": 4},
    ]
    model_rows = [
        {
            "minute": 600,
            "model": "model-b",
            "cache_hit_tokens": 0,
            "cache_miss_tokens": 0,
            "output_tokens": 5,
            "cost_cny": Decimal("0.01"),
        },
        {
            "minute": 600,
            "model": "model-a",
            "cache_hit_tokens": 0,
            "cache_miss_tokens": 0,
            "output_tokens": 5,
            "cost_cny": Decimal("0.01"),
        },
        {
            "minute": 604,
            "model": "model-a",
            "cache_hit_tokens": 0,
            "cache_miss_tokens": 0,
            "output_tokens": 8,
            "cost_cny": Decimal("0.02"),
        },
        {
            "minute": 605,
            "model": "model-c",
            "cache_hit_tokens": 0,
            "cache_miss_tokens": 0,
            "output_tokens": 4,
            "cost_cny": Decimal("0.03"),
        },
    ]
    chart.set_rows(rows, "recorded", interval_minutes=5, model_rows=model_rows)

    assert chart._model_names[chart._bucket_index_for_minute(600)] == [
        "model-a",
        "model-b",
    ]
    assert "模型　model-a、model-b" in chart.tooltip_text(604)
    assert "模型　model-c" in chart.tooltip_text(605)
    chart._show_hover(600, QPoint(120, 50))
    assert chart.hover_tooltip.model_label.text() == "model-a、model-b"
    assert not chart.hover_tooltip.model_row.isHidden()

    chart.set_rows(rows, "recorded", interval_minutes=1)
    assert "模型　" not in chart.tooltip_text(600)
    chart._show_hover(600, QPoint(120, 50))
    assert chart.hover_tooltip.model_row.isHidden()
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
    assert panel._minute_chart is None
    panel.minute_activity_button.click()
    assert panel.activity_stack.currentIndex() == 1
    assert panel.minute_activity_button.isChecked()
    assert panel._minute_chart is not None
    assert not panel.minute_estimate_label.isHidden()
    assert panel.minute_estimate_label.text() == "估算"
    assert "按刷新间隔均摊" in panel.minute_estimate_label.toolTip()
    assert not panel.activity_summary.text().startswith("估算")
    panel.resize(820, panel.height())
    APP.processEvents()
    assert not panel.minute_estimate_label.isHidden()
    assert [
        panel.minute_legend_buttons[token_type].text()
        for token_type, _label in MinuteUsageChart.SERIES
    ] == ["命中缓存", "未命中", "输出"]
    assert "峰值" in panel.minute_chart.summary_text()
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


def test_minute_chart_model_tooltip_stays_single_line_and_keeps_cost_visible():
    chart = MinuteUsageChart()
    chart.resize(900, 220)
    chart.set_rows(
        [{"minute": 600, "token_type": "RESPONSE_TOKEN", "token_amount": 1}],
        "recorded",
        cost_rows=[{"minute": 600, "cost_cny": Decimal("0.2833")}],
        currency="USD",
        model_rows=[
            {
                "minute": 600,
                "model": "gpt-5.6-terra",
                "cache_hit_tokens": 0,
                "cache_miss_tokens": 0,
                "output_tokens": 1,
            }
        ],
    )
    chart.show()
    APP.processEvents()

    chart._show_hover(600, QPoint(chart.plot.width() - 1, chart.plot.height() - 1))
    tooltip = chart.hover_tooltip
    APP.processEvents()

    assert tooltip.model_label.text() == "gpt-5.6-terra"
    assert not tooltip.model_label.wordWrap()
    assert tooltip.model_label.height() <= tooltip.model_label.fontMetrics().height() + 2
    cost_bottom = tooltip.cost_label.mapTo(
        tooltip, tooltip.cost_label.rect().bottomRight()
    ).y()
    assert cost_bottom < tooltip.contentsRect().bottom()
    assert tooltip.y() + tooltip.height() <= chart.plot.height() - 6
    chart.close()


def test_nayuto_reuses_amount_ball_and_exact_minute_ui_with_usd_precision():
    data = TokenData(
        currency="USD",
        today_cost_cny=0.0861,
        balance_cny=9.0961378,
        today_tokens=10,
        status="ok",
        minute_usage=[
            {
                "minute": 600,
                "token_type": "PROMPT_CACHE_HIT_TOKEN",
                "token_amount": 3,
            },
            {
                "minute": 600,
                "token_type": "PROMPT_CACHE_MISS_TOKEN",
                "token_amount": 2,
            },
            {"minute": 600, "token_type": "RESPONSE_TOKEN", "token_amount": 5},
        ],
        minute_cost_usage=[
            {"minute": 600, "cost_cny": Decimal("0.0861")}
        ],
        minute_model_usage=[
            {
                "minute": 600,
                "model": "model-a",
                "cache_hit_tokens": 3,
                "cache_miss_tokens": 2,
                "output_tokens": 5,
                "cost_cny": Decimal("0.0861"),
            }
        ],
        minute_usage_status="recorded",
        minute_usage_date="2026-08-15",
        minute_usage_days=["2026-08-15"],
        minute_usage_source="provider",
        per_provider=[
            PerProviderData(
                "nayuto",
                "NayutoAI",
                currency="USD",
                today_cost_cny=0.0861,
                balance_cny=9.0961378,
                status="ok",
            )
        ],
    )
    with patch("ui.qt_widget.FloatingWidget.refresh"):
        widget = FloatingWidget()
        widget._data = data
        widget._refreshing = False
        widget._apply_update()

    assert widget.ball._quota_mode is False
    assert widget.ball._primary_label == "今日使用"
    assert widget.ball._secondary_label == "余额"
    assert widget.ball._today == "$0.09"
    assert widget.ball._balance == "$9.10"

    panel = MainPanel()
    panel.update_data(data)
    assert panel.provider_quick_combo.findText("NayutoAI") >= 0
    assert panel.minute_estimate_label.text() == "平台明细"
    assert "服务商请求明细" in panel.minute_estimate_label.toolTip()
    with patch("ui.qt_panel.config_manager.get") as get_config:
        get_config.side_effect = lambda key, default=None: (
            1 if key == "MINUTE_USAGE_INTERVAL_MINUTES" else default
        )
        panel._set_activity_view("minute")
        assert "模型　model-a" in panel.minute_chart.tooltip_text(600)
        assert "本分钟消耗金额　$0.0861" in panel.minute_chart.tooltip_text(600)
        panel.minute_chart._show_hover(600, QPoint(120, 50))
        assert panel.minute_chart.hover_tooltip.model_label.text() == "model-a"
        assert panel.minute_chart.hover_tooltip.cost_label.text() == "$0.0861"
        assert panel.trend.plot.toolTip() == ""

    panel.close()
    widget._closed = True
    widget.hide()


def test_nayuto_model_views_restore_per_provider_and_history_keeps_minute_models():
    current = date(2026, 8, 15)
    previous = current - timedelta(days=1)
    data = TokenData(
        currency="USD",
        status="ok",
        daily_usage=[
            {"date": previous.isoformat(), "tokens": 6, "cost_cny": Decimal("0.01")},
            {"date": current.isoformat(), "tokens": 10, "cost_cny": Decimal("0.02")},
        ],
        daily_model_usage=[
            {
                "date": previous.isoformat(),
                "models": [
                    {
                        "model": "model-history",
                        "cache_hit_tokens": 1,
                        "cache_miss_tokens": 2,
                        "output_tokens": 3,
                        "total_tokens": 6,
                        "cost_cny": Decimal("0.01"),
                    }
                ],
            },
            {
                "date": current.isoformat(),
                "models": [
                    {
                        "model": "model-current",
                        "cache_hit_tokens": 3,
                        "cache_miss_tokens": 2,
                        "output_tokens": 5,
                        "total_tokens": 10,
                        "cost_cny": Decimal("0.02"),
                    }
                ],
            },
        ],
        minute_usage=[
            {"minute": 600, "token_type": "RESPONSE_TOKEN", "token_amount": 10}
        ],
        minute_model_usage=[
            {
                "minute": 600,
                "model": "model-current",
                "cache_hit_tokens": 0,
                "cache_miss_tokens": 0,
                "output_tokens": 10,
                "cost_cny": Decimal("0.02"),
            }
        ],
        minute_usage_history={
            previous.isoformat(): [
                {"minute": 600, "token_type": "RESPONSE_TOKEN", "token_amount": 6}
            ]
        },
        minute_model_usage_history={
            previous.isoformat(): [
                {
                    "minute": 600,
                    "model": "model-history",
                    "cache_hit_tokens": 0,
                    "cache_miss_tokens": 0,
                    "output_tokens": 6,
                    "cost_cny": Decimal("0.01"),
                }
            ]
        },
        minute_usage_status="recorded",
        minute_usage_date=current.isoformat(),
        minute_usage_days=[previous.isoformat(), current.isoformat()],
        minute_usage_source="provider",
        per_provider=[PerProviderData("nayuto", "NayutoAI", currency="USD", status="ok")],
    )
    panel = MainPanel()
    with patch(
        "ui.qt_panel.config_manager.get",
        side_effect=lambda key, default=None: (
            1
            if key == "MINUTE_USAGE_INTERVAL_MINUTES"
            else 3
            if key == "MINUTE_USAGE_RETENTION_DAYS"
            else default
        ),
    ):
        panel.update_data(data)
        assert not panel.trend.view_segment.isHidden()
        panel.trend.model_button.click()
        assert panel.trend.title.text() == "近 7 天各模型 Token 使用量"

        panel.minute_activity_button.click()
        panel.minute_previous_button.click()
        assert "模型　model-history" in panel.minute_chart.tooltip_text(600)

        other = sample_data()
        other.per_provider = [PerProviderData("deepseek", "DeepSeek", status="ok")]
        panel.update_data(other)
        assert panel.trend.view_segment.isHidden()
        assert panel.trend.title.text() == "近 7 天使用金额"
        assert "模型　" not in panel.minute_chart.tooltip_text(600)

        panel.update_data(data)
        assert panel.trend.model_button.isChecked()
        assert panel.trend.title.text() == "近 7 天各模型 Token 使用量"

    assert panel.activity_stack.count() == 2
    assert panel.annual_activity_button.text() == "年度活动"
    assert panel.minute_activity_button.text() == "今日分时"
    assert len(panel.statistics._values) == 5
    assert panel.status_text.text()
    panel.close()


def test_nayuto_settings_uses_manual_bearer_capture_and_clean_display_name():
    values = {
        **config_manager.all_config(),
        "ACTIVE_PROVIDER": "nayuto",
        "NAYUTO_AUTH": "",
    }
    saved = Mock()
    refreshed = Mock()
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=values),
        patch("ui.qt_settings.config_manager.all_config", return_value=values),
        patch("ui.qt_settings.config_manager.save_config", saved),
    ):
        window = SettingsWindow(on_saved=refreshed)
        assert window.provider_combo.currentText() == "NayutoAI"
        assert window._cookie_acquire_button.text() == "一键获取 Bearer"
        assert window._credential_acquire_automatic is False

        worker = Mock()
        window._cookie_acquire_worker = worker
        window._finish_cookie_acquire()
        assert window._cookie_acquire_status.text() == "正在读取 Bearer…"
        worker.stop_and_collect.assert_called_once_with()
        window._cookie_acquire_worker = None
        window._cookie_acquire_provider_id = "nayuto"
        window._cookie_acquire_failed("预期的采集失败")
        assert window._cookie_acquire_button.text() == "重试获取 Bearer"

        window._apply_acquired_cookie("nayuto", "Bearer synthetic-captured")
        assert window._save_timer.isActive()
        window.flush_pending_saves()
    assert window._provider_widgets["AUTH"].text() == "Bearer synthetic-captured"
    assert window._provider_drafts["nayuto"]["AUTH"] == "Bearer synthetic-captured"
    saved.assert_called_once()
    refreshed.assert_called_once()
    assert saved.call_args.args[0]["NAYUTO_AUTH"] == "Bearer synthetic-captured"
    window.close()


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


def test_minute_date_edit_disables_dates_without_data_and_skips_them():
    picker = MinuteDateEdit()
    picker.setDateRange(QDate(2026, 7, 12), QDate(2026, 7, 15))
    picker.setSelectableDates([QDate(2026, 7, 12), QDate(2026, 7, 15)])
    picker.setDate(QDate(2026, 7, 15))

    assert not picker.popup.calendar.isDateSelectable(QDate(2026, 7, 13))
    picker.previous_button.click()
    assert picker.date() == QDate(2026, 7, 12)

    picker.date_button.click()
    APP.processEvents()
    picker.popup._select_date(QDate(2026, 7, 14))
    assert picker.date() == QDate(2026, 7, 12)
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
        assert panel.minute_date_edit.isEnabled()

    panel.close()


def test_lazy_minute_history_reads_selected_account_day_and_bounds_cache():
    panel = MainPanel()
    data = sample_data()
    data.per_provider = [PerProviderData("mimo", "MiMo")]
    data.account_key = "account-A"
    data.history_provider = "mimo:account-A"
    data.minute_history_complete = False
    data.minute_usage_date = "2026-07-14"
    data.minute_usage_status = "recorded"
    data.minute_usage_days = ["2026-07-12", "2026-07-13", "2026-07-14"]
    data.minute_usage = [{"minute": 10, "token_type": "RESPONSE_TOKEN", "token_amount": 20}]
    with patch("data.history.minute_history_for_day", return_value=(
        [{"minute": 10, "token_type": "RESPONSE_TOKEN", "token_amount": 10}], [], []
    )) as read:
        panel.update_data(data)
        read.assert_not_called()
        panel.minute_previous_button.click()
        read.assert_called_once_with("mimo:account-A", date(2026, 7, 13))
        panel.minute_previous_button.click()
        assert set(panel._minute_usage_history) == {"2026-07-12"}
        bucket = panel.minute_chart._bucket_index_for_minute(10)
        assert panel.minute_chart._values["RESPONSE_TOKEN"][bucket] == 10
        data.history_provider = "mimo:account-B"
        data.account_key = "account-B"
        panel.update_data(data)
        assert panel.minute_date_edit.date() == QDate(2026, 7, 14)
    panel.close()


def test_minute_date_selection_uses_only_dates_reported_with_data():
    panel = MainPanel()
    data = sample_data()
    data.per_provider = [PerProviderData("mimo", "小米 MiMo")]
    data.minute_usage_date = "2026-07-15"
    data.minute_usage_status = "recorded"
    data.minute_usage_days = ["2026-07-13", "2026-07-15"]
    data.minute_usage = [
        {"minute": 10, "token_type": "RESPONSE_TOKEN", "token_amount": 20}
    ]
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
    assert not panel.minute_date_edit.popup.calendar.isDateSelectable(QDate(2026, 7, 14))

    data.minute_usage = []
    panel.update_data(data)
    assert panel.minute_date_edit.date() == QDate(2026, 7, 13)
    assert panel.minute_date_edit.isEnabled()
    assert panel.minute_date_edit.popup.calendar.isDateSelectable(QDate(2026, 7, 15))

    data.minute_usage_history = {}
    panel.update_data(data)
    assert panel.minute_date_edit.date() == QDate(2026, 7, 15)
    assert panel.minute_date_edit.isEnabled()
    assert not panel.minute_date_edit.popup.calendar.isDateSelectable(QDate(2026, 7, 13))
    panel.close()


def test_minute_date_selection_updates_top_usage_card():
    panel = MainPanel()
    data = sample_data()
    data.today_cost_cny = 0.34
    data.today_tokens = 200_000
    data.daily_usage = [
        {"date": "2026-07-13", "tokens": 100_000, "cost_cny": Decimal("0.12")},
        {"date": "2026-07-14", "tokens": 200_000, "cost_cny": Decimal("0.34")},
    ]
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
        side_effect=lambda key, default=None: (
            3 if key == "MINUTE_USAGE_RETENTION_DAYS" else default
        ),
    ):
        panel.update_data(data)
        panel.minute_activity_button.click()
        assert panel.today_card.title_label.text() == "今日使用金额"
        assert panel.today_card.value.text() == "¥0.34"

        panel.minute_previous_button.click()
        assert panel.today_card.title_label.text() == "7月13日使用金额"
        assert panel.today_card.value.text() == "¥0.12"
        assert panel.today_card.detail.text() == "10万"

        panel.annual_activity_button.click()
        assert panel.today_card.title_label.text() == "今日使用金额"
        assert panel.today_card.value.text() == "¥0.34"

        panel.minute_activity_button.click()
        assert panel.today_card.title_label.text() == "7月13日使用金额"

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
    dark_axis_color = panel.trend.plot.getAxis("left").textPen().color()

    try:
        with (
            patch.object(panel, "update_data") as update_data,
            patch.object(panel.trend, "set_rows") as set_rows,
            patch.object(panel.activity, "set_activity") as set_activity,
            patch.object(TokenData, "fetch") as fetch,
        ):
            controller.set_mode("light")
            APP.processEvents()
            light_axis_color = panel.trend.plot.getAxis("left").textPen().color()

            assert id(panel) == panel_identity
            assert panel._resolved_theme == "light"
            assert current_theme().name == "light"
            assert panel.trend.plot.backgroundBrush().style() == Qt.BrushStyle.NoBrush
            assert panel.trend.plot.palette().base().color().alpha() == 0
            assert light_axis_color.name() == current_theme().subtext.lower()
            assert light_axis_color != dark_axis_color

            controller.set_mode("dark")
            APP.processEvents()
            assert panel._resolved_theme == "dark"
            assert panel.trend.plot.getAxis("left").textPen().color() == dark_axis_color
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
        assert widget.panel is None
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


def test_vpet_source_launch_uses_last_successful_side_by_side_build(tmp_path):
    from ui import vpet_host

    build = tmp_path / "build"
    latest = build / "vpet-dev"
    latest.mkdir(parents=True)
    (latest / "TokenMeter.Pet.exe").touch()
    (build / "vpet-active.json").write_text('{"directory":"vpet-dev"}', encoding="utf-8")
    with patch.object(vpet_host, "__file__", str(tmp_path / "ui/vpet_host.py")):
        assert vpet_host.host_executable() == latest / "TokenMeter.Pet.exe"
        (build / "vpet-active.json").write_text('{"directory":"../outside"}', encoding="utf-8")
        assert vpet_host.host_executable() == build / "vpet/TokenMeter.Pet.exe"


def test_vpet_installed_launch_ignores_developer_build_pointer(tmp_path):
    from ui import vpet_host

    with (
        patch.object(vpet_host.sys, "frozen", True, create=True),
        patch.object(vpet_host.sys, "executable", str(tmp_path / "TokenMeter.exe")),
    ):
        assert vpet_host.host_executable() == tmp_path / "pet/TokenMeter.Pet.exe"


def test_vpet_usage_protocol_preserves_quota_and_never_serializes_credentials():
    data = TokenData(status="ok", last_success_at=datetime.now())
    data.per_provider = [PerProviderData("codex", "Codex")]
    data.quota_windows = [QuotaWindow("weekly", "周额度", 95)]
    data.private_credential = "must-not-leave-main-process"
    message = usage_message(data, False, "codex")
    assert message["primary"] == "剩余 5%"
    assert message["warning"] is True
    assert "must-not-leave" not in json.dumps(message)
    assert set(message) == {"type", "provider", "primary", "secondary", "status", "warning"}
    data.quota_windows = []
    data.status = "error"
    message = usage_message(data, False, "codex")
    assert message["primary"] == "--"
    assert "¥" not in json.dumps(message, ensure_ascii=False)
    assert "更新失败" in message["status"]


def test_vpet_usage_protocol_handles_loading_money_and_invalid_quota():
    data = TokenData(status="loading", balance_cny=20, today_cost_cny=1.25)
    assert usage_message(data, True, "deepseek")["primary"] == "余额 --"
    data.status, data.last_success_at = "ok", datetime.now()
    message = usage_message(data, True, "deepseek")
    assert message["primary"] == "余额 ¥20.00"
    assert message["secondary"] == "今日使用 ¥1.25"
    data.quota_windows = [QuotaWindow("weekly", "周额度", float("nan"))]
    assert usage_message(data, False, "codex")["primary"] == "--"


def test_vpet_pipe_buffers_frames_and_accepts_only_fixed_ui_actions():
    host = VPetHost()
    actions, ready = [], []
    host.action_requested.connect(actions.append)
    host.ready.connect(lambda: ready.append(True))
    with patch.object(host, "_send") as send:
        host.update_usage({"type": "usage", "primary": "65%"})
        host.set_visible(False)
        host._consume_output(b'{"event":"rea')
        assert not host.active
        host._consume_output(b'dy","animations":379}\n')
        assert host.active and host.animations == 379
        assert ready == [True]
        assert send.call_args_list[0].args[0]["primary"] == "65%"
        assert send.call_args_list[1].args[0] == {"type": "visibility", "visible": False}
        host._consume_output(b'not-json\n[]\n{"event":"execute","command":"bad"}\n')
        host._consume_output(b'{"event":"open_panel"}\n{"event":"quit"}\n')
        assert actions == ["open_panel", "quit"]
        host.stop()
        host._consume_output(b'{"event":"open_settings"}\n')
        assert actions == ["open_panel", "quit"]


def test_vpet_pricing_outline_is_optional_and_only_for_deepseek_balance():
    data = TokenData(status="ok", last_success_at=datetime.now(), balance_cny=12.8)
    assert "pricing_peak" not in usage_message(data, False, "deepseek")
    for peak in (True, False):
        message = usage_message(data, False, "deepseek", peak)
        assert message["pricing_peak"] is peak
        assert message["primary"] == "余额 ¥12.80"
        assert "pricing_peak" not in usage_message(data, False, "mimo", peak)
    data.quota_windows = [QuotaWindow("weekly", "周额度", 35)]
    assert "pricing_peak" not in usage_message(data, False, "deepseek", True)


def test_vpet_theme_colors_follow_ball_and_update_without_refresh():
    controller = configure_theme(APP, "dark")
    original_light = controller.appearance("light")
    original_dark = controller.appearance("dark")
    with patch.object(FloatingWidget, "refresh") as refresh:
        widget = FloatingWidget()
        widget._data = TokenData(status="ok", quota_windows=[QuotaWindow("weekly", "周额度", 35)])
        widget._vpet.active = True
        refresh.reset_mock()
        try:
            with patch.object(widget._vpet, "update_usage") as send:
                for mode, accent in (("light", "#158568"), ("dark", "#8A4FFF")):
                    controller.set_mode(mode)
                    send.reset_mock()
                    controller.set_appearance(mode, accent, 100)
                    assert send.called
                    message = send.call_args.args[0]
                    palette = message["theme"]
                    theme = current_theme()
                    assert palette["accent"] == accent
                    assert palette["accent_hover"] == theme.accent_hover
                    assert palette["water_top"] == FloatingUsageBall._water_top_color(theme).name()
                    assert palette["water_deep"] == QColor(theme.accent).darker(138).name()
                    assert palette["peak"] == ("#FFB000" if mode == "light" else theme.warning)
                    assert palette["on_accent"] == theme.on_accent
                    assert message["primary"] == "剩余 65%"
                    assert all(QColor(value).isValid() for value in palette.values())
                refresh.assert_not_called()
        finally:
            widget._vpet.active = False
            widget._closed = True
            widget.hide()
            widget.deleteLater()
            controller.set_appearance("light", *original_light)
            controller.set_appearance("dark", *original_dark)
            controller.set_mode("dark")


def test_vpet_missing_executable_and_overflow_fail_once(tmp_path):
    host = VPetHost()
    errors = []
    host.failed.connect(errors.append)
    with patch("ui.vpet_host.host_executable", return_value=tmp_path / "absent.exe"):
        host.start(tmp_path / "state")
    assert len(errors) == 1
    assert not host.active
    host._consume_output(b"x" * 65537)
    assert len(errors) == 1
    assert not host._buffer


def test_vpet_shutdown_requests_save_before_terminating_its_child():
    host = VPetHost()
    from PySide6.QtCore import QProcess
    host.process = Mock()
    host.process.state.return_value = QProcess.ProcessState.Running
    host.process.waitForFinished.side_effect = [False, True]
    host.stop()
    assert json.loads(host.process.write.call_args.args[0]) == {"type": "shutdown"}
    host.process.closeWriteChannel.assert_called_once()
    host.process.kill.assert_called_once()
    assert not host.active


def test_vpet_requires_installed_pack_even_when_enabled_and_local_build_exists(tmp_path, monkeypatch):
    installed = Mock(return_value=None)
    monkeypatch.setattr("ui.qt_widget.pet_extension.installed_manifest", installed)
    executable = tmp_path / "TokenMeter.Pet.exe"
    executable.touch()
    config_manager.save_config({"VPET_ENABLED": True})
    with (
        patch.object(FloatingWidget, "refresh"),
        patch.object(VPetHost, "start") as start,
        patch("ui.vpet_host.host_executable", return_value=executable),
    ):
        widget = FloatingWidget()
        try:
            start.assert_not_called()
            assert widget.isVisible() and widget.ball.isVisible()
            installed.return_value = {"version": "0.1.0"}
            widget._sync_vpet()
            start.assert_called_once()
            widget._vpet.active = True
            widget._on_vpet_ready()
            installed.return_value = None
            widget._sync_vpet()
            assert not widget._vpet.active
            assert widget.isVisible() and widget.ball.isVisible()
        finally:
            widget._closed = True
            widget.hide()
            widget.deleteLater()


def test_vpet_widget_keeps_ball_until_ready_and_restores_it_on_failure(monkeypatch):
    monkeypatch.setattr("ui.qt_widget.pet_extension.installed_manifest", lambda: {"version": "0.1.0"})
    config_manager.save_config({"VPET_ENABLED": True})
    with patch.object(FloatingWidget, "refresh"), patch.object(VPetHost, "start") as start:
        widget = FloatingWidget()
    try:
        start.assert_called_once()
        assert widget.isVisible()
        widget._vpet._consume_output(b'{"event":"ready","animations":379}\n')
        assert widget._vpet.active and not widget.isVisible()
        widget._on_vpet_action("open_panel")
        assert widget._expanded and widget.isVisible()
        widget.collapse_panel()
        assert not widget.isVisible()
        widget._vpet._fail("test failure")
        assert widget.isVisible() and not widget._vpet.active
        assert widget.ball.isVisible()
    finally:
        widget._closed = True
        widget.hide()
        widget.deleteLater()


def test_vpet_tray_visibility_and_disable_restore_existing_ui():
    with patch.object(FloatingWidget, "refresh"):
        widget = FloatingWidget()
    try:
        widget._vpet.active = True
        widget._on_vpet_ready()
        with patch.object(widget._vpet, "_send") as send:
            widget.set_visible_from_tray()
            assert not widget._vpet.visible
            assert not widget.isVisible()
            widget.set_visible_from_tray()
            assert widget._vpet.visible
            assert send.call_args.args[0] == {"type": "visibility", "visible": True}
        widget._on_vpet_action("disable_pet")
        assert not config_manager.get("VPET_ENABLED")
        assert not widget._vpet.active and widget.isVisible()
    finally:
        widget._closed = True
        widget.hide()
        widget.deleteLater()


def test_vpet_uninstall_stops_host_before_removing_pack_and_preserves_panel():
    config_manager.save_config({"VPET_ENABLED": True})
    with patch.object(FloatingWidget, "refresh"), patch.object(VPetHost, "start"):
        widget = FloatingWidget()
        widget._vpet.active = True
        widget._on_vpet_ready()
        widget.open_settings()
    window = widget._settings_window
    panel = widget.panel
    try:
        def remove(operation):
            assert operation == "uninstall"
            assert not widget._vpet.active
            assert not config_manager.get("VPET_ENABLED")
            assert widget.panel is panel
        with (
            patch("ui.qt_settings.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes),
            patch.object(window, "_start_pet_task", side_effect=remove) as uninstall,
            patch.object(FloatingWidget, "refresh"),
        ):
            window._uninstall_pet()
        uninstall.assert_called_once()
        widget.collapse_panel()
        assert widget.isVisible() and widget.ball.isVisible()
    finally:
        widget._closed = True
        widget.hide()
        widget.deleteLater()


@pytest.mark.parametrize("failed", [False, True])
def test_pet_update_pauses_only_host_and_restores_enabled_preference(failed, monkeypatch):
    from ui.qt_settings import _PetExtensionWorker
    from updater.client import UpdateError

    monkeypatch.setattr("ui.qt_widget.pet_extension.installed_manifest", lambda: {"version": "0.1.0"})
    config_manager.save_config({"VPET_ENABLED": True})
    with patch.object(FloatingWidget, "refresh"), patch.object(VPetHost, "start"):
        widget = FloatingWidget()
        widget.open_settings()
    window = widget._settings_window
    panel = widget.panel
    original = config_manager.all_config()
    try:
        with (patch.object(_PetExtensionWorker, "start"),
              patch.object(widget._vpet, "stop") as stop,
              patch.object(widget._vpet, "start") as start):
            window._start_pet_task("update")
            assert widget._vpet_updating
            stop.assert_called_once()
            widget._sync_vpet()
            start.assert_not_called()
            assert widget.panel is panel and widget.isVisible()
            assert config_manager.all_config() == original
            worker = window._pet_worker
            worker.error = UpdateError("offline") if failed else None
            window._pet_task_finished()
            assert not widget._vpet_updating
            start.assert_called_once()
            assert config_manager.all_config() == original
    finally:
        widget._closed = True
        widget.hide()
        widget.deleteLater()


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


def test_panel_resizes_in_settings_without_refresh_and_restores_after_collapse(tmp_path):
    with (
        # 验证关闭时落盘，但不让应用退出事件关闭后续用例创建的窗口。
        patch.object(APP, "quit"),
        patch("ui.qt_widget.FloatingWidget.refresh") as refresh,
        patch.object(config_manager, "WIDGET_STATE_PATH", tmp_path / "widget-state.json"),
        patch.object(FloatingWidget, "_work_area", return_value=WorkArea(0, 0, 1920, 1080)),
    ):
        widget = FloatingWidget()
        widget.open_settings()
        settings = widget._settings_window
        refresh.reset_mock()
        APP.processEvents()
        handle = widget._panel_resize_handles[1]
        point = QPoint(3, 80)
        QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseMove(handle, point - QPoint(120, 0))
        QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=point)
        assert widget.width() == widget.panel.width() == 700
        assert not settings._save_pending
        assert widget._panel_width_save_timer.isActive()
        refresh.assert_not_called()
        settings.reject()
        widget.panel.minute_activity_button.click()
        assert widget.size() == QSize(700, PANEL_HEIGHT)
        widget.collapse_panel()
        assert widget.width() == widget._compact_size()
        assert all(handle.isHidden() for handle in widget._panel_resize_handles)
        widget.expand_panel()
        assert widget.width() == 700
        # 关闭发生在防抖保存之前时，也必须留下最后一次宽度。
        widget.close()
        assert config_manager.load_panel_width() == 700
        APP.processEvents()
        assert widget.isHidden()
        widget.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        restored = FloatingWidget()
        restored.expand_panel()
        assert restored.width() == 700
        restored.close()
        restored.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("left", [True, False])
def test_panel_edges_resize_and_keep_opposite_edge_anchored(left, tmp_path):
    with (
        patch.object(APP, "quit"),
        patch("ui.qt_widget.FloatingWidget.refresh"),
        patch.object(config_manager, "WIDGET_STATE_PATH", tmp_path / "widget-state.json"),
        patch.object(FloatingWidget, "_work_area", return_value=WorkArea(-1920, 0, 0, 1080)),
    ):
        widget = FloatingWidget()
        widget.expand_panel()
        widget.move(-1400, 120)
        APP.processEvents()
        origin_x = widget.x()
        handle = widget._panel_resize_handles[0 if left else 1]
        assert handle.isVisible()
        point = QPoint(3, 80)
        assert widget.childAt(handle.mapTo(widget, point)) is handle
        QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseMove(handle, point + QPoint(120 if left else -120, 0))
        QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=point)
        assert widget.width() == 700
        assert widget.x() == origin_x + (120 if left else 0)
        assert widget._panel_resize_origin is None
        widget._set_panel_width(1)
        assert widget.width() == 640
        widget._set_panel_width(5000)
        assert widget.width() == 820
        widget.close()
        widget.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_panel_width_is_clamped_to_work_area_without_losing_saved_preference(tmp_path):
    with (
        patch.object(APP, "quit"),
        patch("ui.qt_widget.FloatingWidget.refresh"),
        patch.object(config_manager, "WIDGET_STATE_PATH", tmp_path / "widget-state.json"),
        patch.object(FloatingWidget, "_work_area", return_value=WorkArea(0, 0, 760, 900)),
    ):
        config_manager.save_panel_width(810)
        widget = FloatingWidget()
        widget.expand_panel()
        assert widget.width() == 744
        assert not widget._panel_width_save_timer.isActive()
        assert config_manager.load_panel_width() == 810
        widget._set_panel_width(820)
        assert widget.width() == 744
        assert widget.x() >= 8
        assert widget.x() + widget.width() <= 752
        widget.close()
        widget.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("view", ["compact", "annual", "minute"])
def test_settings_open_inside_panel_without_changing_expanded_geometry(view):
    with patch("ui.qt_widget.FloatingWidget.refresh"):
        widget = FloatingWidget()
        try:
            if view != "compact":
                widget.expand_panel()
                widget.panel._set_activity_view(view)
            geometry = widget.geometry()
            widget.open_settings()
            APP.processEvents()
            settings = widget._settings_window

            # 不只检查置顶标志：设置必须是面板子控件，不能再生成独立原生窗口。
            assert not settings.isWindow()
            assert settings.window() is widget
            assert settings not in APP.topLevelWidgets()
            assert widget.panel.content_stack.currentWidget() is settings
            assert widget.panel.header.isVisible()
            back = widget.panel.settings_back_button
            back_center = back.mapTo(widget.panel.header, back.rect().center())
            assert abs(back_center.y() - widget.panel.header.rect().center().y()) <= 1
            assert back_center.x() < widget.panel.header.width() // 2
            assert settings.isVisible()
            assert widget._expanded
            assert widget.ball.isHidden()
            assert widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
            if view != "compact":
                assert widget.geometry() == geometry

            for index in range(settings.tabs.count()):
                settings.tabs.setCurrentIndex(index)
                APP.processEvents()
                assert isinstance(settings.tabs.widget(index), QScrollArea)
                assert widget.rect().contains(settings.mapTo(widget, settings.rect().bottomRight()))
        finally:
            widget._closed = True
            widget.hide()


def test_settings_return_to_panel_and_keep_drafts_after_collapse():
    values = {**config_manager.all_config(), "ACTIVE_PROVIDER": "deepseek"}
    with (
        patch("ui.qt_widget.FloatingWidget.refresh"),
        patch("ui.qt_settings.config_manager.load_config", return_value=values),
        patch("ui.qt_settings.config_manager.all_config", return_value=values),
    ):
        widget = FloatingWidget()
        try:
            widget.open_settings()
            settings = widget._settings_window
            settings._provider_widgets["AUTH"].setText("draft-token")

            widget.collapse_panel()
            APP.processEvents()
            assert not settings.isVisible()
            widget.expand_panel()
            APP.processEvents()
            assert settings.isVisible()

            widget.panel.settings_back_button.click()
            assert not settings.isVisible()
            assert widget._expanded
            assert widget.panel.content_stack.currentIndex() == 0
            widget.open_settings()
            assert widget._settings_window is settings
            assert settings.isVisible()
            assert settings._provider_widgets["AUTH"].text() == "draft-token"
        finally:
            widget._closed = True
            widget.hide()


@pytest.mark.parametrize("auto_collapse", [True, False])
def test_settings_deactivation_follows_panel_preference(auto_collapse):
    with patch("ui.qt_widget.FloatingWidget.refresh"):
        widget = FloatingWidget()
        try:
            widget.open_settings()
            settings = widget._settings_window
            QTest.keyClicks(settings._provider_widgets["AUTH"], "synthetic-focus-token")
            assert settings._save_timer.isActive()
            values = {
                **config_manager.all_config(),
                "PANEL_AUTO_COLLAPSE_ON_DEACTIVATE": auto_collapse,
            }
            with (
                patch("ui.qt_widget.config_manager.get", side_effect=values.get),
                patch.object(widget, "isActiveWindow", return_value=False),
                patch("ui.qt_widget.QApplication.activeModalWidget", return_value=None),
                patch("ui.qt_widget.QApplication.activePopupWidget", return_value=None),
            ):
                widget._collapse_after_deactivation()

            assert widget._expanded is (not auto_collapse)
            assert widget.ball.isVisible() is auto_collapse
            assert settings.isVisible() is (not auto_collapse)
            if auto_collapse:
                assert config_manager.get("DEEPSEEK_AUTH") == "synthetic-focus-token"
                assert not settings._save_timer.isActive()
                assert widget.panel.content_stack.currentIndex() == 0
                widget.expand_panel()
                assert widget.panel.isVisible()
                assert widget.panel.provider_quick_combo.isVisible()
                assert settings.isHidden()
                assert widget.panel.content_stack.currentIndex() == 0
            else:
                assert config_manager.get("DEEPSEEK_AUTH") == ""
                assert widget.panel.content_stack.currentWidget() is settings
        finally:
            widget._closed = True
            widget.hide()


@pytest.mark.skipif(APP.platformName() != "windows", reason="Requires native Windows focus handling")
@pytest.mark.parametrize("modal", [False, True])
def test_settings_activation_ignores_child_dialog_but_not_other_windows(modal):
    values = {**config_manager.all_config(), "PANEL_AUTO_COLLAPSE_ON_DEACTIVATE": True}
    with (
        patch("ui.qt_widget.FloatingWidget.refresh"),
        patch("ui.qt_widget.config_manager.get", side_effect=values.get),
    ):
        widget = FloatingWidget()
        widget.open_settings()
        settings = widget._settings_window
        other = QDialog(settings) if modal else QWidget()
        if modal:
            other.setModal(True)
        try:
            APP.processEvents()
            other.show()
            other.activateWindow()
            assert QTest.qWaitForWindowActive(other, 1000)
            APP.processEvents()

            assert widget._expanded is modal
            assert settings.isVisible() is modal
            if not modal:
                assert widget.panel.content_stack.currentIndex() == 0
        finally:
            other.close()
            widget._closed = True
            widget.hide()


def test_closing_widget_also_hides_embedded_settings():
    with (
        patch("ui.qt_widget.FloatingWidget.refresh"),
        patch("ui.qt_widget.config_manager.save_widget_position"),
        patch.object(APP, "quit") as quit_app,
    ):
        widget = FloatingWidget()
        try:
            widget.open_settings()
            settings = widget._settings_window
            widget.close()

            assert not settings.isVisible()
            quit_app.assert_called_once()
        finally:
            widget._closed = True
            widget.hide()


@pytest.mark.skipif(APP.platformName() != "windows", reason="Requires native Windows focus handling")
def test_settings_provider_dropdown_keeps_panel_open():
    with patch("ui.qt_widget.FloatingWidget.refresh"):
        widget = FloatingWidget()
        try:
            widget.open_settings()
            combo = widget._settings_window.provider_combo
            combo.showPopup()
            APP.processEvents()
            assert QApplication.activePopupWidget() is not None
            assert widget._expanded
            combo.hidePopup()
            APP.processEvents()
            assert widget._settings_window.isVisible()
        finally:
            widget._closed = True
            widget.hide()


def test_compact_ball_uses_smaller_size_and_keeps_free_drag_position():
    with (
        patch("ui.qt_widget.FloatingWidget.refresh"),
        patch("ui.qt_widget.config_manager.load_widget_size", return_value=None),
    ):
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


def test_ball_wheel_resize_emits_vertical_steps_and_keeps_corner_drag_behavior():
    ball = FloatingUsageBall(88)
    ball.show()
    APP.processEvents()
    pressed: list[QPoint] = []
    dragged: list[QPoint] = []
    released: list[QPoint] = []
    resize_steps: list[int] = []
    ball.pressed.connect(pressed.append)
    ball.dragged.connect(dragged.append)
    ball.released.connect(released.append)
    ball.resize_requested.connect(resize_steps.append)

    def mouse_event(local: QPoint, global_point: QPoint, event_type: str) -> Mock:
        event = Mock()
        event.button.return_value = (
            Qt.MouseButton.LeftButton
            if event_type in {"press", "release"}
            else Qt.MouseButton.NoButton
        )
        event.buttons.return_value = (
            Qt.MouseButton.LeftButton
            if event_type in {"press", "move"}
            else Qt.MouseButton.NoButton
        )
        event.position.return_value = QPointF(local)
        event.globalPosition.return_value = QPointF(global_point)
        return event

    for delta in (60, 60, -240):
        wheel_event = Mock()
        wheel_event.angleDelta.return_value = QPoint(0, delta)
        ball.wheelEvent(wheel_event)
        wheel_event.accept.assert_called_once_with()
    assert resize_steps == [1, -2]

    horizontal_event = Mock()
    horizontal_event.angleDelta.return_value = QPoint(120, 0)
    ball.wheelEvent(horizontal_event)
    horizontal_event.ignore.assert_called_once_with()
    assert resize_steps == [1, -2]

    former_handle = QPoint(68, 68)
    origin = QPoint(600, 400)
    ball.mousePressEvent(mouse_event(former_handle, origin, "press"))
    ball.mouseMoveEvent(
        mouse_event(former_handle, origin + QPoint(20, 20), "move")
    )
    ball.mouseReleaseEvent(
        mouse_event(former_handle, origin + QPoint(20, 20), "release")
    )

    assert pressed == [origin]
    assert dragged == [origin + QPoint(20, 20)]
    assert released == [origin + QPoint(20, 20)]
    ball.close()


def test_ball_wheel_resize_clamps_size_and_window_to_work_area():
    with (
        patch("ui.qt_widget.FloatingWidget.refresh"),
        patch("ui.qt_widget.config_manager.load_widget_size", return_value=None),
    ):
        widget = FloatingWidget()
    work = WorkArea(0, 0, 300, 220)
    widget.move(204, 124)

    with (
        patch.object(widget, "_work_area", return_value=work),
        patch("ui.qt_widget.config_manager.save_widget_position") as save_position,
        patch("ui.qt_widget.config_manager.save_widget_size") as save_size,
    ):
        widget.ball.resize_requested.emit(1)
        assert (widget.width(), widget.height()) == (92, 92)
        assert (widget.ball.width(), widget.ball.height()) == (92, 92)
        assert (widget.x(), widget.y()) == (200, 120)

        widget.ball.resize_requested.emit(20)
        assert (widget.width(), widget.height()) == (124, 124)
        assert (widget.ball.width(), widget.ball.height()) == (124, 124)
        assert (widget.x(), widget.y()) == (168, 88)

        widget.ball.resize_requested.emit(-20)
        assert (widget.width(), widget.height()) == (72, 72)
        assert (widget.ball.width(), widget.ball.height()) == (72, 72)
        assert (widget.x(), widget.y()) == (194, 114)

        assert widget._ball_size_save_timer.isActive()
        widget._ball_size_save_timer.stop()
        widget._save_ball_size()
        save_position.assert_called_once_with(194, 114)
        save_size.assert_called_once_with(72)

        widget.ball.resize_requested.emit(-1)
        assert not widget._ball_size_save_timer.isActive()

    widget._closed = True
    widget.hide()


def test_compact_ball_restores_saved_resize_state():
    with (
        patch("ui.qt_widget.FloatingWidget.refresh"),
        patch("ui.qt_widget.config_manager.load_widget_size", return_value=104),
    ):
        widget = FloatingWidget()

    assert (widget.width(), widget.height()) == (104, 104)
    assert (widget.ball.width(), widget.ball.height()) == (104, 104)
    widget._closed = True
    widget.hide()


@pytest.mark.parametrize("active_child", ["activeModalWidget", "activePopupWidget"])
def test_deactivation_collapses_panel_but_ignores_active_dialogs(active_child):
    with patch("ui.qt_widget.TokenData.fetch", return_value=sample_data()):
        widget = FloatingWidget()
        widget.expand_panel()

        with patch("ui.qt_widget.config_manager.get", return_value=True):
            with (
                patch.object(widget, "isActiveWindow", return_value=False),
                patch(f"ui.qt_widget.QApplication.{active_child}", return_value=Mock()),
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
                patch("ui.qt_widget.QApplication.activeModalWidget", return_value=None),
                patch("ui.qt_widget.QApplication.activePopupWidget", return_value=None),
            ):
                widget._collapse_after_deactivation()
            assert widget._expanded

            with (
                patch.object(widget, "isActiveWindow", return_value=False),
                patch("ui.qt_widget.QApplication.activeModalWidget", return_value=None),
                patch("ui.qt_widget.QApplication.activePopupWidget", return_value=None),
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


def test_escape_returns_from_settings_before_collapsing_panel():
    with patch("ui.qt_widget.FloatingWidget.refresh"):
        widget = FloatingWidget()
        widget.open_settings()
        settings = widget._settings_window
        APP.processEvents()

        QTest.keyClick(settings, Qt.Key.Key_Escape)
        assert settings.isHidden()
        assert widget._expanded
        assert widget.panel.content_stack.currentIndex() == 0

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


@pytest.mark.parametrize(
    ("size", "visible_extent"),
    ((72, 12), (88, 14), (96, 15), (104, 16), (124, 16)),
)
@pytest.mark.parametrize("direction", ("left", "right"))
def test_edge_hide_uses_size_aware_visible_extent_without_fading(
    size: int, visible_extent: int, direction: str
) -> None:
    with (
        patch("ui.qt_widget.FloatingWidget.refresh"),
        patch("ui.qt_widget.config_manager.load_widget_size", return_value=size),
    ):
        widget = FloatingWidget()
    work = WorkArea(0, 0, 1920, 1080)
    start_x = 0 if direction == "left" else work.right - size
    hidden_x = (
        work.left - size + visible_extent
        if direction == "left"
        else work.right - visible_extent
    )
    widget.move(start_x, 200)
    widget._edge_snapped = True
    widget._edge_direction = direction

    with patch.object(widget, "_work_area", return_value=work):
        widget._do_edge_hide()

        assert widget._edge_hidden is True
        assert widget._edge_visible_extent() == visible_extent
        assert widget._edge_reveal_extent() == 40
        assert widget._edge_animation.endValue() == QPoint(hidden_x, 200)
        assert widget.windowOpacity() == pytest.approx(1.0)

        widget._edge_restore()
        assert widget._edge_hidden is False
        assert widget.windowOpacity() == pytest.approx(1.0)

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
            assert widget.windowOpacity() == pytest.approx(1.0)

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


def test_settings_loads_and_persists_background_provider_selection():
    values = {
        **config_manager.all_config(),
        "ACTIVE_PROVIDER": "deepseek",
        "BACKGROUND_PROVIDER_IDS": ["codex", "mimo"],
    }
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=values),
        patch("ui.qt_settings.config_manager.all_config", return_value=values),
    ):
        window = SettingsWindow()
        assert window.background_provider_checks["codex"].isChecked()
        assert window.background_provider_checks["mimo"].isChecked()
        assert not window.background_provider_checks["deepseek"].isChecked()

        window.background_provider_checks["mimo"].setChecked(False)
        assert window._values()["BACKGROUND_PROVIDER_IDS"] == ["codex"]
        window.close()


def test_settings_codex_home_uses_read_only_directory_picker():
    configured = r"C:\Users\example\.codex\auth.json"
    selected = r"D:\CodexData"
    values = {
        **config_manager.all_config(),
        "ACTIVE_PROVIDER": "codex",
        "CODEX_HOME": configured,
    }
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=values),
        patch("ui.qt_settings.config_manager.all_config", return_value=values),
        patch(
            "ui.qt_settings.QFileDialog.getExistingDirectory",
            return_value=selected,
        ) as choose_directory,
    ):
        window = SettingsWindow()
        editor = window._provider_widgets["HOME"]
        browse_button = window.findChild(QPushButton, "credentialDirectoryBrowseButton")
        default_button = window.findChild(QPushButton, "credentialDirectoryDefaultButton")

        assert isinstance(editor, QLineEdit)
        assert editor.isReadOnly()
        assert browse_button is not None
        assert default_button is not None
        assert editor.text() == r"C:\Users\example\.codex"
        browse_button.click()
        assert editor.text() == selected
        assert window._values()["CODEX_HOME"] == selected
        choose_directory.assert_called_once_with(
            window,
            "选择Codex 目录（可选）",
            r"C:\Users\example\.codex",
        )

        default_button.click()
        assert editor.text() == ""
        window.close()


def test_settings_cursor_global_storage_uses_existing_directory_field():
    configured = r"C:\Users\example\AppData\Roaming\Cursor\User\globalStorage"
    values = {
        **config_manager.all_config(),
        "ACTIVE_PROVIDER": "cursor",
        "CURSOR_GLOBAL_STORAGE": configured,
    }
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=values),
        patch("ui.qt_settings.config_manager.all_config", return_value=values),
    ):
        window = SettingsWindow()
        editor = window._provider_widgets["GLOBAL_STORAGE"]

        assert window.provider_combo.currentData() == "cursor"
        assert isinstance(editor, QLineEdit)
        assert editor.isReadOnly()
        assert editor.text() == configured
        assert window._values()["CURSOR_GLOBAL_STORAGE"] == configured
        assert window.findChild(QPushButton, "credentialDirectoryBrowseButton") is not None
        assert window.findChild(QPushButton, "credentialDirectoryDefaultButton") is not None
        window.close()


def test_connection_test_rejects_untrusted_base_without_starting_worker():
    values = {**config_manager.all_config(), "ACTIVE_PROVIDER": "deepseek"}
    original = config_manager.all_config()
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=values),
        patch("ui.qt_settings.config_manager.all_config", return_value=values),
        patch(
            "ui.qt_settings.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ) as question,
        patch("ui.qt_settings.ConnectionWorker") as worker,
    ):
        window = SettingsWindow()
        window._provider_widgets["BASE"].setText("https://untrusted.example")
        window._test_connection()

    question.assert_called_once()
    worker.assert_not_called()
    assert config_manager.all_config() == original
    window.close()


def test_connection_test_passes_read_only_snapshot_without_changing_global_config():
    values = {**config_manager.all_config(), "ACTIVE_PROVIDER": "deepseek"}
    original = config_manager.all_config()
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=values),
        patch("ui.qt_settings.config_manager.all_config", return_value=values),
        patch(
            "ui.qt_settings.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ),
        patch("ui.qt_settings.ConnectionWorker") as worker_cls,
    ):
        window = SettingsWindow()
        window._provider_widgets["AUTH"].setText("draft-auth")
        window._provider_widgets["BASE"].setText("https://untrusted.example")
        window._test_connection()

    snapshot = worker_cls.call_args.args[0]
    assert snapshot["DEEPSEEK_AUTH"] == "draft-auth"
    with pytest.raises(TypeError):
        snapshot["DEEPSEEK_AUTH"] = "changed"
    worker_cls.return_value.start.assert_called_once()
    assert config_manager.all_config() == original
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


def test_codex_water_ball_renders_quota_level_in_dark_and_light_themes(qtbot):
    controller = configure_theme(APP, "dark")
    ball = FloatingUsageBall(88)
    ball.set_quota_state(72, "2 天 8 小时后重置", "每周额度")
    ball.show()
    APP.processEvents()
    dark_image = ball.grab().toImage()
    dark_empty = dark_image.pixelColor(44, 15)
    dark_water = dark_image.pixelColor(44, 68)

    try:
        initial_phase = ball._wave_phase
        # CI 负载下定时器可能延后投递；等待实际帧推进，不能依赖固定 26ms 睡眠。
        qtbot.waitUntil(
            lambda: ball._wave_timer.interval() == 40 and ball._wave_phase != initial_phase,
            timeout=1000,
        )
        assert ball._wave_timer.isActive()
        assert ball._wave_timer.interval() == 40
        assert ball._wave_timer.timerType() == Qt.TimerType.PreciseTimer
        assert ball._wave_phase != initial_phase
        assert dark_water.blue() > dark_water.red()
        assert dark_water != dark_empty
        assert ball._quota_reset_text == "2天 8小时后重置"
        assert ball.toolTip() == "72%"
        assert ball.accessibleDescription() == "72%"

        controller.set_mode("light")
        APP.processEvents()
        light_image = ball.grab().toImage()
        light_empty = light_image.pixelColor(44, 15)
        light_water = light_image.pixelColor(44, 68)

        assert light_empty.lightness() > dark_empty.lightness()
        assert light_water.blue() > light_water.red()
        assert light_water != dark_water

        ball.set_quota_state(0, "即将重置", "周额度")
        APP.processEvents()
        empty_bottom = ball.grab().toImage().pixelColor(44, 68)
        assert empty_bottom != light_water
        assert not ball._wave_timer.isActive()
    finally:
        controller.set_mode("dark")
        ball.close()


def test_codex_water_ball_uses_custom_accent_for_water_and_border():
    controller = configure_theme(APP, "dark")
    controller.set_appearance("dark", "#D14C2F", 100)
    ball = FloatingUsageBall(88)
    ball.set_quota_state(72, "2 天后重置", "周额度")
    ball.show()
    APP.processEvents()
    try:
        image = ball.grab().toImage()
        water = image.pixelColor(44, 68)
        border = image.pixelColor(44, 2)

        assert water.red() > water.blue()
        assert border.red() > border.blue()
    finally:
        controller.set_appearance("dark", DARK_THEME.accent, 100)
        ball.close()


def test_codex_water_ball_pointer_impulse_propagates_and_settles():
    ball = FloatingUsageBall(88)
    ball.set_quota_state(50, "2 小时后重置")
    ball.show()
    APP.processEvents()
    ball._wave_timer.stop()

    pointer = QPointF(18, 70)
    ball.enterEvent(QEnterEvent(pointer, pointer, pointer))
    QTest.qWait(10)
    disturbed = ball._disturb_surface_from_pointer(QPointF(70, 70))
    initial_activity = ball._liquid_surface.activity
    peak_height = 0.0
    peak_trough = 0.0
    for _ in range(60):
        ball._liquid_surface.step(0.016)
        peak_height = max(peak_height, max(ball._liquid_surface.heights))
        peak_trough = min(peak_trough, min(ball._liquid_surface.heights))

    assert disturbed
    assert initial_activity > 0.02
    assert peak_height > 0.005
    assert peak_trough < -0.005

    ball.leaveEvent(QEvent(QEvent.Type.Leave))
    for _ in range(420):
        ball._liquid_surface.step(0.016)

    assert ball._liquid_surface.settled
    ball.close()


def test_codex_water_ball_pointer_speed_scales_the_surface_impulse():
    surface = LiquidSurfaceState()
    surface.disturb(6.5, 0.4, 1)
    slow_impulse = surface.activity
    surface.reset()
    surface.disturb(6.5, 5.0, 1)
    fast_impulse = surface.activity

    assert fast_impulse > slow_impulse * 8


def test_codex_water_ball_visual_split_has_trough_shoulders_and_rebound():
    surface = LiquidSurfaceState()
    surface.disturb(6.5, 5.0, 1)

    assert surface.node_count == 14
    assert surface.velocities[6] > 0
    assert surface.velocities[7] > 0
    assert surface.velocities[4] < 0
    assert surface.velocities[9] < surface.velocities[4]

    center_history = []
    for _ in range(90):
        surface.step(0.016)
        center_history.append((surface.heights[6] + surface.heights[7]) / 2)
    assert max(center_history) > 0.02
    assert min(center_history) < 0


def test_codex_water_ball_animation_timer_drops_to_idle_cadence_after_settling():
    ball = FloatingUsageBall(88)
    ball.set_quota_state(83, "2 小时后重置")
    ball._liquid_surface.disturb(6.5, 2.5, 1)
    ball._ensure_animation()

    for _ in range(600):
        ball._advance_wave()
        if ball._wave_timer.interval() == 40:
            break

    assert ball._liquid_surface.settled
    assert ball._wave_timer.isActive()
    assert ball._wave_timer.interval() == 40
    ball.close()


def test_codex_water_ball_idle_wave_is_low_amplitude_and_blends_back_after_interaction():
    ball = FloatingUsageBall(124)
    rect = QRectF(8, 8, 104, 104)
    initial_offsets = ball._idle_surface_offsets(rect)

    peak_to_peak = max(initial_offsets) - min(initial_offsets)
    assert 3.5 < peak_to_peak < 5.2
    assert max(initial_offsets) != min(initial_offsets)
    assert sum(initial_offsets) == pytest.approx(0)
    for _ in range(200):
        ball._liquid_surface.step(0.05)
    offsets_after_ten_seconds = ball._idle_surface_offsets(rect)
    assert offsets_after_ten_seconds != initial_offsets
    assert max(offsets_after_ten_seconds) != min(offsets_after_ten_seconds)

    ball._liquid_surface.disturb(6.5, 5.0, 1)
    active_weight = ball._liquid_surface.idle_weight
    for _ in range(240):
        ball._liquid_surface.step(0.016)

    assert active_weight <= 0.28
    assert ball._liquid_surface.idle_weight > 0.9
    ball.close()


def test_codex_water_ball_idle_surface_wave_travels_horizontally_without_level_drift():
    ball = FloatingUsageBall(124)
    rect = QRectF(8, 8, 104, 104)
    first_offsets = ball._idle_surface_offsets(rect)
    first_peak = max(range(len(first_offsets)), key=first_offsets.__getitem__)

    ball._liquid_surface.idle_phase += 1.2
    next_offsets = ball._idle_surface_offsets(rect)
    next_peak = max(range(len(next_offsets)), key=next_offsets.__getitem__)

    assert next_offsets != first_offsets
    assert next_peak != first_peak
    assert sum(first_offsets) == pytest.approx(0)
    assert sum(next_offsets) == pytest.approx(0)
    ball.close()


def test_codex_water_ball_idle_flow_gets_stronger_as_quota_decreases():
    assert FloatingUsageBall._idle_flow_scale(0.10) > FloatingUsageBall._idle_flow_scale(0.50)
    assert FloatingUsageBall._idle_flow_scale(0.50) > FloatingUsageBall._idle_flow_scale(1.0)
    assert FloatingUsageBall._idle_flow_scale(0.33) == pytest.approx(2.005)
    # 接近空额度时优先保护真实液位，不能继续无限放大波浪。
    assert FloatingUsageBall._idle_flow_scale(0.01) < FloatingUsageBall._idle_flow_scale(0.10)
    assert FloatingUsageBall._idle_flow_scale(0) == 0


def test_codex_water_ball_idle_flow_gets_faster_as_quota_decreases():
    ball = FloatingUsageBall(88)
    ball.set_quota_state(33, "2 小时后重置")

    assert FloatingUsageBall._idle_flow_speed(0.33) == pytest.approx(2.5075)
    assert ball._liquid_surface.idle_speed == pytest.approx(2.5075)

    initial_phase = ball._liquid_surface.idle_phase
    ball._liquid_surface.step(0.04)
    assert ball._liquid_surface.idle_phase - initial_phase == pytest.approx(0.1003)

    ball.set_quota_state(0, "即将重置")
    assert FloatingUsageBall._idle_flow_speed(0) == 0
    assert ball._liquid_surface.idle_speed == 0
    assert not ball._wave_timer.isActive()
    ball.close()


@pytest.mark.parametrize("remaining", [98, 100])
def test_codex_high_water_keeps_visible_travelling_surface_wave(remaining):
    ball = FloatingUsageBall(124)
    ball.set_quota_state(remaining, "2 小时后重置")
    inner = ball._liquid_inner_rect()
    ratio = remaining / 100
    surface_y = ball._visual_surface_y(inner, ratio)

    _, first_surface = ball._surface_paths(inner, surface_y, ratio)
    first_y = [first_surface.elementAt(index).y for index in range(first_surface.elementCount())]
    ball._liquid_surface.idle_phase += 1.2
    _, next_surface = ball._surface_paths(inner, surface_y, ratio)
    next_y = [next_surface.elementAt(index).y for index in range(next_surface.elementCount())]

    assert max(first_y) - min(first_y) > 0.35
    assert next_y != first_y
    ball.close()


def test_codex_high_water_observation_band_stays_narrow_and_monotonic():
    ball = FloatingUsageBall(124)
    inner = ball._liquid_inner_rect()
    surface_90 = ball._visual_surface_y(inner, 0.90)
    surface_98 = ball._visual_surface_y(inner, 0.98)
    surface_100 = ball._visual_surface_y(inner, 1.0)

    assert surface_90 > surface_98 > surface_100
    assert surface_100 - inner.top() == pytest.approx(7.0)
    ball.close()


def test_codex_water_ball_idle_does_not_inject_random_physics_motion():
    surface = LiquidSurfaceState()
    for _ in range(250):
        surface.step(0.04)

    assert surface.heights == pytest.approx([0] * surface.node_count)
    assert surface.velocities == pytest.approx([0] * surface.node_count)


def test_codex_water_ball_material_gets_darker_toward_the_bottom():
    ball = FloatingUsageBall(88)
    ball.set_quota_state(83, "2 小时后重置")
    ball.show()
    APP.processEvents()
    ball._wave_timer.stop()
    image = ball.grab().toImage()

    top_water = image.pixelColor(22, 21)
    middle_water = image.pixelColor(22, 55)
    bottom_water = image.pixelColor(44, 78)

    assert top_water.blue() > top_water.red()
    assert top_water.lightness() > middle_water.lightness() > bottom_water.lightness()
    ball.close()


def test_codex_water_ball_vertical_drag_acceleration_compresses_and_rebounds():
    ball = FloatingUsageBall(88)
    ball._liquid_surface.add_drag_acceleration(0, 60)

    assert ball._liquid_surface.vertical_compression > 0
    center_velocity = ball._liquid_surface.velocities[ball._liquid_surface.node_count // 2]
    edge_velocity = ball._liquid_surface.velocities[0]
    assert center_velocity * edge_velocity < 0
    ball.close()


def test_codex_water_ball_drag_impulse_sloshes_water_and_then_settles():
    ball = FloatingUsageBall(88)
    ball.set_quota_state(50, "2 小时后重置")
    ball.show()
    APP.processEvents()
    ball._wave_timer.stop()

    def drag_event(global_x: int, event_type: str) -> Mock:
        event = Mock()
        event.button.return_value = (
            Qt.MouseButton.LeftButton
            if event_type in {"press", "release"}
            else Qt.MouseButton.NoButton
        )
        event.buttons.return_value = (
            Qt.MouseButton.LeftButton
            if event_type in {"press", "move"}
            else Qt.MouseButton.NoButton
        )
        event.position.return_value = QPointF(44, 44)
        event.globalPosition.return_value = QPointF(global_x, 400)
        return event

    ball.mousePressEvent(drag_event(600, "press"))
    ball.mouseMoveEvent(drag_event(648, "move"))
    assert ball._liquid_surface.drag_tilt > 0.1
    for _ in range(8):
        ball._liquid_surface.step(0.016)
    assert ball._liquid_surface.heights[0] < ball._liquid_surface.heights[-1]
    rightward_edge_delta = (
        ball._liquid_surface.heights[-1] - ball._liquid_surface.heights[0]
    )

    ball.mouseMoveEvent(drag_event(560, "move"))
    assert ball._liquid_surface.drag_tilt < 0
    for _ in range(8):
        ball._liquid_surface.step(0.016)
    reversed_edge_delta = (
        ball._liquid_surface.heights[-1] - ball._liquid_surface.heights[0]
    )
    assert reversed_edge_delta < rightward_edge_delta

    ball.mouseReleaseEvent(drag_event(560, "release"))
    first_edge_delta = (
        ball._liquid_surface.heights[-1] - ball._liquid_surface.heights[0]
    )
    edge_deltas = []
    for _ in range(80):
        ball._liquid_surface.step(0.016)
        edge_deltas.append(
            ball._liquid_surface.heights[-1] - ball._liquid_surface.heights[0]
        )
    assert any(delta * first_edge_delta < 0 for delta in edge_deltas)

    for _ in range(220):
        ball._liquid_surface.step(0.016)
    assert ball._liquid_surface.activity < 0.025
    ball.close()


def test_codex_water_ball_surface_uses_smooth_cubic_curve():
    rect = QRectF(8, 8, 104, 104)
    offsets = [
        math.sin(index / 13 * math.tau) * 8
        for index in range(14)
    ]
    surface = FloatingUsageBall._smooth_surface_path(
        rect,
        60,
        offsets,
    )

    assert surface.elementCount() == 1 + (len(offsets) - 1) * 3
    assert any(
        surface.elementAt(index).type == QPainterPath.ElementType.CurveToElement
        for index in range(surface.elementCount())
    )


def test_codex_water_ball_pointer_only_disturbs_actual_water_region():
    ball = FloatingUsageBall(88)
    ball.set_quota_state(10, "2 小时后重置")
    ball.show()
    APP.processEvents()
    ball._wave_timer.stop()

    air_start = QPointF(24, 30)
    ball.enterEvent(QEnterEvent(air_start, air_start, air_start))
    QTest.qWait(10)
    disturbed_air = ball._disturb_surface_from_pointer(QPointF(64, 30))
    ball._pointer_last_local = QPointF(28, 76)
    ball._pointer_clock.restart()
    QTest.qWait(10)
    disturbed_water = ball._disturb_surface_from_pointer(QPointF(60, 76))

    assert not disturbed_air
    assert disturbed_water
    assert ball._liquid_surface.activity > 0
    ball.close()


@pytest.mark.parametrize("remaining", [100, 50, 10])
def test_codex_water_ball_render_is_identical_at_wave_loop_boundary(remaining):
    ball = FloatingUsageBall(88)
    ball.set_quota_state(remaining, "2 小时后重置")
    ball.show()
    APP.processEvents()
    ball._wave_timer.stop()

    ball._wave_phase = 0
    first_frame = ball.grab().toImage()
    ball._wave_phase = math.tau
    last_frame = ball.grab().toImage()

    assert last_frame == first_frame
    ball.close()


def test_codex_full_quota_surface_reacts_and_uses_white_text_in_light_theme():
    controller = configure_theme(APP, "dark")
    ball = FloatingUsageBall(88)
    ball.set_quota_state(100, "5 天后重置")
    ball.show()
    APP.processEvents()
    ball._wave_timer.stop()
    first_frame = ball.grab().toImage()

    try:
        start = QPointF(18, 44)
        ball.enterEvent(QEnterEvent(start, start, start))
        QTest.qWait(10)
        assert ball._disturb_surface_from_pointer(QPointF(70, 44))
        for _ in range(12):
            ball._advance_wave()
        APP.processEvents()
        next_frame = ball.grab().toImage()

        assert next_frame != first_frame
        full_water = next_frame.pixelColor(44, 15)
        assert full_water.blue() > full_water.red()

        controller.set_mode("light")
        APP.processEvents()
        light_image = ball.grab().toImage()
        white_text_pixels = sum(
            1
            for y in range(28, 60)
            for x in range(12, 76)
            if light_image.pixelColor(x, y).lightness() > 225
        )
        assert white_text_pixels > 40
    finally:
        controller.set_mode("dark")
        ball.close()


def test_codex_water_ball_high_level_flow_weight_transitions_continuously():
    assert FloatingUsageBall._high_level_factor(0.80) == 0
    assert FloatingUsageBall._high_level_factor(0.90) == pytest.approx(0.5)
    assert 0.5 < FloatingUsageBall._high_level_factor(0.98) < 1
    assert FloatingUsageBall._high_level_factor(1.0) == 1


def test_codex_high_water_pointer_injects_internal_tail_flow_from_any_water_depth():
    ball = FloatingUsageBall(88)
    ball.set_quota_state(98, "2 小时后重置")
    ball.show()
    APP.processEvents()
    ball._wave_timer.stop()

    start = QPointF(18, 48)
    ball.enterEvent(QEnterEvent(start, start, start))
    QTest.qWait(10)
    disturbed = ball._disturb_surface_from_pointer(QPointF(70, 48))
    initial_center = QPointF(ball._internal_flow_center)
    initial_strength = ball._internal_flow_strength

    assert disturbed
    assert initial_strength > 0.2
    assert ball._internal_flow_velocity.x() > 0
    for _ in range(30):
        ball._advance_internal_flow(0.016)
    assert ball._internal_flow_center.x() > initial_center.x()
    assert 0 < ball._internal_flow_strength < initial_strength

    ball.close()


@pytest.mark.parametrize("remaining", [98, 100])
def test_codex_high_water_keeps_visible_surface_idle_motion(remaining):
    ball = FloatingUsageBall(88)
    ball.set_quota_state(remaining, "2 小时后重置")
    ball.show()
    APP.processEvents()
    ball._wave_timer.stop()
    first_frame = ball.grab().toImage()

    for _ in range(40):
        ball._advance_wave()
    APP.processEvents()
    next_frame = ball.grab().toImage()
    changed_surface_pixels = sum(
        1
        for y in range(4, 24)
        for x in range(8, 80)
        if first_frame.pixelColor(x, y) != next_frame.pixelColor(x, y)
    )

    assert changed_surface_pixels > 10
    ball.close()


def test_area_conserving_liquid_motion_is_enabled_only_for_codex():
    ball = FloatingUsageBall(88)
    ball.set_quota_state(50, "2 小时后重置")
    inner = ball._liquid_inner_rect()
    legacy_surface_y = ball._visual_surface_y(inner, 0.5)
    _, legacy_surface = ball._surface_paths(inner, legacy_surface_y, 0.5)

    ball.set_motion_provider("codex")
    _, codex_surface = ball._surface_paths(inner, legacy_surface_y, 0.5)
    assert ball.realistic_motion_enabled
    assert ball.begin_container_motion(QPointF(0, 0))
    assert codex_surface.elementCount() != legacy_surface.elementCount()

    ball.set_motion_provider("cursor")
    _, cursor_surface = ball._surface_paths(inner, legacy_surface_y, 0.5)
    assert not ball.realistic_motion_enabled
    assert not ball.begin_container_motion(QPointF(0, 0))
    assert cursor_surface.elementCount() == legacy_surface.elementCount()
    assert ball._codex_motion.settled
    ball.close()


def test_codex_circle_area_stays_equal_to_quota_across_tilts_and_small_waves():
    ball = FloatingUsageBall(88)
    ball.set_motion_provider("codex")
    inner = QRectF(8, 8, 104, 104)
    clip = QPainterPath()
    clip.addEllipse(inner)
    ball._liquid_surface.heights = [
        math.sin(index / 13 * math.tau) * 0.045 for index in range(14)
    ]

    def sampled_ratio(path: QPainterPath) -> float:
        water = 0
        circle = 0
        samples = 180
        for y_index in range(samples):
            y = inner.top() + inner.height() * (y_index + 0.5) / samples
            for x_index in range(samples):
                x = inner.left() + inner.width() * (x_index + 0.5) / samples
                point = QPointF(x, y)
                if not clip.contains(point):
                    continue
                circle += 1
                water += path.contains(point)
        return water / circle

    high_water_samples = []
    for ratio in (0.06, 0.5, 0.94, 1.0):
        for angle in (-math.radians(12), 0.0, math.radians(12)):
            ball._codex_motion.angle = angle
            water_path, _ = ball._codex_surface_paths(inner, ratio)
            actual = sampled_ratio(water_path)
            assert actual == pytest.approx(ratio, abs=0.008)
            if ratio == 0.94:
                high_water_samples.append(actual)
            if ratio == 1.0:
                assert clip.subtracted(water_path).isEmpty()

    assert max(high_water_samples) - min(high_water_samples) < 0.004
    ball.close()


def test_codex_motion_uses_acceleration_and_settles_after_constant_speed_stop():
    ball = FloatingUsageBall(88)
    ball.set_motion_provider("codex")
    ball.set_quota_state(94, "2 小时后重置")
    assert ball.begin_container_motion(QPointF(0, 0))

    accelerations = []
    angles = []
    for index in range(1, 41):
        assert ball.sample_container_motion(QPointF(index * 4, 0), 0.016)
        accelerations.append(abs(ball._container_acceleration.x()))
        angles.append(ball._codex_motion.angle)

    assert max(angles) > math.radians(0.5)
    assert abs(angles[-1]) < max(abs(angle) for angle in angles)
    assert accelerations[-1] < accelerations[0] * 0.08

    angle_before_stop = ball._codex_motion.angle
    assert ball.end_container_motion(QPointF(160, 0), 0.016)
    assert ball._codex_motion.external_acceleration_x < 0
    rebound_angles = []
    for _ in range(75):
        ball._codex_motion.step(0.016)
        rebound_angles.append(ball._codex_motion.angle)
    assert any(angle < angle_before_stop for angle in rebound_angles)
    assert abs(rebound_angles[-1]) < math.radians(0.2)
    assert ball._codex_motion.settled
    ball.close()


def test_codex_motion_reverses_without_exploding_and_respects_tilt_limit():
    motion = CodexLiquidMotion()
    motion.apply_container_acceleration(48)
    for _ in range(20):
        motion.step(0.016)
    positive_angle = motion.angle

    motion.apply_container_acceleration(-48)
    reversed_angles = []
    for _ in range(80):
        motion.step(0.016)
        reversed_angles.append(motion.angle)

    assert positive_angle > 0
    assert min(reversed_angles) < 0
    assert all(math.isfinite(angle) for angle in reversed_angles)
    assert max(abs(angle) for angle in reversed_angles) <= math.radians(12)

    motion.apply_container_acceleration(float("nan"))
    motion.step(10)
    assert math.isfinite(motion.angle)
    assert math.isfinite(motion.angular_velocity)


def test_codex_fixed_step_is_nearly_independent_of_render_interval():
    def simulate(frame_seconds: float) -> tuple[float, float]:
        motion = CodexLiquidMotion()
        motion.apply_container_acceleration(36)
        elapsed = 0.0
        while elapsed + frame_seconds <= 0.96 + 1e-9:
            motion.step(frame_seconds)
            elapsed += frame_seconds
        if elapsed < 0.96:
            motion.step(0.96 - elapsed)
        return motion.angle, motion.angular_velocity

    reference = simulate(0.008)
    for interval in (0.016, 0.024, 0.032):
        result = simulate(interval)
        assert result[0] == pytest.approx(reference[0], abs=math.radians(0.08))
        assert result[1] == pytest.approx(reference[1], abs=math.radians(0.5))


def test_codex_effective_gravity_and_drag_input_scale_with_ball_diameter():
    horizontal = CodexLiquidMotion()
    horizontal.apply_container_acceleration(3, 0)
    reduced_gravity = CodexLiquidMotion()
    reduced_gravity.apply_container_acceleration(3, 30)
    for _ in range(12):
        horizontal.step(0.016)
        reduced_gravity.step(0.016)
    assert reduced_gravity.angle > horizontal.angle > 0

    def first_sample(size: int) -> tuple[float, float]:
        ball = FloatingUsageBall(size)
        ball.set_motion_provider("codex")
        ball.set_quota_state(50, "2 小时后重置")
        ball.begin_container_motion(QPointF(0, 0))
        ball.sample_container_motion(QPointF(size * 0.3, 0), 0.04)
        result = (ball._container_acceleration.x(), ball._codex_motion.angle)
        ball.close()
        return result

    small = first_sample(88)
    large = first_sample(176)
    assert large[0] == pytest.approx(small[0])
    assert large[1] == pytest.approx(small[1])


def test_codex_zero_unknown_and_hidden_states_stop_motion_timer():
    ball = FloatingUsageBall(88)
    ball.set_motion_provider("codex")
    ball.set_quota_state(50, "2 小时后重置")
    ball.show()
    APP.processEvents()
    ball._codex_motion.apply_container_acceleration(36)
    ball._ensure_animation()
    assert ball._wave_timer.isActive()

    ball.hide()
    APP.processEvents()
    assert not ball._wave_timer.isActive()
    assert ball._codex_motion.settled

    ball.show()
    ball.set_quota_state(None, "额度暂不可用")
    assert not ball._wave_timer.isActive()
    assert ball._codex_motion.settled
    ball.set_quota_state(0, "即将重置")
    assert not ball._wave_timer.isActive()
    ball.close()


def test_floating_widget_reports_actual_window_positions_to_codex_motion():
    with patch("ui.qt_widget.FloatingWidget.refresh"):
        widget = FloatingWidget()
    widget.move(420, 260)

    with (
        patch.object(widget.ball, "begin_container_motion") as begin_motion,
        patch.object(widget.ball, "sample_container_motion") as sample_motion,
        patch.object(widget.ball, "end_container_motion") as end_motion,
        patch.object(widget, "_try_edge_snap", return_value=False),
        patch.object(widget, "_clamp_to_work_area"),
    ):
        widget._start_drag(QPoint(600, 400), "ball")
        widget._move_drag(QPoint(640, 430))
        widget._end_drag(QPoint(640, 430))

    begin_motion.assert_called_once_with(QPointF(420, 260))
    sample_motion.assert_called_once_with(QPointF(460, 290))
    end_motion.assert_called_once_with(QPointF(460, 290))
    widget._closed = True
    widget.hide()


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


def test_settings_exposes_persisted_autostart_toggle():
    values = {
        **config_manager.all_config(),
        "AUTO_START_ENABLED": True,
    }
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=values),
        patch("ui.qt_settings.config_manager.all_config", return_value=values),
    ):
        window = SettingsWindow()

    assert window.autostart_check.isChecked()
    assert window.autostart_check.text() == "开机后自动运行 TokenMeter"
    window.autostart_check.setChecked(False)
    assert window._values()["AUTO_START_ENABLED"] is False
    window.close()


def test_settings_applies_autostart_change_when_saving():
    values = {
        **config_manager.all_config(),
        "AUTO_START_ENABLED": False,
    }
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=values),
        patch("ui.qt_settings.config_manager.all_config", return_value=values),
        patch("ui.qt_settings.config_manager.pending_data_dir", return_value=None),
        patch("ui.qt_settings.config_manager.data_dir_migration_error", return_value=""),
    ):
        window = SettingsWindow()
        window.autostart_check.setChecked(True)
        with (
            patch("ui.qt_settings.config_manager.get", return_value=False),
            patch(
                "ui.qt_settings.config_manager.validate_data_dir_target",
                return_value=config_manager.CONFIG_DIR.resolve(strict=False),
            ),
            patch("ui.qt_settings.config_manager.save_config") as save_config,
            patch("ui.qt_settings.sync_autostart") as sync_autostart,
        ):
            window._save()

    sync_autostart.assert_called_once_with(True)
    assert save_config.call_args.args[0]["AUTO_START_ENABLED"] is True
    window.close()


def test_settings_reconciles_autostart_when_preference_is_unchanged():
    values = {
        **config_manager.all_config(),
        "AUTO_START_ENABLED": True,
    }
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=values),
        patch("ui.qt_settings.config_manager.all_config", return_value=values),
        patch("ui.qt_settings.config_manager.pending_data_dir", return_value=None),
        patch("ui.qt_settings.config_manager.data_dir_migration_error", return_value=""),
    ):
        window = SettingsWindow()
        with (
            patch("ui.qt_settings.config_manager.get", return_value=True),
            patch(
                "ui.qt_settings.config_manager.validate_data_dir_target",
                return_value=config_manager.CONFIG_DIR.resolve(strict=False),
            ),
            patch("ui.qt_settings.config_manager.save_config"),
            patch("ui.qt_settings.sync_autostart") as sync_autostart,
        ):
            window._save()

    sync_autostart.assert_called_once_with(True)
    window.close()


def test_settings_rolls_back_autostart_when_config_save_fails():
    values = {
        **config_manager.all_config(),
        "AUTO_START_ENABLED": False,
    }
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=values),
        patch("ui.qt_settings.config_manager.all_config", return_value=values),
        patch("ui.qt_settings.config_manager.pending_data_dir", return_value=None),
        patch("ui.qt_settings.config_manager.data_dir_migration_error", return_value=""),
    ):
        window = SettingsWindow()
        window.autostart_check.setChecked(True)
        with (
            patch("ui.qt_settings.config_manager.get", return_value=False),
            patch(
                "ui.qt_settings.config_manager.validate_data_dir_target",
                return_value=config_manager.CONFIG_DIR.resolve(strict=False),
            ),
            patch("ui.qt_settings.config_manager.save_config", side_effect=OSError("failed")),
            patch("ui.qt_settings.sync_autostart") as sync_autostart,
        ):
            window._save()

    assert [call.args for call in sync_autostart.call_args_list] == [(True,), (False,)]
    assert "配置已回滚" in window.save_feedback.text()
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
    assert "2N 天" in window.minute_usage_retention_days.toolTip()
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
    assert "立即应用并保存" in window.theme_combo.toolTip()
    window.close()


def test_settings_accent_sync_updates_both_modes_and_saves_policy(tmp_path):
    values = config_manager.validate_config({
        **DEFAULT_CONFIG,
        "UI_LIGHT_ACCENT_COLOR": "#E88298",
        "UI_DARK_ACCENT_COLOR": "#FF55FF",
        "UI_LIGHT_PANEL_OPACITY": 80,
        "UI_DARK_PANEL_OPACITY": 95,
    })
    controller = configure_theme(
        APP, "dark", light_accent=values["UI_LIGHT_ACCENT_COLOR"],
        dark_accent=values["UI_DARK_ACCENT_COLOR"],
        light_panel_opacity=80, dark_panel_opacity=95, sync_accent=True,
    )
    config_path = tmp_path / "config.json"
    with (
        patch.object(config_manager, "_config", values),
        patch.object(config_manager, "CONFIG_PATH", config_path),
        patch.object(config_manager, "load_config", side_effect=config_manager.all_config),
        patch("ui.qt_widget.FloatingWidget.refresh"),
    ):
        widget = FloatingWidget()
        widget.open_settings()
        window = widget._settings_window
        try:
            assert window.sync_accent_check.isChecked()
            assert window.accent_color_edit.text() == "#E88298"
            window.accent_color_edit.setText("#3154A2")
            assert controller.appearance("light") == ("#3154A2", 80)
            assert controller.appearance("dark") == ("#3154A2", 95)
            window._commit_appearance()
            window.theme_combo.setCurrentIndex(window.theme_combo.findData("light"))
            assert window.accent_color_edit.text() == "#3154A2"
            window.sync_accent_check.click()
            window.accent_color_edit.setText("#E88298")
            window._commit_appearance()
            assert controller.appearance("dark") == ("#3154A2", 95)
            assert not json.loads(config_path.read_text())["UI_SYNC_ACCENT_COLOR"]
            window.sync_accent_check.click()
            assert controller.appearance("dark") == ("#E88298", 95)
            saved = json.loads(config_path.read_text())
            assert saved["UI_SYNC_ACCENT_COLOR"]
            assert saved["UI_LIGHT_ACCENT_COLOR"] == saved["UI_DARK_ACCENT_COLOR"] == "#E88298"
            with patch.object(config_manager, "save_ui_appearance", side_effect=OSError("disk full")):
                window.sync_accent_check.click()
            assert window.sync_accent_check.isChecked()
            assert controller.sync_accent
            assert window.save_feedback.property("tone") == "danger"
            window.reset_appearance_button.click()
            assert controller.appearance("light") == (LIGHT_THEME.accent, 100)
            assert controller.appearance("dark") == (LIGHT_THEME.accent, 95)
        finally:
            window._appearance_save_timer.stop()
            widget._closed = True
            widget.hide()
            widget.deleteLater()
            configure_theme(APP, "dark")


def test_settings_theme_palette_previews_saves_and_resets_current_resolved_theme():
    values = {
        **config_manager.all_config(),
        "UI_THEME": "dark",
        "UI_DARK_ACCENT_COLOR": "#3478F6",
        "UI_DARK_PANEL_OPACITY": 100,
    }
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=values),
        patch("ui.qt_settings.config_manager.all_config", return_value=values),
    ):
        window = SettingsWindow()

    previews: list[tuple[str, str, int]] = []
    saves: list[tuple[str, str, int]] = []
    window.appearance_preview_requested.connect(
        lambda theme, color, opacity: previews.append((theme, color, opacity))
    )
    window.appearance_requested.connect(
        lambda theme, color, opacity: saves.append((theme, color, opacity))
    )

    window.accent_color_edit.setText("#D14C2F")
    window.panel_opacity_slider.setValue(82)
    window._commit_appearance()

    assert previews[-1] == ("dark", "#D14C2F", 82)
    assert saves[-1] == ("dark", "#D14C2F", 82)
    assert window.panel_opacity_label.text() == "82%"

    window._reset_appearance()
    assert previews[-1] == ("dark", DARK_THEME.accent, 100)
    assert saves[-1] == ("dark", DARK_THEME.accent, 100)
    window.close()


@pytest.fixture
def custom_color_settings(tmp_path):
    original_colors = [QColorDialog.customColor(index) for index in range(QColorDialog.customCount())]
    with patch.object(config_manager, "CONFIG_PATH", tmp_path / "config.json"):
        window = SettingsWindow()
        try:
            yield window
        finally:
            window.close()
            # QColorDialog 色板是进程级状态，必须恢复以免污染其他 UI 用例。
            for index, color in enumerate(original_colors):
                QColorDialog.setCustomColor(index, color)


@pytest.mark.parametrize("accepted", [True, False])
def test_settings_custom_colors_survive_restart_even_when_dialog_is_cancelled(
    custom_color_settings, accepted
):
    window = custom_color_settings
    original_accent = window.accent_color_edit.text()
    expected = ["#FFFFFF"] * QColorDialog.customCount()
    expected[0] = "#D14C2F"
    expected[-1] = "#198754"
    appearances = []
    window.appearance_requested.connect(lambda *args: appearances.append(args))

    def add_colors(*_args):
        QColorDialog.setCustomColor(0, QColor(expected[0]))
        QColorDialog.setCustomColor(len(expected) - 1, QColor(expected[-1]))
        return QColor(expected[0]) if accepted else QColor()

    with patch("ui.qt_settings.QColorDialog.getColor", side_effect=add_colors):
        window._choose_accent_color()

    saved = json.loads(config_manager.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["UI_CUSTOM_COLORS"] == expected
    assert window.accent_color_edit.text() == (expected[0] if accepted else original_accent)
    assert len(appearances) == int(accepted)
    window.close()

    # 模拟重启：清除 Qt 色板，仅从磁盘配置恢复，不借助之前的全局颜色缓存。
    for index in range(QColorDialog.customCount()):
        QColorDialog.setCustomColor(index, QColor("#FFFFFF"))
    config_manager._config = config_manager.validate_config(config_manager._load_public_config())
    reopened = SettingsWindow()

    def inspect_restored_colors(*_args):
        assert [
            QColorDialog.customColor(index).name().upper()
            for index in range(QColorDialog.customCount())
        ] == expected
        return QColor()

    try:
        with (
            patch("ui.qt_settings.QColorDialog.getColor", side_effect=inspect_restored_colors),
            patch.object(config_manager, "save_ui_custom_colors") as save_colors,
        ):
            reopened._choose_accent_color()
            save_colors.assert_not_called()
    finally:
        reopened.close()


def test_settings_custom_colors_unchanged_does_not_write_config(custom_color_settings):
    with patch("ui.qt_settings.QColorDialog.getColor", return_value=QColor()):
        custom_color_settings._choose_accent_color()
    assert not config_manager.CONFIG_PATH.exists()


def test_settings_custom_colors_save_failure_is_visible(custom_color_settings):
    def add_color(*_args):
        QColorDialog.setCustomColor(0, QColor("#D14C2F"))
        return QColor("#D14C2F")

    with (
        patch("ui.qt_settings.QColorDialog.getColor", side_effect=add_color),
        patch.object(config_manager, "save_ui_custom_colors", side_effect=OSError("disk full")),
    ):
        custom_color_settings._choose_accent_color()

    assert "自定义颜色保存失败" in custom_color_settings.save_feedback.text()
    assert custom_color_settings.save_feedback.property("tone") == "danger"
    assert custom_color_settings.accent_color_edit.text() == "#D14C2F"
    assert not config_manager.CONFIG_PATH.exists()


def test_settings_groups_configuration_into_scrolling_pages_with_separate_pet_page():
    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=config_manager.all_config()),
        patch("ui.qt_settings.config_manager.all_config", return_value=config_manager.all_config()),
    ):
        window = SettingsWindow()

    assert [window.tabs.tabText(index) for index in range(window.tabs.count())] == [
        "账户连接", "外观", "悬浮与启动", "桌宠", "采集与统计", "数据存储", "更新与关于",
    ]
    assert window.tabs.widget(0) is window.scroll_area
    for index, control in (
        (0, window.provider_combo),
        (1, window.theme_combo),
        (2, window.panel_auto_collapse_check),
        (3, window.vpet_check),
        (3, window.pet_version_label),
        (3, window.pet_install_button),
        (4, window.refresh_seconds),
        (4, window.deepseek_peak_pricing_card),
        (5, window.minute_usage_retention_days),
        (5, window.data_dir_edit),
        (6, window.update_card),
    ):
        assert isinstance(window.tabs.widget(index), QScrollArea)
        assert window.tabs.widget(index).isAncestorOf(control)
    assert not any(button.text() == "保存并生效" for button in window.findChildren(QPushButton))
    assert window.test_button.parent() is window.content
    assert window.ball_size_hint.text() == (
        "悬停在悬浮球上时，滚动鼠标滚轮即可调整大小。"
    )
    window.close()


@pytest.fixture
def autosave_settings():
    values = DEFAULT_CONFIG.copy()
    refreshed = Mock()

    def persist(changes):
        values.update(changes)
        return values.copy()

    with (
        patch("ui.qt_settings.config_manager.load_config", return_value=values),
        patch("ui.qt_settings.config_manager.all_config", return_value=values),
        patch("ui.qt_settings.config_manager.get", side_effect=values.get),
        patch("ui.qt_settings.config_manager.pending_data_dir", return_value=None),
        patch("ui.qt_settings.config_manager.save_config", side_effect=persist) as saved,
        patch("ui.qt_settings.config_manager.validate_data_dir_target", return_value=config_manager.CONFIG_DIR),
    ):
        window = SettingsWindow(on_saved=refreshed)
        window.show()
        APP.processEvents()
        try:
            yield window, values, saved, refreshed
        finally:
            window._save_pending = False
            window._save_timer.stop()
            window._appearance_save_timer.stop()
            window.close()


def test_settings_autosave_ignores_loading_and_coalesces_user_input(autosave_settings):
    window, values, saved, refreshed = autosave_settings
    assert not window._save_timer.isActive()
    editor = window._provider_widgets["AUTH"]
    editor.setFocus()
    QTest.keyClicks(editor, "synthetic-token")
    saved.assert_not_called()
    assert window._save_timer.isActive()
    QTest.qWait(750)

    saved.assert_called_once()
    refreshed.assert_called_once()
    assert values["DEEPSEEK_AUTH"] == "synthetic-token"
    assert window.save_feedback.text() == "已自动保存"


def test_settings_switch_autosaves_and_return_flushes_last_edit(autosave_settings):
    window, values, saved, refreshed = autosave_settings
    window.tabs.setCurrentIndex(2)
    window.edge_hide_check.click()
    window.reject()

    saved.assert_called_once()
    refreshed.assert_called_once()
    assert values["EDGE_HIDE_ENABLED"] is False
    assert not window._save_timer.isActive()


def test_settings_vpet_switch_persists_without_changing_ball_preferences(autosave_settings):
    window, values, saved, refreshed = autosave_settings
    with patch("ui.qt_settings.pet_extension.installed_manifest", return_value={"version": "0.1.0"}):
        window._refresh_pet_controls()
    window.vpet_check.click()
    window.flush_pending_saves()
    assert values["VPET_ENABLED"] is True
    assert values["EDGE_HIDE_ENABLED"] is True
    assert values["WIDGET_COMPACT_SIZE"] == 88
    saved.assert_called_once()
    refreshed.assert_called_once()


@pytest.mark.parametrize("width", [560, 820])
@pytest.mark.parametrize("state", ["idle", "update", "download"])
def test_pet_actions_stay_in_one_row_with_visible_version(width, state, tmp_path):
    with (
        patch("ui.qt_settings.pet_extension.installed_manifest", return_value={"version": "0.1.0"}),
        patch("ui.qt_settings.pet_extension.removable_directories", return_value=[tmp_path]),
    ):
        parent = QWidget()
        parent.resize(width, 550)
        window = SettingsWindow(parent, embedded=True)
        try:
            if state == "update":
                window._pet_release = Mock(version="0.2.0")
            elif state == "download":
                window._pet_worker = Mock(operation="install")
            window._refresh_pet_controls()
            window.tabs.setCurrentIndex(3)
            window.resize(width, 550)
            window.show()
            parent.show()
            APP.processEvents()
            assert window.width() == width
            first = window.pet_update_button if state == "update" else window.pet_install_button
            last = window.pet_cancel_button if state == "download" else window.pet_check_button
            buttons = [first, window.pet_uninstall_button, last]
            assert all(button.isVisible() for button in buttons)
            positions = [button.mapTo(window, QPoint(0, 0)) for button in buttons]
            assert len({point.y() for point in positions}) == 1
            for left, right, button in zip(positions, positions[1:], buttons):
                assert left.x() + button.width() < right.x()
            assert positions[-1].x() + last.width() <= window.width()
            assert window.pet_version_label.isVisible()
            assert window.pet_version_label.text() == "桌宠版本：v0.1.0"
            assert window.pet_source_label.isVisible()
            assert window.rect().contains(window.pet_source_label.mapTo(window, window.pet_source_label.rect().bottomRight()))
            assert window.pet_install_button.isHidden() == (state == "update")
            assert window.pet_check_button.isHidden() == (state == "download")
        finally:
            window._pet_worker = None
            window.close()
            parent.close()
            parent.deleteLater()


@pytest.mark.parametrize(("manifest", "expected"), [
    (None, "桌宠版本：未安装"),
    ({"version": "0.1.0"}, "桌宠版本：v0.1.0"),
    ({"app_version": "1.13.2"}, "桌宠版本：旧版（无版本号）"),
])
def test_pet_version_always_describes_installation_state(autosave_settings, manifest, expected):
    window, _values, saved, _refreshed = autosave_settings
    if manifest is None:
        window._pet_release = Mock(version="0.2.0")
    with (
        patch("ui.qt_settings.pet_extension.installed_manifest", return_value=manifest),
        patch("ui.qt_settings.pet_extension.removable_directories", return_value=[]),
    ):
        window._refresh_pet_controls()
    window.tabs.setCurrentIndex(3)
    APP.processEvents()
    assert window.pet_version_label.isVisible()
    assert window.pet_version_label.text() == expected
    assert window.vpet_check.isEnabled() == (manifest is not None)
    assert not window.pet_install_button.isHidden()
    saved.assert_not_called()


def test_settings_autosave_reports_failure_without_success_callback(autosave_settings):
    window, values, saved, refreshed = autosave_settings
    saved.side_effect = OSError("disk full")
    window.edge_hide_check.click()
    window.flush_pending_saves()

    assert values["EDGE_HIDE_ENABLED"] is True
    assert "保存失败" in window.save_feedback.text()
    assert window.save_feedback.property("tone") == "danger"
    refreshed.assert_not_called()


def test_settings_autosave_waits_for_complete_untrusted_address(autosave_settings):
    window, values, saved, refreshed = autosave_settings
    editor = window._provider_widgets["BASE"]
    editor.setFocus()
    editor.selectAll()
    with patch("ui.qt_settings.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as question:
        QTest.keyClicks(editor, "https://untrusted.example")
        QTest.qWait(750)
        question.assert_not_called()
        saved.assert_not_called()
        window.reject()

    question.assert_called_once()
    saved.assert_not_called()
    refreshed.assert_not_called()
    assert values["DEEPSEEK_BASE"] == DEFAULT_CONFIG["DEEPSEEK_BASE"]
    assert window.save_feedback.property("tone") == "danger"


def test_settings_capsule_keeps_full_rounding_at_both_ends():
    with patch("ui.qt_settings.config_manager.load_config", return_value=DEFAULT_CONFIG.copy()):
        window = SettingsWindow()
        window.show()
        APP.processEvents()
        bar = window.tabs.tabBar()
        for index in (0, window.tabs.count() - 1):
            window.tabs.setCurrentIndex(index)
            APP.processEvents()
            image = bar.grab().toImage()
            # 外层左右角都露出面板底色，不能只让首个选中页签变圆而保留直角轨道。
            assert image.pixelColor(0, 0).name() != current_theme().surface.lower()
            assert image.pixelColor(image.width() - 1, 0).name() != current_theme().surface.lower()
            assert bar.tabRect(index).height() == bar.HEIGHT
        window.close()


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("accent", ["#FFADFA", "#FFE58A", "#3154A2"])
def test_settings_switch_keeps_white_thumb_with_custom_accent(mode, accent):
    controller = configure_theme(APP, mode)
    appearance = controller.appearance(mode)
    controller.set_appearance(mode, accent, 100)
    window = SettingsWindow()
    window.tabs.setCurrentIndex(2)
    window.show()
    try:
        for checked, x in ((True, 37), (False, 13)):
            window.edge_hide_check.setChecked(checked)
            APP.processEvents()
            image = window.edge_hide_check.grab().toImage()
            assert image.pixelColor(x, 14).name() == "#ffffff"
    finally:
        window.close()
        controller.set_appearance(mode, *appearance)
        controller.set_mode("dark")


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
    with patch("ui.qt_settings.QDesktopServices.openUrl") as open_url:
        window.project_homepage_button.click()
    assert open_url.call_args.args[0].toString() == "https://github.com/zensoku142/TokenMeter"
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

    scroll_area = window.tabs.widget(0)
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
