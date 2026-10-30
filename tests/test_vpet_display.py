"""The desktop pet receives registered display names and safe normalized quota values."""

import json
from datetime import datetime

import pytest

from api.providers import PROVIDERS
from api.providers.base import QuotaWindow
from config import runtime as config_manager
from data.store import PerProviderData, TokenData
from ui.vpet_host import usage_message


@pytest.mark.parametrize("provider_id", ["codex", "claude", "cursor", "mimo", "nayuto"])
def test_pet_uses_registered_brand_name_instead_of_raw_id(provider_id):
    data = TokenData(status="ok", per_provider=[PerProviderData(provider_id, "ignored-label")])
    message = usage_message(data, False, "deepseek")
    assert message["provider"] == PROVIDERS[provider_id].name
    data.quota_windows = [QuotaWindow("weekly", "周额度", 25)]
    assert usage_message(data, False, "deepseek")["provider"] == f"{PROVIDERS[provider_id].name} · 周额度"


@pytest.mark.parametrize("used", [None, True, False, [], {}, "bad", float("nan"), float("inf"), -1, 10**400])
def test_invalid_quota_keeps_unknown_pet_display_without_crashing(used):
    data = TokenData(status="ok", quota_windows=[QuotaWindow("week", "周额度", used)])
    message = usage_message(data, False, "claude")
    assert message["primary"] == "--"
    assert message["warning"] is False


def test_pet_display_whitelist_preserves_low_quota_and_rejects_private_fields(monkeypatch):
    monkeypatch.setattr(config_manager, "_config", {"QUOTA_ALERT_ENABLED": True})
    data = TokenData(status="ok", last_success_at=datetime.now(),
                     quota_windows=[QuotaWindow("weekly", "周额度", 95)])
    data.private_token = "synthetic-secret"
    data.credential_path = "C:/private/auth.json"
    message = usage_message(data, False, "codex")
    assert set(message) == {"type", "provider", "primary", "secondary", "status", "warning"}
    assert message["primary"] == "剩余 5%" and message["warning"] is True
    assert "不足" in message["status"]
    serialized = json.dumps(message)
    assert "synthetic-secret" not in serialized and "private/auth" not in serialized


@pytest.mark.parametrize("enabled", [None, False])
@pytest.mark.parametrize("used", [90, 95, 100])
def test_pet_low_quota_warning_stays_disabled_across_refreshes(monkeypatch, enabled, used):
    settings = {} if enabled is None else {"QUOTA_ALERT_ENABLED": enabled}
    monkeypatch.setattr(config_manager, "_config", settings)
    data = TokenData(status="ok", last_success_at=datetime.now(),
                     quota_windows=[QuotaWindow("weekly", "周额度", used)])
    for refreshing in (False, True, False, True, False):
        message = usage_message(data, refreshing, "codex")
        assert message["primary"] == f"剩余 {100 - used}%"
        assert message["warning"] is False
        assert message["status"] == ("正在刷新用量" if refreshing else "")


def test_pet_low_quota_warning_follows_current_setting(monkeypatch):
    settings = {"QUOTA_ALERT_ENABLED": True}
    monkeypatch.setattr(config_manager, "_config", settings)
    data = TokenData(status="ok", last_success_at=datetime.now(),
                     quota_windows=[QuotaWindow("weekly", "周额度", 95)])
    assert usage_message(data, False, "codex")["warning"] is True
    settings["QUOTA_ALERT_ENABLED"] = False
    assert usage_message(data, False, "codex")["warning"] is False
    settings["QUOTA_ALERT_ENABLED"] = True
    assert usage_message(data, False, "codex")["warning"] is True


def test_pet_refresh_error_warning_remains_when_quota_alerts_are_disabled(monkeypatch):
    monkeypatch.setattr(config_manager, "_config", {"QUOTA_ALERT_ENABLED": False})
    data = TokenData(status="error", last_success_at=datetime.now(),
                     quota_windows=[QuotaWindow("weekly", "周额度", 95)])
    message = usage_message(data, False, "codex")
    assert message["warning"] is True
    assert message["status"] == "用量更新失败，显示上次数据。"


def test_deepseek_whale_card_receives_numbers_without_account_identity():
    data = TokenData(status="ok", balance_cny=128.60, total_cost_cny=12.38,
                     account_key="private-account-identity")
    message = usage_message(data, False, "deepseek", True)
    assert message["balance_amount"] == "128.6"
    assert message["total_cost_amount"] == "12.38"
    assert message["pricing_peak"] is True
    assert "private-account-identity" not in json.dumps(message)
