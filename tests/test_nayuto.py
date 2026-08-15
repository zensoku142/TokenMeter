from __future__ import annotations

import copy
import json
import tempfile
import threading
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from api import browser_cookie
from api.providers.base import FetchError, ProviderBalance, ProviderSummary
from api.providers.nayuto import NayutoProvider
from config.defaults import SECRET_KEYS
from config.store import public_values
from data import history
from data.store import TokenData

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "nayuto_usage_page.json"


def usage_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def response(payload: dict, status_code: int = 200) -> Mock:
    result = Mock()
    result.status_code = status_code
    result.json.return_value = payload
    return result


def usage_totals(payloads: list[dict], usage_date: str) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for payload in payloads:
        for day in payload["days"]:
            if day["date"] != usage_date:
                continue
            for item in day["data"]:
                for usage in item["usage"]:
                    key = str(usage["type"])
                    totals[key] = totals.get(key, Decimal("0")) + Decimal(
                        str(usage["amount"])
                    )
    return totals


def test_fixture_has_no_credentials_or_personal_data():
    text = FIXTURE_PATH.read_text(encoding="utf-8").lower()
    assert "bearer " not in text
    assert "authorization" not in text
    assert '"api_key":' not in text
    assert "email" not in text
    assert "ip_address" not in text


def test_real_shape_maps_tokens_actual_cost_and_utc_to_shanghai():
    fixture = usage_fixture()
    payloads, exact, dirty = NayutoProvider._normalize_records(
        fixture["items"], {(8, 2026)}
    )

    assert dirty == 0
    first_day = usage_totals(payloads, "2026-08-15")
    assert first_day == {
        "PROMPT_CACHE_HIT_TOKEN": Decimal("14"),
        "PROMPT_CACHE_MISS_TOKEN": Decimal("9"),
        "RESPONSE_TOKEN": Decimal("18"),
        "cost_cny": Decimal("0.0303"),
    }
    assert sum(first_day[key] for key in (
        "PROMPT_CACHE_HIT_TOKEN",
        "PROMPT_CACHE_MISS_TOKEN",
        "RESPONSE_TOKEN",
    )) == 41
    assert usage_totals(payloads, "2026-08-16")["cost_cny"] == Decimal("0.0303")

    first_minute_tokens = {
        row["token_type"]: row["token_amount"]
        for row in exact.token_rows
        if row["usage_date"] == date(2026, 8, 15) and row["minute"] == 0
    }
    assert first_minute_tokens == {
        "PROMPT_CACHE_HIT_TOKEN": 14,
        "PROMPT_CACHE_MISS_TOKEN": 9,
        "RESPONSE_TOKEN": 18,
    }
    assert next(
        row["cost_cny"]
        for row in exact.cost_rows
        if row["usage_date"] == date(2026, 8, 15) and row["minute"] == 0
    ) == Decimal("0.0303")
    # The fixture's failed row is included because billing semantics are not proven.
    assert first_minute_tokens["PROMPT_CACHE_MISS_TOKEN"] == 2 + 7


def test_balance_and_dashboard_stats_use_verified_fields():
    provider = NayutoProvider({"NAYUTO_AUTH": "synthetic"})
    provider._session.get = Mock(
        side_effect=[
            response({"balance": 9.0961378, "status": "active"}),
            response(
                {
                    "today_cost": 0.9038622,
                    "today_tokens": 722588,
                    "total_cost": 1.2038622,
                    "total_tokens": 822588,
                }
            ),
        ]
    )
    try:
        balance, balance_error = provider.fetch_balance()
        summary, summary_error = provider.fetch_summary()
    finally:
        provider.close()

    assert balance_error is None
    assert balance.amount == Decimal("9.0961378")
    assert balance.currency == "USD"
    assert summary_error is None
    assert summary.today_cost == Decimal("0.9038622")
    assert summary.today_tokens == 722588
    assert summary.total_cost == Decimal("1.2038622")
    assert summary.month_cost is None


def test_request_id_then_real_id_then_stable_fallback_deduplicate():
    records = usage_fixture()["items"]
    with_request = copy.deepcopy(records[0])
    with_id = copy.deepcopy(records[1])
    fallback = copy.deepcopy(records[2])
    fallback.pop("request_id")
    fallback.pop("id")
    payloads, exact, dirty = NayutoProvider._normalize_records(
        [with_request, copy.deepcopy(with_request), with_id, copy.deepcopy(with_id),
         fallback, copy.deepcopy(fallback)],
        {(8, 2026)},
    )

    assert dirty == 0
    totals = usage_totals(payloads, "2026-08-15")
    assert totals["PROMPT_CACHE_MISS_TOKEN"] == 9
    assert len(exact.cost_rows) == 2


def test_dirty_core_fields_are_skipped_without_losing_valid_rows():
    records = usage_fixture()["items"]
    missing_time = copy.deepcopy(records[0])
    missing_time["request_id"] = "dirty-missing-time"
    missing_time.pop("created_at")
    missing_cost = copy.deepcopy(records[0])
    missing_cost["request_id"] = "dirty-missing-cost"
    missing_cost.pop("actual_cost")
    negative_tokens = copy.deepcopy(records[0])
    negative_tokens["request_id"] = "dirty-negative-token"
    negative_tokens["input_tokens"] = -1

    payloads, exact, dirty = NayutoProvider._normalize_records(
        [records[0], missing_time, missing_cost, negative_tokens], {(8, 2026)}
    )

    assert dirty == 3
    assert usage_totals(payloads, "2026-08-15")["cost_cny"] == Decimal("0.0101")
    assert len(exact.cost_rows) == 1


def test_pagination_cross_page_dedup_and_total_termination():
    records = usage_fixture()["items"]
    page_one = {"items": records[:2], "total": 3, "page": 1, "page_size": 2}
    page_two = {"items": [records[1], records[2]], "total": 3, "page": 2, "page_size": 2}
    provider = NayutoProvider({"NAYUTO_AUTH": "synthetic", "NAYUTO_BASE": "https://nayutoai.xyz"})
    provider._session.get = Mock(side_effect=[response(page_one), response(page_two)])
    try:
        with patch("api.providers.nayuto._PAGE_SIZE", 2):
            payloads, errors = provider.fetch_payloads([(8, 2026)])
    finally:
        provider.close()

    assert errors == []
    assert provider._session.get.call_count == 2
    assert usage_totals(payloads, "2026-08-15")["cost_cny"] == Decimal("0.0303")
    assert usage_totals(payloads, "2026-08-16")["cost_cny"] == Decimal("0.0303")


def test_empty_page_terminates_and_max_page_guard_fails_atomically():
    provider = NayutoProvider({"NAYUTO_AUTH": "synthetic", "NAYUTO_BASE": "https://nayutoai.xyz"})
    provider._session.get = Mock(return_value=response({"items": [], "total": 0}))
    try:
        payloads, errors = provider.fetch_payloads([(8, 2026)])
        assert errors == []
        assert payloads[0]["days"] == []
        assert provider.exact_minute_usage() is not None

        row = usage_fixture()["items"][0]
        provider._session.get = Mock(
            side_effect=[
                response({"items": [{**row, "id": 1, "request_id": "one"}], "total": 99}),
                response({"items": [{**row, "id": 2, "request_id": "two"}], "total": 99}),
            ]
        )
        with patch("api.providers.nayuto._MAX_PAGES", 2), patch(
            "api.providers.nayuto._PAGE_SIZE", 1
        ):
            payloads, errors = provider.fetch_payloads([(8, 2026)])
    finally:
        provider.close()

    assert payloads == []
    assert errors[0].code == "INVALID_RESPONSE"
    assert provider.exact_minute_usage() is None


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [(401, "AUTH_EXPIRED"), (403, "AUTH_EXPIRED"), (429, "RATE_LIMITED"), (503, "SERVER_ERROR")],
)
def test_http_failures_have_stable_non_secret_error_codes(status_code, expected_code):
    provider = NayutoProvider({"NAYUTO_AUTH": "synthetic", "NAYUTO_BASE": "https://nayutoai.xyz"})
    provider._session.get = Mock(return_value=response({}, status_code))
    try:
        balance, error = provider.fetch_balance()
    finally:
        provider.close()
    assert balance is None
    assert error.code == expected_code
    assert "synthetic" not in error.message


@pytest.mark.parametrize(
    ("raised", "expected_code"),
    [
        (requests.Timeout(), "NETWORK_TIMEOUT"),
        (requests.ConnectionError(), "NETWORK_ERROR"),
    ],
)
def test_timeout_and_network_failure_keep_fixed_messages(raised, expected_code):
    provider = NayutoProvider({"NAYUTO_AUTH": "synthetic"})
    provider._session.get = Mock(side_effect=raised)
    try:
        balance, error = provider.fetch_balance()
    finally:
        provider.close()
    assert balance is None
    assert error.code == expected_code


def test_browser_capture_keeps_only_validated_bearer():
    session = Mock()
    session.capture_request_headers.return_value = {
        "Accept": "*/*",
        "authorization": "Bearer synthetic-captured",
        "Cookie": "must-not-be-saved",
    }
    session.fetch_json.return_value = browser_cookie.BrowserFetchResult(
        200, {"balance": 1, "status": "active"}, "ignored-cookie"
    )
    stop_event = threading.Event()
    browser_opened = threading.Event()
    values: list[str] = []
    errors: list[BaseException] = []

    def open_session(*_args, **_kwargs):
        browser_opened.set()
        return session

    def collect() -> None:
        try:
            values.append(NayutoProvider.acquire_credentials_via_chrome(stop_event))
        except BaseException as exc:  # pragma: no cover - surfaced by assertion
            errors.append(exc)

    with patch(
        "api.providers.nayuto.browser_cookie.open_chrome_session",
        side_effect=open_session,
    ):
        worker = threading.Thread(target=collect)
        worker.start()
        assert browser_opened.wait(timeout=1)
        session.capture_request_headers.assert_not_called()
        stop_event.set()
        worker.join(timeout=1)

    assert not worker.is_alive()
    assert errors == []
    value = values[0]

    assert value == "Bearer synthetic-captured"
    session.capture_request_headers.assert_called_once_with(
        url_prefix="https://nayutoai.xyz/portal/",
        timeout_seconds=5.0,
    )
    session.fetch_json.assert_called_once_with(
        url="https://nayutoai.xyz/portal/auth/me",
        headers={"Authorization": "Bearer synthetic-captured"},
        allowed_domains=("nayutoai.xyz",),
    )
    session.close.assert_called_once()
    assert NayutoProvider.acquired_credential_values(value) == {
        "AUTH": "Bearer synthetic-captured"
    }


def test_browser_capture_rejects_inactive_account():
    session = Mock()
    session.capture_request_headers.return_value = {
        "Authorization": "Bearer synthetic-captured"
    }
    session.fetch_json.return_value = browser_cookie.BrowserFetchResult(
        200, {"balance": 1, "status": "disabled"}, ""
    )
    stop_event = threading.Event()
    stop_event.set()
    with patch(
        "api.providers.nayuto.browser_cookie.open_chrome_session",
        return_value=session,
    ), pytest.raises(RuntimeError, match="NAYUTO_ACCOUNT_INACTIVE"):
        NayutoProvider.acquire_credentials_via_chrome(stop_event)
    session.close.assert_called_once()


def test_nayuto_auth_is_excluded_from_public_config():
    assert "NAYUTO_AUTH" in SECRET_KEYS
    exported = public_values({"NAYUTO_AUTH": "Bearer synthetic", "NAYUTO_BASE": "https://nayutoai.xyz"})
    assert "NAYUTO_AUTH" not in exported
    assert exported["NAYUTO_BASE"] == "https://nayutoai.xyz"


def test_exact_minute_replace_is_decimal_and_idempotent():
    with tempfile.TemporaryDirectory() as temp_dir, patch.object(
        history, "DB_PATH", Path(temp_dir) / "usage.db"
    ):
        usage_day = date(2026, 8, 15)
        token_rows = [
            {
                "usage_date": usage_day,
                "minute": 601,
                "token_type": "PROMPT_CACHE_HIT_TOKEN",
                "token_amount": 3,
            },
            {
                "usage_date": usage_day,
                "minute": 601,
                "token_type": "PROMPT_CACHE_HIT_TOKEN",
                "token_amount": 4,
            },
        ]
        cost_rows = [
            {"usage_date": usage_day, "minute": 601, "cost_cny": Decimal("0.1")},
            {"usage_date": usage_day, "minute": 601, "cost_cny": Decimal("0.2")},
        ]
        for _ in range(2):
            assert history.replace_exact_minute_usage(
                "nayuto", {usage_day}, token_rows, cost_rows, usage_day, 3
            ) == "recorded"
        assert history.minute_usage_for_day("nayuto", usage_day) == [
            {
                "minute": 601,
                "token_type": "PROMPT_CACHE_HIT_TOKEN",
                "token_amount": 7,
            }
        ]
        assert history.minute_cost_usage_for_day("nayuto", usage_day) == [
            {"minute": 601, "cost_cny": Decimal("0.3")}
        ]

        history.replace_exact_minute_usage("nayuto", {usage_day}, [], [], usage_day, 3)
        assert history.minute_usage_for_day("nayuto", usage_day) == []
        assert history.minute_cost_usage_for_day("nayuto", usage_day) == []


def test_store_uses_exact_minutes_and_preserves_them_on_fetch_failures():
    fixture = usage_fixture()
    payloads, exact, _dirty = NayutoProvider._normalize_records(
        fixture["items"], {(8, 2026)}
    )
    for payload in payloads:
        payload["_complete"] = False
    provider = NayutoProvider({"NAYUTO_AUTH": "synthetic", "MINUTE_USAGE_RETENTION_DAYS": 3})
    provider.fetch_balance = Mock(
        return_value=(ProviderBalance("USD", Decimal("9.0961378")), None)
    )
    provider.fetch_summary = Mock(return_value=(ProviderSummary(), None))
    provider.fetch_payloads = Mock(return_value=(payloads, []))
    provider.exact_minute_usage = Mock(return_value=exact)

    previous_snapshots = TokenData._provider_snapshots
    previous_last = TokenData._last_snapshot
    TokenData._provider_snapshots = {}
    TokenData._last_snapshot = None
    try:
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            history, "DB_PATH", Path(temp_dir) / "usage.db"
        ):
            stale_day = date(2026, 8, 14)
            history.replace_exact_minute_usage(
                "nayuto",
                {stale_day},
                [
                    {
                        "usage_date": stale_day,
                        "minute": 1,
                        "token_type": "RESPONSE_TOKEN",
                        "token_amount": 99,
                    }
                ],
                [],
                date(2026, 8, 15),
                3,
            )
            first = TokenData._fetch_with_provider(provider, date(2026, 8, 15))
            assert first.status == "ok"
            assert first.currency == "USD"
            assert first.today_tokens == 41
            assert first.today_cost_cny == pytest.approx(0.0303)
            assert first.minute_usage_source == "provider"
            assert sum(row["token_amount"] for row in first.minute_usage) == 41
            assert first.minute_cost_usage == [
                {"minute": 0, "cost_cny": Decimal("0.0303")}
            ]
            assert history.minute_usage_for_day("nayuto", stale_day) == []

            second = TokenData._fetch_with_provider(provider, date(2026, 8, 15))
            assert second.minute_usage == first.minute_usage
            assert second.minute_cost_usage == first.minute_cost_usage

            for code in (
                "AUTH_EXPIRED",
                "RATE_LIMITED",
                "SERVER_ERROR",
                "NETWORK_TIMEOUT",
                "NETWORK_ERROR",
            ):
                fetch_error = FetchError(code, "用量明细", "NayutoAI 数据暂不可用")
                provider.fetch_balance = Mock(return_value=(None, fetch_error))
                provider.fetch_summary = Mock(return_value=(None, fetch_error))
                provider.fetch_payloads = Mock(return_value=([], [fetch_error]))
                provider.exact_minute_usage = Mock(return_value=None)
                failed = TokenData._fetch_with_provider(
                    provider, date(2026, 8, 15)
                )
                assert failed.status == "error"
                assert failed.is_stale is True
                assert failed.today_tokens == 41
                assert failed.minute_usage == first.minute_usage
                assert failed.minute_cost_usage == first.minute_cost_usage
    finally:
        provider.close()
        TokenData._provider_snapshots = previous_snapshots
        TokenData._last_snapshot = previous_last
