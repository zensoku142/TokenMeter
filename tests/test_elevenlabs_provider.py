"""Official subscription counters remain character credits, not LLM tokens."""

from datetime import UTC, datetime

import pytest

from api.providers.elevenlabs import ElevenLabsProvider
from tests.test_quota_api_provider import make_provider as _quota_fixture

make_provider = _quota_fixture


def payload(**overrides):
    data = {
        "character_count": 1200,
        "character_limit": 10000,
        "tier": "starter",
        "next_character_count_reset_unix": 1790000000,
    }
    data.update(overrides)
    return data


def test_official_subscription_and_xi_api_key(make_provider):
    provider, session = make_provider(ElevenLabsProvider, payload())
    quota, error = provider.fetch_quota()
    assert error is None and quota.windows[0].used_percent == 12
    assert quota.windows[0].resets_at == datetime.fromtimestamp(1790000000, UTC)
    assert quota.windows[0].window_minutes is None
    assert quota.metrics[0].value == "1,200 / 10,000" and quota.plan == "starter"
    assert session.get.call_args.args == ("https://api.elevenlabs.io/v1/user/subscription",)
    assert session.get.call_args.kwargs["headers"]["xi-api-key"] == "sk-synthetic-test"
    assert "Authorization" not in session.get.call_args.kwargs["headers"]
    assert provider.fetch_summary() == (None, None)


def test_overage_preserves_counter_but_never_adds_extension_to_free_quota(make_provider):
    provider, _ = make_provider(
        ElevenLabsProvider,
        payload(
            character_count=12000,
            max_credit_limit_extension="unlimited",
            allowed_to_extend_character_limit=True,
        ),
    )
    quota, error = provider.fetch_quota()
    assert error is None and quota.windows[0].used_percent == 100
    assert quota.metrics[0].value == "12,000 / 10,000"


def test_zero_limit_remains_explicitly_no_allowance(make_provider):
    provider, _ = make_provider(ElevenLabsProvider, payload(character_count=0, character_limit=0))
    quota, error = provider.fetch_quota()
    assert error is None and quota.windows == ()
    assert quota.metrics[1].value == "无可用订阅额度"


@pytest.mark.parametrize("field", ["character_count", "character_limit"])
@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        "NaN",
        "Inf",
        "-Inf",
        float("nan"),
        float("inf"),
        -1,
        0.5,
        "",
        [],
        {},
        "1e999",
    ],
)
def test_bad_character_counters_are_not_zero(make_provider, field, value):
    provider, _ = make_provider(ElevenLabsProvider, payload(**{field: value}))
    result, error = provider.fetch_quota()
    assert result is None and error.code == "INVALID_RESPONSE"


@pytest.mark.parametrize("value", [True, False, "NaN", "Inf", -1, {}, [], "1e100"])
def test_bad_reset_is_rejected(make_provider, value):
    provider, _ = make_provider(ElevenLabsProvider, payload(next_character_count_reset_unix=value))
    result, error = provider.fetch_quota()
    assert result is None and error.code == "INVALID_RESPONSE"


def test_null_reset_keeps_valid_quota(make_provider):
    provider, _ = make_provider(ElevenLabsProvider, payload(next_character_count_reset_unix=None))
    quota, error = provider.fetch_quota()
    assert error is None and quota.windows[0].resets_at is None
