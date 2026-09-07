import json
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from config import runtime as config_manager
from config.defaults import DEFAULT_CONFIG
from data.store import TokenData
from test_refresh import widget_stub
from ui.compact_overview import CompactOverview

APP = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def preferences(monkeypatch, tmp_path):
    monkeypatch.setattr(config_manager, "_config", dict(DEFAULT_CONFIG))
    monkeypatch.setattr(config_manager, "PANEL_LAYOUT_PATH", tmp_path / "layout.json")


def test_two_provider_choices_persist_without_duplicate_cards(tmp_path):
    config_manager.PANEL_LAYOUT_PATH.write_text(json.dumps({"pinned_providers": ["codex"]}))
    window = CompactOverview()
    window.set_sources(["codex", "claude"])
    assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert window.selected_providers() == ["codex", "claude"]
    window.selectors[0].setCurrentIndex(window.selectors[0].findData("claude"))
    window._selection_changed()
    assert window.selected_providers() == ["claude"]
    saved = json.loads(config_manager.PANEL_LAYOUT_PATH.read_text())
    assert saved["compact_providers"] == ["claude"]
    assert saved["pinned_providers"] == ["codex"]
    reopened = CompactOverview()
    reopened.set_sources(["codex", "claude"])
    assert reopened.selected_providers() == ["claude"]
    for selector in window.selectors:
        selector.setCurrentIndex(0)
    window._selection_changed()
    empty = CompactOverview()
    empty.set_sources(["codex", "claude"])
    assert empty.selected_providers() == []


def test_compact_header_and_round_corners_match_main_panel():
    from ui.qt_panel import HEADER_HEIGHT, MainPanel
    from ui.qt_theme import configure_theme

    configure_theme(APP, "light")
    window = CompactOverview()
    window.set_sources(["codex", "claude"])
    window.show()
    main = MainPanel()
    main.show()
    APP.processEvents()
    assert window.header.height() == HEADER_HEIGHT
    assert window.close_button.objectName() == "panelToolButton"
    assert window.close_button.property("role") == "close"
    assert window.close_button.size() == main.close_button.size()
    assert window.close_button.iconSize().width() == 18
    assert window.width() == 430
    image = window.grab().toImage()
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(image.width() - 1, image.height() - 1).alpha() == 0
    window.close_button.click()
    assert not window.isVisible()


def test_compact_snapshot_uses_only_matching_account(monkeypatch):
    widget = widget_stub()
    widget.panel = None
    widget._compact_overview = Mock()
    widget._compact_overview.selected_providers.return_value = ["codex", "claude"]
    safe = TokenData(account_key="current-claude")
    widget._provider_results = {"codex": TokenData(account_key="old-codex"), "claude": safe}
    monkeypatch.setattr(TokenData, "account_key_for_config", lambda config: "current-" + config["ACTIVE_PROVIDER"])
    widget._update_provider_overview()
    widget._compact_overview.board.set_data.assert_called_once_with({"codex": None, "claude": safe})


def test_compact_refresh_limits_targets_and_keeps_only_visible_queue(monkeypatch):
    widget = widget_stub()
    widget._overview_refresh_queue = []
    widget._overview_refresh_active = None
    widget._advance_overview_refresh = Mock()
    widget._compact_overview = Mock()
    widget._compact_overview.selected_providers.return_value = ["codex"]
    monkeypatch.setattr("ui.qt_widget.configured_provider_ids", lambda: ["codex", "claude"])
    widget._refresh_overview(automatic=True, provider_ids=["codex"])
    assert widget._overview_refresh_queue == ["codex"]
    widget._overview_refresh_queue = ["codex", "claude"]
    widget._stop_overview_auto_refresh()
    assert widget._overview_refresh_queue == ["codex"]
    widget._compact_overview.isVisible.return_value = False
    widget._stop_overview_auto_refresh()
    assert widget._overview_refresh_queue == []
