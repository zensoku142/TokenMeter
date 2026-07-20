import os
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

os.environ["APPDATA"] = str(Path.cwd() / ".test-appdata")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from data.store import FetchError, PerProviderData, TokenData
from deepseek_pricing import BEIJING_TIMEZONE, PricingState
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from ui.qt_panel import MainPanel
from ui.qt_widget import FloatingWidget, MiMoRenewalTask

APP = QApplication.instance() or QApplication([])


def widget_stub():
    widget = FloatingWidget.__new__(FloatingWidget)
    widget._refresh_lock = __import__("threading").Lock()
    widget._refreshing = False
    widget._pending_refresh = False
    widget._request_id = 0
    widget._closed = False
    widget._data = TokenData()
    widget._expanded = False
    widget._edge_snapped = False
    widget._apply_update = Mock()
    widget._thread_pool = Mock()
    widget._refresh_timer = Mock()
    widget.tray = Mock()
    widget.open_settings = Mock()
    widget._auth_expired_notified = False
    widget._auth_expired_provider_id = None
    widget._mimo_renewal_task = None
    widget._mimo_renewal_attempted = False
    return widget


def pricing_widget_stub():
    widget = FloatingWidget.__new__(FloatingWidget)
    widget._pricing_state = None
    widget._pricing_timer = Mock()
    widget.panel = Mock()
    widget.ball = Mock()
    widget.tray = Mock()
    return widget


class RefreshTests(unittest.TestCase):
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

        with patch("ui.qt_widget.config_manager.get") as get_config:
            get_config.side_effect = lambda key, default=None: {
                "ACTIVE_PROVIDER": "mimo",
            }.get(key, default)
            widget.refresh()

        task = widget._thread_pool.start.call_args.args[0]
        self.assertTrue(task._lightweight)

    def test_repeated_refresh_runs_once_then_one_pending(self):
        widget = widget_stub()
        widget.refresh()
        widget.refresh()
        widget.refresh()
        self.assertEqual(widget._thread_pool.start.call_count, 1)
        self.assertTrue(widget._pending_refresh)

    def test_older_request_does_not_replace_newer_data(self):
        widget = widget_stub()
        current = TokenData(balance_cny=2)
        widget._data = current
        widget._refreshing = True
        widget._request_id = 2
        widget._finish_refresh(1, TokenData(balance_cny=1))
        self.assertIs(widget._data, current)

    def test_auth_expired_shows_one_tray_notification_until_recovery(self):
        widget = widget_stub()
        widget._request_id = 1
        expired = TokenData(
            errors=[FetchError("AUTH_EXPIRED", "余额", "Cookie 已失效")]
        )

        widget._finish_refresh(1, expired)
        widget._finish_refresh(1, expired)

        self.assertEqual(widget.tray.showMessage.call_count, 1)
        title, message, icon, timeout = widget.tray.showMessage.call_args.args
        self.assertEqual(title, "TokenMeter：登录凭据已失效")
        self.assertIn("Cookie 已失效", message)
        self.assertIn("点击此通知", message)
        self.assertEqual(icon, QSystemTrayIcon.MessageIcon.Warning)
        self.assertEqual(timeout, 10_000)

        widget._finish_refresh(1, TokenData())
        widget._finish_refresh(1, expired)
        self.assertEqual(widget.tray.showMessage.call_count, 2)

    def test_mimo_auth_expired_starts_silent_renewal(self):
        widget = widget_stub()
        widget._request_id = 1
        expired = TokenData(
            errors=[FetchError("AUTH_EXPIRED", "MiMo 余额", "Cookie 已失效")],
            per_provider=[PerProviderData("mimo", "小米 MiMo")],
        )

        widget._finish_refresh(1, expired)
        task = widget._thread_pool.start.call_args.args[0]
        self.assertIsInstance(task, MiMoRenewalTask)
        self.assertTrue(widget._mimo_renewal_attempted)
        widget.tray.showMessage.assert_not_called()

    @patch("ui.qt_widget.config_manager.save_config")
    def test_successful_mimo_renewal_saves_only_cookie_credentials(self, save_config):
        widget = widget_stub()
        widget._mimo_renewal_task = Mock()
        widget._mimo_renewal_attempted = True
        widget._settings_window = Mock()
        widget.refresh = Mock()

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
        widget.refresh.assert_called_once_with()
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
        self.assertTrue(widget._auth_expired_notified)
        self.assertEqual(widget.tray.showMessage.call_count, 1)

    @patch("ui.qt_widget.MiMoProvider.recover_verified_cookie_via_chrome")
    def test_mimo_renewal_falls_back_to_visible_browser(self, recover_cookie):
        recover_cookie.side_effect = [RuntimeError("MIMO_COOKIE_EMPTY"), "fresh-cookie"]
        task = MiMoRenewalTask()
        finished = Mock()
        task.signals.finished.connect(finished)

        with patch("ui.qt_widget.MiMoProvider.is_direct_cookie_usable", return_value=True):
            task.run()

        self.assertEqual(recover_cookie.call_count, 2)
        self.assertTrue(recover_cookie.call_args_list[0].kwargs["headless"])
        self.assertFalse(recover_cookie.call_args_list[1].kwargs["headless"])
        finished.assert_called_once_with("fresh-cookie", "")

    @patch("ui.qt_widget.config_manager.save_config")
    def test_browser_only_renewal_does_not_overwrite_cookie_credentials(self, save_config):
        widget = widget_stub()
        widget._mimo_renewal_task = Mock()
        widget.refresh = Mock()

        widget._finish_mimo_cookie_renewal(
            "session=browser-only",
            "BROWSER_CONTEXT_ONLY",
        )

        save_config.assert_not_called()
        widget.refresh.assert_called_once_with()
        self.assertFalse(widget._auth_expired_notified)

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
