"""Official balance provider regressions use synthetic responses only."""

from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
import requests

from api.providers.api_balance import MoonshotProvider, OpenRouterProvider


@pytest.fixture
def make_provider():
    providers = []

    def create(provider_class, payload=None, *, status=200, base=None, key="sk-synthetic-test"):
        config = {f"{provider_class.id.upper()}_API_KEY": key}
        if base is not None:
            config[f"{provider_class.id.upper()}_BASE"] = base
        session = Mock()
        session.get.return_value.status_code = status
        session.get.return_value.json.return_value = payload
        with patch("api.providers.api_balance.build_session", return_value=session):
            provider = provider_class(config)
        providers.append(provider)
        return provider, session

    yield create
    for provider in providers:
        provider.close()


def key_payload(**overrides):
    data = {
        "limit_remaining": 7.25,
        "limit": 10,
        "usage_daily": 0.5,
        "usage_monthly": 2.75,
        "usage": 12.75,
    }
    data.update(overrides)
    return {"data": data}


def moonshot_payload(amount="49.58894", **overrides):
    payload = {
        "code": 0,
        "status": True,
        "scode": "0x0",
        "data": {"available_balance": amount, "cash_balance": "-10", "voucher_balance": "49.58894"},
    }
    payload.update(overrides)
    return payload


def test_openrouter_fetches_key_limit_and_costs_once_per_refresh(make_provider):
    provider, session = make_provider(OpenRouterProvider, key_payload())

    balance, balance_error = provider.fetch_balance()
    summary, summary_error = provider.fetch_summary()

    assert balance_error is None and summary_error is None
    assert balance.amount == Decimal("7.25")
    assert balance.currency == "USD"
    assert summary.today_cost == Decimal("0.5")
    assert summary.month_cost == Decimal("2.75")
    assert summary.total_cost == Decimal("12.75")
    assert summary.today_tokens is None and summary.month_tokens is None
    assert session.get.call_count == 1
    assert session.get.call_args.args == ("https://openrouter.ai/api/v1/key",)
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert session.get.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-synthetic-test"
    assert session.get.call_args.kwargs["timeout"] == 20
    assert not provider.supports_daily_usage
    assert not provider.supports_subscription_quota
    assert provider.fetch_payloads([(9, 2026)]) == ([], [])
    assert "密钥" in provider.balance_label
    assert "不代表账户余额" in provider.balance_description

    provider.reset_refresh_cache()
    provider.fetch_summary()
    assert session.get.call_count == 2


@pytest.mark.parametrize("value", [None, 0, "0", "-0.01"])
def test_openrouter_preserves_unlimited_zero_and_overdrawn_key_limits(make_provider, value):
    provider, _ = make_provider(OpenRouterProvider, key_payload(limit_remaining=value))
    balance, error = provider.fetch_balance()
    assert error is None
    assert balance.amount == (None if value is None else Decimal(str(value)))


def test_openrouter_does_not_substitute_lifetime_usage_for_reset_limit(make_provider):
    provider, _ = make_provider(OpenRouterProvider, key_payload(limit=10, usage=500, limit_remaining=8))
    balance, error = provider.fetch_balance()
    assert error is None
    assert balance.amount == Decimal("8")


@pytest.mark.parametrize("field", ["limit_remaining", "usage_daily", "usage_monthly", "usage"])
@pytest.mark.parametrize("value", [True, False, "", "bad", "NaN", "Infinity", "-Infinity", float("nan"), float("inf"), [], {}])
def test_openrouter_rejects_malformed_numbers(make_provider, field, value):
    provider, _ = make_provider(OpenRouterProvider, key_payload(**{field: value}))
    result, error = provider.fetch_balance() if field == "limit_remaining" else provider.fetch_summary()
    assert result is None
    assert error.code == "INVALID_RESPONSE"


@pytest.mark.parametrize("field", ["limit_remaining", "usage_daily", "usage_monthly", "usage"])
def test_openrouter_rejects_missing_numbers(make_provider, field):
    payload = key_payload()
    del payload["data"][field]
    provider, _ = make_provider(OpenRouterProvider, payload)
    result, error = provider.fetch_balance() if field == "limit_remaining" else provider.fetch_summary()
    assert result is None
    assert error.code == "INVALID_RESPONSE"


@pytest.mark.parametrize("value", [None, -1, "-0.01"])
@pytest.mark.parametrize("field", ["usage_daily", "usage_monthly", "usage"])
def test_openrouter_requires_nonnegative_known_spend(make_provider, field, value):
    provider, _ = make_provider(OpenRouterProvider, key_payload(**{field: value}))
    summary, error = provider.fetch_summary()
    assert summary is None
    assert error.code == "INVALID_RESPONSE"


@pytest.mark.parametrize(
    "base,currency,url",
    [
        (None, "CNY", "https://api.moonshot.cn/v1/users/me/balance"),
        ("https://api.moonshot.ai/v1/", "USD", "https://api.moonshot.ai/v1/users/me/balance"),
        ("https://api.moonshot.cn/v1/", "CNY", "https://api.moonshot.cn/v1/users/me/balance"),
    ],
)
def test_moonshot_reads_official_available_balance_and_site_currency(make_provider, base, currency, url):
    provider, session = make_provider(MoonshotProvider, moonshot_payload(), base=base)
    balance, error = provider.fetch_balance()
    assert error is None
    assert balance.amount == Decimal("49.58894")
    assert balance.currency == currency
    assert session.get.call_args.args == (url,)
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert provider.fetch_summary() == (None, None)
    provider.fetch_balance()
    assert session.get.call_count == 1


@pytest.mark.parametrize("amount", [None, True, False, "", "NaN", "Infinity", "-Infinity", float("nan"), float("inf"), [], {}])
def test_moonshot_rejects_malformed_amounts(make_provider, amount):
    provider, _ = make_provider(MoonshotProvider, moonshot_payload(amount))
    balance, error = provider.fetch_balance()
    assert balance is None
    assert error.code == "INVALID_RESPONSE"


def test_moonshot_rejects_missing_available_balance(make_provider):
    payload = moonshot_payload()
    del payload["data"]["available_balance"]
    provider, _ = make_provider(MoonshotProvider, payload)
    balance, error = provider.fetch_balance()
    assert balance is None
    assert error.code == "INVALID_RESPONSE"


@pytest.mark.parametrize("amount", [0, "-0.01"])
def test_moonshot_preserves_empty_and_negative_balance(make_provider, amount):
    provider, _ = make_provider(MoonshotProvider, moonshot_payload(amount))
    balance, error = provider.fetch_balance()
    assert error is None
    assert balance.amount == Decimal(str(amount))


@pytest.mark.parametrize(
    "overrides,expected_code",
    [
        ({"code": 401, "status": False}, "API_ERROR"),
        ({"code": 0, "status": False}, "API_ERROR"),
        ({"code": True}, "INVALID_RESPONSE"),
        ({"code": "0"}, "INVALID_RESPONSE"),
        ({"status": "true"}, "INVALID_RESPONSE"),
        ({"status": None}, "INVALID_RESPONSE"),
    ],
)
def test_moonshot_validates_business_status(make_provider, overrides, expected_code):
    provider, _ = make_provider(MoonshotProvider, moonshot_payload(**overrides))
    balance, error = provider.fetch_balance()
    assert balance is None
    assert error.code == expected_code


@pytest.mark.parametrize("provider_class", [OpenRouterProvider, MoonshotProvider])
@pytest.mark.parametrize(
    "status,expected_code",
    [(401, "AUTH_EXPIRED"), (403, "AUTH_EXPIRED"), (429, "RATE_LIMITED"), (503, "SERVER_ERROR"),
     (400, "HTTP_ERROR"), (301, "HTTP_ERROR"), (302, "HTTP_ERROR"), (307, "HTTP_ERROR")],
)
def test_http_errors_are_redacted_and_do_not_follow_redirects(make_provider, provider_class, status, expected_code):
    provider, session = make_provider(provider_class, {"secret": "private-response"}, status=status)
    balance, error = provider.fetch_balance()
    assert balance is None
    assert error.code == expected_code
    assert "sk-synthetic-test" not in str(error)
    assert "private-response" not in str(error)
    assert session.get.call_args.kwargs["allow_redirects"] is False
    session.get.return_value.json.assert_not_called()
    provider.fetch_balance()
    assert session.get.call_count == 1


@pytest.mark.parametrize("provider_class", [OpenRouterProvider, MoonshotProvider])
@pytest.mark.parametrize(
    "exception,expected_code",
    [(requests.Timeout("private-response"), "NETWORK_TIMEOUT"),
     (requests.ConnectionError("sk-synthetic-test"), "NETWORK_ERROR")],
)
def test_network_failures_are_cached_redacted_and_retry_next_refresh(make_provider, provider_class, exception, expected_code):
    provider, session = make_provider(provider_class)
    session.get.side_effect = exception
    result, error = provider.fetch_balance()
    assert result is None
    assert error.code == expected_code
    assert "private-response" not in str(error)
    assert "sk-synthetic-test" not in str(error)
    provider.fetch_balance()
    assert session.get.call_count == 1

    session.get.side_effect = None
    session.get.return_value.json.return_value = key_payload() if provider_class is OpenRouterProvider else moonshot_payload()
    provider.reset_refresh_cache()
    result, error = provider.fetch_balance()
    assert result is not None and error is None
    assert session.get.call_count == 2


@pytest.mark.parametrize("provider_class", [OpenRouterProvider, MoonshotProvider])
@pytest.mark.parametrize("payload", [None, [], "private-response", {}, {"data": []}, {"data": {}}])
def test_invalid_response_shape_is_not_silently_zero(make_provider, provider_class, payload):
    provider, _ = make_provider(provider_class, payload)
    result, error = provider.fetch_balance()
    assert result is None
    assert error.code == "INVALID_RESPONSE"
    assert "private-response" not in str(error)


@pytest.mark.parametrize("provider_class", [OpenRouterProvider, MoonshotProvider])
def test_non_json_response_is_redacted(make_provider, provider_class):
    provider, session = make_provider(provider_class)
    session.get.return_value.json.side_effect = ValueError("private-response sk-synthetic-test")
    result, error = provider.fetch_balance()
    assert result is None
    assert error.code == "INVALID_RESPONSE"
    assert "private-response" not in str(error)
    assert "sk-synthetic-test" not in str(error)


@pytest.mark.parametrize("provider_class", [OpenRouterProvider, MoonshotProvider])
@pytest.mark.parametrize("base", ["http://example.com/v1", "https://user:secret@example.com/v1", "https://example.com/v1?q=secret", "https://example.com/v1#secret", "not-a-url", "https://example.com:bad/v1"])
def test_invalid_base_never_sends_credentials(make_provider, provider_class, base):
    provider, session = make_provider(provider_class, base=base)
    result, error = provider.fetch_balance()
    assert result is None
    assert error.code == "INVALID_CONFIG"
    assert "secret" not in str(error)
    session.get.assert_not_called()


@pytest.mark.parametrize("provider_class", [OpenRouterProvider, MoonshotProvider])
@pytest.mark.parametrize("key", ["", None, "   "])
def test_missing_credentials_do_not_make_network_requests(make_provider, provider_class, key):
    provider, session = make_provider(provider_class, key=key)
    assert not provider.is_configured()
    result, error = provider.fetch_balance()
    assert result is None
    assert error.code == "NOT_CONFIGURED"
    session.get.assert_not_called()


@pytest.mark.parametrize("provider_class", [OpenRouterProvider, MoonshotProvider])
def test_provider_closes_owned_session(make_provider, provider_class):
    provider, session = make_provider(provider_class)
    provider.close()
    session.close.assert_called_once()
