import os
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

os.environ["APPDATA"] = str(Path.cwd() / ".test-appdata")

from api.providers.base import FetchError, ModelUsage, ProviderBalance, ProviderSummary
from data.store import TokenData, months_for_activity, months_for_week, top_model_stats


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

    def is_configured(self):
        return self.configured

    def fetch_balance(self):
        return ProviderBalance("CNY", Decimal("12.3"), 9), None

    def fetch_summary(self):
        return ProviderSummary(Decimal("1.2"), 100), None

    def fetch_payloads(self, _months):
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

    def fetch_with(self, provider, today=date(2026, 7, 3)):
        with patch("data.store.active_providers", return_value=iter([provider])):
            return TokenData.fetch(today)

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


if __name__ == "__main__":
    unittest.main()
