"""Configuration and visible data contracts for the expanded provider registry."""

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
import requests
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton

from api.providers import PROVIDERS, get_provider
from api.providers.base import Provider, ProviderSummary, _decimal, safe_int
from config import runtime as config_manager
from config.defaults import DEFAULT_CONFIG, FIELD_META, PROVIDER_IDS, SECRET_KEYS
from config.store import public_values, validate_config
from data import history
from data.store import PerProviderData, TokenData
from scripts.claude_statusline import make_snapshot, write_snapshot
from ui.i18n import configure_language
from ui.qt_panel import MainPanel
from ui.qt_settings import SettingsWindow
from ui.qt_theme import configure_theme


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config_manager, "_config", DEFAULT_CONFIG.copy())
    monkeypatch.setattr(config_manager, "load_config", lambda: config_manager.all_config())
    monkeypatch.setattr(config_manager, "pending_data_dir", lambda: None)
    monkeypatch.setattr(config_manager, "data_dir_migration_error", lambda: "")
    monkeypatch.setattr(history, "DB_PATH", tmp_path / "usage.db")
    monkeypatch.setattr(TokenData, "_provider_snapshots", {})
    monkeypatch.setattr(TokenData, "_last_snapshot", None)
    app = QApplication.instance() or QApplication([])
    configure_language(app, "zh-cn")
    configure_theme(app, "dark")
    return app


@pytest.mark.parametrize("detail_cost", [None, 0, 2])
def test_live_aggregation_does_not_persist_missing_cost_as_token_amount(isolated, detail_cost):
    class TokenOnlyProvider(Provider):
        id = "token-only"
        name = "Token only"
        supports_daily_usage = True
        supports_cost = True

        def is_configured(self):
            return True

        def fetch_payloads(self, _months):
            usages = [{"type": "RESPONSE_TOKEN", "amount": 700}]
            if detail_cost is not None:
                usages.append({"type": "cost_cny", "amount": detail_cost})
            return [{"days": [{"date": "2026-09-07", "data": [{"model": "model", "usage": usages}]}]}], []

    provider = TokenOnlyProvider({})
    TokenData._provider_snapshots["token-only"] = TokenData(
        last_success_at=datetime(2026, 9, 6),
        per_provider=[PerProviderData("token-only", "Token only", today_cost_cny=9, weekly_cost_cny=9)],
    )
    provider.fetch_summary = Mock(return_value=(ProviderSummary(today_cost=Decimal("5")), None))
    data = TokenData._fetch_with_provider(provider, date(2026, 9, 7))
    assert data.status == "ok"
    assert data.today_tokens == 700
    assert data.today_cost_cny == (5 if detail_cost is None else detail_cost)
    assert data.weekly_cost_cny == detail_cost
    assert history.total_cost("token-only") == Decimal(detail_cost or 0)


def test_registry_configuration_and_secret_storage_are_in_sync():
    assert tuple(PROVIDERS) == PROVIDER_IDS
    for provider_id, provider_cls in PROVIDERS.items():
        values = validate_config({"ACTIVE_PROVIDER": provider_id, "BACKGROUND_PROVIDER_IDS": [provider_id]})
        assert values["ACTIVE_PROVIDER"] == provider_id
        for field, meta in provider_cls.credential_fields.items():
            key = f"{provider_id.upper()}_{field}"
            assert key in DEFAULT_CONFIG
            if meta.get("secret"):
                assert key in SECRET_KEYS
                assert FIELD_META[key]["secret"]
                assert key not in public_values({key: "synthetic-secret"})


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "sNaN"])
def test_shared_numbers_reject_nonfinite_values(value):
    with pytest.raises(ValueError):
        _decimal(value)
    assert safe_int(value) == 0


def test_openrouter_refresh_keeps_key_scope_and_unknown_token_totals(isolated):
    provider = get_provider("openrouter", {"OPENROUTER_API_KEY": "synthetic-key"})
    provider._session.get = Mock(return_value=Mock(status_code=200))
    provider._session.get.return_value.json.return_value = {"data": {
        "limit_remaining": 6.25, "usage_daily": 0.1, "usage_monthly": 1.2, "usage": 3.75,
    }}
    try:
        data = TokenData._fetch_with_provider(provider, date.today())
        assert data.status == "ok"
        assert data.balance_cny == 6.25
        assert data.currency == "USD"
        assert data.monthly_usage_tokens is None
        assert data.today_tokens is None
        assert data.total_cost_cny == 3.75
        assert provider._session.get.call_count == 1
        panel = MainPanel()
        panel.update_data(data)
        assert panel.balance_card.title_label.text() == "密钥剩余额度"
        assert panel.today_card.title_label.text() == "今日使用金额 (UTC)"
        assert panel.month_card.title_label.text() == "本月累计 (UTC)"
        assert panel.month_card.detail.text() == "--"
        assert panel.trend.empty_label.text() == "平台未提供历史明细"
        assert not panel.trend.empty_label.isHidden()
        provider._session.get.return_value.json.return_value = {"data": {"limit_remaining": None}}
        provider.reset_refresh_cache()
        TokenData._provider_snapshots.clear()
        TokenData._last_snapshot = None
        incomplete = TokenData._fetch_with_provider(provider, date.today())
        assert incomplete.total_cost_cny is None
    finally:
        provider.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_new_provider_settings_keep_secrets_masked_and_explain_support(isolated, theme):
    configure_theme(isolated, theme)
    window = SettingsWindow()
    window._autosave_ready = False
    window._save_timer.stop()
    for provider_id in ("openrouter", "moonshot", "copilot", "claude", "zai"):
        index = window.provider_combo.findData(provider_id)
        assert index >= 0
        window.provider_combo.setCurrentIndex(index)
        assert window.findChild(QLabel, "providerSupportDescription").text()
        assert window.findChild(QPushButton, "providerDashboardButton") is not None
        for field, meta in PROVIDERS[provider_id].credential_fields.items():
            editor = window._provider_widgets[field]
            if meta.get("secret"):
                assert isinstance(editor, QLineEdit)
                assert editor.echoMode() == QLineEdit.EchoMode.Password
    assert set(window.background_provider_checks) == set(PROVIDER_IDS)
    layout = next(iter(window.background_provider_checks.values())).parentWidget().layout()
    assert layout.columnCount() == 3
    # 表单保存仅需元数据，不能为每个供应商创建未关闭的网络连接池。
    with patch("api.providers.api_balance.build_session", side_effect=AssertionError("session leak")):
        window._values()


@pytest.mark.parametrize("value", ["1e1000", "-1e1000"])
def test_out_of_range_balance_cannot_enter_float_cache(value):
    provider = get_provider("openrouter", {"OPENROUTER_API_KEY": "synthetic-key"})
    provider._session.get = Mock(return_value=Mock(status_code=200))
    provider._session.get.return_value.json.return_value = {"data": {"limit_remaining": value}}
    try:
        balance, error = provider.fetch_balance()
        assert balance is None
        assert error.code == "INVALID_RESPONSE"
    finally:
        provider.close()


def test_moonshot_international_cached_amount_keeps_currency_offline(isolated):
    provider = get_provider("moonshot", {
        "MOONSHOT_API_KEY": "synthetic-key", "MOONSHOT_BASE": "https://api.moonshot.ai/v1",
    })
    provider._session.get = Mock(return_value=Mock(status_code=200))
    provider._session.get.return_value.json.return_value = {
        "code": 0, "status": True, "data": {"available_balance": "12.34"},
    }
    try:
        first = TokenData._fetch_with_provider(provider, date.today())
        assert first.currency == "USD"
        provider.reset_refresh_cache()
        provider._session.get.side_effect = requests.ConnectionError("synthetic")
        offline = TokenData._fetch_with_provider(provider, date.today())
        assert offline.is_stale
        assert offline.balance_cny == 12.34
        assert offline.currency == "USD"
        assert offline.last_success_at == first.last_success_at
    finally:
        provider.close()


def test_claude_snapshot_is_labelled_as_local_with_compact_time(isolated, tmp_path):
    import time

    path = tmp_path / "statusline.json"
    write_snapshot(path, make_snapshot({"rate_limits": {
        "five_hour": {"used_percentage": 25, "resets_at": time.time() + 3600},
        "seven_day": {"used_percentage": 61, "resets_at": time.time() + 86400},
    }}))
    provider = get_provider("claude", {"CLAUDE_STATUSLINE_FILE": str(path)})
    data = TokenData._fetch_with_provider(provider, date.today())
    assert data.quota_source == "local_snapshot"
    assert len(data.quota_metrics[0].value) == 5
    assert "UTC" in data.quota_metrics[0].detail
    panel = MainPanel()
    panel.update_data(data)
    assert "本机快照" in panel.status_text.text()
    assert panel.activity_summary.text() == "平台未提供 Token 明细"
