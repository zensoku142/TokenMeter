import os
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

os.environ["APPDATA"] = str(Path.cwd() / ".test-appdata")

from api.providers.base import FetchError, ModelUsage, ProviderBalance, ProviderSummary
from data.store import (
    TokenData,
    months_for_activity,
    months_for_week,
    provider_usage_day,
    provider_observed_at,
    token_breakdown_for_day,
    top_model_stats,
)


def payload(day, tokens, cost="0", model="deepseek-test"):
    return {
        "_month": (int(day[5:7]), int(day[:4])),
        "days": [{
            "date": day,
            "data": [{
                "model": model,
                "usage": [
                    {"type": "RESPONSE_TOKEN", "amount": tokens},
                    {"type": "cost_cny", "amount": cost},
                ],
            }],
        }],
        "total": [],
    }


class FakeProvider:
    id = "deepseek"
    name = "DeepSeek"
    supports_daily_usage = True
    supports_cost = True

    def __init__(self, *, payloads=None, errors=None, configured=True):
        self.payloads = payloads or []
        self.errors = errors or []
        self.configured = configured
        self.requested_months = []

    def is_configured(self):
        return self.configured

    def fetch_balance(self):
        return ProviderBalance("CNY", Decimal("12.3"), 9), None

    def fetch_summary(self):
        return ProviderSummary(Decimal("1.2"), 100), None

    def fetch_payloads(self, months):
        self.requested_months.append(list(months))
        return self.payloads, self.errors


class StoreTests(unittest.TestCase):
    def setUp(self):
        TokenData._last_snapshot = None
        TokenData._provider_snapshots = {}
        self.patches = [
            patch("data.store.history.unsynced_months", return_value=[]),
            patch("data.store.history.save_usage"),
            patch("data.store.history.total_cost", return_value=Decimal("1.25")),
            patch("data.store.history.recent_daily", return_value=[]),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def fetch_with(self, provider, today=date(2026, 7, 3), lightweight=False):
        with patch("data.store.active_providers", return_value=iter([provider])):
            return TokenData.fetch(today, lightweight=lightweight)

    def test_month_ranges(self):
        self.assertEqual(months_for_week(date(2026, 7, 3)), [(6, 2026), (7, 2026)])
        months = months_for_activity(date(2026, 7, 4))
        self.assertEqual(months[0], (7, 2026))
        self.assertEqual(months[-1], (7, 2025))

    def test_dynamic_models_merge_remainder(self):
        stats = {
            "a": ModelUsage("a", 30, Decimal(".3")),
            "b": ModelUsage("b", 20, Decimal(".2")),
            "c": ModelUsage("c", 10, Decimal(".1")),
            "d": ModelUsage("d", 5, Decimal(".05")),
        }
        models = top_model_stats(stats)
        self.assertEqual([model.model for model in models], ["a", "b", "其他"])
        self.assertEqual(models[-1].tokens, 15)
        self.assertEqual(models[-1].cost_cny, Decimal(".15"))

    def test_cross_month_week_and_today_cost(self):
        provider = FakeProvider(payloads=[
            payload("2026-06-30", 10, ".1"),
            payload("2026-07-01", 20, ".2"),
            payload("2026-07-03", 30, ".23"),
        ])
        data = self.fetch_with(provider)
        self.assertEqual(data.today_tokens, 30)
        self.assertEqual(data.weekly_tokens, 60)
        self.assertAlmostEqual(data.today_cost_cny, .23)
        self.assertAlmostEqual(data.weekly_cost_cny, .53)
        self.assertEqual(data.total_cost_cny, 1.25)
        self.assertEqual(data.status, "ok")

    def test_lightweight_mimo_fetch_only_requests_current_month(self):
        provider = FakeProvider(payloads=[payload("2026-07-03", 30, ".23")])
        provider.id = "mimo"
        provider.name = "小米 MiMo"

        with patch(
            "data.store.history.unsynced_months", return_value=[(6, 2026)]
        ) as unsynced_months:
            data = self.fetch_with(provider, lightweight=True)

        self.assertEqual(provider.requested_months, [[(7, 2026)]])
        unsynced_months.assert_not_called()
        self.assertEqual(data.today_tokens, 30)
        self.assertAlmostEqual(data.today_cost_cny, .23)

    def test_partial_payload_failure_keeps_available_values(self):
        provider = FakeProvider(
            payloads=[payload("2026-07-03", 7, "0")],
            errors=[FetchError("AUTH_EXPIRED", "费用明细", "凭证失效")],
        )
        data = self.fetch_with(provider)
        self.assertEqual(data.today_tokens, 7)
        self.assertEqual(data.status, "partial")
        self.assertTrue(data.is_stale)

    def test_total_failure_retains_same_provider_cache(self):
        first = self.fetch_with(FakeProvider(payloads=[payload("2026-07-03", 7, ".2")]))

        class FailedProvider(FakeProvider):
            def fetch_balance(self):
                return None, FetchError("NETWORK_TIMEOUT", "余额", "连接超时")

            def fetch_summary(self):
                return None, FetchError("NETWORK_TIMEOUT", "摘要", "连接超时")

            def fetch_payloads(self, _months):
                return [], [FetchError("NETWORK_TIMEOUT", "明细", "连接超时")]

        second = self.fetch_with(FailedProvider())
        self.assertEqual(second.balance_cny, first.balance_cny)
        self.assertEqual(second.today_tokens, 7)
        self.assertEqual(second.status, "error")
        self.assertTrue(second.is_stale)

    def test_switching_provider_never_reuses_previous_provider_data(self):
        deepseek = self.fetch_with(FakeProvider(payloads=[payload("2026-07-03", 7, ".2")]))
        self.assertEqual(deepseek.today_tokens, 7)
        mimo = FakeProvider(configured=False)
        mimo.id = "mimo"
        mimo.name = "小米 MiMo"
        result = self.fetch_with(mimo)
        self.assertIsNone(result.today_tokens)
        self.assertEqual(result.daily_usage, [])
        self.assertEqual(result.status, "not_configured")

    def test_bad_usage_row_does_not_drop_batch(self):
        bad = payload("2026-07-03", "bad")
        bad["days"][0]["data"].append({
            "model": "good",
            "usage": [{"type": "RESPONSE_TOKEN", "amount": 4}],
        })
        data = self.fetch_with(FakeProvider(payloads=[bad]))
        self.assertEqual(data.today_tokens, 4)

    def test_today_token_breakdown_keeps_all_three_real_token_types(self):
        raw = payload("2026-07-03", 0)
        raw["days"][0]["data"][0]["usage"] = [
            {"type": "PROMPT_CACHE_HIT_TOKEN", "amount": 8},
            {"type": "PROMPT_CACHE_MISS_TOKEN", "amount": 3},
            {"type": "RESPONSE_TOKEN", "amount": 2},
        ]
        self.assertEqual(token_breakdown_for_day([raw], date(2026, 7, 3)), {
            "PROMPT_CACHE_HIT_TOKEN": 8,
            "PROMPT_CACHE_MISS_TOKEN": 3,
            "RESPONSE_TOKEN": 2,
        })
        self.assertIsNone(token_breakdown_for_day([raw], date(2026, 7, 4)))

    def test_provider_usage_day_uses_mimo_shanghai_and_deepseek_local_time(self):
        observed = datetime(2026, 7, 13, 16, 30, tzinfo=timezone.utc)
        self.assertEqual(provider_usage_day("mimo", observed), date(2026, 7, 14))
        self.assertEqual(provider_observed_at("mimo", observed).hour, 0)
        self.assertEqual(provider_usage_day("deepseek", observed), observed.astimezone().date())

    def test_mimo_usage_day_falls_back_to_utc_plus_eight_without_tzdata(self):
        observed = datetime(2026, 7, 13, 16, 30, tzinfo=timezone.utc)
        with patch("data.store.ZoneInfo", side_effect=ZoneInfoNotFoundError("missing")):
            converted = provider_observed_at("mimo", observed)
        self.assertEqual(converted.date(), date(2026, 7, 14))
        self.assertEqual(converted.hour, 0)
        self.assertEqual(converted.utcoffset(), timedelta(hours=8))

    def test_minute_cache_failure_does_not_block_daily_usage_refresh(self):
        provider = FakeProvider(payloads=[payload("2026-07-03", 7, ".2")])
        provider.supports_estimated_minute_usage = True
        with (
            patch("data.store.history.clear_expired_minute_usage", side_effect=OSError("locked")),
            patch("data.store.history.minute_usage_for_day", return_value=[]),
            patch("data.store.history.save_estimated_minute_usage", return_value="baseline"),
        ):
            data = self.fetch_with(provider)
        self.assertEqual(data.today_tokens, 7)
        self.assertEqual(data.minute_usage_status, "baseline")
        self.assertEqual(data.status, "partial")
        self.assertIn("LOCAL_STORAGE", {error.code for error in data.errors})

    def test_fetch_exposes_retained_minute_dates_and_history(self):
        provider = FakeProvider(payloads=[payload("2026-07-03", 7, ".2")])
        provider.supports_estimated_minute_usage = True
        historical_rows = [
            {"minute": 10, "token_type": "RESPONSE_TOKEN", "token_amount": 5}
        ]

        with (
            patch("data.store.history.clear_expired_minute_usage"),
            patch(
                "data.store.history.minute_usage_for_day",
                side_effect=lambda _provider, usage_day: (
                    historical_rows if usage_day == date(2026, 7, 2) else []
                ),
            ),
            patch(
                "data.store.history.minute_usage_dates",
                return_value=["2026-07-02", "2026-07-03"],
            ),
            patch("data.store.history.save_estimated_minute_usage", return_value="baseline"),
        ):
            data = self.fetch_with(provider)

        self.assertEqual(data.minute_usage_days, ["2026-07-02", "2026-07-03"])
        self.assertEqual(data.minute_usage_history["2026-07-02"], historical_rows)
        self.assertEqual(data.minute_usage_history["2026-07-03"], [])


if __name__ == "__main__":
    unittest.main()
