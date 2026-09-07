from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from api.providers.base import QuotaWindow
from config import runtime as config_manager
from config.defaults import DEFAULT_CONFIG
from data.quota_history import estimate_seconds, in_quiet_hours, load_alerts, record_quota, save_alerts
from data.store import TokenData
from test_refresh import widget_stub


def test_estimate_requires_contiguous_same_cycle_samples():
    assert estimate_seconds([(0, 10), (300, 20), (600, 30)]) == 2100
    assert estimate_seconds([(0, 10), (300, 10), (600, 10)]) == 0
    assert estimate_seconds([(0, 10), (300, 20)]) is None
    assert estimate_seconds([(0, 10), (300, 20), (600, 5)]) is None
    assert estimate_seconds([(0, 10), (300, 20), (2000, 30)]) is None
    assert estimate_seconds([(0, 10), (300, 20), (600, float("nan"))]) is None
    assert estimate_seconds([(0, 10), (300, 20), (600, 100)]) is None


def sample(minutes=0, used=10, **kwargs):
    start = datetime(2026, 9, 7, 12)
    return TokenData(**{
        "account_key": "account-a", "status": "ok", "quota_source": "interface",
        "last_success_at": start + timedelta(minutes=minutes),
        "quota_windows": [QuotaWindow("weekly", "周额度", used, start + timedelta(days=1))],
        **kwargs,
    })


def test_quota_samples_isolate_accounts_and_changed_sources():
    assert record_quota("codex", sample()) == {}
    assert record_quota("codex", sample(5, 20)) == {}
    assert record_quota("codex", sample(10, 30)) == {"weekly": 2100}
    assert record_quota("codex", sample(15, 40, account_key="account-b")) == {}
    assert record_quota("codex", sample(15, 40, quota_source="local_snapshot")) == {}
    assert record_quota("codex", sample(20, 50)) == {}
    assert record_quota("codex", sample(25, 60, is_stale=True)) == {}
    assert record_quota("codex", sample(25, 60, quota_source="cache")) == {}


def test_alert_state_survives_restart_and_can_be_cleared():
    state = {("codex", "a", "weekly"): datetime.now() + timedelta(hours=2), ("claude", "b", "weekly"): None}
    save_alerts(state)
    assert load_alerts() == state
    save_alerts({})
    assert load_alerts() == {}


@pytest.mark.parametrize("hour,quiet", [(23, True), (7, True), (8, False), (12, False)])
def test_quiet_hours_cross_midnight(hour, quiet):
    assert in_quiet_hours({"QUOTA_QUIET_ENABLED": True, "QUOTA_QUIET_START": "22:00", "QUOTA_QUIET_END": "08:00"}, datetime(2026, 9, 7, hour)) is quiet


def test_low_quota_restart_dedup_recovery_and_quiet_hours(monkeypatch):
    settings = {**DEFAULT_CONFIG, "QUOTA_ALERT_ENABLED": True, "QUOTA_RECOVERY_ALERT_ENABLED": True}
    monkeypatch.setattr(config_manager, "_config", settings)
    result = sample(used=95, quota_windows=[QuotaWindow("weekly", "周额度", 95)])
    first = widget_stub()
    first._notify_low_quota(result, "codex", current_account_key="account-a")
    first.tray.showMessage.assert_called_once()
    restarted = widget_stub()
    restarted._notify_low_quota(result, "codex", current_account_key="account-a")
    restarted.tray.showMessage.assert_not_called()
    recovered = replace(result, quota_windows=[QuotaWindow("weekly", "周额度", 10)])
    restarted._notify_low_quota(recovered, "codex", current_account_key="account-a")
    restarted.tray.showMessage.assert_called_once()
    assert load_alerts() == {}
    settings.update(QUOTA_QUIET_ENABLED=True, QUOTA_QUIET_START="00:00", QUOTA_QUIET_END="00:00")
    restarted.tray.showMessage.reset_mock()
    restarted._notify_low_quota(result, "codex", current_account_key="account-a")
    restarted.tray.showMessage.assert_not_called()


def test_forecast_and_quiet_settings_round_trip(monkeypatch, tmp_path):
    from unittest.mock import Mock

    from PySide6.QtWidgets import QApplication

    from ui.qt_settings import SettingsWindow

    app = QApplication.instance() or QApplication([])
    values = {**DEFAULT_CONFIG, "QUOTA_ALERT_ENABLED": True, "QUOTA_RECOVERY_ALERT_ENABLED": True,
              "QUOTA_FORECAST_ENABLED": True, "QUOTA_QUIET_ENABLED": True,
              "QUOTA_QUIET_START": "22:30", "QUOTA_QUIET_END": "07:15"}
    monkeypatch.setattr(config_manager, "_config", values)
    monkeypatch.setattr(config_manager, "load_config", lambda: dict(values))
    monkeypatch.setattr(config_manager, "PANEL_LAYOUT_PATH", tmp_path / "layout.json")
    monkeypatch.setattr(config_manager, "save_config", Mock())
    window = SettingsWindow()
    saved = window._values()
    for key in ("QUOTA_RECOVERY_ALERT_ENABLED", "QUOTA_FORECAST_ENABLED", "QUOTA_QUIET_ENABLED", "QUOTA_QUIET_START", "QUOTA_QUIET_END"):
        assert saved[key] == values[key]
    assert window.quota_recovery_check.isEnabled()
    window._autosave_ready = False
