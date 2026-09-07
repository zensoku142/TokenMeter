"""The desktop pet receives registered display names and safe normalized quota values."""

import json
from datetime import datetime

import pytest

from api.providers import PROVIDERS
from api.providers.base import QuotaWindow
from data.store import PerProviderData, TokenData
from ui.vpet_host import usage_message


@pytest.mark.parametrize("provider_id", ["codex", "claude", "gemini", "minimax", "openrouter"])
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


def test_pet_display_whitelist_preserves_low_quota_and_rejects_private_fields():
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
