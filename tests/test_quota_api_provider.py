"""Shared quota API error and credential handling, with synthetic HTTP responses."""

from unittest.mock import Mock, patch

import pytest
import requests

from api.providers.elevenlabs import ElevenLabsProvider
from api.providers.kimi import KimiProvider
from api.providers.minimax import MiniMaxProvider

PROVIDERS = [KimiProvider, MiniMaxProvider, ElevenLabsProvider]


@pytest.fixture
def make_provider():
    providers = []

    def make(provider_class, payload=None, *, status=200, base=None, key="sk-synthetic-test"):
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

    yield make
    for provider in providers:
        provider.close()


@pytest.mark.parametrize("provider_class", PROVIDERS)
@pytest.mark.parametrize(
    "status,code",
    [
        (401, "AUTH_EXPIRED"),
        (403, "AUTH_EXPIRED"),
        (429, "RATE_LIMITED"),
        (503, "SERVER_ERROR"),
        (301, "HTTP_ERROR"),
        (307, "HTTP_ERROR"),
        (400, "HTTP_ERROR"),
        (404, "HTTP_ERROR"),
    ],
)
def test_status_errors_are_redacted_and_cached(make_provider, provider_class, status, code):
    provider, session = make_provider(provider_class, {"secret": "private-response"}, status=status)
    result, error = provider.fetch_quota()
    assert result is None and error.code == code
    assert "private-response" not in str(error) and "sk-synthetic-test" not in str(error)
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert session.get.call_args.kwargs["timeout"] == 20
    assert session.get.call_args.kwargs.get("verify", True) is True
    session.get.return_value.json.assert_not_called()
    provider.fetch_quota()
    assert session.get.call_count == 1
    provider.reset_refresh_cache()
    provider.fetch_quota()
    assert session.get.call_count == 2


@pytest.mark.parametrize("provider_class", PROVIDERS)
@pytest.mark.parametrize(
    "exception,code",
    [
        (requests.Timeout("sk-synthetic-test private-response"), "NETWORK_TIMEOUT"),
        (requests.ConnectionError("sk-synthetic-test private-response"), "NETWORK_ERROR"),
        (requests.exceptions.SSLError("private-response"), "NETWORK_ERROR"),
    ],
)
def test_network_failures_do_not_echo_secrets(make_provider, provider_class, exception, code):
    provider, session = make_provider(provider_class)
    session.get.side_effect = exception
    result, error = provider.fetch_quota()
    assert result is None and error.code == code
    assert "private-response" not in str(error) and "sk-synthetic-test" not in str(error)


@pytest.mark.parametrize("provider_class", PROVIDERS)
@pytest.mark.parametrize(
    "base",
    [
        "http://example.com",
        "https://user:secret@example.com",
        "https://example.com/v1?secret=1",
        "https://example.com#secret",
        "bad",
        "https://example.com:bad",
    ],
)
def test_invalid_base_never_sends_key(make_provider, provider_class, base):
    provider, session = make_provider(provider_class, base=base)
    result, error = provider.fetch_quota()
    assert result is None and error.code == "INVALID_CONFIG"
    assert "secret" not in str(error)
    session.get.assert_not_called()


@pytest.mark.parametrize("provider_class", PROVIDERS)
@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_key_has_no_network_side_effect(make_provider, provider_class, key):
    provider, session = make_provider(provider_class, key=key)
    assert not provider.is_configured()
    result, error = provider.fetch_quota()
    assert result is None and error.code == "NOT_CONFIGURED"
    session.get.assert_not_called()


@pytest.mark.parametrize("provider_class", PROVIDERS)
@pytest.mark.parametrize("payload", [None, [], True, "private-response", {}])
def test_malformed_body_does_not_become_zero_quota(make_provider, provider_class, payload):
    provider, _ = make_provider(provider_class, payload)
    result, error = provider.fetch_quota()
    assert result is None and error.code in {"INVALID_RESPONSE", "QUOTA_UNAVAILABLE"}
    assert "private-response" not in str(error)


@pytest.mark.parametrize("provider_class", PROVIDERS)
def test_non_json_api_errors_and_session_close(make_provider, provider_class):
    provider, session = make_provider(provider_class)
    session.get.return_value.json.side_effect = ValueError("sk-synthetic-test private-response")
    result, error = provider.fetch_quota()
    assert result is None and error.code == "INVALID_RESPONSE"
    assert "private-response" not in str(error)
    provider.close()
    session.close.assert_called_once()


@pytest.mark.parametrize("provider_class", PROVIDERS)
def test_body_error_is_redacted(make_provider, provider_class):
    provider, _ = make_provider(provider_class, {"error": {"message": "sk-synthetic-test"}})
    result, error = provider.fetch_quota()
    assert result is None and error.code == "API_ERROR"
    assert "sk-synthetic-test" not in str(error)
