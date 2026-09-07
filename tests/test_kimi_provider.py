"""Kimi CLI response semantics checked without credentials or inference calls."""

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from api.providers.kimi import KimiProvider
from tests.test_quota_api_provider import make_provider as _quota_fixture

make_provider = _quota_fixture


def payload():
    return {
        "usage": {
            "limit": "100",
            "used": "21",
            "remaining": "79",
            "resetTime": "2026-09-10T15:23:13.716839300Z",
        },
        "limits": [
            {
                "window": {"duration": 300, "timeUnit": "TIME_UNIT_MINUTE"},
                "detail": {
                    "limit": "100",
                    "used": "70",
                    "remaining": "30",
                    "resetTime": "2026-09-05T13:33:02Z",
                },
            }
        ],
    }


def test_weekly_and_five_hour_quota_and_api_key_header(make_provider):
    provider, session = make_provider(KimiProvider, payload())
    quota, error = provider.fetch_quota()
    assert error is None
    assert [window.used_percent for window in quota.windows] == [21, 70]
    assert [window.window_minutes for window in quota.windows] == [10080, 300]
    assert quota.windows[0].resets_at == datetime(2026, 9, 10, 15, 23, 13, 716839, UTC)
    assert session.get.call_args.args == ("https://api.kimi.com/coding/v1/usages",)
    assert session.get.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-synthetic-test"
    assert provider.fetch_balance() == (None, None)
    assert provider.fetch_summary() == (None, None)


def test_weekly_only_does_not_invent_five_hour_data(make_provider):
    data = payload()
    del data["limits"]
    del data["usage"]["used"]
    provider, _ = make_provider(KimiProvider, data)
    quota, error = provider.fetch_quota()
    assert error is None
    assert len(quota.windows) == 1 and quota.windows[0].used_percent == 21


@pytest.mark.parametrize(
    "duration,unit,expected",
    [(18000, "TIME_UNIT_SECOND", 300), (5, "TIME_UNIT_HOUR", 300), (1, "TIME_UNIT_DAY", 1440)],
)
def test_time_unit_is_read_from_window_not_array_order(make_provider, duration, unit, expected):
    data = payload()
    data["limits"][0]["window"] = {"duration": duration, "timeUnit": unit}
    provider, _ = make_provider(KimiProvider, data)
    quota, error = provider.fetch_quota()
    assert error is None and quota.windows[1].window_minutes == expected


@pytest.mark.parametrize("field", ["limit", "used", "remaining"])
@pytest.mark.parametrize(
    "value",
    [None, True, False, "NaN", "Inf", "-Inf", float("nan"), float("inf"), -1, "", [], {}, "1e999"],
)
def test_invalid_quota_numbers_fail_closed(make_provider, field, value):
    data = payload()
    data["usage"][field] = value
    provider, _ = make_provider(KimiProvider, data)
    result, error = provider.fetch_quota()
    assert result is None and error.code == "INVALID_RESPONSE"


@pytest.mark.parametrize("bad", [None, True, "NaN", "Inf", -1, 0, [], {}])
def test_bad_window_duration_is_rejected(make_provider, bad):
    data = payload()
    data["limits"][0]["window"]["duration"] = bad
    provider, _ = make_provider(KimiProvider, data)
    result, error = provider.fetch_quota()
    assert result is None and error.code == "INVALID_RESPONSE"


@pytest.mark.parametrize("reset", [True, 1788000000, "NaN", "bad", "2026-09-05T13:33:02"])
def test_bad_reset_does_not_use_local_timezone(make_provider, reset):
    data = payload()
    data["usage"]["resetTime"] = reset
    provider, _ = make_provider(KimiProvider, data)
    result, error = provider.fetch_quota()
    assert result is None and error.code == "INVALID_RESPONSE"


def test_absent_reset_is_unknown_and_zero_limit_is_not_normal_usage(make_provider):
    data = payload()
    data["usage"]["resetTime"] = None
    provider, _ = make_provider(KimiProvider, data)
    quota, error = provider.fetch_quota()
    assert error is None and quota.windows[0].resets_at is None
    data = deepcopy(data)
    data["usage"]["limit"] = 0
    provider, _ = make_provider(KimiProvider, data)
    quota, error = provider.fetch_quota()
    assert quota is None and error.code == "INVALID_RESPONSE"
