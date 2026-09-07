"""Removing a monitored provider preserves external logins and unrelated configuration."""

import os
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from api import providers
from config import runtime as config_manager
from config.defaults import DEFAULT_CONFIG
from config.store import validate_config
from ui import provider_picker
from ui.provider_picker import ProviderPicker, pinned_provider_ids

APP = QApplication.instance() or QApplication([])


@pytest.fixture
def state(monkeypatch, tmp_path):
    values = {**DEFAULT_CONFIG, "ACTIVE_PROVIDER": "codex", "CODEX_HOME": "external-login-directory",
              "DEEPSEEK_API_KEY": "other-provider-secret", "BACKGROUND_PROVIDER_IDS": ["codex", "deepseek"]}
    monkeypatch.setattr(config_manager, "_config", values)
    monkeypatch.setattr(config_manager, "all_config", lambda: dict(values))
    monkeypatch.setattr(config_manager, "PANEL_LAYOUT_PATH", tmp_path / "layout.json")

    def persist(changes):
        validated = validate_config({**values, **changes})
        values.clear()
        values.update(validated)
        return dict(values)

    save = Mock(side_effect=persist)
    monkeypatch.setattr(config_manager, "save_config", save)
    monkeypatch.setattr(provider_picker, "configured_provider_ids", lambda config: [
        value for value in ("codex", "deepseek") if value not in config.get("DISABLED_PROVIDER_IDS", [])
    ])
    monkeypatch.setattr(QMessageBox, "question", Mock(return_value=QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "warning", Mock())
    picker = ProviderPicker()
    for provider_id, provider in providers.PROVIDERS.items():
        picker.addItem(provider.name, provider_id)
    picker.setCurrentIndex(picker.findData("codex"))
    picker.showPopup()
    yield values, save, picker
    picker.hidePopup()


def test_remove_current_provider_clears_only_its_settings_and_switches_to_remaining(state):
    values, save, picker = state
    changed = QSignalSpy(picker.configuration_changed)
    QTest.mouseClick(picker.remove_buttons["codex"], Qt.MouseButton.LeftButton)
    assert values["CODEX_HOME"] == ""
    assert values["DEEPSEEK_API_KEY"] == "other-provider-secret"
    assert values["ACTIVE_PROVIDER"] == "deepseek"
    assert values["DISABLED_PROVIDER_IDS"] == ["codex"]
    assert values["BACKGROUND_PROVIDER_IDS"] == ["deepseek"]
    assert "codex" not in pinned_provider_ids()
    assert changed.count() == 1 and changed.at(0) == ["codex", True]
    save.assert_called_once()


def test_delete_key_uses_confirmation_and_cancel_keeps_config(state):
    values, save, picker = state
    before = dict(values)
    QMessageBox.question.return_value = QMessageBox.StandardButton.No
    QTest.keyClick(picker.grid, Qt.Key.Key_Delete)
    QMessageBox.question.assert_called_once()
    assert values == before
    save.assert_not_called()


def test_each_card_button_removes_its_own_provider_without_switching_selected_card(state):
    values, _save, picker = state
    values["DEEPSEEK_PEAK_PRICING_ENABLED"] = True
    assert picker.currentData() == "codex"
    QTest.mouseClick(picker.remove_buttons["deepseek"], Qt.MouseButton.LeftButton)
    assert values["ACTIVE_PROVIDER"] == "codex"
    assert values["CODEX_HOME"] == "external-login-directory"
    assert values["DEEPSEEK_API_KEY"] == ""
    assert values["DISABLED_PROVIDER_IDS"] == ["deepseek"]
    assert values["DEEPSEEK_PEAK_PRICING_ENABLED"] is True


def test_card_buttons_remain_in_card_bottom_right_after_scroll_and_search(state, monkeypatch):
    _values, _save, picker = state
    picker.hidePopup()
    monkeypatch.setattr(provider_picker, "configured_provider_ids", lambda _config: list(providers.PROVIDERS))
    picker.showPopup()
    APP.processEvents()
    picker.grid.verticalScrollBar().setValue(picker.grid.verticalScrollBar().maximum())
    APP.processEvents()
    for row in range(picker.grid.count()):
        item = picker.grid.item(row)
        rect = picker.grid.visualItemRect(item)
        button = picker.remove_buttons[item.data(Qt.ItemDataRole.UserRole)[0]]
        assert rect.contains(button.geometry())
        assert button.x() > rect.center().x()
        assert button.y() > rect.center().y()
    picker.search.setText("gemini")
    APP.processEvents()
    assert list(picker.remove_buttons) == ["gemini"]
    assert picker.grid.visualItemRect(picker.grid.item(0)).contains(picker.remove_buttons["gemini"].geometry())


def test_focusing_offscreen_delete_button_reveals_its_card_and_escape_cancels(state, monkeypatch):
    _values, save, picker = state
    picker.hidePopup()
    monkeypatch.setattr(provider_picker, "configured_provider_ids", lambda _config: list(providers.PROVIDERS))
    picker.showPopup()
    APP.processEvents()
    last = picker.grid.item(picker.grid.count() - 1)
    provider_id = last.data(Qt.ItemDataRole.UserRole)[0]
    button = picker.remove_buttons[provider_id]
    button.setFocus(Qt.FocusReason.TabFocusReason)
    APP.processEvents()
    assert picker.grid.viewport().rect().contains(button.geometry())
    assert picker.grid.currentItem() is last
    QTest.keyClick(button, Qt.Key.Key_Escape)
    assert not picker._popup_open
    save.assert_not_called()


def test_failed_removal_keeps_configuration_and_does_not_signal_success(state):
    values, save, picker = state
    before = dict(values)
    save.side_effect = OSError("synthetic save failure")
    changed = QSignalSpy(picker.configuration_changed)
    picker._remove_selected()
    assert values == before
    assert changed.count() == 0
    QMessageBox.warning.assert_called_once()


def test_explicit_selection_reenables_local_discovery_without_restoring_deleted_path(state):
    values, save, picker = state
    picker._remove_selected()
    save.reset_mock()
    changed = QSignalSpy(picker.configuration_changed)
    picker.select_provider("codex")
    assert "codex" not in values["DISABLED_PROVIDER_IDS"]
    assert values["ACTIVE_PROVIDER"] == "codex"
    assert values["CODEX_HOME"] == ""
    assert changed.at(0) == ["codex", False]
    save.assert_called_once()


def test_removed_card_cannot_silently_rejoin_favorites(state):
    _values, save, picker = state
    picker._remove_selected()
    save.reset_mock()
    picker.showPopup()
    picker.search.setText("codex")
    picker._toggle_pin(picker.grid.item(0))
    assert "重新启用" in picker.hint.text()
    assert "保存失败" not in picker.hint.text()
    save.assert_not_called()


def test_unconfigured_selection_cannot_be_deleted(state):
    _values, save, picker = state
    picker._set_filter("all")
    for row in range(picker.grid.count()):
        item = picker.grid.item(row)
        if item.data(Qt.ItemDataRole.UserRole)[0] == "claude":
            picker.grid.setCurrentItem(item)
            break
    assert "claude" not in picker.remove_buttons
    picker._remove_selected()
    save.assert_not_called()


def test_disabled_provider_is_not_probed_or_instantiated(monkeypatch):
    provider = Mock()
    monkeypatch.setattr(providers, "PROVIDERS", {"codex": provider})
    config = {"ACTIVE_PROVIDER": "codex", "DISABLED_PROVIDER_IDS": ["codex"]}
    assert providers.configured_provider_ids(config) == []
    assert list(providers.active_providers(config)) == []
    provider.assert_not_called()


def test_header_card_removal_updates_active_view_and_clears_old_provider_cache(state, monkeypatch):
    from data.store import PerProviderData, TokenData
    from ui.qt_widget import FloatingWidget

    values, _save, _picker = state
    monkeypatch.setattr(config_manager, "load_config", lambda: dict(values))
    monkeypatch.setattr(FloatingWidget, "refresh", Mock())
    widget = FloatingWidget()
    widget._update_controller.reload_cached_release = Mock()
    widget._update_controller.schedule_startup_check = Mock()
    old = TokenData(account_key="old", per_provider=[PerProviderData("codex", "Codex")])
    widget._provider_results["codex"] = old
    widget._data = old
    try:
        widget.expand_panel()
        picker = widget.panel.provider_quick_combo
        picker.showPopup()
        QTest.mouseClick(picker.remove_buttons["codex"], Qt.MouseButton.LeftButton)
        assert values["ACTIVE_PROVIDER"] == "deepseek"
        assert widget._data.per_provider[0].provider_id == "deepseek"
        assert "codex" not in widget._provider_results
        assert "codex" not in widget.panel.provider_shortcuts.buttons
    finally:
        widget._closed = True
        widget.hide()


def test_removed_settings_form_explains_reactivation_and_reenables_cleanly(state, monkeypatch):
    from ui.qt_settings import SettingsWindow

    values, _save, _picker = state
    values["DISABLED_PROVIDER_IDS"] = ["codex"]
    monkeypatch.setattr(config_manager, "load_config", lambda: dict(values))
    window = SettingsWindow()
    assert not window.credentials_card.isEnabled()
    assert not window.test_button.isEnabled()
    assert window.provider_combo.isEnabled()
    assert "重新启用" in window.connection_feedback.text()
    assert not window.background_provider_checks["codex"].isEnabled()
    values["DISABLED_PROVIDER_IDS"] = []
    window.reload_provider_configuration("codex", False)
    assert window.credentials_card.isEnabled()
    assert window.test_button.isEnabled()
    assert window.background_provider_checks["codex"].isEnabled()
    assert window.connection_feedback.text() == ""


def test_settings_cannot_resave_removed_secret_from_draft_or_late_browser_result(state, monkeypatch):
    from ui.qt_settings import SettingsWindow

    values, save, _picker = state
    monkeypatch.setattr(config_manager, "load_config", lambda: dict(values))
    window = SettingsWindow(on_saved=Mock())
    window.provider_combo.setCurrentIndex(window.provider_combo.findData("deepseek"))
    window._provider_drafts["deepseek"] = {"API_KEY": "old-draft-secret"}
    worker = Mock()
    window._cookie_acquire_worker = worker
    window._cookie_acquire_provider_id = "deepseek"
    values.update(DEEPSEEK_API_KEY="", DISABLED_PROVIDER_IDS=["deepseek"], ACTIVE_PROVIDER="codex",
                  BACKGROUND_PROVIDER_IDS=["codex"])
    window.reload_provider_configuration("deepseek", True)
    worker.stop_and_collect.assert_called_once()
    assert not window.background_provider_checks["deepseek"].isEnabled()
    assert window._values()["DEEPSEEK_API_KEY"] == ""
    assert "deepseek" not in window._values()["BACKGROUND_PROVIDER_IDS"]
    window._apply_acquired_cookie("deepseek", "old-browser-cookie")
    assert "deepseek" not in window._provider_drafts
    save.assert_not_called()
    window.deleteLater()
