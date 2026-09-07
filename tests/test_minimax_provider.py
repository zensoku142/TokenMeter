"""MiniMax first-party remaining percentage/count and plan-state regressions."""

from datetime import UTC, datetime

import pytest

from api.providers.minimax import MiniMaxProvider
from tests.test_quota_api_provider import make_provider as _quota_fixture

make_provider = _quota_fixture


def payload(**overrides):
    entry = {
        "model_name": "general",
        "start_time": 1788577200000,
        "end_time": 1788595200000,
        "current_interval_total_count": 0,
        "current_interval_usage_count": 0,
        "current_interval_remaining_percent": 30,
        "current_interval_status": 1,
        "current_weekly_total_count": 0,
        "current_weekly_usage_count": 0,
        "current_weekly_remaining_percent": 75,
        "current_weekly_status": 1,
        "weekly_start_time": 1788393600000,
        "weekly_end_time": 1788998400000,
    }
    entry.update(overrides)
    return {"base_resp": {"status_code": 0}, "model_remains": [entry]}


def test_explicit_remaining_percent_wins_over_zero_or_ambiguous_counts(make_provider):
    provider, session = make_provider(
        MiniMaxProvider, payload(current_interval_total_count=100, current_interval_usage_count=70)
    )
    quota, error = provider.fetch_quota()
    assert error is None
    assert [window.used_percent for window in quota.windows] == [70, 25]
    assert [window.window_minutes for window in quota.windows] == [300, 10080]
    assert quota.windows[0].resets_at == datetime.fromtimestamp(1788595200, UTC)
    assert session.get.call_args.args == ("https://api.minimax.io/v1/token_plan/remains",)
    assert session.get.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-synthetic-test"
    assert provider.fetch_summary() == (None, None)


def test_legacy_count_is_remaining_not_used(make_provider):
    data = payload(current_interval_total_count=100, current_interval_usage_count=30)
    del data["model_remains"][0]["current_interval_remaining_percent"]
    provider, _ = make_provider(MiniMaxProvider, data, base="https://api.minimaxi.com/v1/")
    quota, error = provider.fetch_quota()
    assert error is None and quota.windows[0].used_percent == 70


@pytest.mark.parametrize("kind", ["interval", "weekly"])
def test_null_remaining_percent_uses_valid_legacy_counts(make_provider, kind):
    provider, _ = make_provider(MiniMaxProvider, payload(**{
        f"current_{kind}_remaining_percent": None,
        f"current_{kind}_total_count": 100,
        f"current_{kind}_usage_count": 30,
    }))
    quota, error = provider.fetch_quota()
    assert error is None
    assert quota.windows[0 if kind == "interval" else 1].used_percent == 70


def test_null_boost_preserves_known_weekly_percentage(make_provider):
    provider, _ = make_provider(MiniMaxProvider, payload(weekly_boost_permille=None))
    quota, error = provider.fetch_quota()
    assert error is None and quota.windows[1].used_percent == 25
    assert quota.windows[1].detail == ""


def test_weekly_unlimited_and_not_in_plan_are_distinct(make_provider):
    data = payload(current_weekly_status=3)
    absent = payload(current_interval_status=3, current_weekly_status=3)["model_remains"][0]
    absent["model_name"] = "video"
    data["model_remains"].append(absent)
    provider, _ = make_provider(MiniMaxProvider, data)
    quota, error = provider.fetch_quota()
    assert error is None and len(quota.windows) == 1
    assert [metric.value for metric in quota.metrics] == ["不限额", "不在当前套餐中"]


def test_boost_is_preserved_without_reversing_used_percentage(make_provider):
    provider, _ = make_provider(MiniMaxProvider, payload(weekly_boost_permille=1500))
    quota, error = provider.fetch_quota()
    assert error is None and quota.windows[1].used_percent == 25
    assert "112.5%" in quota.windows[1].detail


@pytest.mark.parametrize(
    "field", ["current_interval_remaining_percent", "current_weekly_remaining_percent"]
)
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
        101,
        "",
        [],
        {},
        "1e999",
    ],
)
def test_invalid_percent_never_falls_back_to_empty_counts(make_provider, field, value):
    provider, _ = make_provider(MiniMaxProvider, payload(**{field: value}))
    result, error = provider.fetch_quota()
    assert result is None and error.code == "INVALID_RESPONSE"


@pytest.mark.parametrize("value", [True, False, "NaN", "Inf", -1, {}, [], "1e100"])
def test_bad_reset_is_rejected(make_provider, value):
    provider, _ = make_provider(MiniMaxProvider, payload(end_time=value))
    result, error = provider.fetch_quota()
    assert result is None and error.code == "INVALID_RESPONSE"


@pytest.mark.parametrize(
    "code,expected",
    [
        (1004, "AUTH_EXPIRED"),
        (1008, "API_ERROR"),
        (True, "INVALID_RESPONSE"),
        (None, "INVALID_RESPONSE"),
        ("0", "INVALID_RESPONSE"),
    ],
)
def test_business_status_is_checked_without_echo(make_provider, code, expected):
    data = payload()
    data["base_resp"] = {"status_code": code, "status_msg": "sk-synthetic-test"}
    provider, _ = make_provider(MiniMaxProvider, data)
    result, error = provider.fetch_quota()
    assert result is None and error.code == expected
    assert "sk-synthetic-test" not in str(error)


def test_no_quota_does_not_report_free_usage(make_provider):
    provider, _ = make_provider(MiniMaxProvider, {"model_remains": []})
    result, error = provider.fetch_quota()
    assert result is None and error.code == "QUOTA_UNAVAILABLE"
