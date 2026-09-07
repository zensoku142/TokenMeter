import os
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

os.environ["APPDATA"] = str(Path.cwd() / ".test-appdata")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from data.store import FetchError, PerProviderData, TokenData
from api.deepseek_pricing import BEIJING_TIMEZONE, PricingState
from api.providers.base import QuotaMetric, QuotaWindow
from config.defaults import DEFAULT_CONFIG
from config.store import validate_value
from ui.qt_panel import MainPanel
from ui.qt_widget import (
    BACKGROUND_PROVIDER_INTERVAL_MS,
    FetchTask,
    FloatingWidget,
    MiMoRenewalTask,
    _fetch_tokens_safely,
)

APP = QApplication.instance() or QApplication([])


def widget_stub():
    widget = FloatingWidget.__new__(FloatingWidget)
    widget._refresh_lock = __import__("threading").Lock()
    widget._refreshing = False
    widget._request_id = 0
    widget._in_flight_requests = {}
    widget._pending_refreshes = {}
    widget._provider_results = {}
    widget._provider_last_started = {}
    widget._provider_task_started = {}
    widget._provider_refresh_backoff = {}
    widget._quota_alerted_windows = {}
    widget._closed = False
    widget._vpet = Mock(active=False)
    widget._vpet_updating = False
    widget._data = TokenData()
    widget._expanded = False
    widget._edge_snapped = False
    widget._apply_update = Mock()
    widget._thread_pool = Mock()
    widget._refresh_timer = Mock()
    widget._background_refresh_timer = Mock()
    widget.panel = Mock()
    widget.tray = Mock()
    widget.open_settings = Mock()
    widget._sync_pricing_state = Mock()
    widget._auth_expired_providers = set()
    widget._auth_notified_providers = set()
    widget._auth_expired_provider_id = None
    widget._mimo_renewal_task = None
    widget._mimo_renewal_account_key = None
    widget._mimo_renewal_attempted = False
    return widget


def finish(widget, provider_id, request_id, result, active_provider=None):
    active = active_provider or provider_id
    widget._in_flight_requests[provider_id] = request_id
    widget._provider_task_started[provider_id] = __import__("time").monotonic()
    with patch("ui.qt_widget.config_manager.get", return_value=active):
        widget._finish_refresh(request_id, provider_id, result)


def pricing_widget_stub():
    widget = FloatingWidget.__new__(FloatingWidget)
    widget._pricing_state = None
    widget._pricing_timer = Mock()
    widget.panel = Mock()
    widget.ball = Mock()
    widget.tray = Mock()
    widget._vpet = Mock(active=True)
    widget._data = TokenData(status="ok", balance_cny=12.8)
    widget._refreshing = False
    return widget


class RefreshTests(unittest.TestCase):
    def setUp(self):
        # 调度测试不读取开发机 CLI 登录；账号切换用例显式提供模拟指纹。
        self.identity_patch = patch.object(TokenData, "account_key_for_config", return_value="")
        self.identity_patch.start()
        self.addCleanup(self.identity_patch.stop)

    def test_pet_pricing_outline_updates_at_boundary_and_clears_when_disabled(self):
        widget = pricing_widget_stub()
        values = {"DEEPSEEK_PEAK_PRICING_ENABLED": True, "ACTIVE_PROVIDER": "deepseek"}
        boundary = datetime(2026, 7, 15, 12, 0, tzinfo=BEIJING_TIMEZONE)
        with (
            patch("ui.qt_widget.config_manager.get", side_effect=lambda key, default=None: values.get(key, default)),
            patch("ui.qt_widget.config_manager.all_config", return_value={}),
            patch("ui.qt_widget.pricing_state", side_effect=[
                PricingState(False, "平时", "", boundary), PricingState(True, "峰时", "", boundary)
            ]),
        ):
            widget._sync_pricing_state(notify_transition=False)
            self.assertIs(widget._vpet.update_usage.call_args.args[0]["pricing_peak"], False)
            widget._on_pricing_boundary()
            self.assertIs(widget._vpet.update_usage.call_args.args[0]["pricing_peak"], True)
            values["DEEPSEEK_PEAK_PRICING_ENABLED"] = False
            widget._sync_pricing_state(notify_transition=False)
            self.assertNotIn("pricing_peak", widget._vpet.update_usage.call_args.args[0])

    def test_peak_pricing_notifies_only_on_running_offpeak_to_peak_transition(self):
        widget = pricing_widget_stub()
        offpeak = PricingState(
            False,
            "平时 1× · 09:00 进入峰时",
            "offpeak",
            datetime(2026, 7, 15, 9, 0, tzinfo=BEIJING_TIMEZONE),
        )
        peak = PricingState(
            True,
            "峰时 2× · 12:00 结束",
            "peak",
            datetime(2026, 7, 15, 12, 0, tzinfo=BEIJING_TIMEZONE),
        )
        with (
            patch("ui.qt_widget.config_manager.get") as get_config,
            patch("ui.qt_widget.config_manager.all_config", return_value={}),
            patch("ui.qt_widget.pricing_state", return_value=peak),
        ):
            get_config.side_effect = lambda key, default=None: {
                "DEEPSEEK_PEAK_PRICING_ENABLED": True,
                "ACTIVE_PROVIDER": "deepseek",
            }.get(key, default)

            # Startup or a config save renders the current state without notification.
            widget._sync_pricing_state(notify_transition=False)
            widget.tray.showMessage.assert_not_called()

            widget._pricing_state = offpeak
            widget._auth_expired_provider_id = "mimo"
            widget._sync_pricing_state(notify_transition=True)
            widget._sync_pricing_state(notify_transition=True)

        widget.tray.showMessage.assert_called_once()
        title, message, icon, timeout = widget.tray.showMessage.call_args.args
        self.assertEqual(title, "TokenMeter：DeepSeek 已进入高峰计价")
        self.assertIn("本时段至 12:00（北京时间）", message)
        self.assertEqual(icon, QSystemTrayIcon.MessageIcon.Warning)
        self.assertEqual(timeout, 10_000)
        widget.panel.set_pricing_state.assert_called_with(
            True, True, peak.label, peak.tooltip
        )
        widget.ball.set_peak_highlight.assert_called_with(True)
        self.assertIsNone(widget._auth_expired_provider_id)

    def test_pet_failure_notification_does_not_open_stale_authentication_action(self):
        widget = widget_stub()
        widget._expanded = True
        widget._set_theme_feedback = Mock()
        widget._auth_expired_provider_id = "mimo"
        widget._on_vpet_failed("synthetic pet failure")
        widget.handle_auth_expired_notification_click()
        widget.open_settings.assert_not_called()

    def test_peak_pricing_stops_and_clears_ui_when_disabled_or_provider_changes(self):
        widget = pricing_widget_stub()
        widget._pricing_state = PricingState(
            True,
            "peak",
            "peak",
            datetime(2026, 7, 15, 12, 0, tzinfo=BEIJING_TIMEZONE),
        )
        with patch("ui.qt_widget.config_manager.get") as get_config:
            get_config.side_effect = lambda key, default=None: {
                "DEEPSEEK_PEAK_PRICING_ENABLED": True,
                "ACTIVE_PROVIDER": "mimo",
            }.get(key, default)
            widget._sync_pricing_state(notify_transition=True)

        widget._pricing_timer.stop.assert_called_once()
        self.assertIsNone(widget._pricing_state)
        widget.panel.set_pricing_state.assert_called_once_with(False)
        widget.ball.set_peak_highlight.assert_called_once_with(False)
        widget.tray.showMessage.assert_not_called()

    def test_panel_and_ball_use_configured_refresh_interval(self):
        for provider, expanded in (("deepseek", False), ("mimo", False), ("deepseek", True)):
            widget = widget_stub()
            widget._expanded = expanded
            with patch("ui.qt_widget.config_manager.get") as get_config:
                get_config.side_effect = lambda key, default=None: {
                    "ACTIVE_PROVIDER": provider,
                    "REFRESH_INTERVAL": 51_000,
                }.get(key, default)
                widget._reschedule_refresh()
            self.assertEqual(widget._refresh_timer.start.call_args.args[0], 51_000)

    def test_compact_mimo_uses_lightweight_refresh(self):
        widget = widget_stub()

        with patch(
            "ui.qt_widget.config_manager.all_config",
            return_value={"ACTIVE_PROVIDER": "mimo"},
        ):
            widget.refresh()

        task = widget._thread_pool.start.call_args.args[0]
        self.assertTrue(task._lightweight)
        self.assertEqual(task._config["ACTIVE_PROVIDER"], "mimo")

    def test_background_cycle_starts_only_selected_configured_non_current_provider(self):
        widget = widget_stub()
        config = {
            "ACTIVE_PROVIDER": "codex",
            "BACKGROUND_PROVIDER_IDS": ["deepseek", "codex", "nayuto"],
            "MARKER": "captured",
        }
        with (
            patch("ui.qt_widget.config_manager.all_config", return_value=config),
            patch(
                "ui.qt_widget.configured_provider_ids",
                return_value=["deepseek", "mimo", "codex", "nayuto"],
            ),
            patch("ui.qt_widget.config_manager.get", return_value="codex"),
        ):
            widget._periodic_background_refresh()

        self.assertEqual(widget._thread_pool.start.call_count, 2)
        tasks = [call.args[0] for call in widget._thread_pool.start.call_args_list]
        self.assertEqual(
            [task.provider_id for task in tasks], ["deepseek", "nayuto"]
        )
        self.assertTrue(all(task._lightweight for task in tasks))
        self.assertTrue(all(task._config["MARKER"] == "captured" for task in tasks))

    def test_background_cycle_does_not_start_unconfigured_provider(self):
        widget = widget_stub()
        with (
            patch(
                "ui.qt_widget.config_manager.all_config",
                return_value={
                    "ACTIVE_PROVIDER": "codex",
                    "BACKGROUND_PROVIDER_IDS": ["mimo"],
                },
            ),
            patch("ui.qt_widget.configured_provider_ids", return_value=["codex"]),
        ):
            widget._periodic_background_refresh()

        widget._thread_pool.start.assert_not_called()

    def test_background_cycle_defaults_to_current_provider_only(self):
        widget = widget_stub()
        with (
            patch(
                "ui.qt_widget.config_manager.all_config",
                return_value={"ACTIVE_PROVIDER": "codex"},
            ),
            patch("ui.qt_widget.configured_provider_ids") as configured,
        ):
            widget._periodic_background_refresh()

        configured.assert_not_called()
        widget._thread_pool.start.assert_not_called()

    def test_provider_level_in_flight_does_not_block_other_provider(self):
        widget = widget_stub()
        with patch("ui.qt_widget.config_manager.get", return_value="codex"):
            self.assertTrue(
                widget._start_provider_refresh(
                    "deepseek",
                    {"ACTIVE_PROVIDER": "codex"},
                    lightweight=True,
                    queue_if_busy=False,
                    reason="test",
                )
            )
            self.assertFalse(
                widget._start_provider_refresh(
                    "deepseek",
                    {"ACTIVE_PROVIDER": "codex"},
                    lightweight=True,
                    queue_if_busy=False,
                    reason="test",
                )
            )
            self.assertTrue(
                widget._start_provider_refresh(
                    "mimo",
                    {"ACTIVE_PROVIDER": "codex"},
                    lightweight=True,
                    queue_if_busy=False,
                    reason="test",
                )
            )

        self.assertEqual(widget._thread_pool.start.call_count, 2)
        self.assertEqual(set(widget._in_flight_requests), {"deepseek", "mimo"})

    def test_background_provider_never_runs_faster_than_sixty_seconds(self):
        widget = widget_stub()
        widget._provider_last_started["mimo"] = __import__("time").monotonic()
        with (
            patch(
                "ui.qt_widget.config_manager.all_config",
                return_value={
                    "ACTIVE_PROVIDER": "codex",
                    "BACKGROUND_PROVIDER_IDS": ["mimo"],
                },
            ),
            patch("ui.qt_widget.configured_provider_ids", return_value=["mimo", "codex"]),
        ):
            widget._periodic_background_refresh()

        self.assertEqual(BACKGROUND_PROVIDER_INTERVAL_MS, 60_000)
        widget._thread_pool.start.assert_not_called()

    def test_fetch_task_owns_independent_config_snapshot(self):
        config = {"ACTIVE_PROVIDER": "mimo", "MIMO_COOKIE": "original"}
        task = FetchTask(1, config, lightweight=True)
        config["ACTIVE_PROVIDER"] = "codex"
        config["MIMO_COOKIE"] = "changed"

        self.assertEqual(task.provider_id, "mimo")
        self.assertEqual(task._config["MIMO_COOKIE"], "original")

    def test_unexpected_refresh_failure_preserves_only_same_account_cache(self):
        saved_at = datetime(2026, 7, 3, 10, 0)
        cached = TokenData(
            account_key="A", status="ok", today_tokens=7, balance_cny=12.3,
            last_success_at=saved_at,
            per_provider=[PerProviderData("deepseek", "DeepSeek", status="ok")],
            refresh_error_codes=("RATE_LIMITED",),
        )
        for account_key in ("A", "B"):
            with (
                self.subTest(account_key=account_key),
                patch.object(TokenData, "_provider_snapshots", {"deepseek": cached}),
                patch.object(TokenData, "account_key_for_config", return_value=account_key),
                patch.object(TokenData, "fetch", side_effect=RuntimeError("synthetic failure")),
            ):
                result = _fetch_tokens_safely({"ACTIVE_PROVIDER": "deepseek"})
                self.assertEqual(result.account_key, account_key)
                self.assertEqual(result.status, "error")
                self.assertEqual(result.errors[0].code, "UNKNOWN_ERROR")
                self.assertEqual(result.refresh_error_codes, ("UNKNOWN_ERROR",))
                self.assertEqual(result.per_provider[0].errors, result.errors)
                if account_key == "A":
                    self.assertEqual(result.today_tokens, 7)
                    self.assertEqual(result.balance_cny, 12.3)
                    self.assertEqual(result.last_success_at, saved_at)
                    self.assertTrue(result.is_stale)
                    self.assertTrue(result.per_provider[0].is_stale)
                else:
                    self.assertIsNone(result.balance_cny)
                    self.assertIsNone(result.last_success_at)
                    self.assertFalse(result.is_stale)
        self.assertEqual(cached.status, "ok")
        self.assertEqual(cached.errors, [])

    def test_identity_failure_does_not_restore_unscoped_cache(self):
        with (
            patch.object(TokenData, "account_key_for_config", side_effect=RuntimeError("identity failed")),
            patch.object(TokenData, "fetch", return_value=TokenData(status="ok")),
            patch.object(TokenData, "cached_snapshot") as cached,
        ):
            result = _fetch_tokens_safely({"ACTIVE_PROVIDER": "deepseek"})
        self.assertEqual(result.status, "error")
        self.assertEqual(result.account_key, "")
        self.assertEqual(result.errors[0].code, "UNKNOWN_ERROR")
        cached.assert_not_called()

    def test_old_account_result_is_discarded_and_current_account_is_queued(self):
        widget = widget_stub()
        old = TokenData(account_key="A", today_tokens=999)
        widget._data = TokenData(account_key="B", today_tokens=1)
        with (
            patch.object(TokenData, "account_key_for_config", return_value="B"),
            patch("ui.qt_widget.config_manager.all_config", return_value={"ACTIVE_PROVIDER": "codex"}),
            patch("ui.qt_widget.QTimer.singleShot") as queue,
        ):
            finish(widget, "codex", 1, old)
        assert widget._data.today_tokens != 999
        assert "codex" not in widget._provider_results
        assert widget._refreshing
        queue.assert_called_once()

    def test_unscoped_claude_result_is_not_cached_for_provider_switches(self):
        widget = widget_stub()
        result = TokenData(account_key="", status="ok")
        with patch("ui.qt_widget.config_manager.all_config", return_value={"ACTIVE_PROVIDER": "deepseek"}):
            finish(widget, "claude", 1, result, active_provider="deepseek")
        self.assertNotIn("claude", widget._provider_results)

    def test_repeated_refresh_runs_once_then_one_pending(self):
        widget = widget_stub()
        with patch("ui.qt_widget.config_manager.get", return_value="deepseek"):
            widget.refresh()
            widget.refresh()
            widget.refresh()
        self.assertEqual(widget._thread_pool.start.call_count, 1)
        self.assertIn("deepseek", widget._pending_refreshes)

    def test_provider_switch_starts_without_waiting_for_previous_refresh(self):
        widget = widget_stub()
        widget._request_id = 4
        widget._in_flight_requests["deepseek"] = 4
        widget._provider_task_started["deepseek"] = __import__("time").monotonic()
        loading = TokenData(
            per_provider=[PerProviderData("codex", "Codex")],
            status="loading",
        )
        snapshot = {"ACTIVE_PROVIDER": "codex", "CODEX_HOME": ""}

        with patch("ui.qt_widget.config_manager.get", return_value="codex"):
            widget._prepare_scope_switch(loading, snapshot)

        self.assertEqual(widget._thread_pool.start.call_count, 1)
        task = widget._thread_pool.start.call_args.args[0]
        self.assertEqual(task.request_id, 5)
        self.assertEqual(task._config, snapshot)
        self.assertIs(widget._data, loading)
        self.assertTrue(widget._refreshing)

        finish(
            widget,
            "deepseek",
            4,
            TokenData(today_tokens=1),
            active_provider="codex",
        )
        self.assertIs(widget._data, loading)
        self.assertTrue(widget._refreshing)
        self.assertEqual(widget._provider_results["deepseek"].today_tokens, 1)

        current = TokenData(
            per_provider=[PerProviderData("codex", "Codex")],
            today_tokens=2,
            status="ok",
        )
        finish(widget, "codex", 5, current)
        self.assertIs(widget._data, current)
        self.assertFalse(widget._refreshing)

    def test_provider_switch_displays_cached_snapshot_during_refresh(self):
        widget = widget_stub()
        cached = TokenData(
            per_provider=[PerProviderData("codex", "Codex")],
            today_tokens=9,
            status="ok",
            last_success_at=datetime.now(),
        )
        snapshot = {"ACTIVE_PROVIDER": "codex", "CODEX_HOME": ""}

        with (
            patch("ui.qt_widget.config_manager.get", side_effect=["deepseek", "codex"]),
            patch("ui.qt_widget.config_manager.save_config", return_value=snapshot),
            patch.object(TokenData, "cached_snapshot", return_value=cached),
        ):
            widget._switch_provider("codex")

        self.assertIs(widget._data, cached)
        self.assertEqual(widget._data.today_tokens, 9)
        self.assertTrue(widget._refreshing)
        self.assertEqual(widget._thread_pool.start.call_count, 1)

    def test_non_current_provider_result_does_not_replace_current_data(self):
        widget = widget_stub()
        current = TokenData(balance_cny=2)
        background = TokenData(balance_cny=1)
        widget._data = current
        widget._refreshing = True
        finish(
            widget,
            "deepseek",
            1,
            background,
            active_provider="codex",
        )
        self.assertIs(widget._data, current)
        self.assertTrue(widget._refreshing)
        self.assertIs(widget._provider_results["deepseek"], background)
        self.assertEqual(widget._provider_results["deepseek"].balance_cny, 1)

    def test_late_nayuto_result_is_cached_without_replacing_deepseek_view(self):
        widget = widget_stub()
        current = TokenData(
            currency="CNY",
            today_cost_cny=2,
            per_provider=[PerProviderData("deepseek", "DeepSeek")],
        )
        relay = TokenData(
            currency="USD",
            today_cost_cny=1,
            daily_model_usage=[
                {"date": "2026-08-15", "models": [{"model": "model-a"}]}
            ],
            minute_model_usage=[{"minute": 1, "model": "model-a"}],
            per_provider=[PerProviderData("nayuto", "NayutoAI", currency="USD")],
        )
        widget._data = current
        widget._refreshing = True

        finish(widget, "nayuto", 1, relay, active_provider="deepseek")

        self.assertIs(widget._data, current)
        self.assertTrue(widget._refreshing)
        self.assertIs(widget._provider_results["nayuto"], relay)
        self.assertEqual(
            widget._provider_results["nayuto"].minute_model_usage[0]["model"],
            "model-a",
        )
        self.assertEqual(widget._data.per_provider[0].provider_id, "deepseek")
        self.assertEqual(widget._data.daily_model_usage, [])
        widget._apply_update.assert_not_called()

    def test_current_provider_result_updates_interface(self):
        widget = widget_stub()
        result = TokenData(
            per_provider=[PerProviderData("codex", "Codex")],
            balance_cny=3,
            status="ok",
        )

        finish(widget, "codex", 1, result)

        self.assertIs(widget._data, result)
        self.assertFalse(widget._refreshing)
        widget._apply_update.assert_called_once()

    def test_provider_failure_does_not_block_other_provider_completion(self):
        widget = widget_stub()
        failed = TokenData(
            per_provider=[PerProviderData("mimo", "小米 MiMo")],
            status="error",
            errors=[FetchError("NETWORK_ERROR", "明细", "连接失败")],
        )
        success = TokenData(
            per_provider=[PerProviderData("codex", "Codex")],
            today_tokens=8,
            status="ok",
        )

        finish(widget, "mimo", 1, failed, active_provider="codex")
        finish(widget, "codex", 2, success)

        self.assertEqual(widget._provider_results["mimo"].status, "error")
        self.assertIs(widget._data, success)
        self.assertEqual(widget._data.today_tokens, 8)

    def test_auth_expired_shows_one_tray_notification_until_recovery(self):
        widget = widget_stub()
        balance_expired = TokenData(
            errors=[FetchError("AUTH_EXPIRED", "余额", "Cookie 已失效")]
        )
        usage_expired = TokenData(
            errors=[FetchError("AUTH_EXPIRED", "用量明细", "登录状态已失效")]
        )

        widget._notify_auth_expired(balance_expired, "deepseek", is_current=True)
        widget._notify_auth_expired(usage_expired, "deepseek", is_current=True)

        self.assertEqual(widget.tray.showMessage.call_count, 1)
        title, message, icon, timeout = widget.tray.showMessage.call_args.args
        self.assertEqual(title, "TokenMeter：登录凭据已失效")
        self.assertIn("Cookie 已失效", message)
        self.assertIn("点击此通知", message)
        self.assertEqual(icon, QSystemTrayIcon.MessageIcon.Warning)
        self.assertEqual(timeout, 10_000)

        widget._notify_auth_expired(TokenData(status="ok"), "deepseek", is_current=True)
        widget._notify_auth_expired(balance_expired, "deepseek", is_current=True)
        self.assertEqual(widget.tray.showMessage.call_count, 2)

    def test_auth_expired_suspends_only_that_provider_periodic_collection(self):
        widget = widget_stub()
        widget._auth_expired_providers.add("deepseek")

        with patch("ui.qt_widget.config_manager.get", return_value="codex"):
            self.assertFalse(
                widget._start_provider_refresh(
                    "deepseek",
                    {"ACTIVE_PROVIDER": "codex"},
                    lightweight=True,
                    queue_if_busy=False,
                    reason="periodic_background",
                )
            )
            self.assertTrue(
                widget._start_provider_refresh(
                    "mimo",
                    {"ACTIVE_PROVIDER": "codex"},
                    lightweight=True,
                    queue_if_busy=False,
                    reason="periodic_background",
                )
            )

        self.assertEqual(widget._thread_pool.start.call_count, 1)
        self.assertEqual(widget._thread_pool.start.call_args.args[0].provider_id, "mimo")

    def test_cached_or_silent_failed_refresh_cannot_rearm_auth_notification(self):
        for values in (
            {"is_stale": True},
            {"refresh_error_codes": ("NETWORK_TIMEOUT",)},
            {"refresh_error_codes": ("RATE_LIMITED",)},
            {"refresh_error_codes": ("SERVER_ERROR",)},
        ):
            with self.subTest(values=values):
                widget = widget_stub()
                expired = TokenData(errors=[FetchError("AUTH_EXPIRED", "额度", "登录已失效")])
                widget._notify_auth_expired(expired, "codex", is_current=True)
                widget._notify_auth_expired(TokenData(status="ok", **values), "codex", is_current=True)
                self.assertIn("codex", widget._auth_expired_providers)
                widget._notify_auth_expired(expired, "codex", is_current=True)
                widget.tray.showMessage.assert_called_once()

    def test_auth_expired_allows_manual_retry(self):
        widget = widget_stub()
        widget._auth_expired_providers.add("deepseek")

        with patch("ui.qt_widget.config_manager.get", return_value="deepseek"):
            started = widget._start_provider_refresh(
                "deepseek",
                {"ACTIVE_PROVIDER": "deepseek"},
                lightweight=True,
                queue_if_busy=False,
                reason="manual",
            )

        self.assertTrue(started)
        widget._thread_pool.start.assert_called_once()

    @patch("ui.qt_widget.config_manager.load_config")
    def test_config_save_reopens_auth_validation_for_all_providers(self, load_config):
        widget = widget_stub()
        widget._auth_expired_providers.update({"deepseek", "mimo"})
        widget._auth_notified_providers.update({"deepseek", "mimo"})
        widget._update_controller = Mock()
        widget._reschedule_refresh = Mock()
        widget.refresh = Mock()

        widget._on_config_saved()

        load_config.assert_called_once_with()
        self.assertEqual(widget._auth_expired_providers, set())
        self.assertEqual(widget._auth_notified_providers, set())
        widget.refresh.assert_called_once_with()

    def test_mimo_auth_expired_starts_silent_renewal(self):
        widget = widget_stub()
        expired = TokenData(
            errors=[FetchError("AUTH_EXPIRED", "MiMo 余额", "Cookie 已失效")],
            per_provider=[PerProviderData("mimo", "小米 MiMo")],
        )

        widget._notify_auth_expired(expired, "mimo", is_current=True)
        task = widget._thread_pool.start.call_args.args[0]
        self.assertIsInstance(task, MiMoRenewalTask)
        self.assertTrue(widget._mimo_renewal_attempted)
        widget.tray.showMessage.assert_not_called()

    def test_non_current_mimo_auth_expired_only_notifies_without_browser_task(self):
        widget = widget_stub()
        expired = TokenData(
            errors=[FetchError("AUTH_EXPIRED", "MiMo 余额", "Cookie 已失效")],
            per_provider=[PerProviderData("mimo", "小米 MiMo")],
        )

        widget._notify_auth_expired(expired, "mimo", is_current=False)

        widget._thread_pool.start.assert_not_called()
        widget.tray.showMessage.assert_called_once()
        self.assertIn("不会自动打开浏览器", widget.tray.showMessage.call_args.args[1])

    def test_background_mimo_auth_expired_still_renews_after_provider_switch(self):
        widget = widget_stub()
        balance_expired = TokenData(
            errors=[FetchError("AUTH_EXPIRED", "MiMo 余额", "Cookie 已失效")]
        )
        usage_expired = TokenData(
            errors=[FetchError("AUTH_EXPIRED", "MiMo 用量", "登录状态已失效")]
        )

        widget._notify_auth_expired(balance_expired, "mimo", is_current=False)
        widget._notify_auth_expired(usage_expired, "mimo", is_current=True)

        self.assertEqual(widget.tray.showMessage.call_count, 1)
        task = widget._thread_pool.start.call_args.args[0]
        self.assertIsInstance(task, MiMoRenewalTask)
        self.assertTrue(widget._mimo_renewal_attempted)

    @patch("ui.qt_widget.config_manager.save_config")
    def test_successful_mimo_renewal_saves_only_cookie_credentials(self, save_config):
        widget = widget_stub()
        widget._mimo_renewal_task = Mock()
        widget._mimo_renewal_attempted = True
        widget._settings_window = Mock()
        widget._refresh_mimo_after_renewal = Mock()

        with patch("ui.qt_widget.MiMoProvider.is_direct_cookie_usable", return_value=True):
            widget._finish_mimo_cookie_renewal(
                "api-platform_ph=ph; api-platform_serviceToken=token; api-platform_slh=slh; userId=1",
                "",
            )

        save_config.assert_called_once_with(
            {
                "MIMO_COOKIE": "api-platform_ph=ph; api-platform_serviceToken=token; api-platform_slh=slh; userId=1",
                "MIMO_API_PLATFORM_PH": "ph",
            }
        )
        widget._settings_window.sync_persisted_cookie.assert_called_once_with(
            "mimo",
            "api-platform_ph=ph; api-platform_serviceToken=token; api-platform_slh=slh; userId=1",
        )
        widget._refresh_mimo_after_renewal.assert_called_once_with()
        self.assertNotIn("mimo", widget._auth_expired_providers)
        self.assertIsNone(widget._mimo_renewal_task)

    def test_mimo_renewal_cannot_overwrite_credentials_changed_while_browser_runs(self):
        for cookie, error in (("old-browser-cookie", ""), ("session", "BROWSER_CONTEXT_ONLY"), ("", "AUTH_EXPIRED")):
            with self.subTest(error=error):
                widget = widget_stub()
                widget._refresh_mimo_after_renewal = Mock()
                account = "A"
                with (
                    patch.object(TokenData, "account_key_for_config", side_effect=lambda config: account),
                    patch("ui.qt_widget.config_manager.all_config", return_value={"ACTIVE_PROVIDER": "codex"}),
                    patch("ui.qt_widget.config_manager.save_config") as save,
                ):
                    widget._start_mimo_cookie_renewal()
                    account = "B"
                    widget._finish_mimo_cookie_renewal(cookie, error)
                save.assert_not_called()
                widget._refresh_mimo_after_renewal.assert_not_called()
                widget.tray.showMessage.assert_not_called()
                self.assertIsNone(widget._mimo_renewal_task)

    @patch("ui.qt_widget.config_manager.save_config", side_effect=OSError("failed"))
    def test_failed_mimo_renewal_save_keeps_manual_recovery_available(self, _save_config):
        widget = widget_stub()
        widget._mimo_renewal_task = Mock()

        with patch("ui.qt_widget.MiMoProvider.is_direct_cookie_usable", return_value=True):
            widget._finish_mimo_cookie_renewal(
                "api-platform_ph=ph; api-platform_serviceToken=token; api-platform_slh=slh; userId=1",
                "",
            )

        self.assertEqual(widget._auth_expired_provider_id, "mimo")
        self.assertIn("mimo", widget._auth_expired_providers)
        self.assertIn("mimo", widget._auth_notified_providers)
        self.assertEqual(widget.tray.showMessage.call_count, 1)

    @patch("ui.qt_widget.MiMoProvider.recover_verified_cookie_via_chrome")
    def test_mimo_renewal_falls_back_to_visible_browser(self, recover_cookie):
        recover_cookie.side_effect = [RuntimeError("MIMO_COOKIE_EMPTY"), "fresh-cookie"]
        task = MiMoRenewalTask()
        finished = Mock()
        task.signals.finished.connect(finished)

        with (
            patch("ui.qt_widget.config_manager.get", return_value="mimo"),
            patch("ui.qt_widget.MiMoProvider.is_direct_cookie_usable", return_value=True),
        ):
            task.run()

        self.assertEqual(recover_cookie.call_count, 2)
        self.assertTrue(recover_cookie.call_args_list[0].kwargs["headless"])
        self.assertFalse(recover_cookie.call_args_list[1].kwargs["headless"])
        finished.assert_called_once_with("fresh-cookie", "")

    @patch("ui.qt_widget.MiMoProvider.recover_verified_cookie_via_chrome")
    def test_mimo_renewal_does_not_open_visible_browser_after_provider_switch(
        self, recover_cookie
    ):
        recover_cookie.side_effect = RuntimeError("MIMO_COOKIE_EMPTY")
        task = MiMoRenewalTask()
        finished = Mock()
        task.signals.finished.connect(finished)

        with patch("ui.qt_widget.config_manager.get", return_value="codex"):
            task.run()

        recover_cookie.assert_called_once()
        self.assertTrue(recover_cookie.call_args.kwargs["headless"])
        finished.assert_called_once_with("", "MIMO_COOKIE_EMPTY")

    @patch("ui.qt_widget.config_manager.save_config")
    def test_browser_only_renewal_does_not_overwrite_cookie_credentials(self, save_config):
        widget = widget_stub()
        widget._mimo_renewal_task = Mock()
        widget._refresh_mimo_after_renewal = Mock()
        widget._auth_expired_provider_id = "deepseek"

        widget._finish_mimo_cookie_renewal(
            "session=browser-only",
            "BROWSER_CONTEXT_ONLY",
        )

        save_config.assert_not_called()
        widget._refresh_mimo_after_renewal.assert_called_once_with()
        self.assertNotIn("mimo", widget._auth_expired_providers)
        self.assertIsNone(widget._auth_expired_provider_id)

    def test_status_summary_distinguishes_configuration_and_request_errors(self):
        cases = (
            ("NOT_CONFIGURED", "尚未配置"),
            ("AUTH_EXPIRED", "认证信息已失效"),
            ("NETWORK_ERROR", "网络连接失败"),
            ("SERVER_ERROR", "API 服务异常"),
        )
        for code, expected in cases:
            data = TokenData(
                status="error", errors=[FetchError(code, "test", "failed")]
            )
            self.assertIn(expected, MainPanel.status_summary(data)[0])

    def test_status_summary_treats_successful_zero_usage_as_normal(self):
        data = TokenData(status="ok", daily_usage=[])
        self.assertIn("暂无 Token 活动", MainPanel.status_summary(data)[0])

    def test_unlimited_quota_ball_supports_semantic_kind_and_both_legacy_values(self):
        for value, kind in (("不限额", ""), ("不限量", ""), ("Unlimited", "unlimited")):
            with self.subTest(value=value, kind=kind):
                widget = widget_stub()
                widget.ball = Mock()
                widget._sync_vpet_usage = Mock()
                widget._data = TokenData(
                    per_provider=[PerProviderData("minimax", "MiniMax")],
                    quota_metrics=[QuotaMetric("Chat", value, "Provider policy", value_kind=kind)],
                )
                FloatingWidget._apply_update(widget)
                widget.ball.set_quota_state.assert_called_once_with(
                    None, "Provider policy", "Chat", value_text=value,
                )


class RefreshBackoffTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.config = {"ACTIVE_PROVIDER": "deepseek", "TEST_ACCOUNT": "A", "REFRESH_INTERVAL": 5000}
        self.widget = widget_stub()
        for item in (
            patch("ui.qt_widget.time.monotonic", side_effect=lambda: self.now),
            patch("ui.qt_widget.config_manager.all_config", side_effect=lambda: dict(self.config)),
            patch("ui.qt_widget.config_manager.get", side_effect=lambda key, default=None: self.config.get(key, default)),
            patch.object(TokenData, "account_key_for_config", side_effect=lambda config: str(config.get("TEST_ACCOUNT", "A"))),
        ):
            item.start()
            self.addCleanup(item.stop)

    def start(self, provider="deepseek", reason="periodic_current"):
        return self.widget._start_provider_refresh(
            provider, self.config, lightweight=True, queue_if_busy=False, reason=reason,
        )

    def complete(self, error_code="", *, provider="deepseek", silent=False, account=None):
        result = TokenData(
            account_key=account or str(self.config["TEST_ACCOUNT"]),
            status="ok" if silent or not error_code else "partial",
            errors=[] if silent or not error_code else [FetchError(error_code, "额度", "测试错误")],
            refresh_error_codes=(error_code,) if silent and error_code else (),
        )
        self.widget._finish_refresh(self.widget._in_flight_requests[provider], provider, result)
        return result

    def test_rate_limit_backoff_increases_from_completion_and_caps_at_fifteen_minutes(self):
        self.assertTrue(self.start())
        for delay in (60, 120, 240, 480, 900, 900, 900):
            self.now += 7
            self.complete("RATE_LIMITED")
            deadline = self.now + delay
            self.now = deadline - 0.001
            self.assertFalse(self.start())
            self.assertFalse(self.widget._pending_refreshes)
            self.now = deadline
            self.assertTrue(self.start())

    def test_network_errors_throttle_current_timer_without_changing_configured_interval(self):
        for error_code in ("NETWORK_ERROR", "NETWORK_TIMEOUT", "SERVER_ERROR"):
            with self.subTest(error_code=error_code):
                self.widget = widget_stub()
                self.assertTrue(self.start())
                self.complete(error_code)
                self.now += 5
                self.widget._periodic_refresh()
                self.assertEqual(self.widget._thread_pool.start.call_count, 1)
                self.widget._refresh_timer.start.assert_called_once_with(5000)
                self.now += 25
                self.assertTrue(self.start())

    def test_schema_and_unexpected_failures_back_off_but_allow_manual_retry(self):
        for code in ("INVALID_RESPONSE", "UNKNOWN_ERROR"):
            with self.subTest(code=code):
                self.widget = widget_stub()
                self.assertTrue(self.start())
                self.complete(code)
                self.now += 5
                self.assertFalse(self.start())
                self.assertTrue(self.start(reason="manual"))

    def test_silent_cached_quota_failure_still_backs_off(self):
        self.assertTrue(self.start())
        result = self.complete("RATE_LIMITED", silent=True)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.errors, [])
        self.now += 5
        self.assertFalse(self.start())

    def test_backoff_isolated_between_providers_and_accounts(self):
        self.assertTrue(self.start())
        self.complete("RATE_LIMITED")
        self.assertFalse(self.start())
        self.assertTrue(self.start("mimo", reason="periodic_background"))
        self.complete(provider="mimo")
        self.config["TEST_ACCOUNT"] = "B"
        self.assertTrue(self.start())
        self.complete()
        self.config["TEST_ACCOUNT"] = "A"
        self.assertFalse(self.start())

    def test_manual_success_restores_normal_cadence_and_resets_failure_count(self):
        self.assertTrue(self.start())
        self.complete("RATE_LIMITED")
        self.now += 5
        self.assertTrue(self.start(reason="manual"))
        self.complete()
        self.assertFalse(self.widget._provider_refresh_backoff)
        self.now += 5
        self.widget._periodic_refresh()
        self.assertEqual(self.widget._thread_pool.start.call_count, 3)
        self.complete("RATE_LIMITED")
        self.now += 60
        self.assertTrue(self.start())

    def test_pending_manual_retry_survives_automatic_failure_backoff(self):
        self.assertTrue(self.start())
        self.widget.refresh()
        self.widget.refresh()
        with patch("ui.qt_widget.QTimer.singleShot") as queue:
            self.complete("RATE_LIMITED")
        queue.assert_called_once()
        queue.call_args.args[1]()
        self.assertEqual(self.widget._thread_pool.start.call_count, 2)

    def test_switch_allows_immediate_retry(self):
        self.assertTrue(self.start())
        self.complete("NETWORK_ERROR")
        self.widget.refresh(force=True)
        self.assertEqual(self.widget._thread_pool.start.call_count, 2)

    def test_old_account_failure_cannot_delay_queued_new_account_request(self):
        self.assertTrue(self.start())
        self.config["TEST_ACCOUNT"] = "B"
        with patch("ui.qt_widget.QTimer.singleShot") as queue:
            self.complete("RATE_LIMITED", account="A")
        self.assertFalse(self.widget._provider_refresh_backoff)
        queue.call_args.args[1]()
        self.assertEqual(self.widget._thread_pool.start.call_count, 2)
        self.assertEqual(self.widget._thread_pool.start.call_args.args[0]._config["TEST_ACCOUNT"], "B")

    def test_pending_retry_uses_credentials_saved_before_timer_callback(self):
        self.assertTrue(self.start())
        self.widget.refresh()
        with patch("ui.qt_widget.QTimer.singleShot") as queue:
            self.complete()
        self.config["TEST_ACCOUNT"] = "B"
        queue.call_args.args[1]()
        self.assertEqual(self.widget._thread_pool.start.call_args.args[0]._config["TEST_ACCOUNT"], "B")

    def test_pending_retry_cannot_clear_new_account_view_or_cache(self):
        self.assertTrue(self.start())
        self.widget.refresh()
        with patch("ui.qt_widget.QTimer.singleShot") as queue:
            self.complete()
        self.config["TEST_ACCOUNT"] = "B"
        self.assertTrue(self.start(reason="manual"))
        current = TokenData(account_key="B", today_tokens=123, status="ok")
        self.widget._data = current
        self.widget._provider_results["deepseek"] = current
        queue.call_args.args[1]()
        self.assertIs(self.widget._data, current)
        self.assertIs(self.widget._provider_results["deepseek"], current)
        self.assertEqual(self.widget._thread_pool.start.call_count, 2)

    def test_pending_retry_keeps_original_provider_after_view_switch(self):
        self.assertTrue(self.start())
        self.widget.refresh()
        with patch("ui.qt_widget.QTimer.singleShot") as queue:
            self.complete()
        self.config.update(ACTIVE_PROVIDER="codex", TEST_ACCOUNT="B")
        current = TokenData(account_key="B", today_tokens=123)
        self.widget._data = current
        queue.call_args.args[1]()
        task = self.widget._thread_pool.start.call_args.args[0]
        self.assertEqual(task.provider_id, "deepseek")
        self.assertEqual(task._config["TEST_ACCOUNT"], "B")
        self.assertIs(self.widget._data, current)

    def test_pending_retry_after_close_does_not_read_config_or_change_view(self):
        current = self.widget._data
        self.widget._closed = True
        with patch("ui.qt_widget.config_manager.all_config") as read_config:
            self.widget._schedule_pending_refresh("deepseek", (self.config, True, "manual"))
        read_config.assert_not_called()
        self.widget._thread_pool.start.assert_not_called()
        self.assertIs(self.widget._data, current)

    def test_background_backoff_skips_only_failed_provider(self):
        self.config["ACTIVE_PROVIDER"] = "codex"
        self.config["BACKGROUND_PROVIDER_IDS"] = ["deepseek", "mimo"]
        self.assertTrue(self.start(reason="periodic_background"))
        self.complete("RATE_LIMITED")
        self.assertTrue(self.start(reason="manual"))
        self.complete("RATE_LIMITED")
        self.now += 60
        with patch("ui.qt_widget.configured_provider_ids", return_value=["codex", "deepseek", "mimo"]):
            self.widget._periodic_background_refresh()
        tasks = [call.args[0].provider_id for call in self.widget._thread_pool.start.call_args_list]
        self.assertEqual(tasks, ["deepseek", "deepseek", "mimo"])

    def test_local_storage_failure_does_not_throttle_network_collection(self):
        self.assertTrue(self.start())
        self.complete("LOCAL_STORAGE")
        self.now += 5
        self.assertTrue(self.start())

    def test_external_account_change_reopens_paused_auth_collection(self):
        self.assertTrue(self.start())
        self.complete("AUTH_EXPIRED")
        self.assertFalse(self.start())
        self.config["TEST_ACCOUNT"] = "B"
        self.assertTrue(self.start())
        self.assertNotIn("deepseek", self.widget._auth_expired_providers)
        self.assertNotIn("deepseek", self.widget._auth_notified_providers)
        self.complete("NETWORK_TIMEOUT")
        self.now += 30
        self.assertTrue(self.start())

    def test_same_account_relogin_can_recover_via_infrequent_auth_recheck(self):
        self.assertTrue(self.start())
        self.complete("AUTH_EXPIRED")
        self.now += 899
        self.assertFalse(self.start())
        self.now += 1
        self.assertTrue(self.start())
        self.complete()
        self.now += 5
        self.assertTrue(self.start())

    def test_failed_auth_recheck_keeps_notifications_deduplicated_and_waits_again(self):
        self.assertTrue(self.start())
        self.complete("AUTH_EXPIRED")
        self.now += 900
        self.assertTrue(self.start())
        self.complete("AUTH_EXPIRED")
        self.now += 5
        self.assertFalse(self.start())
        self.widget.tray.showMessage.assert_called_once()

    def test_removal_discards_running_result_and_pending_retry(self):
        self.assertTrue(self.start())
        self.widget.refresh()
        self.config["DISABLED_PROVIDER_IDS"] = ["deepseek"]
        current = self.widget._data
        with patch("ui.qt_widget.QTimer.singleShot") as queue:
            self.complete("AUTH_EXPIRED")
        queue.assert_not_called()
        self.assertFalse(self.widget._in_flight_requests)
        self.assertFalse(self.widget._pending_refreshes)
        self.assertNotIn("deepseek", self.widget._provider_results)
        self.assertIs(self.widget._data, current)
        self.widget.tray.showMessage.assert_not_called()
        self.assertFalse(self.start(reason="manual"))


class QuotaAlertTests(unittest.TestCase):
    def setUp(self):
        self.widget = widget_stub()
        self.config = {
            "ACTIVE_PROVIDER": "codex", "QUOTA_ALERT_ENABLED": True,
            "QUOTA_ALERT_THRESHOLD": 10, "TEST_ACCOUNT": "A",
        }
        self.now = datetime(2026, 9, 5, 12)
        clock = Mock()
        clock.now.side_effect = lambda zone=None: self.now.replace(tzinfo=zone)
        for item in (
            patch("ui.qt_widget.config_manager.get", side_effect=lambda key, default=None: self.config.get(key, default)),
            patch("ui.qt_widget.config_manager.all_config", side_effect=lambda: dict(self.config)),
            patch.object(TokenData, "account_key_for_config", side_effect=lambda config: config.get("TEST_ACCOUNT", "A")),
            patch("ui.qt_widget.datetime", clock),
        ):
            item.start()
            self.addCleanup(item.stop)

    def result(self, used=95, **changes):
        return TokenData(**{
            "account_key": "A", "status": "ok", "last_success_at": self.now,
            "quota_source": "interface", "quota_windows": [QuotaWindow("week", "Weekly", used)],
            **changes,
        })

    def notify(self, result=None, provider="codex", account="A"):
        self.widget._notify_low_quota(
            result if result is not None else self.result(), provider, current_account_key=account,
        )

    def test_alert_is_disabled_by_default_and_configuration_bounds_are_validated(self):
        self.assertIs(DEFAULT_CONFIG["QUOTA_ALERT_ENABLED"], False)
        self.assertEqual(DEFAULT_CONFIG["QUOTA_ALERT_THRESHOLD"], 10)
        self.config.pop("QUOTA_ALERT_ENABLED")
        self.notify()
        self.widget.tray.showMessage.assert_not_called()
        self.assertFalse(self.widget._quota_alerted_windows)
        self.assertEqual(validate_value("QUOTA_ALERT_THRESHOLD", 1), 1)
        self.assertEqual(validate_value("QUOTA_ALERT_THRESHOLD", "50"), 50)
        for invalid in (0, 51, "invalid"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_value("QUOTA_ALERT_THRESHOLD", invalid)

    def test_every_new_low_window_is_merged_in_one_private_data_free_notification(self):
        result = self.result(
            account_key="secret-fingerprint", account_label="private@example.test",
            account_plan="private-plan", per_provider=[PerProviderData("codex", "private-name")],
            quota_windows=[
                QuotaWindow("session", "Session", 95, detail="Bearer PRIVATE"),
                QuotaWindow("week", "Weekly", 100),
                QuotaWindow("other", "Healthy", 20),
            ],
        )
        self.notify(result, account="secret-fingerprint")
        self.widget.tray.showMessage.assert_called_once()
        title, message, icon, duration = self.widget.tray.showMessage.call_args.args
        self.assertIn("Codex", title)
        self.assertIn("Session", message)
        self.assertIn("5%", message)
        self.assertIn("Weekly", message)
        self.assertIn("0%", message)
        self.assertNotIn("Healthy", message)
        for private in ("secret-fingerprint", "private@example.test", "private-plan", "private-name", "PRIVATE"):
            self.assertNotIn(private, title + message)
        self.assertEqual(icon, QSystemTrayIcon.MessageIcon.Warning)
        self.assertEqual(duration, 10_000)

    def test_low_window_notifies_once_until_recovery_exceeds_hysteresis_margin(self):
        for used in (90, 95, 89, 90, 85, 90):
            self.notify(self.result(used))
        self.assertEqual(self.widget.tray.showMessage.call_count, 1)
        self.notify(self.result(84.9))
        self.notify(self.result(90))
        self.assertEqual(self.widget.tray.showMessage.call_count, 2)

    def test_newly_low_window_can_notify_without_repeating_already_alerted_window(self):
        self.notify(self.result(95))
        self.notify(self.result(quota_windows=[
            QuotaWindow("week", "Weekly", 95), QuotaWindow("session", "Session", 90),
        ]))
        self.assertEqual(self.widget.tray.showMessage.call_count, 2)
        message = self.widget.tray.showMessage.call_args.args[1]
        self.assertIn("Session", message)
        self.assertNotIn("Weekly", message)

    def test_new_cycle_alerts_even_when_recovery_was_not_observed(self):
        for zone in (None, timezone.utc):
            with self.subTest(zone=zone):
                self.widget._quota_alerted_windows.clear()
                self.widget.tray.reset_mock()
                reset = self.now.replace(tzinfo=zone) + timedelta(hours=1)
                self.notify(self.result(quota_windows=[QuotaWindow("week", "Weekly", 95, reset)]))
                self.now += timedelta(hours=2)
                next_cycle = self.result(quota_windows=[QuotaWindow(
                    "week", "Weekly", 95, reset + timedelta(days=7),
                )])
                self.notify(next_cycle)
                self.notify(next_cycle)
                self.assertEqual(self.widget.tray.showMessage.call_count, 2)

    def test_reset_estimate_changes_do_not_repeat_alert_before_previous_cycle_ends(self):
        reset = self.now + timedelta(hours=1)
        for offset in (0, 1, 2):
            self.notify(self.result(quota_windows=[QuotaWindow(
                "week", "Weekly", 95, reset + timedelta(minutes=offset),
            )]))
        self.widget.tray.showMessage.assert_called_once()

    def test_stale_or_expired_next_cycle_does_not_consume_new_cycle_alert(self):
        reset = self.now + timedelta(hours=1)
        first = self.result(quota_windows=[QuotaWindow("week", "Weekly", 95, reset)])
        self.notify(first)
        self.now += timedelta(hours=2)
        self.notify(first)
        next_cycle = self.result(quota_windows=[QuotaWindow(
            "week", "Weekly", 95, reset + timedelta(days=7),
        )])
        self.notify(replace(next_cycle, is_stale=True))
        self.widget.tray.showMessage.assert_called_once()
        self.notify(next_cycle)
        self.assertEqual(self.widget.tray.showMessage.call_count, 2)

    def test_scopes_are_isolated_by_account_and_provider(self):
        self.notify()
        self.notify(self.result(account_key="B"), account="B")
        self.notify(provider="claude")
        self.notify()
        self.assertEqual(self.widget.tray.showMessage.call_count, 3)

    def test_unverified_or_stale_results_cannot_alert_or_rearm(self):
        changes = (
            {"status": "partial"}, {"status": "error"}, {"is_stale": True},
            {"errors": [FetchError("SERVER_ERROR", "quota", "test")]},
            {"refresh_error_codes": ("RATE_LIMITED",)}, {"last_success_at": None},
            {"quota_source": "cache"}, {"quota_source": ""}, {"account_key": ""},
        )
        for values in changes:
            with self.subTest(values=values):
                self.widget._quota_alerted_windows.clear()
                self.widget.tray.reset_mock()
                self.notify(self.result(**values))
                self.widget.tray.showMessage.assert_not_called()
                self.notify()
                self.notify(self.result(0, **values))
                self.notify()
                self.assertEqual(self.widget.tray.showMessage.call_count, 1)

    def test_unknown_or_nonfinite_percentages_do_not_alert(self):
        for used in (None, float("nan"), float("inf"), float("-inf"), True, -1, "invalid"):
            with self.subTest(used=used):
                self.notify(self.result(used))
        self.widget.tray.showMessage.assert_not_called()
        self.notify(self.result(120))
        self.assertIn("0%", self.widget.tray.showMessage.call_args.args[1])

    def test_expired_windows_are_ignored_for_naive_and_aware_reset_times(self):
        for zone in (None, timezone.utc):
            self.notify(self.result(quota_windows=[QuotaWindow(
                "expired", "Expired", 100, self.now.replace(tzinfo=zone) - timedelta(seconds=1),
            )]))
        self.widget.tray.showMessage.assert_not_called()
        self.notify(self.result(quota_windows=[QuotaWindow(
            "future", "Future", 100, self.now + timedelta(minutes=1),
        )]))
        self.widget.tray.showMessage.assert_called_once()

    def test_fresh_local_snapshot_with_verified_account_can_alert(self):
        self.notify(self.result(quota_source="local_snapshot"))
        self.widget.tray.showMessage.assert_called_once()

    def test_account_mismatch_and_non_subscription_provider_never_alert(self):
        self.notify(account="B")
        self.notify(provider="deepseek")
        self.notify(provider="unknown")
        self.widget.tray.showMessage.assert_not_called()

    def test_configured_threshold_is_inclusive_and_invalid_values_fall_back_to_ten(self):
        self.config["QUOTA_ALERT_THRESHOLD"] = 50
        self.notify(self.result(49.9))
        self.widget.tray.showMessage.assert_not_called()
        self.notify(self.result(50))
        self.widget.tray.showMessage.assert_called_once()
        for invalid in (None, True, 0, 51, float("nan"), "invalid", 10.5):
            with self.subTest(invalid=invalid):
                self.widget._quota_alerted_windows.clear()
                self.widget.tray.reset_mock()
                self.config["QUOTA_ALERT_THRESHOLD"] = invalid
                self.notify(self.result(89))
                self.widget.tray.showMessage.assert_not_called()
                self.notify(self.result(90))
                self.widget.tray.showMessage.assert_called_once()

    def test_absent_tray_does_not_consume_alert_for_later_visible_tray(self):
        tray = self.widget.tray
        self.widget.tray = None
        self.notify()
        self.assertFalse(self.widget._quota_alerted_windows)
        self.widget.tray = tray
        self.notify()
        tray.showMessage.assert_called_once()

    def test_background_refresh_notifies_without_replacing_current_provider_view(self):
        self.config["ACTIVE_PROVIDER"] = "deepseek"
        current = self.widget._data
        self.widget._in_flight_requests["codex"] = 1
        self.widget._finish_refresh(1, "codex", self.result())
        self.assertIs(self.widget._data, current)
        self.widget.tray.showMessage.assert_called_once()

    def test_finished_old_account_request_cannot_notify_new_account(self):
        self.config["TEST_ACCOUNT"] = "B"
        self.widget._in_flight_requests["codex"] = 1
        with patch("ui.qt_widget.QTimer.singleShot"):
            self.widget._finish_refresh(1, "codex", self.result())
        self.widget.tray.showMessage.assert_not_called()

    def test_duplicate_window_ids_produce_one_notification_line(self):
        result = self.result()
        self.notify(replace(result, quota_windows=result.quota_windows * 2))
        self.assertEqual(len(self.widget.tray.showMessage.call_args.args[1].splitlines()), 1)

    def test_quota_notification_click_does_not_reuse_previous_authentication_action(self):
        self.widget._auth_expired_provider_id = "mimo"
        self.widget._auth_expired_providers.add("mimo")
        self.notify()
        self.widget.handle_auth_expired_notification_click()
        self.widget.open_settings.assert_not_called()
        self.assertIn("mimo", self.widget._auth_expired_providers)

    def test_notification_translates_without_altering_provider_or_percentage(self):
        with patch("ui.i18n.current_language", return_value="en"):
            self.notify()
        title, message = self.widget.tray.showMessage.call_args.args[:2]
        self.assertIn("Codex quota running low", title)
        self.assertEqual(message, "Weekly: 5% remaining")


if __name__ == "__main__":
    unittest.main()
