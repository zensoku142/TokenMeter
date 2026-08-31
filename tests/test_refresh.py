import os
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

os.environ["APPDATA"] = str(Path.cwd() / ".test-appdata")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from data.store import FetchError, PerProviderData, TokenData
from api.deepseek_pricing import BEIJING_TIMEZONE, PricingState
from ui.qt_panel import MainPanel
from ui.qt_widget import (
    BACKGROUND_PROVIDER_INTERVAL_MS,
    FetchTask,
    FloatingWidget,
    MiMoRenewalTask,
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

        widget._finish_mimo_cookie_renewal(
            "session=browser-only",
            "BROWSER_CONTEXT_ONLY",
        )

        save_config.assert_not_called()
        widget._refresh_mimo_after_renewal.assert_called_once_with()
        self.assertNotIn("mimo", widget._auth_expired_providers)

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


if __name__ == "__main__":
    unittest.main()
