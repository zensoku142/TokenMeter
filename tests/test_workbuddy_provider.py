import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import requests

from api.providers.workbuddy import WorkBuddyProvider
from config import runtime as config_manager
from config.defaults import DEFAULT_CONFIG
from config.store import validate_config
from data.store import TokenData
from ui.qt_settings import SettingsWindow

CONFIG = {
    "ACTIVE_PROVIDER": "workbuddy",
    "BACKGROUND_PROVIDER_IDS": ["workbuddy"],
    "WORKBUDDY_COOKIE": "session=synthetic",
    "WORKBUDDY_USER_AGENT": "Synthetic Browser",
}


def response(data=None, status=200):
    result = Mock(spec=requests.Response, status_code=status)
    result.json.return_value = {"code": 0, "data": data}
    return result


def usage_row(request_id, product, credit):
    return {
        "requestId": request_id,
        "appType": product,
        "credit": credit,
        "model": "test-model",
        "client": "WorkBuddy",
        "requestTime": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "input": "This prompt must not reach display or cache",
    }


def test_registered_workbuddy_refreshes_paginated_workbuddy_credits(monkeypatch):
    session = Mock()
    session.post.side_effect = [
        response({"TotalCredits": "2868.17"}),
        response({"data": [usage_row("a", "codebuddy", "0.25")], "total": 3}),
        response({"data": [
            usage_row("a", "codebuddy", "0.25"),
            usage_row("b", "workbuddy", "0.10"),
            usage_row("c", "ima", "80"),
        ], "total": 3}),
    ]
    monkeypatch.setattr("api.providers.workbuddy.build_session", lambda: session)

    config = validate_config(CONFIG)
    assert config["BACKGROUND_PROVIDER_IDS"] == ["workbuddy"]
    data = TokenData.fetch(config=config)
    assert data.status == "ok"
    assert data.quota_metrics[0].value == "2868.17"
    assert data.statistics_source == "interface"
    assert data.quota_statistics[1].value == "0.35"
    assert data.quota_statistics[2].value == "2"
    assert data.quota_statistics[-1].detail == "WorkBuddy"
    # 今日模型单独成组，主面板右栏切到“今日”时不用再请求一次。
    today_rows = [m for m in data.quota_statistics if m.value_kind == "credits_today"]
    assert [(m.title, m.value) for m in today_rows] == [("test-model", "0.35")]
    assert today_rows[0].detail == "今日 · WorkBuddy"
    assert "This prompt" not in repr(data.quota_statistics)
    first_page, second_page = [call.kwargs for call in session.post.call_args_list[1:]]
    assert first_page["json"]["appTypes"] == ["codebuddy"]
    assert first_page["json"]["pageNum"] == 1
    assert second_page["json"]["pageNum"] == 2
    assert first_page["allow_redirects"] is False


def test_workbuddy_partial_refresh_keeps_fresh_balance_and_marks_usage_as_cached(monkeypatch):
    session = Mock()
    session.post.side_effect = [
        response({"TotalCredits": "5"}),
        response({"data": [usage_row("a", "codebuddy", "0.25")], "total": 1}),
        response({"TotalCredits": "4.75"}),
        response({"data": [usage_row("a", "codebuddy", "0.25")], "total": 2}),
        response({"data": [], "total": 2}),
    ]
    monkeypatch.setattr("api.providers.workbuddy.build_session", lambda: session)
    config = validate_config(CONFIG)
    TokenData.fetch(config=config)

    data = TokenData.fetch(config=config)
    assert data.status == "partial"
    assert data.quota_source == "interface"
    assert data.quota_metrics[0].value == "4.75"
    assert data.statistics_source == "cache"
    assert data.quota_statistics[1].value == "0.25"
    assert [error.code for error in data.errors] == ["USAGE_INCOMPLETE"]


def test_workbuddy_reconnects_after_login_and_validates_after_browser_exit(monkeypatch):
    browser = Mock()
    browser.cookies.return_value = CONFIG["WORKBUDDY_COOKIE"]
    browser.user_agent.return_value = CONFIG["WORKBUDDY_USER_AGENT"]
    monkeypatch.setattr("api.providers.workbuddy.browser_cookie.open_chrome_session", lambda *a, **kw: browser)
    session = Mock()

    def fetch(*args, **kwargs):
        browser.close_and_verify.assert_called_once()
        return response({"TotalCredits": "0"})

    session.post.side_effect = fetch
    monkeypatch.setattr("api.providers.workbuddy.build_session", lambda: session)
    finish = threading.Event()
    finish.set()
    credentials = WorkBuddyProvider.acquire_credentials_via_chrome(finish)
    browser.reconnect_page.assert_called_once_with("www.workbuddy.cn")
    assert WorkBuddyProvider.acquired_credential_values(credentials)["COOKIE"] == CONFIG["WORKBUDDY_COOKIE"]


def test_verified_workbuddy_login_is_saved_immediately_and_loaded_on_restart(monkeypatch, tmp_path):
    vault = {}
    path = tmp_path / "config.json"
    monkeypatch.setattr(config_manager, "_config", DEFAULT_CONFIG.copy())
    monkeypatch.setattr(config_manager, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_manager, "CONFIG_PATH", path)
    monkeypatch.setattr(config_manager, "ensure_config_file", lambda: None)
    monkeypatch.setattr(config_manager, "_write_credential", vault.__setitem__)
    monkeypatch.setattr(config_manager, "_read_credential", lambda key: vault.get(key, ""))
    settings = SimpleNamespace(
        _provider_drafts={}, _rendered_provider_id="deepseek", on_saved=Mock(), _schedule_save=Mock(),
    )
    acquired = {field: CONFIG[f"WORKBUDDY_{field}"] for field in ("COOKIE", "USER_AGENT")}

    SettingsWindow._apply_acquired_cookie(settings, "workbuddy", json.dumps(acquired))

    settings.on_saved.assert_called_once()
    settings._schedule_save.assert_not_called()
    public = json.loads(path.read_text(encoding="utf-8"))
    assert "WORKBUDDY_COOKIE" not in public
    assert public["WORKBUDDY_USER_AGENT"] == acquired["USER_AGENT"]
    monkeypatch.setattr(config_manager, "_config", DEFAULT_CONFIG.copy())
    loaded = config_manager.load_config()
    assert loaded["WORKBUDDY_COOKIE"] == acquired["COOKIE"]
    assert loaded["WORKBUDDY_USER_AGENT"] == acquired["USER_AGENT"]


def test_aggregate_usage_separates_today_models_in_china_time():
    from datetime import date

    records = [
        # UTC+8 今天上午 => 计入今日与本月
        {"appType": "codebuddy", "credit": "10", "model": "model-a", "client": "WorkBuddy",
         "requestTime": "2026-11-11T09:00:00+08:00"},
        {"appType": "codebuddy", "credit": "5", "model": "model-b", "client": "WorkBuddy",
         "requestTime": "2026-11-10T23:00:00+08:00"},
        # UTC+5 的 23:30 已经是中国时区次日 02:30，超出“今日”范围
        {"appType": "codebuddy", "credit": "99", "model": "model-c", "client": "WorkBuddy",
         "requestTime": "2026-11-11T23:30:00+05:00"},
    ]
    usage = WorkBuddyProvider._aggregate_usage(records, date(2026, 11, 11), date(2026, 11, 1))

    assert [(item["model"], item["credits"]) for item in usage["today_models"]] == [
        ("model-a", "10"),
    ]
    assert [(item["model"], item["credits"]) for item in usage["models"]] == [
        ("model-a", "10"),
        ("model-b", "5"),
    ]
    assert usage["models"][0]["clients"] == ["WorkBuddy"]
    # 本月汇总仍按全部已发生请求计算，今日只切分模型视角。
    assert [item["value"] for item in usage["statistics"][:2]] == ["15", "10"]
