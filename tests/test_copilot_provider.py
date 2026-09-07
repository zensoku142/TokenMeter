"""Synthetic Copilot entitlement responses; no GitHub account or network is used."""

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
import requests

from api.providers.copilot import CopilotProvider


def quota_payload(**snapshot):
    return {"quota_snapshots": {"premium_interactions": {
        "entitlement": "300", "percent_remaining": 75, **snapshot,
    }}}


@pytest.fixture
def provider(monkeypatch):
    session = Mock()
    session.get.return_value.status_code = 200
    session.get.return_value.json.return_value = quota_payload()
    monkeypatch.setattr("api.providers.copilot.build_session", lambda: session)
    result = CopilotProvider({"COPILOT_TOKEN": "synthetic-secret", "COPILOT_BASE": "https://example.invalid"})
    yield result
    result.close()


def test_new_ai_credits_are_percentages_and_ignore_false_has_quota(provider):
    payload = quota_payload(entitlement="2000.5", percent_remaining="62.5", has_quota=False)
    payload.update(token_based_billing=True, copilot_plan="pro")
    payload["quota_snapshots"]["chat"] = {"unlimited": True}
    payload["quota_snapshots"]["completions"] = {"unlimited": True}
    provider._session.get.return_value.json.return_value = payload
    quota, error = provider.fetch_quota()
    assert error is None
    assert quota.plan == "pro"
    assert len(quota.windows) == 1
    assert quota.windows[0].title == "AI credits"
    assert quota.windows[0].used_percent == 37.5
    assert quota.windows[0].detail == "AI credits"
    assert [metric.value for metric in quota.metrics] == ["不限量", "不限量"]


def test_legacy_request_snapshot_and_fixed_https_transport(provider):
    quota, error = provider.fetch_quota()
    assert error is None
    assert quota.windows[0].title == "高级请求额度"
    assert quota.windows[0].used_percent == 25
    assert quota.windows[0].resets_at is None
    args, kwargs = provider._session.get.call_args
    assert args == ("https://api.github.com/copilot_internal/user",)
    assert kwargs["headers"]["Authorization"] == "Bearer synthetic-secret"
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == (3, 10)


def test_legacy_free_sku_without_quota_snapshots(provider):
    provider._session.get.return_value.json.return_value = {
        "monthly_quotas": {"chat": 50, "completions": 2000},
        "limited_user_quotas": {"chat": 30, "completions": 500},
        "limited_user_reset_date": "2026-10-01",
    }
    quota, error = provider.fetch_quota()
    assert error is None
    assert {window.id: window.used_percent for window in quota.windows} == {
        "copilot-chat": 40, "copilot-completions": 75,
    }
    assert all(window.resets_at == datetime(2026, 10, 1, tzinfo=timezone.utc) for window in quota.windows)


def test_modern_snapshot_overrides_only_corresponding_legacy_category(provider):
    provider._session.get.return_value.json.return_value = {
        "monthly_quotas": {"chat": 50, "completions": 2000},
        "limited_user_quotas": {"chat": 30, "completions": 500},
        "quota_snapshots": {"chat": {"entitlement": 100, "percent_remaining": 90}},
    }
    quota, error = provider.fetch_quota()
    assert error is None
    assert {window.id: window.used_percent for window in quota.windows} == {
        "copilot-chat": 10, "copilot-completions": 75,
    }


def test_unallocated_modern_category_preserves_valid_legacy_free_quota(provider):
    provider._session.get.return_value.json.return_value = {
        "monthly_quotas": {"chat": 50}, "limited_user_quotas": {"chat": 30},
        "quota_snapshots": {"chat": {"entitlement": 0, "percent_remaining": 0}},
    }
    quota, error = provider.fetch_quota()
    assert error is None
    assert quota.windows[0].used_percent == 40


@pytest.mark.parametrize("field,value", [
    ("monthly_quotas", {"chat": "NaN"}), ("monthly_quotas", {"chat": -1}),
    ("monthly_quotas", {"chat": True}), ("monthly_quotas", []),
    ("limited_user_quotas", {"chat": "Infinity"}),
    ("limited_user_quotas", {"chat": False}), ("limited_user_quotas", "invalid"),
])
def test_malformed_legacy_counts_do_not_escape_parser(provider, field, value):
    payload = {"monthly_quotas": {"chat": 50}, "limited_user_quotas": {"chat": 30}}
    payload[field] = value
    provider._session.get.return_value.json.return_value = payload
    quota, error = provider.fetch_quota()
    assert quota is None
    assert error.code == "INVALID_RESPONSE"


def test_zero_legacy_allocation_is_unavailable(provider):
    provider._session.get.return_value.json.return_value = {
        "monthly_quotas": {"chat": 0}, "limited_user_quotas": {"chat": 0},
    }
    quota, error = provider.fetch_quota()
    assert quota is None
    assert error.code == "QUOTA_UNAVAILABLE"


@pytest.mark.parametrize("remaining,used", [(0, 100), (100, 0), (-50, 100), (150, 0), ("1e1000000", 0)])
def test_percentage_clamps_before_subtraction(provider, remaining, used):
    provider._session.get.return_value.json.return_value = quota_payload(percent_remaining=remaining)
    quota, error = provider.fetch_quota()
    assert error is None
    assert quota.windows[0].used_percent == used


def test_zero_entitlement_is_unavailable_but_unlimited_is_not_zero_percent(provider):
    provider._session.get.return_value.json.return_value = quota_payload(entitlement=0)
    quota, error = provider.fetch_quota()
    assert quota is None
    assert error.code == "QUOTA_UNAVAILABLE"
    provider._session.get.return_value.json.return_value = quota_payload(entitlement=0, unlimited=True)
    quota, error = provider.fetch_quota()
    assert error is None
    assert quota.windows == ()
    assert quota.metrics[0].value == "不限量"


@pytest.mark.parametrize("payload", [{}, {"quota_snapshots": {}}, {"quota_snapshots": {"chat": None}}])
def test_missing_windows_do_not_fabricate_zero_usage(provider, payload):
    provider._session.get.return_value.json.return_value = payload
    quota, error = provider.fetch_quota()
    assert quota is None
    assert error.code == "QUOTA_UNAVAILABLE"


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity", float("nan"), float("inf"), True, False, {}, []])
@pytest.mark.parametrize("field", ["percent_remaining", "entitlement"])
def test_invalid_numeric_values_fail_safely(provider, field, value):
    provider._session.get.return_value.json.return_value = quota_payload(**{field: value})
    quota, error = provider.fetch_quota()
    assert quota is None
    assert error.code == "INVALID_RESPONSE"


@pytest.mark.parametrize("payload", [
    None, [], "secret", {"quota_snapshots": []}, {"quota_snapshots": {"chat": []}},
    quota_payload(unlimited="true"), quota_payload(entitlement=-1),
    {**quota_payload(), "token_based_billing": 1}, {**quota_payload(), "copilot_plan": []},
])
def test_invalid_response_shapes_fail_safely(provider, payload):
    provider._session.get.return_value.json.return_value = payload
    quota, error = provider.fetch_quota()
    assert quota is None
    assert error.code == "INVALID_RESPONSE"


@pytest.mark.parametrize("reset,expected", [
    ("2026-10-01T03:04:05Z", datetime(2026, 10, 1, 3, 4, 5, tzinfo=timezone.utc)),
    ("2026-10-01", datetime(2026, 10, 1, tzinfo=timezone.utc)),
    ("2026-10-01T11:04:05+08:00", datetime(2026, 10, 1, 3, 4, 5, tzinfo=timezone.utc)),
])
def test_snapshot_reset_has_priority_over_global_dates(provider, reset, expected):
    payload = quota_payload(quota_reset_at=reset)
    payload.update(quota_reset_date_utc="2026-11-01T00:00:00Z", quota_reset_date="2026-12-01")
    provider._session.get.return_value.json.return_value = payload
    quota, error = provider.fetch_quota()
    assert error is None
    assert quota.windows[0].resets_at == expected


def test_global_reset_dates_follow_official_priority(provider):
    dates = {
        "quota_reset_date_utc": "2026-10-01T03:04:05Z",
        "quota_reset_date": "2026-11-01", "limited_user_reset_date": "2026-12-01",
    }
    for field in tuple(dates):
        provider._session.get.return_value.json.return_value = {**quota_payload(), **dates}
        quota, error = provider.fetch_quota()
        assert error is None
        expected = datetime.fromisoformat(dates[field].replace("Z", "+00:00"))
        if expected.tzinfo is None:
            expected = expected.replace(tzinfo=timezone.utc)
        assert quota.windows[0].resets_at == expected
        dates.pop(field)


@pytest.mark.parametrize("reset", [True, False, 123, "invalid", "2026-13-99"])
def test_invalid_reset_does_not_escape_parser(provider, reset):
    provider._session.get.return_value.json.return_value = quota_payload(quota_reset_at=reset)
    quota, error = provider.fetch_quota()
    assert quota is None
    assert error.code == "INVALID_RESPONSE"


@pytest.mark.parametrize("status,code", [(401, "AUTH_EXPIRED"), (403, "PERMISSION_DENIED"),
    (429, "RATE_LIMITED"), (302, "INVALID_RESPONSE"), (404, "INVALID_RESPONSE"), (503, "SERVER_ERROR")])
def test_http_errors_never_echo_response_credentials(provider, status, code, caplog):
    response = provider._session.get.return_value
    response.status_code = status
    response.text = "synthetic-secret"
    quota, error = provider.fetch_quota()
    assert quota is None
    assert error.code == code
    assert "synthetic-secret" not in repr(error) + caplog.text
    response.json.assert_not_called()


@pytest.mark.parametrize("failure,code", [
    (requests.Timeout("synthetic-secret"), "NETWORK_TIMEOUT"),
    (requests.ConnectionError("synthetic-secret"), "NETWORK_ERROR"),
    (requests.exceptions.InvalidURL("synthetic-secret"), "NETWORK_ERROR"),
])
def test_transport_errors_never_echo_credentials(provider, failure, code, caplog):
    provider._session.get.side_effect = failure
    quota, error = provider.fetch_quota()
    assert quota is None
    assert error.code == code
    assert "synthetic-secret" not in repr(error) + caplog.text


def test_json_failure_never_echoes_credentials(provider, caplog):
    provider._session.get.return_value.json.side_effect = ValueError("synthetic-secret")
    quota, error = provider.fetch_quota()
    assert quota is None
    assert error.code == "INVALID_RESPONSE"
    assert "synthetic-secret" not in repr(error) + caplog.text


def test_empty_token_skips_request_and_identity_is_account_scoped(provider):
    first = provider.snapshot_identity()
    assert first and "synthetic-secret" not in first
    provider._config = {"COPILOT_TOKEN": "another-synthetic-secret"}
    assert first != provider.snapshot_identity()
    provider._config = {"COPILOT_TOKEN": "  "}
    assert provider.snapshot_identity() == ""
    assert not provider.is_configured()
    quota, error = provider.fetch_quota()
    assert quota is None
    assert error.code == "NOT_CONFIGURED"
    provider._session.get.assert_not_called()


def test_close_releases_owned_http_session(provider):
    provider.close()
    provider._session.close.assert_called_once_with()
