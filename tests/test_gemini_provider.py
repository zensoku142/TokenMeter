"""Synthetic OAuth fixtures only; never read host credentials or contact Google."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from api.providers.gemini import GeminiProvider


def bucket(**fields):
    return {"modelId": "gemini-2.5-pro", "remainingFraction": 0.75,
            "resetTime": "2026-09-06T08:00:00Z", **fields}


def response(payload, status=200):
    return Mock(status_code=status, text="synthetic-secret", json=Mock(return_value=payload))


@pytest.fixture
def provider(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    session = Mock()
    session.post.return_value = response({"buckets": [bucket()]})
    monkeypatch.setattr("api.providers.gemini.build_session", lambda: session)
    value = GeminiProvider({"GEMINI_ACCESS_TOKEN": "synthetic-secret", "GEMINI_PROJECT_ID": "test-project"})
    yield value
    value.close()


def save_credentials(provider, tmp_path, **fields):
    path = tmp_path / ".gemini" / "oauth_creds.json"
    path.parent.mkdir(exist_ok=True)
    data = {"access_token": "synthetic-local-secret", "refresh_token": "never-use-refresh",
            "expiry_date": (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp() * 1000, **fields}
    path.write_text(json.dumps(data), encoding="utf-8")
    provider._config = {"GEMINI_PROJECT_ID": "test-project"}
    return path


def test_manual_token_has_priority_and_fixed_transport(provider, monkeypatch):
    monkeypatch.setattr(Path, "open", Mock(side_effect=AssertionError("must not read local files")))
    quota, error = provider.fetch_quota()
    assert error is None
    assert quota.windows[0].used_percent == 25
    assert quota.windows[0].resets_at == datetime(2026, 9, 6, 8, tzinfo=timezone.utc)
    assert quota.windows[0].window_minutes is None
    args, kwargs = provider._session.post.call_args
    assert args == ("https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota",)
    assert kwargs["json"] == {"project": "test-project"}
    assert kwargs["headers"]["Authorization"] == "Bearer synthetic-secret"
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == (3, 10)


@pytest.mark.parametrize("project", ["managed-project", {"id": "managed-project"}, {"projectId": "managed-project"}])
def test_project_discovery_then_quota(provider, project):
    provider._config = {"GEMINI_ACCESS_TOKEN": "synthetic-secret"}
    provider._session.post.side_effect = [
        response({"cloudaicompanionProject": project, "paidTier": {"name": "Code Assist Enterprise"}}),
        response({"buckets": [bucket()]}),
    ]
    quota, error = provider.fetch_quota()
    assert error is None
    assert quota.plan == "Code Assist Enterprise"
    calls = provider._session.post.call_args_list
    assert calls[0].args[0].endswith(":loadCodeAssist")
    assert calls[1].kwargs["json"] == {"project": "managed-project"}


@pytest.mark.parametrize("status", [{}, {"cloudaicompanionProject": ""}, {"cloudaicompanionProject": {}},
                                  {"cloudaicompanionProject": 123}])
def test_missing_project_never_onboards_or_guesses(provider, status):
    provider._config = {"GEMINI_ACCESS_TOKEN": "synthetic-secret"}
    provider._session.post.return_value = response(status)
    quota, error = provider.fetch_quota()
    assert quota is None
    assert error.code == "PROJECT_REQUIRED"
    assert provider._session.post.call_count == 1


def test_local_credentials_are_read_only_and_reloaded(provider, tmp_path):
    path = save_credentials(provider, tmp_path)
    before = path.read_bytes()
    assert provider.is_configured()
    first = provider.snapshot_identity()
    assert provider.fetch_quota()[1] is None
    assert path.read_bytes() == before
    assert provider._session.post.call_args.kwargs["headers"]["Authorization"] == "Bearer synthetic-local-secret"
    save_credentials(provider, tmp_path, access_token="another-account-secret")
    assert provider.snapshot_identity() != first
    assert "secret" not in first


def test_identity_includes_project_and_path(provider, tmp_path):
    initial = provider.snapshot_identity()
    provider._config["GEMINI_PROJECT_ID"] = "another-project"
    assert provider.snapshot_identity() != initial
    initial = provider.snapshot_identity()
    provider._config["GEMINI_CREDENTIALS_FILE"] = str(tmp_path / "another.json")
    assert provider.snapshot_identity() != initial


def test_expired_local_token_does_not_refresh_or_query(provider, tmp_path):
    path = save_credentials(provider, tmp_path, expiry_date=1)
    before = path.read_bytes()
    quota, error = provider.fetch_quota()
    assert quota is None
    assert error.code == "AUTH_EXPIRED"
    assert path.read_bytes() == before
    provider._session.post.assert_not_called()


@pytest.mark.parametrize("selected", ["api-key", "gemini-api-key", "vertex-ai"])
def test_old_oauth_is_ignored_after_cli_auth_switch(provider, tmp_path, selected):
    path = save_credentials(provider, tmp_path)
    path.with_name("settings.json").write_text(json.dumps({"security": {"auth": {"selectedType": selected}}}))
    assert not provider.is_configured()
    assert provider.fetch_quota()[1].code == "UNSUPPORTED_AUTH"
    provider._session.post.assert_not_called()


@pytest.mark.parametrize("expiry", [True, "123", -1, float("nan"), float("inf"), 10**400])
def test_malformed_expiry_fails_safely(provider, tmp_path, expiry):
    save_credentials(provider, tmp_path, expiry_date=expiry)
    assert provider.fetch_quota()[1].code == "INVALID_CREDENTIALS"
    assert provider.snapshot_identity() == ""
    provider._session.post.assert_not_called()


@pytest.mark.parametrize("raw", ["{", "[]", "x" * (64 * 1024 + 1)], ids=["json", "list", "oversize"])
def test_malformed_credentials_fail_safely(provider, tmp_path, raw):
    path = save_credentials(provider, tmp_path)
    path.write_text(raw, encoding="utf-8")
    assert provider.fetch_quota()[1].code == "INVALID_CREDENTIALS"
    provider._session.post.assert_not_called()


def test_missing_credentials_skip_network(provider):
    provider._config = {}
    assert not provider.is_configured()
    assert provider.snapshot_identity() == ""
    assert provider.fetch_quota()[1].code == "NOT_CONFIGURED"
    provider._session.post.assert_not_called()


def test_multiple_token_buckets_use_lowest_remaining_with_its_reset(provider):
    provider._session.post.return_value = response({"buckets": [
        bucket(remainingFraction=0.9), bucket(remainingFraction=0.2, resetTime="2026-09-07T08:00:00Z"),
        bucket(modelId="gemini-2.5-flash", remainingFraction=0),
        bucket(modelId="new-model", remainingFraction=1),
    ]})
    quota, error = provider.fetch_quota()
    assert error is None
    assert [window.used_percent for window in quota.windows] == [100, 80, 0]
    assert quota.windows[1].resets_at.day == 7


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("remaining", [0, 0.2])
def test_equally_limited_buckets_use_later_reset_regardless_of_order(provider, reverse, remaining):
    buckets = [
        bucket(remainingFraction=remaining, resetTime="2026-09-06T08:00:00Z"),
        bucket(remainingFraction=remaining, resetTime="2026-09-07T16:00:00+08:00"),
    ]
    provider._session.post.return_value = response({"buckets": buckets[::-1] if reverse else buckets})
    quota, error = provider.fetch_quota()
    assert error is None and len(quota.windows) == 1
    assert quota.windows[0].resets_at == datetime(2026, 9, 7, 8, tzinfo=timezone.utc)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("reset", [None, ""])
def test_equally_limited_bucket_with_unknown_reset_does_not_promise_recovery(provider, reverse, reset):
    buckets = [bucket(remainingFraction=0), bucket(remainingFraction=0, resetTime=reset)]
    provider._session.post.return_value = response({"buckets": buckets[::-1] if reverse else buckets})
    quota, error = provider.fetch_quota()
    assert error is None and quota.windows[0].used_percent == 100
    assert quota.windows[0].resets_at is None


@pytest.mark.parametrize("payload", [{}, {"buckets": None}, {"buckets": []},
                                    {"buckets": [bucket(remainingFraction=None)]},
                                    {"buckets": [{"modelId": "gemini-2.5-pro", "remainingAmount": "0"}]}])
def test_missing_percentages_never_fabricate_zero_or_full_usage(provider, payload):
    provider._session.post.return_value = response(payload)
    quota, error = provider.fetch_quota()
    assert quota is None
    assert error.code == "QUOTA_UNAVAILABLE"


@pytest.mark.parametrize("field,value", [("remainingFraction", True), ("remainingFraction", "0.5"),
    ("remainingFraction", -1), ("remainingFraction", 1.1), ("remainingFraction", float("nan")),
    ("remainingFraction", float("inf")), ("remainingFraction", 10**400), ("modelId", []),
    ("resetTime", True), ("resetTime", "invalid"), ("resetTime", "2026-09-05T12:00:00")])
def test_malformed_bucket_is_not_a_success(provider, field, value):
    provider._session.post.return_value = response({"buckets": [bucket(**{field: value})]})
    assert provider.fetch_quota()[1].code == "INVALID_RESPONSE"


@pytest.mark.parametrize("payload", [[], None, {"buckets": {}}, {"buckets": [None]}, {"error": {"message": "secret"}}])
def test_invalid_shape_fails_safely(provider, payload):
    provider._session.post.return_value = response(payload)
    assert provider.fetch_quota()[1].code == "INVALID_RESPONSE"


@pytest.mark.parametrize("status,code", [(401, "AUTH_EXPIRED"), (403, "PERMISSION_DENIED"),
    (429, "RATE_LIMITED"), (302, "INVALID_RESPONSE"), (503, "SERVER_ERROR")])
def test_http_error_never_echoes_secret(provider, status, code, caplog):
    provider._session.post.return_value = response({}, status)
    error = provider.fetch_quota()[1]
    assert error.code == code
    assert "synthetic-secret" not in repr(error) + caplog.text


def test_migration_signal_is_distinct_from_expired_login(provider):
    provider._session.post.return_value = response({}, 403)
    provider._session.post.return_value.text = '{"error": {"reason": "UNSUPPORTED_CLIENT"}}'
    assert provider.fetch_quota()[1].code == "CONSUMER_TIER_DEPRECATED"


@pytest.mark.parametrize("failure,code", [(requests.Timeout("synthetic-secret"), "NETWORK_TIMEOUT"),
                                         (requests.ConnectionError("synthetic-secret"), "NETWORK_ERROR")])
def test_transport_failure_is_safe(provider, failure, code, caplog):
    provider._session.post.side_effect = failure
    error = provider.fetch_quota()[1]
    assert error.code == code
    assert "synthetic-secret" not in repr(error) + caplog.text


def test_json_failure_is_safe(provider):
    provider._session.post.return_value.json.side_effect = ValueError("synthetic-secret")
    assert provider.fetch_quota()[1].code == "INVALID_RESPONSE"


def test_close_releases_http_session(provider):
    provider.close()
    provider._session.close.assert_called_once_with()
