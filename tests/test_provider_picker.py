"""Provider switching exercises real Qt input with isolated layout preferences."""

import json
import os
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from api.providers import PROVIDERS
from ui import provider_picker
from ui.provider_picker import ProviderPicker, ProviderShortcuts
from ui.qt_theme import configure_theme

APP = QApplication.instance() or QApplication([])


@pytest.fixture
def picker_state(monkeypatch, tmp_path):
    layout_path = tmp_path / "panel-layout.json"
    layout_path.write_text(
        json.dumps(
            {"pinned_providers": ["codex", "claude", "cursor"], "keep_this_field": {"width": 640}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(provider_picker.config_manager, "PANEL_LAYOUT_PATH", layout_path)
    monkeypatch.setattr(provider_picker.config_manager, "all_config", lambda: {})
    save_config = Mock(side_effect=AssertionError("Picker must not save credentials"))
    monkeypatch.setattr(provider_picker.config_manager, "save_config", save_config)
    configured = Mock(return_value=[])
    monkeypatch.setattr(provider_picker, "configured_provider_ids", configured)
    configure_theme(APP, "dark")
    yield layout_path, configured, save_config
    configure_theme(APP, "dark")


@pytest.fixture
def picker(picker_state):
    widget = ProviderPicker()
    for provider_id, provider in PROVIDERS.items():
        widget.addItem(provider.name, provider_id)
    widget.setCurrentIndex(widget.findData("codex"))
    widget.resize(220, 34)
    widget.show()
    APP.processEvents()
    yield widget
    widget.hidePopup()
    widget.close()


def visible_ids(picker):
    return [
        picker.grid.item(row).data(Qt.ItemDataRole.UserRole)[0]
        for row in range(picker.grid.count())
    ]


def test_search_focused_shortcut_can_pin_the_visible_result(picker, picker_state):
    picker.showPopup()
    picker.search.setText("elevenlabs")
    QTest.keyClick(picker.search, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    assert "elevenlabs" in provider_picker.pinned_provider_ids()
    assert picker.search.text() == "elevenlabs"


def test_failed_pin_save_keeps_persisted_state_and_reports_error(picker, picker_state, monkeypatch):
    saved = picker_state[0].read_text(encoding="utf-8")
    monkeypatch.setattr(provider_picker.config_manager, "save_panel_layout_state", Mock())
    changed = QSignalSpy(picker.pins_changed)
    picker.showPopup()
    picker.search.setText("elevenlabs")
    picker.grid.setFocus()
    QTest.keyClick(picker.grid, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    assert picker_state[0].read_text(encoding="utf-8") == saved
    assert changed.count() == 0
    assert "保存失败" in picker.hint.text()


def open_all(picker):
    picker.showPopup()
    QTest.mouseClick(picker.filter_buttons["all"], Qt.MouseButton.LeftButton)
    APP.processEvents()


@pytest.mark.parametrize(
    "query,expected",
    [
        ("Claude", "claude"),
        ("cLaUdE", "claude"),
        ("智谱", "zai"),
        ("月之暗面", "moonshot"),
        ("kimi", "kimi"),
        ("elevenlabs", "elevenlabs"),
        ("GLM zai", "zai"),
        ("MiniMax Token", "minimax"),
    ],
)
def test_search_matches_names_aliases_ids_and_multiple_words(picker, picker_state, query, expected):
    open_all(picker)
    configured = picker_state[1]
    initial_calls = configured.call_count
    picker.search.setText(query)
    APP.processEvents()
    assert expected in visible_ids(picker)
    assert configured.call_count == initial_calls
    assert picker.currentData() == "codex"


def test_no_matches_is_explicit_and_enter_does_not_change_provider(picker):
    open_all(picker)
    activated = QSignalSpy(picker.activated)
    picker.search.setText("no-such-provider-28374")
    QTest.keyClick(picker.search, Qt.Key.Key_Return)
    assert picker.grid.count() == 0
    assert picker.empty_label.isVisible()
    assert picker.popup.isVisible()
    assert picker.currentData() == "codex" and activated.count() == 0


def test_default_filter_shows_configured_but_search_can_find_unconfigured(picker, picker_state):
    configured = picker_state[1]
    configured.return_value = ["kimi", "claude"]
    picker.showPopup()
    assert picker.filter_buttons["configured"].isChecked()
    assert set(visible_ids(picker)) == {"kimi", "claude"}
    picker.search.setText("kim")
    assert visible_ids(picker) == ["kimi", "moonshot"]
    assert picker.filter_buttons["all"].isChecked()
    assert configured.call_count == 1
    picker.search.clear()
    QTest.mouseClick(picker.filter_buttons["all"], Qt.MouseButton.LeftButton)
    assert set(visible_ids(picker)) == set(PROVIDERS)
    assert set(visible_ids(picker)[:2]) == {"kimi", "claude"}


def test_pinned_filter_follows_layout_and_deduplicates_invalid_ids(picker, picker_state):
    picker_state[0].write_text(
        json.dumps({"pinned_providers": ["kimi", "bad-id", "kimi", None]}), encoding="utf-8"
    )
    picker.showPopup()
    QTest.mouseClick(picker.filter_buttons["pinned"], Qt.MouseButton.LeftButton)
    assert visible_ids(picker) == ["kimi"]


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_enter_from_search_switches_once_and_closes_popup(picker, key):
    open_all(picker)
    changed = QSignalSpy(picker.currentIndexChanged)
    activated = QSignalSpy(picker.activated)
    text_activated = QSignalSpy(picker.textActivated)
    picker.search.setText("claude")
    QTest.keyClick(picker.search, key)
    assert picker.currentData() == "claude"
    assert changed.count() == activated.count() == text_activated.count() == 1
    assert not picker.popup.isVisible()


def test_down_moves_focus_then_enter_activates_grid_once(picker):
    open_all(picker)
    picker.search.setText("kimi")
    activated = QSignalSpy(picker.activated)
    QTest.keyClick(picker.search, Qt.Key.Key_Down)
    assert picker.grid.hasFocus()
    expected = picker.grid.currentItem().data(Qt.ItemDataRole.UserRole)[0]
    assert picker.currentData() == "codex" and activated.count() == 0
    QTest.keyClick(picker.grid, Qt.Key.Key_Return)
    assert picker.currentData() == expected and activated.count() == 1
    assert not picker.popup.isVisible()


@pytest.mark.parametrize("target", ["search", "grid"])
def test_escape_dismisses_without_selection_change(picker, target):
    open_all(picker)
    activated = QSignalSpy(picker.activated)
    picker.search.setText("claude")
    QTest.keyClick(getattr(picker, target), Qt.Key.Key_Escape)
    assert not picker.popup.isVisible()
    assert picker.currentData() == "codex" and activated.count() == 0


def test_click_card_switches_once(picker):
    open_all(picker)
    picker.search.setText("elevenlabs")
    APP.processEvents()
    activated = QSignalSpy(picker.activated)
    rect = picker.grid.visualItemRect(picker.grid.item(0))
    QTest.mouseClick(picker.grid.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert picker.currentData() == "elevenlabs" and activated.count() == 1
    assert not picker.popup.isVisible()


def test_select_provider_rejects_unknown_id_without_signals(picker):
    activated = QSignalSpy(picker.activated)
    picker.select_provider("no-such-provider")
    assert picker.currentData() == "codex" and activated.count() == 0


def test_ctrl_d_persists_only_layout_preserving_other_fields_and_never_switches(
    picker, picker_state
):
    open_all(picker)
    picker.search.setText("elevenlabs")
    QTest.keyClick(picker.search, Qt.Key.Key_Down)
    pins_changed = QSignalSpy(picker.pins_changed)
    activated = QSignalSpy(picker.activated)
    QTest.keyClick(picker.grid, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    state = json.loads(picker_state[0].read_text(encoding="utf-8"))
    assert state["pinned_providers"] == ["elevenlabs", "codex", "claude", "cursor"]
    assert state["keep_this_field"] == {"width": 640}
    assert set(state) == {"pinned_providers", "keep_this_field"}
    assert pins_changed.count() == 1 and activated.count() == 0
    assert picker.currentData() == "codex" and picker.popup.isVisible()
    picker_state[2].assert_not_called()
    QTest.keyClick(picker.grid, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    assert json.loads(picker_state[0].read_text(encoding="utf-8"))["pinned_providers"] == [
        "codex",
        "claude",
        "cursor",
    ]
    assert pins_changed.count() == 2 and activated.count() == 0


def test_star_click_toggles_favorite_without_activating_card(picker, picker_state):
    open_all(picker)
    picker.search.setText("elevenlabs")
    APP.processEvents()
    pins_changed = QSignalSpy(picker.pins_changed)
    activated = QSignalSpy(picker.activated)
    rect = picker.grid.visualItemRect(picker.grid.item(0))
    star_center = QPoint(rect.right() - 23, rect.top() + 26)
    QTest.mouseClick(picker.grid.viewport(), Qt.MouseButton.LeftButton, pos=star_center)
    state = json.loads(picker_state[0].read_text(encoding="utf-8"))
    assert "elevenlabs" in state["pinned_providers"]
    assert pins_changed.count() == 1 and activated.count() == 0
    assert picker.currentData() == "codex" and picker.popup.isVisible()


def test_shortcuts_show_three_pins_and_emit_correct_id(picker_state):
    shortcuts = ProviderShortcuts()
    shortcuts.show()
    assert list(shortcuts.buttons) == ["codex", "claude", "cursor"]
    shortcuts.set_current("codex")
    assert shortcuts.buttons["codex"].isChecked()
    selected = QSignalSpy(shortcuts.selected)
    QTest.mouseClick(shortcuts.buttons["claude"], Qt.MouseButton.LeftButton)
    assert selected.count() == 1 and selected.at(0) == ["claude"]
    shortcuts.set_current("claude")
    assert shortcuts.buttons["claude"].isChecked() and not shortcuts.buttons["codex"].isChecked()
    assert all(
        button.accessibleName() and not button.icon().isNull()
        for button in shortcuts.buttons.values()
    )
    shortcuts.close()


def test_theme_change_refreshes_shortcut_icons_and_open_picker(picker, picker_state):
    shortcuts = ProviderShortcuts()
    shortcuts.show()
    open_all(picker)
    dark_icon = shortcuts.buttons["codex"].icon().pixmap(20, 20).toImage()
    dark_styles = picker.popup.styleSheet()
    configure_theme(APP, "light")
    APP.processEvents()
    light_icon = shortcuts.buttons["codex"].icon().pixmap(20, 20).toImage()
    assert dark_icon != light_icon
    assert picker.popup.styleSheet() != dark_styles
    assert picker.popup.isVisible() and picker.currentData() == "codex"
    shortcuts.close()
