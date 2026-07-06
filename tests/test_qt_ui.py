import os
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = str(Path.cwd() / ".test-appdata")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QLineEdit, QToolButton

from data.store import TokenData
from ui.geometry import WorkArea
from ui.qt_panel import MainPanel, StatisticsCard, TrendCard, format_money_axis, format_token_axis
from ui.qt_settings import SettingsWindow
from ui.qt_theme import (
    ACTIVITY_CARD_HEIGHT,
    APP_STYLE,
    C_ACCENT,
    C_GLASS_BORDER,
    C_GLASS_CARD,
    C_HEAT,
    C_PANEL,
    C_TEXT,
    LOWER_CARD_HEIGHT,
    METRIC_CARD_HEIGHT,
)
from ui.qt_widget import FloatingWidget


APP = QApplication.instance() or QApplication([])
APP.setStyleSheet(APP_STYLE)


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


def test_trend_uses_daily_cost_and_money_tooltip():
    trend = TrendCard()
    trend.set_rows(sample_data().daily_usage, date.today())
    trend.resize(480, LOWER_CARD_HEIGHT)
    trend.show()
    APP.processEvents()
    scene_point = trend.plot.getViewBox().mapViewToScene(QPointF(0, 0.6))
    trend._on_mouse_moved((scene_point,))

    assert trend.title.text() == "近 7 天使用金额"
    assert trend._values == [0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0]
    assert len(trend._series.xData) > len(trend._values)
    x_min, x_max = trend.plot.getViewBox().viewRange()[0]
    assert x_min == -0.5
    assert x_max == 6.5
    for index, value in enumerate(trend._values):
        point = next(
            position
            for position, x_value in enumerate(trend._series.xData)
            if x_value == index
        )
        assert trend._series.yData[point] == value
    assert (date.today() - timedelta(days=6)).isoformat() in trend.tooltip_text(0)
    assert "使用金额：¥0.60" in trend.tooltip_text(0)
    assert trend._hover_marker.isVisible()
    scene_point = trend.plot.getViewBox().mapViewToScene(QPointF(6, 0))
    trend._on_mouse_moved((scene_point,))
    marker_x, marker_y = trend._hover_marker.getData()
    assert marker_x.tolist() == [6]
    assert marker_y.tolist() == [0.0]
    trend.close()


def test_money_axis_and_zero_cost_range_remain_readable():
    assert format_money_axis(0) == "¥0.00"
    assert format_money_axis(0.006) == "¥0.0060"
    trend = TrendCard()
    trend.set_rows([], date.today())

    assert trend._values == [0.0] * 7
    assert trend.plot.getViewBox().viewRange()[1][1] >= 0.01
    trend.close()


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


def test_panel_charts_keep_dark_background_and_compact_heatmap_height():
    panel = MainPanel()
    panel.resize(820, 550)
    panel.update_data(sample_data())
    panel.show()
    APP.processEvents()
    panel.activity.grab()
    plot_image = panel.trend.plot.grab().toImage()
    center = plot_image.pixelColor(plot_image.width() // 2, plot_image.height() // 2)

    assert len(panel.activity._hits) >= 365
    assert METRIC_CARD_HEIGHT == 100
    assert ACTIVITY_CARD_HEIGHT == 176
    assert LOWER_CARD_HEIGHT == 128
    assert all(
        card.height() == METRIC_CARD_HEIGHT
        for card in (panel.today_card, panel.balance_card, panel.month_card)
    )
    assert panel.activity_card.height() == ACTIVITY_CARD_HEIGHT
    assert panel.activity.height() == 133
    assert panel.trend.height() == LOWER_CARD_HEIGHT
    assert panel.statistics.height() == LOWER_CARD_HEIGHT
    assert len(panel.statistics._values) == 5
    assert all(
        0 <= value.mapTo(panel.statistics, QPoint()).y()
        and value.mapTo(panel.statistics, QPoint()).y() + value.height()
        <= panel.statistics.height()
        for value in panel.statistics._values
    )
    assert not panel.activity_scroll.horizontalScrollBar().isVisible()
    assert max(center.red(), center.green(), center.blue()) < 245
    panel.close()


def test_panel_uses_shared_glass_theme_and_fluent_action_buttons():
    panel = MainPanel()
    buttons = panel.findChildren(QToolButton, "panelToolButton")

    assert C_PANEL == "#051228"
    assert C_GLASS_CARD == "rgba(38, 89, 158, 26)"
    assert C_GLASS_BORDER == "rgba(102, 166, 255, 41)"
    assert C_ACCENT == "#2767E5"
    assert C_TEXT == "#E5E9F0"
    assert C_HEAT == (
        "#0B2440",
        "#0B4087",
        "#0958B8",
        "#116CD2",
        "#2497FA",
        "#9ED0FD",
    )
    assert [button.toolTip() for button in buttons] == ["设置", "刷新", "收起"]
    assert all(not button.icon().isNull() for button in buttons)
    assert all(button.iconSize().width() == 18 for button in buttons)
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
        assert widget.height() == 550
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

        assert (widget.width(), widget.height()) == (96, 96)
        assert (widget.ball.width(), widget.ball.height()) == (96, 96)
        assert (widget.x(), widget.y()) == (420, 260)
        save_position.assert_called_once_with(420, 260)
        widget._closed = True
        widget.hide()


def test_deactivation_collapses_panel_but_ignores_visible_settings():
    with patch("ui.qt_widget.TokenData.fetch", return_value=sample_data()):
        widget = FloatingWidget()
        widget.expand_panel()

        with (
            patch.object(widget, "isActiveWindow", return_value=False),
            patch.object(widget, "_has_settings_child", return_value=True),
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
