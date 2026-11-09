"""OpenAI balance aggregation and shared view scheduling (no live login/network)."""

import os
import threading
from datetime import datetime
from decimal import Decimal
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from api.providers.base import FetchError, ProviderBalance
from api.providers.openai import OpenAIProvider
from config.defaults import DEFAULT_CONFIG
from data.store import PerProviderData, TokenData
from ui.qt_widget import FloatingWidget

APP = QApplication.instance() or QApplication([])


@pytest.fixture
def configured(monkeypatch):
    values = {**DEFAULT_CONFIG, "ACTIVE_PROVIDER": "codex", "OPENAI_AUTH": "Bearer synthetic-test",
              "OPENAI_ORGANIZATION": "org-test-alpha"}
    monkeypatch.setattr("ui.qt_widget.config_manager.all_config", lambda: dict(values))
    monkeypatch.setattr("ui.qt_widget.config_manager.get", lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(TokenData, "_provider_snapshots", {})
    return values


def identity(values):
    return TokenData.account_key_for_config({**values, "ACTIVE_PROVIDER": "openai"})


def snapshot(values, balance=24.8486408):
    return TokenData(account_key=identity(values), balance_cny=balance, currency="USD", status="ok",
                     last_success_at=datetime(2026, 11, 9, 18, 0), last_updated="18:00:00",
                     per_provider=[PerProviderData("openai", "OpenAI API", currency="USD")])


def controller():
    widget = FloatingWidget.__new__(FloatingWidget)
    widget._closed = False
    widget._expanded = True
    widget._data = TokenData(status="ok", last_updated="codex unchanged",
                             per_provider=[PerProviderData("codex", "Codex")])
    widget._refresh_lock = threading.Lock()
    widget._request_id = 0
    widget._in_flight_requests = {}
    widget._pending_refreshes = {}
    widget._provider_results = {}
    widget._provider_last_started = {}
    widget._provider_task_started = {}
    widget._provider_refresh_backoff = {}
    widget._auth_expired_providers = set()
    widget._auth_notified_providers = set()
    widget._auth_expired_provider_id = None
    widget._overview_refresh_active = None
    widget._thread_pool = Mock()
    widget._refreshing = False
    widget._settings_window = None
    widget.panel = Mock()
    widget.panel.isVisible.return_value = True
    widget.panel.content_stack.currentIndex.return_value = 0
    widget._apply_update = Mock()
    widget._sync_vpet_usage = Mock()
    widget._notify_auth_expired = Mock()
    widget._notify_low_quota = Mock()
    widget._update_provider_overview = Mock(side_effect=widget._sync_openai_balance)
    return widget


def test_unconnected_balance_hides_footer_and_does_not_schedule(configured):
    widget = controller()
    configured["OPENAI_AUTH"] = ""
    widget._start_provider_refresh = Mock()
    widget._sync_openai_balance()
    widget._refresh_openai_balance(configured)
    assert widget.panel.set_openai_balance.call_args.kwargs["connected"] is False
    assert widget.panel.set_openai_balance.call_args.args == (None,)
    widget._start_provider_refresh.assert_not_called()


def test_no_success_record_passes_unknown_not_zero(configured):
    widget = controller()
    widget._sync_openai_balance()
    assert widget.panel.set_openai_balance.call_args.args == (None,)
    assert widget.panel.set_openai_balance.call_args.kwargs["connected"] is True
    assert "org-test-alpha" not in widget.panel.set_openai_balance.call_args.kwargs["organization"]


def test_selected_unconnected_rail_explains_not_connected(configured):
    widget = controller()
    configured["OPENAI_AUTH"] = ""
    rail = widget._light_ring = Mock()
    rail.isVisible.return_value = True
    rail.selected_providers.return_value = ["openai"]
    rail.selected_sources.return_value = ["openai"]
    FloatingWidget._update_provider_overview(widget)
    empty = rail.set_data.call_args.args[0]["openai"]
    assert empty.balance_cny is None and empty.status == "not_configured"
    assert empty.errors[0].code == "NOT_CONFIGURED"
    widget._thread_pool.start.assert_not_called()


def test_footer_and_rail_share_default_snapshot_without_request(configured):
    widget = controller()
    data = snapshot(configured, 0)
    widget._provider_results["openai"] = data
    widget._sync_openai_balance()
    assert widget.panel.set_openai_balance.call_args.args[0] is data
    widget._thread_pool.start.assert_not_called()
    assert widget._data.last_updated == "codex unchanged"


def test_organization_switch_never_exposes_previous_balance(configured):
    widget = controller()
    old = snapshot(configured)
    widget._provider_results["openai"] = old
    TokenData._provider_snapshots["openai"] = old
    configured["OPENAI_ORGANIZATION"] = "org-test-beta"
    widget._sync_openai_balance()
    assert widget.panel.set_openai_balance.call_args.args[0] is None
    assert widget.panel.set_openai_balance.call_args.kwargs["connected"] is True


def test_codex_visible_panel_adds_one_shared_request_not_a_pending_retry(configured):
    widget = controller()
    widget._refresh_openai_balance(configured)
    widget._refresh_openai_balance(configured)
    assert widget._thread_pool.start.call_count == 1
    task = widget._thread_pool.start.call_args.args[0]
    assert task.provider_id == "openai"
    assert task._config["ACTIVE_PROVIDER"] == "openai"
    assert configured["ACTIVE_PROVIDER"] == "codex"
    assert configured["BACKGROUND_PROVIDER_IDS"] == []
    assert widget._pending_refreshes == {}
    assert widget._start_provider_refresh("openai", configured, lightweight=True,
                                          queue_if_busy=False, reason="overview") is False
    assert widget._thread_pool.start.call_count == 1
    assert widget._pending_refreshes == {}


def test_current_api_panel_refresh_does_not_queue_second_request_from_rail(configured):
    widget = controller()
    configured["ACTIVE_PROVIDER"] = "openai"
    widget._in_flight_requests["openai"] = 1
    widget.refresh()
    assert widget._pending_refreshes == {}
    widget._thread_pool.start.assert_not_called()


def test_automatic_rail_queue_rechecks_api_collected_while_waiting(configured):
    import time

    widget = controller()
    widget._overview_refresh_queue = ["openai"]
    widget._overview_batch_automatic = True
    widget._provider_last_started["openai"] = time.monotonic()
    widget._start_provider_refresh = Mock()
    widget._advance_overview_refresh()
    widget._start_provider_refresh.assert_not_called()
    assert widget._overview_refresh_queue == []


@pytest.mark.parametrize("view", ["collapsed", "settings", "hidden", "deepseek", "openai"])
def test_auxiliary_request_only_when_visible_codex_details_need_it(configured, view):
    widget = controller()
    if view == "collapsed":
        widget._expanded = False
    elif view == "settings":
        widget.panel.content_stack.currentIndex.return_value = 1
    elif view == "hidden":
        widget.panel.isVisible.return_value = False
    else:
        configured["ACTIVE_PROVIDER"] = view
    widget._start_provider_refresh = Mock()
    widget._refresh_openai_balance(configured)
    widget._start_provider_refresh.assert_not_called()


def test_auxiliary_requests_respect_existing_backoff(configured):
    widget = controller()
    widget._provider_refresh_backoff[("openai", identity(configured))] = (1, float("inf"))
    widget._refresh_openai_balance(configured)
    widget._thread_pool.start.assert_not_called()


@pytest.mark.parametrize("change", ["organization", "auth", "remove"])
def test_late_result_cannot_refill_changed_or_deleted_connection(configured, change):
    widget = controller()
    old = snapshot(configured)
    widget._in_flight_requests["openai"] = 1
    if change == "organization":
        configured["OPENAI_ORGANIZATION"] = "org-test-beta"
    elif change == "auth":
        configured["OPENAI_AUTH"] = "Bearer synthetic-new"
    else:
        configured["OPENAI_AUTH"] = configured["OPENAI_ORGANIZATION"] = ""
        configured["DISABLED_PROVIDER_IDS"] = ["openai"]
    with patch("ui.qt_widget.QTimer.singleShot"):
        widget._finish_refresh(1, "openai", old)
    assert "openai" not in widget._provider_results
    assert widget._data.last_updated == "codex unchanged"
    widget._notify_auth_expired.assert_not_called()
    widget._apply_update.assert_not_called()
    if change != "remove":
        assert widget.panel.set_openai_balance.call_args.args[0] is None


def test_noncurrent_failure_keeps_codex_state_time_and_shared_old_balance(configured):
    widget = controller()
    result = snapshot(configured)
    result.status, result.is_stale = "error", True
    result.errors = [FetchError("NETWORK_ERROR", "API 余额", "安全错误提示")]
    widget._in_flight_requests["openai"] = 1
    current = widget._data
    widget._finish_refresh(1, "openai", result)
    assert widget._data is current
    assert widget._data.status == "ok" and widget._data.last_updated == "codex unchanged"
    assert widget.panel.set_openai_balance.call_args.args[0] is result
    widget._apply_update.assert_not_called()


def test_ordinary_ball_follows_current_source_and_marks_only_api_stale(configured):
    from ui.qt_ball import FloatingUsageBall

    widget = controller()
    widget.ball = FloatingUsageBall()
    widget.ball.set_values = Mock(wraps=widget.ball.set_values)
    widget.ball.set_labels = Mock(wraps=widget.ball.set_labels)
    widget._sync_openai_balance = Mock()
    result = snapshot(configured)
    result.is_stale = True
    result.errors = [FetchError("NETWORK_ERROR", "API 余额", "安全错误提示")]
    widget._data = result
    FloatingWidget._apply_update(widget)
    widget.ball.set_values.assert_called_with("--", "$24.85")
    widget.ball.set_labels.assert_called_with("今日使用", "API 未更新")
    widget._data = TokenData(status="ok", balance_cny=12.15, currency="CNY",
                            per_provider=[PerProviderData("deepseek", "DeepSeek")])
    FloatingWidget._apply_update(widget)
    widget.ball.set_values.assert_called_with("--", "¥12.15")
    widget.ball.set_labels.assert_called_with("今日使用", "余额")
    assert widget.ball.toolTip() == ""
    assert "_ball_openai_details" not in widget.__dict__


def fetch_with_balance(values, outcome):
    provider = OpenAIProvider({**values, "ACTIVE_PROVIDER": "openai"})
    provider.fetch_balance = Mock(return_value=outcome)
    try:
        return TokenData._fetch_with_provider(provider, lightweight=True, include_minute_history=False)
    finally:
        provider.close()


def test_server_confirmed_zero_is_success_and_unknown_usage_remains_unavailable(configured):
    result = fetch_with_balance(configured, (ProviderBalance("USD", Decimal("0")), None))
    assert result.balance_cny == 0 and result.last_success_at is not None
    assert result.currency == "USD" and result.status == "ok"
    assert isinstance(result.balance_cny, Decimal)
    assert result.today_tokens is None and result.monthly_usage_tokens is None
    assert result.today_cost_cny is None and result.monthly_cost_cny is None
    assert result.account_label and "org-test-alpha" not in result.account_label


def test_aggregation_retains_only_same_identity_old_value_and_success_time(configured):
    success = fetch_with_balance(configured, (ProviderBalance("USD", Decimal("24.8486408")), None))
    failure = (None, FetchError("NETWORK_ERROR", "API 余额", "稍后重试"))
    stale = fetch_with_balance(configured, failure)
    assert stale.balance_cny == success.balance_cny
    assert stale.last_success_at == success.last_success_at and stale.last_updated == success.last_updated
    assert stale.is_stale and stale.status == "error"
    configured["OPENAI_ORGANIZATION"] = "org-test-beta"
    other = fetch_with_balance(configured, failure)
    assert other.balance_cny is None and other.last_success_at is None


def test_subcent_nonzero_balance_does_not_underflow_to_real_zero(configured):
    from ui.formatting import format_openai_balance

    result = fetch_with_balance(configured, (ProviderBalance("USD", Decimal("1e-400")), None))
    assert result.balance_cny == Decimal("1e-400")
    assert format_openai_balance(result.balance_cny) == "<$0.01"


def test_missing_credentials_never_query_or_keep_old_balance(configured):
    fetch_with_balance(configured, (ProviderBalance("USD", Decimal("24.85")), None))
    configured["OPENAI_AUTH"] = ""
    provider = OpenAIProvider(configured)
    provider.fetch_balance = Mock()
    try:
        result = TokenData._fetch_with_provider(provider, lightweight=True, include_minute_history=False)
    finally:
        provider.close()
    provider.fetch_balance.assert_not_called()
    assert result.balance_cny is None and result.last_success_at is None
