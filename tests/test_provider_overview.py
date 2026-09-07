"""Overview reads isolated snapshots and schedules bounded, account-safe work."""

import os
from datetime import datetime, timedelta
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from api.providers import PROVIDERS
from api.providers.base import FetchError, QuotaWindow
from config import runtime as config_manager
from config.defaults import DEFAULT_CONFIG
from data.store import PerProviderData, TokenData
from test_refresh import widget_stub
from ui.i18n import configure_language
from ui.provider_overview import ProviderOverview, provider_status_message
from ui.qt_panel import MainPanel

APP = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(config_manager, "_config", dict(DEFAULT_CONFIG))
    monkeypatch.setattr(config_manager, "PANEL_LAYOUT_PATH", tmp_path / "layout.json")
    configure_language(APP, "zh-cn")
    yield
    configure_language(APP, "zh-cn")


def snapshot(provider="codex", **values):
    return TokenData(**{
        "per_provider": [PerProviderData(provider, PROVIDERS[provider].name)],
        "last_success_at": datetime.now(), "account_key": "account-a",
        "status": "ok", "quota_source": "interface", **values,
    })


def texts(widget):
    return "\n".join(label.text() for label in widget.findChildren(QLabel))


def test_overview_is_lazy_and_preserves_original_default_page():
    panel = MainPanel()
    assert panel.provider_overview is None
    assert panel.content_stack.currentIndex() == 0
    assert panel.settings_back_button.isHidden()
    page = panel.show_provider_overview()
    assert not panel.settings_back_button.isHidden()
    assert panel.provider_manage_button.isHidden()
    assert page is panel.show_provider_overview()
    panel.show_overview()
    assert panel.content_stack.currentIndex() == 0


def test_analytics_uses_the_same_header_back_button_and_no_native_window():
    panel = MainPanel()
    panel._open_local_analytics()
    assert panel.content_stack.currentWidget() is panel._local_analytics_dialog
    assert not panel._local_analytics_dialog.isWindow()
    assert panel.provider_manage_button.isHidden()
    assert not panel.settings_back_button.isHidden()
    panel.settings_back_button.click()
    assert panel.content_stack.currentIndex() == 0


def test_header_refresh_in_local_analytics_only_scans_logs():
    widget = widget_stub()
    widget.refresh = Mock()
    widget.panel.content_stack.currentWidget.return_value = widget.panel._local_analytics_dialog
    widget._refresh_from_panel()
    widget.panel._local_analytics_dialog.scan.assert_called_once_with()
    widget.refresh.assert_not_called()


def test_empty_unknown_and_mixed_currencies_do_not_invent_values():
    page = ProviderOverview()
    page.set_data({})
    assert not page.empty.isHidden()
    page.set_data({"codex": None, "deepseek": snapshot("deepseek", balance_cny=12),
                   "openrouter": snapshot("openrouter", currency="USD", today_cost_cny=0.3)})
    assert "尚未采集" in texts(page.cards["codex"])
    assert "0%" not in texts(page.cards["codex"])
    assert "¥12.00" in texts(page.cards["deepseek"])
    assert "$0.30" in texts(page.cards["openrouter"])
    assert "¥" not in texts(page.cards["openrouter"])


def test_tightest_quota_first_and_hostile_titles_are_plain_text():
    page = ProviderOverview()
    data = snapshot(quota_windows=[
        QuotaWindow("a", "loose", 10), QuotaWindow("b", "middle", 30),
        QuotaWindow("c", "<img src='https://invalid'>", 95),
    ])
    page.set_data({"codex": data})
    summary = page.cards["codex"].layout().itemAt(1).widget()
    gauge = page.cards["codex"]._gauges[0]
    assert gauge[1].text().startswith("<img")
    assert "剩余 5%" in gauge[2].text()
    assert gauge[3].value() == 50
    assert "还有 1 个" in summary.text()
    assert summary.textFormat() == Qt.TextFormat.PlainText


@pytest.mark.parametrize("view", ["overview", "analytics"])
def test_embedded_pages_preserve_transparent_bottom_corners(view):
    from ui.qt_theme import configure_theme

    configure_theme(APP, "light")
    host = QWidget()
    host.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    panel = MainPanel()
    layout.addWidget(panel)
    if view == "overview":
        panel.show_provider_overview().set_data({"codex": snapshot()})
    else:
        panel._open_local_analytics()
    host.show()
    APP.processEvents()
    image = host.grab().toImage()
    assert image.pixelColor(0, image.height() - 1).alpha() == 0
    assert image.pixelColor(image.width() - 1, image.height() - 1).alpha() == 0
    host.close()


def test_unknown_quota_has_no_fake_bar_and_cached_quota_is_muted():
    page = ProviderOverview()
    page.set_data({"codex": snapshot(is_stale=True, quota_windows=[QuotaWindow("weekly", "周额度", 80)])})
    assert page.cards["codex"]._gauges[0][3].property("tone") == "stale"
    page.set_data({"codex": snapshot(quota_windows=[QuotaWindow("weekly", "周额度", float("nan"))])})
    assert page.cards["codex"]._gauges[0][3].isHidden()


def test_provider_with_unlimited_metric_is_not_shown_as_unavailable():
    from api.providers.base import QuotaMetric

    page = ProviderOverview()
    page.set_data({"copilot": snapshot("copilot", quota_metrics=[QuotaMetric("Chat", "不限量", value_kind="unlimited")])})
    assert "不限量" in texts(page.cards["copilot"])


def test_data_refresh_does_not_reapply_whole_overview_styles(monkeypatch):
    page = ProviderOverview()
    styles = Mock(wraps=page.setStyleSheet)
    monkeypatch.setattr(page, "setStyleSheet", styles)
    page.set_data({"codex": snapshot(quota_windows=[QuotaWindow("weekly", "周额度", 20)])})
    page.set_data({"codex": snapshot(quota_windows=[QuotaWindow("weekly", "周额度", 95)])})
    assert styles.call_count == 0
    assert page.cards["codex"]._gauges[0][3].property("tone") == "low"


def test_refresh_preserves_cards_scroll_and_language_bindings():
    page = ProviderOverview()
    page.resize(640, 430)
    page.show()
    data = {pid: snapshot(pid) for pid in PROVIDERS}
    page.set_data(data)
    APP.processEvents()
    first = page.cards["deepseek"]
    page.scroll.verticalScrollBar().setValue(150)
    page.set_data(data)
    APP.processEvents()
    assert page.cards["deepseek"] is first
    assert page.scroll.verticalScrollBar().value() == 150
    configure_language(APP, "en")
    assert page.refresh_button.text() == "Refresh overview"
    page.set_data({"codex": None})
    assert set(page.cards) == {"codex"}


@pytest.mark.parametrize("code,expected", [
    ("AUTH_EXPIRED", "重新连接"), ("RATE_LIMITED", "退避"), ("NOT_CONFIGURED", "连接配置"),
])
def test_recovery_status_prioritizes_codes_without_leaking_response(code, expected):
    data = snapshot(is_stale=True, errors=[FetchError(code, "quota", "secret-token")])
    assert expected in provider_status_message(data)
    assert "secret-token" not in provider_status_message(data)


def test_connection_action_preserves_provider_identity():
    page = ProviderOverview()
    page.set_data({"claude": snapshot("claude")})
    spy = QSignalSpy(page.connection_requested)
    page.cards["claude"].layout().itemAt(0).layout().itemAt(3).widget().click()
    assert spy.at(0) == ["claude"]


def test_overview_drops_other_accounts_and_disabled_providers(monkeypatch):
    widget = widget_stub()
    widget._overview_opened = True
    widget._overview_ids = ["codex", "claude"]
    widget._overview_refresh_queue = []
    widget._overview_refresh_active = None
    widget._provider_results = {"codex": snapshot(), "claude": snapshot("claude")}
    monkeypatch.setattr(TokenData, "account_key_for_config", lambda _: "account-b")
    monkeypatch.setattr(config_manager, "all_config", lambda: {"DISABLED_PROVIDER_IDS": ["claude"]})
    widget._update_provider_overview()
    widget.panel.provider_overview.set_data.assert_called_once_with({"codex": None})


def test_batch_starts_one_provider_and_skips_backoff_without_changing_preferences(monkeypatch):
    widget = widget_stub()
    widget._overview_opened = False
    widget._overview_refresh_queue = ["codex", "claude", "deepseek"]
    widget._overview_refresh_active = None
    widget._start_provider_refresh = Mock(side_effect=[False, True])
    widget._advance_overview_refresh()
    assert widget._overview_refresh_active == "claude"
    assert widget._overview_refresh_queue == ["deepseek"]
    assert widget._start_provider_refresh.call_count == 2
    assert widget._start_provider_refresh.call_args.kwargs == {
        "lightweight": True, "queue_if_busy": False, "reason": "overview",
    }


def test_overview_obeys_rate_limit_backoff(monkeypatch):
    import time

    widget = widget_stub()
    monkeypatch.setattr(TokenData, "account_key_for_config", lambda _: "account-a")
    widget._provider_refresh_backoff[("codex", "account-a")] = (1, time.monotonic() + 60)
    assert not widget._start_provider_refresh("codex", DEFAULT_CONFIG, lightweight=True,
                                               queue_if_busy=False, reason="overview")
    widget._thread_pool.start.assert_not_called()


def test_overview_auto_refresh_starts_on_show_and_stops_when_hidden():
    page = ProviderOverview()
    spy = QSignalSpy(page.auto_refresh_requested)
    page.show()
    APP.processEvents()
    assert spy.count() == 1
    assert page._auto_timer.isActive()
    page.hide()
    assert not page._auto_timer.isActive()


def test_auto_overview_skips_recently_refreshed_providers(monkeypatch):
    import time

    widget = widget_stub()
    widget._overview_refresh_queue = []
    widget._overview_refresh_active = None
    widget._provider_last_started["codex"] = time.monotonic() - 10
    widget._advance_overview_refresh = Mock()
    monkeypatch.setattr("ui.qt_widget.configured_provider_ids", lambda: ["codex", "claude"])
    widget._refresh_overview(automatic=True)
    assert widget._overview_refresh_queue == ["claude"]
    widget._advance_overview_refresh.assert_called_once_with()
    widget._stop_overview_auto_refresh()
    assert widget._overview_refresh_queue == []
