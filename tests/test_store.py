import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from types import MappingProxyType
from zoneinfo import ZoneInfoNotFoundError

os.environ["APPDATA"] = str(Path.cwd() / ".test-appdata")

from api.providers.base import (
    FetchError,
    ModelUsage,
    ProviderBalance,
    ProviderQuota,
    ProviderSummary,
    QuotaMetric,
    QuotaWindow,
)
from data import history
from data.store import (
    PerProviderData,
    TokenData,
    cost_breakdown_for_day,
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

    def __init__(self, *, payloads=None, errors=None, configured=True, config=None):
        self.payloads = payloads or []
        self.errors = errors or []
        self.configured = configured
        self.config = config or {}
        self.requested_months = []

    def config_get(self, key, default=None):
        return self.config.get(key, default)

    def is_configured(self):
        return self.configured

    def fetch_balance(self):
        return ProviderBalance("CNY", Decimal("12.3"), 9), None

    def fetch_quota(self):
        return None, None

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
            patch("data.store.history.provider_monthly_payload", return_value=None),
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

    def test_cached_provider_snapshot_is_an_isolated_copy(self):
        original = TokenData(
            per_provider=[PerProviderData("deepseek", "DeepSeek")],
            today_tokens=7,
            status="ok",
        )
        TokenData._provider_snapshots["deepseek"] = original

        cached = TokenData.cached_snapshot("deepseek")

        self.assertIsNotNone(cached)
        self.assertIsNot(cached, original)
        cached.today_tokens = 8
        self.assertEqual(original.today_tokens, 7)
        self.assertIsNone(TokenData.cached_snapshot("mimo"))

    def test_connection_uses_snapshot_without_touching_refresh_cache_or_history(self):
        provider = FakeProvider(payloads=[payload("2026-07-03", 7, ".2")])
        snapshot = MappingProxyType({"ACTIVE_PROVIDER": "deepseek", "DEEPSEEK_AUTH": "draft"})
        existing = TokenData(status="ok", today_tokens=99)
        TokenData._last_snapshot = existing

        with (
            patch("data.store.active_providers", return_value=iter([provider])) as active,
            patch("data.store.history.save_usage") as save_usage,
            patch("data.store.history.recent_daily") as recent_daily,
        ):
            result = TokenData.test_connection(snapshot)

        active.assert_called_once_with(snapshot)
        save_usage.assert_not_called()
        recent_daily.assert_not_called()
        self.assertIs(TokenData._last_snapshot, existing)
        self.assertEqual(result.status, "ok")

    def test_refresh_closes_provider_after_network_failure(self):
        class ClosingProvider(FakeProvider):
            def __init__(self):
                super().__init__()
                self.closed = False

            def fetch_balance(self):
                raise RuntimeError("network failed")

            def close(self):
                self.closed = True

        provider = ClosingProvider()
        self.fetch_with(provider)

        self.assertTrue(provider.closed)

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

    def test_current_month_details_override_zero_summary_placeholders(self):
        class ZeroSummaryProvider(FakeProvider):
            def fetch_summary(self):
                return ProviderSummary(Decimal("0"), 0), None

        provider = ZeroSummaryProvider(payloads=[
            payload("2026-07-01", 20, ".2"),
            payload("2026-07-03", 30, ".23"),
        ])

        data = self.fetch_with(provider)

        self.assertEqual(data.monthly_usage_tokens, 50)
        self.assertAlmostEqual(data.monthly_cost_cny, .43)

    def test_subscription_quota_is_propagated_without_billing_data(self):
        class QuotaProvider(FakeProvider):
            id = "codex"
            name = "Codex"
            supports_daily_usage = False
            supports_cost = False

            def fetch_balance(self):
                return None, None

            def fetch_summary(self):
                return None, None

            def fetch_quota(self):
                return ProviderQuota(
                    windows=(
                        QuotaWindow(
                            "codex-weekly", "每周额度", 25, window_minutes=10_080
                        ),
                    ),
                    metrics=(QuotaMetric("Credits", "12"),),
                    activity=(("2026-07-03", 1234),),
                    statistics=(QuotaMetric("累计 Token 数", "0.12万"),),
                    account_label="a@example.com",
                    plan="pro",
                ), None

        data = self.fetch_with(QuotaProvider())

        self.assertEqual(data.status, "ok")
        self.assertEqual(data.quota_windows[0].used_percent, 25)
        self.assertEqual(data.quota_metrics[0].value, "12")
        self.assertEqual(data.quota_statistics[0].value, "0.12万")
        self.assertEqual(data.daily_usage[0]["tokens"], 1234)
        self.assertEqual(data.weekly_usage[0]["tokens"], 1234)
        self.assertEqual(data.account_label, "a@example.com")
        self.assertEqual(data.account_plan, "pro")

    def test_codex_network_failure_keeps_cached_quota_and_remote_success_time(self):
        class QuotaProvider(FakeProvider):
            id = "codex"
            name = "Codex"
            supports_daily_usage = False
            supports_cost = False
            supports_subscription_quota = True

            def fetch_balance(self):
                return None, None

            def fetch_summary(self):
                return None, None

        class SuccessfulQuotaProvider(QuotaProvider):
            def fetch_quota(self):
                return ProviderQuota(
                    windows=(
                        QuotaWindow(
                            "codex-weekly", "每周额度", 25, window_minutes=10_080
                        ),
                    ),
                    metrics=(QuotaMetric("Credits", "12"),),
                    activity=(("2026-07-03", 1234),),
                    statistics=(QuotaMetric("累计 Token 数", "0.12万"),),
                    account_label="a@example.com",
                    plan="pro",
                    account_plan_active_until=datetime(
                        2026, 8, 11, 6, 17, tzinfo=timezone.utc
                    ),
                ), None

        class FailedQuotaProvider(QuotaProvider):
            def fetch_quota(self):
                return ProviderQuota(
                    activity=(("2026-07-03", 2345),),
                    statistics=(QuotaMetric("累计 Token 数", "0.23万"),),
                ), FetchError(
                    "NETWORK_ERROR", "Codex 订阅额度", "无法连接 Codex 额度服务"
                )

        self.fetch_with(SuccessfulQuotaProvider())
        remote_success_at = datetime(2026, 7, 3, 10, 30)
        cached = TokenData._provider_snapshots["codex"]
        cached.last_success_at = remote_success_at
        cached.last_updated = "10:30:00"

        data = self.fetch_with(FailedQuotaProvider())

        self.assertEqual(data.status, "ok")
        self.assertEqual(data.errors, [])
        self.assertTrue(data.is_stale)
        self.assertEqual(data.quota_windows[0].used_percent, 25)
        self.assertEqual(data.quota_metrics[0].value, "12")
        self.assertEqual(data.account_label, "a@example.com")
        self.assertEqual(data.account_plan, "pro")
        self.assertEqual(
            data.account_plan_active_until,
            datetime(2026, 8, 11, 6, 17, tzinfo=timezone.utc),
        )
        self.assertEqual(data.quota_statistics[0].value, "0.12万")
        self.assertEqual(data.daily_usage[0]["tokens"], 1234)
        self.assertEqual(data.weekly_usage[0]["tokens"], 1234)
        self.assertEqual(data.quota_source, "cache")
        self.assertEqual(data.activity_source, "cache")
        self.assertEqual(data.weekly_activity_source, "cache")
        self.assertEqual(data.statistics_source, "cache")
        self.assertEqual(data.last_success_at, remote_success_at)
        self.assertEqual(data.last_updated, "10:30:00")

    def test_cursor_transient_failure_keeps_quota_but_auth_failure_does_not(self):
        class CursorQuotaProvider(FakeProvider):
            id = "cursor"
            name = "Cursor"
            supports_daily_usage = False
            supports_cost = False
            supports_subscription_quota = True

            def fetch_balance(self):
                return None, None

            def fetch_summary(self):
                return None, None

        class SuccessfulCursorProvider(CursorQuotaProvider):
            def fetch_quota(self):
                return ProviderQuota(
                    windows=(QuotaWindow("cursor-monthly", "每月额度", 42),),
                    metrics=(
                        QuotaMetric("套餐用量", "$8.40 / $20.00"),
                        QuotaMetric("额外消费", "--"),
                    ),
                    activity=(("2026-08-14", 95_363),),
                    weekly_activity=(("2026-08-14", 95_363),),
                    statistics=(QuotaMetric("套餐", "Pro"),),
                    plan="Pro",
                    activity_source="interface",
                    weekly_activity_source="interface",
                    statistics_source="interface",
                ), None

        class FailedCursorProvider(CursorQuotaProvider):
            error_code = "NETWORK_ERROR"

            def fetch_quota(self):
                return None, FetchError(
                    self.error_code, "Cursor 订阅额度", "Cursor 用量暂不可用"
                )

        self.fetch_with(SuccessfulCursorProvider())
        cached = self.fetch_with(FailedCursorProvider())

        self.assertEqual(cached.status, "ok")
        self.assertTrue(cached.is_stale)
        self.assertEqual(cached.errors, [])
        self.assertEqual(cached.quota_windows[0].used_percent, 42)
        self.assertEqual(cached.quota_metrics[0].value, "$8.40 / $20.00")
        self.assertEqual(cached.daily_usage[0]["tokens"], 95_363)
        self.assertEqual(cached.weekly_usage[0]["tokens"], 95_363)
        self.assertEqual(cached.activity_source, "cache")
        self.assertEqual(cached.weekly_activity_source, "cache")
        self.assertEqual(cached.quota_source, "cache")

        FailedCursorProvider.error_code = "AUTH_EXPIRED"
        expired = self.fetch_with(FailedCursorProvider())
        self.assertEqual(expired.status, "error")
        self.assertEqual(expired.quota_windows, [])
        self.assertEqual(expired.errors[0].code, "AUTH_EXPIRED")

    def test_codex_transient_remote_errors_keep_the_complete_cached_snapshot(self):
        class QuotaProvider(FakeProvider):
            id = "codex"
            name = "Codex"
            supports_daily_usage = False
            supports_cost = False
            supports_subscription_quota = True

            def fetch_balance(self):
                return None, None

            def fetch_summary(self):
                return None, None

        class SuccessfulQuotaProvider(QuotaProvider):
            def fetch_quota(self):
                return ProviderQuota(
                    windows=(QuotaWindow("codex-weekly", "每周额度", 25),),
                    activity=(("2026-07-03", 1234),),
                    weekly_activity=(("2026-07-03", 5678),),
                    statistics=(QuotaMetric("累计 Token 数", "0.12万"),),
                    plan="pro",
                ), None

        for error_code in (
            "INVALID_RESPONSE",
            "RATE_LIMITED",
            "SERVER_ERROR",
            "UNKNOWN_ERROR",
        ):
            with self.subTest(error_code=error_code):
                TokenData._provider_snapshots = {}
                self.fetch_with(SuccessfulQuotaProvider())

                class FailedQuotaProvider(QuotaProvider):
                    def fetch_quota(self):
                        return None, FetchError(error_code, "Codex 订阅额度", "暂时不可用")

                data = self.fetch_with(FailedQuotaProvider())

                self.assertEqual(data.quota_windows[0].used_percent, 25)
                self.assertEqual(data.quota_statistics[0].value, "0.12万")
                self.assertEqual(data.daily_usage[0]["tokens"], 1234)
                self.assertEqual(data.weekly_usage[0]["tokens"], 5678)
                self.assertEqual(data.account_plan, "pro")
                self.assertEqual(data.errors, [])

    def test_codex_stats_failure_keeps_official_cache_and_merges_only_today(self):
        class QuotaProvider(FakeProvider):
            id = "codex"
            name = "Codex"
            supports_daily_usage = False
            supports_cost = False
            supports_subscription_quota = True

            def fetch_balance(self):
                return None, None

            def fetch_summary(self):
                return None, None

        class SuccessfulQuotaProvider(QuotaProvider):
            def fetch_quota(self):
                return ProviderQuota(
                    windows=(QuotaWindow("codex-weekly", "每周额度", 25),),
                    activity=(("2026-07-02", 1234),),
                    weekly_activity=(("2026-07-02", 1234),),
                    statistics=(QuotaMetric("累计 Token 数", "0.12万"),),
                    activity_source="interface",
                    weekly_activity_source="interface",
                    statistics_source="interface",
                ), None

        class StatsUnavailableProvider(QuotaProvider):
            def fetch_quota(self):
                return ProviderQuota(
                    windows=(QuotaWindow("codex-weekly", "每周额度", 30),),
                    weekly_activity=(("2026-07-03", 5678),),
                    weekly_activity_source="local",
                ), None

        self.fetch_with(SuccessfulQuotaProvider())
        data = self.fetch_with(StatsUnavailableProvider())

        self.assertEqual(data.quota_windows[0].used_percent, 30)
        self.assertEqual(data.daily_usage[0]["tokens"], 1234)
        self.assertEqual(data.quota_statistics[0].value, "0.12万")
        self.assertEqual(
            {item["date"]: item["tokens"] for item in data.weekly_usage},
            {"2026-07-02": 1234, "2026-07-03": 5678},
        )
        self.assertEqual(data.quota_source, "interface")
        self.assertEqual(data.activity_source, "cache")
        self.assertEqual(data.statistics_source, "cache")
        self.assertEqual(data.weekly_activity_source, "cache_mixed")

    def test_codex_offline_restart_restores_only_the_same_account_snapshot(self):
        class QuotaProvider(FakeProvider):
            id = "codex"
            name = "Codex"
            supports_daily_usage = False
            supports_cost = False
            supports_subscription_quota = True

            def __init__(self, account_key: str):
                super().__init__()
                self.account_key = account_key

            def snapshot_identity(self):
                return self.account_key

            def fetch_balance(self):
                return None, None

            def fetch_summary(self):
                return None, None

        class SuccessfulQuotaProvider(QuotaProvider):
            def fetch_quota(self):
                return ProviderQuota(
                    windows=(QuotaWindow("codex-weekly", "每周额度", 25),),
                    activity=(("2026-07-03", 1234),),
                    weekly_activity=(("2026-07-03", 5678),),
                    statistics=(QuotaMetric("累计 Token 数", "0.12万"),),
                    plan="pro",
                ), None

        class FailedQuotaProvider(QuotaProvider):
            def fetch_quota(self):
                return None, FetchError(
                    "NETWORK_ERROR", "Codex 订阅额度", "无法连接 Codex 额度服务"
                )

        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "usage.db"
            with patch.object(history, "DB_PATH", db_path):
                successful = self.fetch_with(SuccessfulQuotaProvider("same-account"))
                TokenData._provider_snapshots = {}

                with patch(
                    "data.store.active_providers",
                    return_value=iter([QuotaProvider("same-account")]),
                ):
                    preloaded = TokenData.persisted_snapshot(
                        {"ACTIVE_PROVIDER": "codex"}
                    )
                self.assertEqual(preloaded.quota_windows[0].used_percent, 25)
                self.assertEqual(preloaded.daily_usage[0]["tokens"], 1234)
                self.assertEqual(preloaded.weekly_usage[0]["tokens"], 5678)
                self.assertEqual(preloaded.weekly_activity_source, "cache")
                TokenData._provider_snapshots = {}

                restored = self.fetch_with(FailedQuotaProvider("same-account"))
                self.assertEqual(restored.quota_windows[0].used_percent, 25)
                self.assertEqual(restored.quota_statistics[0].value, "0.12万")
                self.assertEqual(restored.daily_usage[0]["tokens"], 1234)
                self.assertEqual(restored.weekly_usage[0]["tokens"], 5678)
                self.assertEqual(restored.account_plan, "pro")
                self.assertEqual(restored.last_success_at, successful.last_success_at)
                self.assertEqual(restored.errors, [])

                TokenData._provider_snapshots = {}
                different_account = self.fetch_with(
                    FailedQuotaProvider("different-account")
                )

        self.assertEqual(different_account.quota_windows, [])
        self.assertEqual(
            [error.code for error in different_account.errors], ["NETWORK_ERROR"]
        )

    def test_legacy_codex_snapshot_does_not_restore_unverified_statistics(self):
        class QuotaProvider(FakeProvider):
            id = "codex"
            name = "Codex"
            supports_subscription_quota = True

            def snapshot_identity(self):
                return "same-account"

        payload = {
            "version": 2,
            "windows": [
                {
                    "id": "codex-weekly",
                    "title": "每周额度",
                    "used_percent": 25,
                }
            ],
            "statistics": [
                {"title": "累计 Token 数", "value": "错误估算", "detail": "本机估算"}
            ],
            "activity": [["2026-07-02", 9999]],
            "weekly_activity": [["2026-07-02", 9999]],
        }
        with patch.object(
            history,
            "load_provider_quota_snapshot",
            return_value=(payload, datetime(2026, 7, 3, 10, 30)),
        ):
            restored = TokenData._load_persisted_quota_snapshot(QuotaProvider())

        self.assertEqual(restored.quota_windows[0].used_percent, 25)
        self.assertEqual(restored.quota_statistics, [])
        self.assertEqual(restored.daily_usage, [])
        self.assertEqual(restored.weekly_usage, [])

    def test_legacy_codex_snapshot_restores_verified_interface_cache(self):
        class QuotaProvider(FakeProvider):
            id = "codex"
            name = "Codex"
            supports_subscription_quota = True

            def snapshot_identity(self):
                return "same-account"

        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        payload = {
            "version": 2,
            "statistics": [
                {
                    "title": "累计 Token 数",
                    "value": "26.1亿",
                    "detail": "来自 Codex 账号统计",
                }
            ],
            "activity": [[yesterday, 1000], [today, 2000]],
            "weekly_activity": [[yesterday, 1000], [today, 3000]],
        }
        with patch.object(
            history,
            "load_provider_quota_snapshot",
            return_value=(payload, datetime.now()),
        ):
            restored = TokenData._load_persisted_quota_snapshot(QuotaProvider())

        self.assertEqual(restored.quota_statistics[0].value, "26.1亿")
        self.assertEqual(
            {item["date"]: item["tokens"] for item in restored.daily_usage},
            {yesterday: 1000},
        )
        self.assertEqual(
            {item["date"]: item["tokens"] for item in restored.weekly_usage},
            {yesterday: 1000, today: 3000},
        )
        self.assertEqual(restored.activity_source, "cache")
        self.assertEqual(restored.statistics_source, "cache")

    def test_codex_network_failure_without_cached_quota_keeps_error_visible(self):
        class FailedQuotaProvider(FakeProvider):
            id = "codex"
            name = "Codex"
            supports_daily_usage = False
            supports_cost = False
            supports_subscription_quota = True

            def fetch_balance(self):
                return None, None

            def fetch_summary(self):
                return None, None

            def fetch_quota(self):
                return ProviderQuota(
                    activity=(("2026-07-03", 2345),),
                    statistics=(QuotaMetric("累计 Token 数", "0.23万"),),
                ), FetchError(
                    "NETWORK_ERROR", "Codex 订阅额度", "无法连接 Codex 额度服务"
                )

        data = self.fetch_with(FailedQuotaProvider())

        self.assertEqual(data.status, "partial")
        self.assertEqual([error.code for error in data.errors], ["NETWORK_ERROR"])
        self.assertEqual(data.quota_windows, [])
        self.assertIsNone(data.last_success_at)

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

    def test_lightweight_background_fetch_skips_history_sync_for_other_provider(self):
        provider = FakeProvider(payloads=[payload("2026-07-03", 30, ".23")])

        with patch(
            "data.store.history.unsynced_months", return_value=[(6, 2026)]
        ) as unsynced_months:
            data = self.fetch_with(provider, lightweight=True)

        self.assertEqual(provider.requested_months, [[(7, 2026)]])
        unsynced_months.assert_not_called()
        self.assertEqual(data.today_tokens, 30)

    def test_complete_previous_month_uses_cache_without_refetch(self):
        provider = FakeProvider(payloads=[payload("2026-07-01", 20, ".2")])
        cached = payload("2026-06-30", 10, ".1")
        with (
            patch("data.store.history.unsynced_months", return_value=[]),
            patch("data.store.history.provider_monthly_payload", return_value=cached),
        ):
            data = self.fetch_with(provider, today=date(2026, 7, 1))

        self.assertEqual(provider.requested_months, [[(7, 2026)]])
        self.assertEqual(data.weekly_tokens, 30)

    def test_incomplete_previous_month_is_requested_again(self):
        provider = FakeProvider(payloads=[payload("2026-07-01", 20, ".2")])
        with patch(
            "data.store.history.unsynced_months", return_value=[(6, 2026)]
        ):
            self.fetch_with(provider, today=date(2026, 7, 1))

        self.assertEqual(provider.requested_months, [[(7, 2026), (6, 2026)]])

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

    def test_today_cost_breakdown_distinguishes_missing_from_zero(self):
        rows = [payload("2026-07-03", 7, ".24"), payload("2026-07-03", 2, ".06")]
        self.assertEqual(cost_breakdown_for_day(rows, date(2026, 7, 3)), Decimal(".30"))
        zero = [payload("2026-07-03", 7, "0")]
        self.assertEqual(cost_breakdown_for_day(zero, date(2026, 7, 3)), Decimal("0"))
        missing = payload("2026-07-03", 7)
        missing["days"][0]["data"][0]["usage"] = [
            {"type": "RESPONSE_TOKEN", "amount": 7}
        ]
        self.assertIsNone(cost_breakdown_for_day([missing], date(2026, 7, 3)))

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

    def test_minute_retention_uses_captured_provider_config(self):
        provider = FakeProvider(
            payloads=[payload("2026-07-03", 7, ".2")],
            config={"MINUTE_USAGE_RETENTION_DAYS": 11},
        )
        provider.supports_estimated_minute_usage = True
        with (
            patch("data.store.history.clear_expired_minute_usage") as clear_expired,
            patch("data.store.history.minute_usage_for_day", return_value=[]),
            patch("data.store.history.minute_cost_usage_for_day", return_value=[]),
            patch("data.store.history.minute_usage_dates", return_value=[]),
            patch("data.store.history.save_estimated_minute_usage", return_value="baseline"),
        ):
            self.fetch_with(provider)

        clear_expired.assert_called_once_with("deepseek", date(2026, 7, 3), 11)

    def test_minute_cost_aggregation_failure_keeps_token_sampling(self):
        provider = FakeProvider(payloads=[payload("2026-07-03", 7, ".2")])
        provider.supports_estimated_minute_usage = True
        with (
            patch("data.store.history.clear_expired_minute_usage"),
            patch("data.store.history.minute_usage_for_day", return_value=[]),
            patch("data.store.history.minute_cost_usage_for_day", return_value=[]),
            patch("data.store.history.minute_usage_dates", return_value=[]),
            patch("data.store.cost_breakdown_for_day", side_effect=ValueError("bad cost")),
            patch("data.store.history.save_estimated_minute_usage", return_value="recorded") as save_minute,
        ):
            data = self.fetch_with(provider)

        self.assertEqual(data.today_tokens, 7)
        self.assertEqual(data.minute_usage_status, "recorded")
        self.assertEqual(save_minute.call_args.kwargs["cost_cny"], None)

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
            ) as minute_usage_for_day,
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
        self.assertIs(data.minute_usage_history["2026-07-03"], data.minute_usage)
        self.assertIs(
            data.minute_cost_usage_history["2026-07-03"], data.minute_cost_usage
        )
        self.assertEqual(minute_usage_for_day.call_count, 2)

        snapshot = TokenData._provider_snapshots["deepseek"]
        self.assertIsNot(snapshot, data)
        self.assertIsNot(snapshot.minute_usage_history, data.minute_usage_history)
        self.assertIs(
            snapshot.minute_usage_history["2026-07-02"],
            data.minute_usage_history["2026-07-02"],
        )
        self.assertIsNot(
            snapshot.minute_cost_usage_history, data.minute_cost_usage_history
        )
        self.assertIs(
            snapshot.minute_cost_usage_history["2026-07-03"],
            data.minute_cost_usage_history["2026-07-03"],
        )
        self.assertEqual(TokenData._base_snapshot("deepseek").minute_usage_history, {})
        isolated = TokenData.cached_snapshot("deepseek")
        self.assertIsNotNone(isolated)
        self.assertIsNot(isolated.minute_usage_history, data.minute_usage_history)
        self.assertEqual(isolated.minute_usage_history, data.minute_usage_history)


if __name__ == "__main__":
    unittest.main()
