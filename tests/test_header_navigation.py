"""Real Qt navigation and quota details with isolated preferences and synthetic data."""

import json
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import requests
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLabel, QToolButton, QWidget

from api.providers import PROVIDERS
from api.providers.base import QuotaMetric, QuotaWindow
from config import runtime as config_manager
from config.defaults import DEFAULT_CONFIG
from data import history
from data.store import PerProviderData, TokenData
from ui import provider_picker
from ui.i18n import configure_language
from ui.provider_branding import provider_icon
from ui.qt_panel import MainPanel
from ui.qt_theme import configure_theme

APP = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_navigation(monkeypatch, tmp_path):
    values = {**DEFAULT_CONFIG, "ACTIVE_PROVIDER": "deepseek"}
    layout_path = tmp_path / "panel-layout.json"
    layout_path.write_text(json.dumps({
        "pinned_providers": ["codex", "claude", "cursor", "gemini", "kimi"],
    }), encoding="utf-8")
    monkeypatch.setattr(config_manager, "_config", values)
    monkeypatch.setattr(config_manager, "load_config", lambda: values.copy())
    monkeypatch.setattr(config_manager, "PANEL_LAYOUT_PATH", layout_path)
    monkeypatch.setattr(config_manager, "save_config", Mock(side_effect=AssertionError("no credential writes")))
    monkeypatch.setattr(provider_picker, "configured_provider_ids", lambda *_args: [])
    monkeypatch.setattr(history, "DB_PATH", tmp_path / "usage.db")
    monkeypatch.setattr(TokenData, "_provider_snapshots", {})
    monkeypatch.setattr(TokenData, "_last_snapshot", None)
    # 导航仅消费合成快照；防止未来自动发现账号的改动读取宿主凭据或发送网络请求。
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(requests.Session, "request", Mock(side_effect=AssertionError("no network")))
    configure_language(APP, "zh-cn")
    configure_theme(APP, "dark")
    yield
    configure_language(APP, "zh-cn")
    configure_theme(APP, "dark")


@pytest.fixture
def panel():
    widget = MainPanel()
    widget.resize(820, widget.height())
    widget.show()
    APP.processEvents()
    yield widget
    if widget._quota_details_dialog is not None:
        widget._quota_details_dialog.reject()
    widget.provider_quick_combo.hidePopup()
    widget.close()


def provider_data(provider_id="claude", account="synthetic-account", windows=None):
    if windows is None:
        windows = [QuotaWindow(f"quota-{index}", f"Synthetic quota {index}", index * 20)
                   for index in range(5)]
    return TokenData(
        status="ok", account_key=account,
        last_success_at=datetime(2026, 9, 5, 12),
        quota_source="interface", quota_windows=windows,
        per_provider=[PerProviderData(provider_id, PROVIDERS[provider_id].name, status="ok")],
    )


def header_button_bounds(panel):
    return {
        button: QRect(button.mapTo(panel.header, QPoint()), button.size())
        for button in panel.header.findChildren(QToolButton)
        if button.isVisible()
    }


def details_labels(dialog):
    return [label.text() for label in dialog.scroll_area.widget().findChildren(QLabel)]


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [20, 32])
def test_codex_uses_recognizable_openai_brand_image(theme, size):
    configure_theme(APP, theme)
    codex = provider_icon("codex", size).pixmap(size, size).toImage()
    openai = provider_icon("openai", size).pixmap(size, size).toImage()
    assert not codex.isNull()
    assert codex == openai
    assert codex != provider_icon("nayuto", size).pixmap(size, size).toImage()


def test_header_uses_only_brand_shortcuts_with_accessible_names(panel):
    assert panel.provider_quick_combo.isHidden()
    assert panel.provider_shortcuts.isVisible()
    assert panel.provider_manage_button.isVisible()
    assert panel.provider_manage_button.text() == ""
    assert panel.provider_manage_button.toolTip()
    assert panel.provider_manage_button.accessibleName()
    for button in panel.provider_shortcuts.buttons.values():
        assert button.isVisible()
        assert button.text() == ""
        assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        assert button.toolTip() and button.accessibleName()
        assert not button.icon().isNull()


@pytest.mark.parametrize("provider_id", ["deepseek", "minimax"])
def test_current_provider_is_visible_and_checked_even_when_not_pinned(panel, provider_id):
    if provider_id != "deepseek":
        panel.update_data(provider_data(provider_id))
        APP.processEvents()
    current = panel.provider_shortcuts.buttons[provider_id]
    assert current.isVisible() and current.isChecked()
    assert sum(button.isChecked() for button in panel.provider_shortcuts.buttons.values()) == 1


def test_clicking_selected_brand_cannot_remove_its_highlight(panel):
    current = panel.provider_shortcuts.buttons["deepseek"]
    for _ in range(2):
        QTest.mouseClick(current, Qt.MouseButton.LeftButton)
        APP.processEvents()
        assert current.isChecked()
        assert panel.provider_quick_combo.isHidden()


def test_brand_click_selects_correct_provider_once(panel):
    selected = QSignalSpy(panel.provider_selected)
    QTest.mouseClick(panel.provider_shortcuts.buttons["claude"], Qt.MouseButton.LeftButton)
    APP.processEvents()
    assert selected.count() == 1 and selected.at(0) == ["claude"]
    assert panel.provider_shortcuts.buttons["claude"].isChecked()
    assert not panel.provider_shortcuts.buttons["deepseek"].isChecked()
    assert panel.provider_quick_combo.isHidden()


def test_plus_opens_search_without_exposing_dropdown(panel):
    QTest.mouseClick(panel.provider_manage_button, Qt.MouseButton.LeftButton)
    APP.processEvents()
    assert panel.provider_quick_combo.popup.isVisible()
    assert panel.provider_quick_combo.search.isVisible()
    assert panel.provider_quick_combo.isHidden()
    panel.provider_quick_combo.search.setText("claude")
    QTest.keyClick(panel.provider_quick_combo.search, Qt.Key.Key_Return)
    APP.processEvents()
    assert panel.provider_quick_combo.currentData() == "claude"
    assert not panel.provider_quick_combo.popup.isVisible()
    assert panel.provider_quick_combo.isHidden()


def test_returning_from_settings_keeps_icon_navigation(panel):
    panel.show_settings(QWidget())
    APP.processEvents()
    assert panel.provider_quick_combo.isHidden()
    assert not panel.provider_shortcuts.isVisible()
    panel.show_overview()
    APP.processEvents()
    assert panel.provider_quick_combo.isHidden()
    assert panel.provider_shortcuts.isVisible()
    assert panel.provider_manage_button.isVisible()


@pytest.mark.parametrize("width", [640, 820])
def test_peak_badge_stays_in_today_card_and_never_moves_or_clips_header_buttons(panel, width):
    panel.resize(width, panel.height())
    APP.processEvents()
    assert panel.width() == width
    assert panel.pricing_badge.parentWidget() is panel.today_card
    assert not panel.header.isAncestorOf(panel.pricing_badge)
    baseline = header_button_bounds(panel)
    assert baseline and panel.provider_manage_button in baseline
    for enabled, peak, label in (
        (True, True, "峰时 2× · 12:00 结束"),
        (True, False, "谷时 1× · 08:00 结束"),
        (False, False, ""),
    ):
        panel.set_pricing_state(enabled, peak, label, "Synthetic price state")
        APP.processEvents()
        assert panel.pricing_badge.isVisible() is enabled
        assert panel.provider_shortcuts.isVisible()
        assert panel.provider_quick_combo.isHidden()
        bounds = header_button_bounds(panel)
        assert bounds == baseline
        for button, rect in bounds.items():
            assert panel.header.rect().contains(rect), button.accessibleName()
        rectangles = list(bounds.values())
        assert all(not rect.intersects(other)
                   for index, rect in enumerate(rectangles) for other in rectangles[index + 1:])


def test_quota_details_button_opens_every_window_and_account_metric(panel):
    data = provider_data()
    data.quota_metrics = [QuotaMetric("Synthetic credits", "42")]
    panel.update_data(data)
    APP.processEvents()
    assert panel.quota_details_button.isVisible()
    QTest.mouseClick(panel.quota_details_button, Qt.MouseButton.LeftButton)
    APP.processEvents()
    dialog = panel._quota_details_dialog
    assert dialog is not None and dialog.isVisible()
    assert dialog.provider_id == "claude" and dialog.account_key == data.account_key
    labels = details_labels(dialog)
    assert all(window.title in labels for window in data.quota_windows)
    assert "Synthetic credits" in labels and "42" in labels
    assert len(dialog.progress_bars) == 5


def test_open_details_replaces_same_account_rows_without_mixing_old_windows(panel):
    initial = provider_data()
    panel.update_data(initial)
    panel.quota_details_button.click()
    dialog = panel._quota_details_dialog
    assert dialog is not None
    updated = provider_data(windows=[QuotaWindow("new-window", "Replacement quota", 88)])
    panel.update_data(updated)
    APP.processEvents()
    assert dialog.isVisible()
    labels = details_labels(dialog)
    assert "Replacement quota" in labels and "已用 88%" in labels
    assert not any(window.title in labels for window in initial.quota_windows)
    assert len(dialog.progress_bars) == 1 and dialog.progress_bars[0].value() == 880


@pytest.mark.parametrize("provider_id,account", [
    ("gemini", "synthetic-account"), ("claude", "other-synthetic-account"),
])
def test_provider_or_account_change_closes_old_details_and_reopens_current_data(panel, provider_id, account):
    panel.update_data(provider_data())
    panel.quota_details_button.click()
    dialog = panel._quota_details_dialog
    assert dialog is not None and dialog.isVisible()
    panel.update_data(provider_data(provider_id, account, [QuotaWindow("new", "New account quota", 15)]))
    APP.processEvents()
    assert not dialog.isVisible()
    panel.quota_details_button.click()
    APP.processEvents()
    assert dialog.isVisible()
    assert (dialog.provider_id, dialog.account_key) == (provider_id, account)
    assert "New account quota" in details_labels(dialog)
    assert "Synthetic quota 0" not in details_labels(dialog)


def test_switching_to_balance_provider_hides_and_closes_quota_details(panel):
    panel.update_data(provider_data())
    panel.quota_details_button.click()
    dialog = panel._quota_details_dialog
    assert dialog is not None and dialog.isVisible()
    panel.update_data(provider_data("deepseek", windows=[]))
    APP.processEvents()
    assert not dialog.isVisible()
    assert not panel.quota_details_button.isVisible()
