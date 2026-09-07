import json
from datetime import datetime
from decimal import Decimal
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication

from api.providers.base import Provider, ProviderBalance
from config import account_profiles as store
from config import credentials
from config import runtime as config_manager
from config.defaults import DEFAULT_CONFIG
from data import history
from data.store import TokenData


@pytest.fixture(autouse=True)
def isolated_profiles(monkeypatch, tmp_path):
    secrets = {}
    monkeypatch.setattr(config_manager, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_manager, "_config", dict(DEFAULT_CONFIG))
    monkeypatch.setattr(credentials, "read_credential", lambda key: secrets.get(key, ""))
    monkeypatch.setattr(credentials, "write_credential", lambda key, value: secrets.__setitem__(key, value))
    return secrets


def test_profile_secrets_are_isolated_and_never_written_to_json(isolated_profiles, tmp_path):
    first = store.save_profile("openrouter", "Personal", {"API_KEY": "secret-one"})
    second = store.save_profile("openrouter", "Work", {"API_KEY": "secret-two"})
    assert first["id"] != second["id"]
    raw = (tmp_path / "account-profiles.json").read_text(encoding="utf-8")
    assert "secret-one" not in raw and "secret-two" not in raw
    assert store.profile_config(first)["OPENROUTER_API_KEY"] == "secret-one"
    assert store.profile_config(second)["OPENROUTER_API_KEY"] == "secret-two"
    assert store.profile_config(first)["OPENROUTER_BASE"] == DEFAULT_CONFIG["OPENROUTER_BASE"]
    store.delete_profile(first["id"])
    assert len(store.load_profiles()) == 1
    assert store.profile_config(second)["OPENROUTER_API_KEY"] == "secret-two"


def test_failed_profile_update_preserves_original_credentials(monkeypatch):
    original = store.save_profile("openrouter", "Original", {"API_KEY": "old-secret"})
    monkeypatch.setattr(store, "_write_profiles", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        store.save_profile("openrouter", "Changed", {"API_KEY": "new-secret"}, original["id"])
    assert store.load_profiles() == [original]
    assert store.profile_fields(original)["API_KEY"] == "old-secret"


def test_profiles_never_fall_back_to_default_credentials(monkeypatch):
    monkeypatch.setattr(config_manager, "_config", {**DEFAULT_CONFIG, "OPENROUTER_API_KEY": "default-secret"})
    profile = store.save_profile("openrouter", "Separate", {"API_KEY": "profile-secret"})
    assert store.profile_config(profile)["OPENROUTER_API_KEY"] == "profile-secret"
    with pytest.raises(ValueError):
        store.save_profile("codex", "Missing path", {})
    with pytest.raises(ValueError):
        store.save_profile("gemini", "Project only", {"PROJECT_ID": "project"})


def test_invalid_profile_file_is_not_silently_overwritten(tmp_path):
    path = tmp_path / "account-profiles.json"
    path.write_text('[{"id":"bad"}]', encoding="utf-8")
    with pytest.raises(ValueError):
        store.save_profile("codex", "New", {"HOME": "C:/test"})
    assert json.loads(path.read_text()) == [{"id": "bad"}]


def test_profile_fetch_preserves_default_cache_and_legacy_history(monkeypatch):
    class TestProvider(Provider):
        id = "openrouter"
        name = "OpenRouter"
        def snapshot_identity(self):
            return "profile-account"
        def is_configured(self):
            return True
        def fetch_balance(self):
            return ProviderBalance("USD", Decimal("4")), None
    original = TokenData(account_key="default-account", balance_cny=99)
    monkeypatch.setattr(TokenData, "_provider_snapshots", {"openrouter": original})
    with history._connect() as connection:
        connection.execute("INSERT INTO daily_usage VALUES (?, ?, ?, ?, ?, ?, ?)",
                           ("2026-09-01", "legacy", "tokens", 1, "0", "2026-09-01", "openrouter"))
    result = TokenData._fetch_with_provider(TestProvider({"_ACCOUNT_PROFILE_ID": "test"}), lightweight=True, include_minute_history=False)
    assert result.balance_cny == 4
    assert TokenData._provider_snapshots["openrouter"] is original
    with history._connect() as connection:
        assert connection.execute("SELECT provider FROM daily_usage WHERE model='legacy'").fetchone()[0] == "openrouter"


def test_same_provider_cards_and_late_deleted_profile_result(monkeypatch):
    from ui import account_profiles

    app = QApplication.instance() or QApplication([])
    first = store.save_profile("openrouter", "One", {"API_KEY": "one"})
    second = store.save_profile("openrouter", "Two", {"API_KEY": "two"})
    page = account_profiles.AccountProfilesPage()
    assert set(page.cards) == {first["id"], second["id"]}
    tasks = []
    class Pool:
        def start(self, task):
            tasks.append(task)
        def waitForDone(self):
            pass
    monkeypatch.setattr(account_profiles.QThreadPool, "globalInstance", lambda: Pool())
    page.refresh_profiles()
    assert len(tasks) == 1
    store.delete_profile(first["id"])
    page._finished(first["id"], first["revision"], TokenData(account_key="late", last_success_at=datetime.now()))
    assert first["id"] not in page.results
    assert first["id"] not in page.cards
    page._queue.clear()
    page.deleteLater()
    app.processEvents()


def test_profile_notification_opens_its_profile_instead_of_default_account(monkeypatch):
    from api.providers.base import QuotaWindow
    from test_refresh import widget_stub

    monkeypatch.setattr(config_manager, "_config", {**DEFAULT_CONFIG, "QUOTA_ALERT_ENABLED": True})
    widget = widget_stub()
    widget.expand_panel = Mock()
    widget._settings_window = Mock()
    widget._switch_provider = Mock()
    result = TokenData(status="ok", account_key="profile-account", last_success_at=datetime.now(),
                       quota_source="interface", quota_windows=[QuotaWindow("weekly", "周额度", 95)])
    widget._notify_profile_quota("profile-id", "Work", "codex", result)
    assert "Work" in widget.tray.showMessage.call_args.args[0]
    widget.handle_auth_expired_notification_click()
    widget.open_settings.assert_called_once_with()
    widget._settings_window.open_account_profiles.assert_called_once_with("profile-id")
    widget._switch_provider.assert_not_called()


def test_profile_management_is_lazy_inside_settings(monkeypatch):
    from ui.qt_settings import SettingsWindow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(config_manager, "load_config", lambda: dict(DEFAULT_CONFIG))
    window = SettingsWindow()
    assert window.account_profiles_page is None
    window.manage_profiles_button.click()
    assert window.tabs.currentIndex() == window._profiles_tab_index
    assert window.account_profiles_page is not None
    assert window.isAncestorOf(window.account_profiles_page)
    window._autosave_ready = False
