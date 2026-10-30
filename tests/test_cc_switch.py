import json
import sqlite3
from datetime import datetime
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from api import cc_switch
from api.providers.codex import CodexProvider
from config import account_profiles as store
from config import runtime as config_manager
from config.defaults import DEFAULT_CONFIG
from data.store import TokenData


@pytest.fixture
def cc_accounts(tmp_path, monkeypatch):
    path = tmp_path / "cc-switch.db"
    monkeypatch.setattr(cc_switch, "database_path", lambda: path)
    monkeypatch.setattr(config_manager, "CONFIG_DIR", tmp_path / "tokenmeter")
    monkeypatch.setattr(cc_switch, "_retry_after", 0)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE providers (app_type, name, meta, sort_index, created_at)")
        for index, account_id in enumerate(("personal", "work", "personal", "api-key")):
            binding = {"source": "managed_account", "authProvider": "codex_oauth", "accountId": account_id}
            if account_id == "api-key":
                binding["authProvider"] = "api_key"
            db.execute("INSERT INTO providers VALUES (?, ?, ?, ?, ?)",
                       ("codex", account_id, json.dumps({"authBinding": binding}), index, index))
        db.execute("INSERT INTO providers VALUES ('claude', 'Other', '{}', 9, 9)")
    return path


def test_discovery_only_reads_configured_codex_bindings(cc_accounts):
    before = cc_accounts.read_bytes()
    assert cc_switch.codex_accounts() == [
        {"id": "personal", "name": "personal"}, {"id": "work", "name": "work"},
    ]
    assert cc_accounts.read_bytes() == before
    assert cc_switch.account_identity("personal") != cc_switch.account_identity("work")
    assert cc_switch.account_identity("missing") == ""


def test_no_configuration_does_not_create_database_or_launch_cc(tmp_path, monkeypatch):
    path = tmp_path / "missing.db"
    monkeypatch.setattr(cc_switch, "database_path", lambda: path)
    launch = Mock()
    monkeypatch.setattr(cc_switch.subprocess, "Popen", launch)
    assert cc_switch.codex_accounts() == []
    with pytest.raises(RuntimeError, match="CC_SWITCH_NOT_CONFIGURED"):
        cc_switch.connect()
    assert not path.exists()
    launch.assert_not_called()


def test_profile_import_is_idempotent_and_preserves_default_connection(cc_accounts, monkeypatch):
    monkeypatch.setattr(config_manager, "_config", {**DEFAULT_CONFIG, "CODEX_HOME": "default-home"})
    assert store.import_cc_switch_profiles() == 2
    profiles = store.load_profiles()
    first, second = profiles
    assert store.import_cc_switch_profiles() == 0
    assert first["fields"] == {"CC_SWITCH_ACCOUNT_ID": "personal"}
    assert store.profile_config(first)["CODEX_CC_SWITCH_ACCOUNT_ID"] == "personal"
    assert store.profile_config(first)["CODEX_HOME"] == ""
    assert TokenData.account_key_for_config(store.profile_config(first)) != TokenData.account_key_for_config(store.profile_config(second))
    before = cc_accounts.read_bytes()
    store.delete_profile(first["id"])
    assert cc_accounts.read_bytes() == before
    assert config_manager.get("CODEX_HOME") == "default-home"
    assert "token" not in json.dumps(store.load_profiles()).lower()


@pytest.mark.parametrize("url,ws,title", [
    ("https://example.com", "ws://127.0.0.1:9222/x", "CC Switch"),
    ("http://tauri.localhost", "ws://example.com:9222/x", "CC Switch"),
    ("http://tauri.localhost", "ws://127.0.0.1:9223/x", "CC Switch"),
    ("http://tauri.localhost", "ws://127.0.0.1:9222/x", "Other app"),
])
def test_bridge_rejects_unrelated_or_remote_targets(monkeypatch, url, ws, title):
    monkeypatch.setattr(cc_switch, "_http_json", lambda *args: [
        {"type": "page", "title": title, "url": url, "webSocketDebuggerUrl": ws},
    ])
    with pytest.raises(RuntimeError, match="CC_SWITCH_NOT_CONNECTED"):
        cc_switch._endpoint(9222)


def test_connect_attaches_to_existing_bridge_without_restarting_cc(cc_accounts, monkeypatch):
    monkeypatch.setattr(cc_switch, "_saved_port", lambda: 9222)
    monkeypatch.setattr(cc_switch, "_endpoint", lambda port: "ws://127.0.0.1:9222/page")
    launch = Mock()
    monkeypatch.setattr(cc_switch.subprocess, "Popen", launch)
    cc_switch.connect()
    launch.assert_not_called()


def test_bridge_accepts_observed_legacy_cc_window_title(monkeypatch):
    monkeypatch.setattr(cc_switch, "_http_json", lambda *args: [
        {"type": "page", "title": "Claude Code 供应商切换器", "url": "http://tauri.localhost/",
         "webSocketDebuggerUrl": "ws://127.0.0.1:9222/page"},
    ])
    assert cc_switch._endpoint(9222) == "ws://127.0.0.1:9222/page"


def test_running_non_bridge_cc_is_not_terminated(cc_accounts, monkeypatch):
    endpoint = Mock(side_effect=OSError())
    monkeypatch.setattr(cc_switch, "_endpoint", endpoint)
    monkeypatch.setattr(cc_switch, "_CDP_PORT_RANGE", range(9222, 9224))
    monkeypatch.setattr(cc_switch, "_executable", lambda: "C:/CC Switch/cc-switch.exe")
    monkeypatch.setattr(cc_switch, "pick_free_cdp_port", lambda: 9222)
    process = Mock()
    process.poll.return_value = 0
    launch = Mock(return_value=process)
    monkeypatch.setattr(cc_switch.subprocess, "Popen", launch)
    with pytest.raises(RuntimeError, match="CC_SWITCH_RESTART_REQUIRED"):
        cc_switch.connect()
    assert launch.call_args.args[0] == ["C:/CC Switch/cc-switch.exe"]
    assert "127.0.0.1" in launch.call_args.kwargs["env"]["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"]
    process.terminate.assert_not_called()
    process.kill.assert_not_called()
    with pytest.raises(RuntimeError, match="CC_SWITCH_RESTART_REQUIRED"):
        cc_switch.connect()
    assert launch.call_count == 1


def test_quota_request_delegates_refresh_to_cc_and_cleans_up_job(cc_accounts, monkeypatch):
    monkeypatch.setattr(cc_switch, "_endpoint", lambda port: "ws://127.0.0.1:9222/page")
    connection = Mock()
    quota = {"success": True, "tiers": [{"name": "seven_day", "utilization": 93}]}
    connection.call.side_effect = [{}, {"result": {"value": {"done": True, "quota": quota}}}, {}]
    monkeypatch.setattr(cc_switch, "_CdpConnection", lambda endpoint: connection)
    assert cc_switch.fetch_codex_quota("work") == quota
    start = connection.call.call_args_list[0].args[1]["expression"]
    assert "get_codex_oauth_quota" in start and 'accountId:"work"' in start
    assert "access_token" not in start and "refresh_token" not in start
    assert "delete globalThis[" in connection.call.call_args_list[-1].args[1]["expression"]
    connection.close.assert_called_once()


def test_bridge_rejection_does_not_expose_cc_error_text(cc_accounts, monkeypatch):
    monkeypatch.setattr(cc_switch, "_endpoint", lambda port: "ws://127.0.0.1:9222/page")
    connection = Mock()
    connection.call.side_effect = [{}, {"result": {"value": {"done": True, "failed": True}}}, {}]
    monkeypatch.setattr(cc_switch, "_CdpConnection", lambda endpoint: connection)
    with pytest.raises(RuntimeError, match="^CC_SWITCH_QUERY_FAILED$"):
        cc_switch.fetch_codex_quota("personal")
    connection.close.assert_called_once()


def test_codex_cc_path_never_reads_default_cli_or_shared_logs(cc_accounts, monkeypatch):
    monkeypatch.setattr(cc_switch, "fetch_codex_quota", lambda account_id: {
        "success": True, "credentialStatus": "valid", "tiers": [
            {"name": "five_hour", "utilization": 23, "resetsAt": None},
            {"name": "seven_day", "utilization": 93, "resetsAt": "2026-11-03T12:00:00Z"},
        ], "creditsBalance": 7,
    })
    provider = CodexProvider({"CODEX_CC_SWITCH_ACCOUNT_ID": "personal"})
    provider._credentials = Mock(side_effect=AssertionError("must not use default auth"))
    provider._local_activity = Mock(side_effect=AssertionError("must not scan shared logs"))
    try:
        assert provider.is_configured()
        assert provider.local_usage_scope == ""
        assert provider.exact_minute_usage() is None
        quota, error = provider.fetch_quota()
        assert error is None
        assert [window.used_percent for window in quota.windows] == [23, 93]
        assert [window.window_minutes for window in quota.windows] == [300, 10080]
        assert quota.windows[1].resets_at.utcoffset().total_seconds() == 0
        assert not quota.activity and not quota.weekly_activity
    finally:
        provider.close()


@pytest.mark.parametrize("used", [True, -1, 101, float("nan"), "23"])
def test_invalid_cc_quota_is_not_reported_as_remaining_allowance(cc_accounts, monkeypatch, used):
    monkeypatch.setattr(cc_switch, "fetch_codex_quota", lambda _: {
        "success": True, "tiers": [{"name": "seven_day", "utilization": used}],
    })
    provider = CodexProvider({"CODEX_CC_SWITCH_ACCOUNT_ID": "personal"})
    try:
        quota, error = provider.fetch_quota()
        assert quota is None and error.code == "INVALID_RESPONSE"
    finally:
        provider.close()


def test_missing_cc_account_does_not_fall_back_to_cli(cc_accounts):
    provider = CodexProvider({"CODEX_CC_SWITCH_ACCOUNT_ID": "missing"})
    provider._credentials = Mock(side_effect=AssertionError("must not use default auth"))
    try:
        assert not provider.is_configured()
        assert not provider.snapshot_identity()
        quota, error = provider.fetch_quota()
        assert quota is None and error.code == "NOT_CONFIGURED"
    finally:
        provider.close()


def test_profiles_ui_hides_cc_when_unconfigured_and_refreshes_without_editor(cc_accounts, monkeypatch):
    from ui import account_profiles

    app = QApplication.instance() or QApplication([])
    normal = store.save_profile("codex", "CLI", {"HOME": "C:/isolated"})
    page = account_profiles.AccountProfilesPage()
    # 页面加载直接发现 CC 账号，不再要求先点击连接按钮或手动导入。
    profile = next(p for p in store.load_profiles() if p["fields"].get("CC_SWITCH_ACCOUNT_ID"))
    assert len(page.profiles) == 3
    assert all(button.text() != "连接 CC Switch" for button in page.findChildren(QPushButton))
    editor = Mock(side_effect=AssertionError("CC account must not open an editor"))
    monkeypatch.setattr(account_profiles, "ProfileEditor", editor)
    tasks = []
    pool = Mock()
    pool.start.side_effect = tasks.append
    monkeypatch.setattr(account_profiles.QThreadPool, "globalInstance", lambda: pool)
    button = page.cards[profile["id"]].findChild(QPushButton, "overviewConnection")
    assert button.text() == "刷新"
    assert page.cards[normal["id"]].findChild(QPushButton, "overviewConnection").text() == "账户连接"
    page._due[profile["id"]] = float("inf")
    button.click()
    assert len(tasks) == 1 and tasks[0].profile_id == profile["id"]
    assert not button.isEnabled()
    button.click()
    assert len(tasks) == 1
    editor.assert_not_called()
    monkeypatch.setattr(cc_switch, "codex_accounts", lambda: [])
    page.reload_profiles()
    app.processEvents()


def test_missing_port_record_recovers_existing_cc_bridge_without_launch(cc_accounts, monkeypatch):
    def endpoint(port, **_kwargs):
        if port != 9223:
            raise OSError
        return "ws://127.0.0.1:9223/page"

    monkeypatch.setattr(cc_switch, "_endpoint", endpoint)
    monkeypatch.setattr(cc_switch, "_CDP_PORT_RANGE", range(9222, 9225))
    launch = Mock()
    monkeypatch.setattr(cc_switch.subprocess, "Popen", launch)
    assert cc_switch._saved_port() == 0
    cc_switch.connect()
    assert cc_switch._saved_port() == 9223
    # 新的采集进程只靠保存的端口即可恢复，不依赖上一轮的内存状态。
    cc_switch.connect()
    launch.assert_not_called()


def test_first_fetch_automatically_starts_cc_when_no_bridge_exists(cc_accounts, monkeypatch):
    monkeypatch.setattr(cc_switch, "_CDP_PORT_RANGE", range(9222, 9224))
    monkeypatch.setattr(cc_switch, "_executable", lambda: "C:/CC Switch/cc-switch.exe")
    monkeypatch.setattr(cc_switch, "pick_free_cdp_port", lambda: 9222)
    process = Mock()
    launch = Mock(return_value=process)
    monkeypatch.setattr(cc_switch.subprocess, "Popen", launch)

    def endpoint(port, **kwargs):
        if not launch.called:
            raise OSError
        return "ws://127.0.0.1:9222/page"

    monkeypatch.setattr(cc_switch, "_endpoint", endpoint)
    connection = Mock()
    connection.call.side_effect = [{}, {"result": {"value": {
        "done": True, "quota": {"success": True, "tiers": []},
    }}}, {}]
    monkeypatch.setattr(cc_switch, "_CdpConnection", lambda endpoint: connection)
    assert cc_switch.fetch_codex_quota("work")["success"]
    assert launch.call_count == 1
    assert cc_switch._saved_port() == 9222


def test_bridge_disconnect_preserves_last_successful_quota_across_restart(cc_accounts, monkeypatch):
    quota = {"success": True, "tiers": [{"name": "seven_day", "utilization": 84}]}
    fetch = Mock(return_value=quota)
    monkeypatch.setattr(cc_switch, "fetch_codex_quota", fetch)
    config = {"ACTIVE_PROVIDER": "codex", "CODEX_CC_SWITCH_ACCOUNT_ID": "personal",
              "_ACCOUNT_PROFILE_ID": "profile"}
    default = TokenData(account_key="default-account")
    monkeypatch.setattr(TokenData, "_provider_snapshots", {"codex": default})
    first = TokenData.fetch(config=config, lightweight=True, include_minute_history=False)
    assert first.status == "ok" and first.quota_windows[0].used_percent == 84
    restored = TokenData.persisted_snapshot(config)
    assert restored is not None and restored.account_key == first.account_key
    assert TokenData._provider_snapshots["codex"] is default
    fetch.side_effect = RuntimeError("CC_SWITCH_NOT_CONNECTED")
    stale = TokenData.fetch(config=config, lightweight=True, include_minute_history=False)
    assert stale.refresh_error_codes == ("NETWORK_ERROR",)
    assert stale.quota_windows[0].used_percent == 84
    assert stale.is_stale and stale.quota_source == "cache"
    assert stale.last_success_at == first.last_success_at


def test_cc_accounts_refresh_before_panel_creation_and_continue_while_page_hidden(cc_accounts, monkeypatch):
    from api.providers.base import QuotaWindow
    from ui.qt_settings import SettingsWindow
    from ui.qt_update import AppUpdateController
    from ui.qt_widget import FloatingWidget

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(config_manager, "_config", {**DEFAULT_CONFIG, "FLOATING_STYLE": "ball", "VPET_ENABLED": False})
    monkeypatch.setattr(FloatingWidget, "refresh", Mock())
    monkeypatch.setattr(FloatingWidget, "_sync_codex_reset_news", lambda self: None)
    monkeypatch.setattr(AppUpdateController, "schedule_startup_check", lambda self: None)
    tasks = []
    pool = Mock()
    pool.start.side_effect = tasks.append
    monkeypatch.setattr("ui.qt_widget.QThreadPool.globalInstance", lambda: pool)
    widget = FloatingWidget()
    try:
        profiles = store.load_profiles()
        assert widget.panel is None and widget._settings_window is None
        assert len(profiles) == len(tasks) == 2
        assert widget._background_refresh_timer.isActive()
        assert widget._background_refresh_timer.interval() == 60_000
        widget._periodic_background_refresh()
        assert len(tasks) == 2
        for index, profile in enumerate(profiles):
            result = TokenData(status="ok", last_success_at=datetime.now(),
                               account_key=TokenData.account_key_for_config(store.profile_config(profile)),
                               quota_windows=[QuotaWindow("week", "每周额度", 84 + index * 10)])
            widget._finish_vpet_profile(profile["id"], profile["revision"], result)
        window = SettingsWindow(profile_monitor=widget)
        widget._settings_window = window
        assert window.account_profiles_page is None
        window.open_account_profiles()
        page = window.account_profiles_page
        assert len(page.results) == 2
        assert [page.results[p["id"]].quota_windows[0].used_percent for p in profiles] == [84, 94]
        page.refresh_profiles()
        assert len(tasks) == 2 and not page._queue
        assert not page.isVisible()
        widget._vpet_profile_due = {}
        widget._periodic_background_refresh()
        assert len(tasks) == 4
        # 面板隐藏后仍更新同一份结果；重开页面不会重新排一批请求。
        updated = TokenData(status="ok", last_success_at=datetime.now(),
                            account_key=page.results[profiles[0]["id"]].account_key,
                            quota_windows=[QuotaWindow("week", "每周额度", 95)])
        widget._finish_vpet_profile(profiles[0]["id"], profiles[0]["revision"], updated)
        assert page.results[profiles[0]["id"]] is updated
        page.reload_profiles()
        assert len(tasks) == 4
    finally:
        widget._closed = True
        widget.hide()
        widget.deleteLater()
        app.processEvents()


def test_cc_background_restores_account_cache_before_first_network_result(cc_accounts, monkeypatch):
    from api.providers.base import QuotaWindow
    from ui.account_profiles import AccountProfilesPage
    from ui.qt_update import AppUpdateController
    from ui.qt_widget import FloatingWidget

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(config_manager, "_config", {**DEFAULT_CONFIG, "FLOATING_STYLE": "ball", "VPET_ENABLED": False})
    monkeypatch.setattr(FloatingWidget, "refresh", Mock())
    monkeypatch.setattr(FloatingWidget, "_sync_codex_reset_news", lambda self: None)
    monkeypatch.setattr(AppUpdateController, "schedule_startup_check", lambda self: None)
    monkeypatch.setattr("ui.qt_widget.QThreadPool.globalInstance", Mock(return_value=Mock()))

    def cached(config):
        if config.get("CODEX_CC_SWITCH_ACCOUNT_ID"):
            return TokenData(status="ok", is_stale=True, quota_source="cache",
                             last_success_at=datetime.now(),
                             account_key=TokenData.account_key_for_config(config),
                             quota_windows=[QuotaWindow("week", "每周额度", 90)])

    monkeypatch.setattr(TokenData, "persisted_snapshot", cached)
    widget = FloatingWidget()
    try:
        page = AccountProfilesPage(monitor=widget)
        assert len(page.results) == 2
        assert len(widget._vpet_profile_in_flight) == 2
        assert all(result.quota_source == "cache" for result in page.results.values())
        profiles = store.load_profiles()
        profiles[0]["revision"] = "a" * 32
        store._write_profiles(profiles)
        page.reload_profiles()
        assert profiles[0]["id"] not in page.results
    finally:
        widget._closed = True
        widget.hide()
        widget.deleteLater()
        app.processEvents()


def test_background_profile_results_arrive_through_real_thread_pool(cc_accounts, monkeypatch, qtbot):
    from PySide6.QtCore import QThread

    from api.providers.base import QuotaWindow
    from ui.qt_update import AppUpdateController
    from ui.qt_widget import FloatingWidget

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(config_manager, "_config", {**DEFAULT_CONFIG, "FLOATING_STYLE": "ball", "VPET_ENABLED": False})
    monkeypatch.setattr(FloatingWidget, "refresh", Mock())
    monkeypatch.setattr(FloatingWidget, "_sync_codex_reset_news", lambda self: None)
    monkeypatch.setattr(AppUpdateController, "schedule_startup_check", lambda self: None)

    def fetch(config, lightweight=False):
        return TokenData(status="ok", last_success_at=datetime.now(),
                         account_key=TokenData.account_key_for_config(config),
                         quota_windows=[QuotaWindow("week", "每周额度", 84)])

    monkeypatch.setattr("ui.qt_widget._fetch_tokens_safely", fetch)
    threads = []
    accept = FloatingWidget._accept_profile_result

    def accepted(self, *args):
        threads.append(QThread.currentThread())
        accept(self, *args)

    monkeypatch.setattr(FloatingWidget, "_accept_profile_result", accepted)
    widget = FloatingWidget()
    try:
        qtbot.waitUntil(lambda: not widget._vpet_profile_in_flight, timeout=5000)
        assert widget.panel is None and widget._settings_window is None
        assert len(widget._vpet_profile_results) == 2
        assert len(threads) == 2 and all(thread == app.thread() for thread in threads)
    finally:
        widget._closed = True
        widget.hide()
        widget.deleteLater()
        app.processEvents()
