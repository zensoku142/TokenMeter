import json
import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, unquote, urlsplit

import requests

os.environ["APPDATA"] = str(Path.cwd() / ".test-appdata")

import config_manager
from api.deepseek import APIError
from api.providers import configured_provider_ids, list_providers
from api.providers.base import QuotaMetric, build_session
from api.providers.codex import CodexProvider
from api.providers.cursor import CursorProvider
from api.providers.deepseek import DeepSeekProvider
from api.providers.mimo import MiMoProvider


def response(payload, status=200):
    result = Mock()
    result.status_code = status
    result.ok = 200 <= status < 400
    result.json.return_value = payload
    return result


class MultiProviderTests(unittest.TestCase):
    def test_configured_provider_ids_includes_all_configured_and_closes_probes(self):
        instances = []

        class ProbeProvider:
            def __init__(self, config):
                self._config = config
                self.closed = False
                instances.append(self)

            def is_configured(self):
                return bool(self._config.get(self.id))

            def close(self):
                self.closed = True

        classes = {}
        for provider_id in ("deepseek", "mimo", "codex"):
            classes[provider_id] = type(provider_id, (ProbeProvider,), {"id": provider_id})

        config = {"deepseek": True, "mimo": False, "codex": True}
        with patch("api.providers.PROVIDERS", classes):
            self.assertEqual(configured_provider_ids(config), ["deepseek", "codex"])

        self.assertTrue(all(instance.closed for instance in instances))

    def test_configured_provider_ids_isolates_probe_failure(self):
        good = Mock()
        good.is_configured.return_value = True
        failed = Mock()
        failed.is_configured.side_effect = RuntimeError("bad config")
        registry = {
            "failed": Mock(return_value=failed),
            "good": Mock(return_value=good),
        }

        with patch("api.providers.PROVIDERS", registry):
            self.assertEqual(configured_provider_ids({}), ["good"])

        failed.close.assert_called_once()
        good.close.assert_called_once()

    def test_codex_home_accepts_legacy_auth_file_path(self):
        home = Path("C:/Users/example/.codex")
        provider = CodexProvider({"CODEX_HOME": str(home / "auth.json")})
        try:
            self.assertEqual(provider._home(), home)
        finally:
            provider.close()

    def test_codex_reads_subscription_windows_and_credits(self):
        provider = CodexProvider()
        provider._credentials = Mock(return_value=("access", "account", {"email": "a@example.com"}))
        provider._local_activity = Mock(
            return_value=((('2026-08-09', 12_000),), ())
        )
        provider._session.get = Mock(
            return_value=response(
                {
                    "plan_type": "pro",
                    "rate_limit": {
                        "primary_window": {
                            "used_percent": 27,
                            "reset_at": 1785816000,
                            "limit_window_seconds": 604800,
                        },
                    },
                    "credits": {"has_credits": True, "balance": "14.5"},
                }
            )
        )
        provider._metadata_session.get = Mock(
            side_effect=[
                response(
                    {
                        "stats": {
                            "lifetime_tokens": 2_607_632_527,
                            "peak_daily_tokens": 202_936_827,
                            "longest_running_turn_sec": 3_155,
                            "current_streak_days": 4,
                            "longest_streak_days": 27,
                            "daily_usage_buckets": [
                                {"start_date": "2026-04-02", "tokens": 6_124_138},
                                {"start_date": "2026-08-12", "tokens": 202_936_827},
                            ],
                        },
                        "metadata": {},
                    }
                ),
                response({"active_until": "2026-08-11T06:17:00Z"}),
            ]
        )
        try:
            quota, error = provider.fetch_quota()
        finally:
            provider.close()
        self.assertIsNone(error)
        self.assertEqual([window.title for window in quota.windows], ["每周额度"])
        self.assertEqual(quota.windows[0].window_minutes, 10_080)
        self.assertEqual(quota.metrics[0].value, "14.5")
        self.assertEqual(quota.account_label, "a@example.com")
        self.assertEqual(quota.plan, "pro")
        self.assertEqual(
            quota.account_plan_active_until,
            datetime(2026, 8, 11, 6, 17, tzinfo=timezone.utc),
        )
        self.assertEqual(quota.activity, (
            ("2026-04-02", 6_124_138),
            ("2026-08-12", 202_936_827),
        ))
        self.assertEqual(
            [item.value for item in quota.statistics],
            ["26.1亿", "2亿", "52分 35秒", "4 天", "27 天"],
        )
        self.assertTrue(
            all(item.detail == "来自 Codex 账号统计" for item in quota.statistics)
        )
        self.assertEqual(provider._session.get.call_count, 1)
        self.assertEqual(provider._metadata_session.get.call_count, 2)
        self.assertEqual(
            provider._metadata_session.get_adapter("https://").max_retries.total,
            0,
        )
        self.assertEqual(
            provider._session.get.call_args_list[0].kwargs["headers"]["originator"],
            "Codex Desktop",
        )
        self.assertEqual(
            provider._session.get.call_args_list[0].kwargs["timeout"], (3, 10)
        )
        self.assertTrue(
            all(
                call.kwargs["timeout"] == (3, 5)
                for call in provider._metadata_session.get.call_args_list
            )
        )
        self.assertEqual(
            provider._metadata_session.get.call_args_list[1].kwargs["params"],
            {"account_id": "account"},
        )

    def test_codex_refreshes_quota_without_refetching_recent_activity(self):
        claims = {"email": "activity-cache@example.com"}
        cache_key = "email:activity-cache@example.com"
        today = datetime.now().astimezone().date()
        yesterday = today - timedelta(days=1)
        with CodexProvider._session_cache_lock:
            CodexProvider._activity_cache.pop(cache_key, None)

        first = CodexProvider()
        second = CodexProvider()
        first._credentials = Mock(return_value=("access", None, claims))
        second._credentials = Mock(return_value=("access", None, claims))
        first._local_activity = Mock(
            return_value=(
                (
                    (yesterday.isoformat(), 9_999),
                    (today.isoformat(), 7_000),
                ),
                (),
            )
        )
        second._local_activity = Mock(return_value=((), ()))
        first._session.get = Mock(
            return_value=response(
                {
                    "rate_limit": {
                        "primary_window": {
                            "used_percent": 10,
                            "reset_at": 1785816000,
                            "limit_window_seconds": 604800,
                        }
                    }
                }
            )
        )
        first._metadata_session.get = Mock(
            return_value=response(
                {
                    "stats": {
                        "lifetime_tokens": 12_000,
                        "daily_usage_buckets": [
                            {"start_date": yesterday.isoformat(), "tokens": 3_000}
                        ],
                    },
                    "metadata": {},
                }
            )
        )
        second._session.get = Mock(
            return_value=response(
                {
                    "rate_limit": {
                        "primary_window": {
                            "used_percent": 20,
                            "reset_at": 1785816000,
                            "limit_window_seconds": 604800,
                        }
                    }
                }
            )
        )
        second._metadata_session.get = Mock()
        try:
            first_quota, first_error = first.fetch_quota()
            second_quota, second_error = second.fetch_quota()
        finally:
            first.close()
            second.close()
            with CodexProvider._session_cache_lock:
                CodexProvider._activity_cache.pop(cache_key, None)

        self.assertIsNone(first_error)
        self.assertIsNone(second_error)
        self.assertEqual(first_quota.windows[0].used_percent, 10)
        self.assertEqual(second_quota.windows[0].used_percent, 20)
        self.assertEqual(second_quota.activity, first_quota.activity)
        self.assertEqual(second_quota.weekly_activity, first_quota.weekly_activity)
        self.assertEqual(second_quota.statistics, first_quota.statistics)
        self.assertEqual(dict(first_quota.activity)[yesterday.isoformat()], 3_000)
        self.assertNotIn(today.isoformat(), dict(first_quota.activity))
        self.assertEqual(dict(first_quota.weekly_activity)[today.isoformat()], 7_000)
        self.assertEqual(first_quota.statistics[0].value, "1.2万")
        self.assertEqual(first_quota.statistics[0].detail, "来自 Codex 账号统计")
        self.assertEqual(first_quota.activity_source, "interface")
        self.assertEqual(first_quota.weekly_activity_source, "mixed")
        self.assertEqual(first_quota.statistics_source, "interface")
        self.assertEqual(second_quota.activity_source, "cache")
        self.assertEqual(second_quota.weekly_activity_source, "cache")
        self.assertEqual(second_quota.statistics_source, "cache")
        self.assertEqual(
            [call.args[0] for call in first._session.get.call_args_list],
            ["https://chatgpt.com/backend-api/wham/usage"],
        )
        self.assertEqual(
            [call.args[0] for call in first._metadata_session.get.call_args_list],
            ["https://chatgpt.com/backend-api/wham/profiles/me"],
        )
        self.assertEqual(
            [call.args[0] for call in second._session.get.call_args_list],
            ["https://chatgpt.com/backend-api/wham/usage"],
        )
        self.assertEqual(CodexProvider._activity_cache_ttl_seconds, 3_600)
        first._local_activity.assert_called_once_with()
        second._local_activity.assert_not_called()
        second._metadata_session.get.assert_not_called()

    def test_codex_activity_cache_expires_after_one_hour(self):
        cache_key = "account:activity-cache-expiry"
        first_activity = (
            (("2026-08-12", 3_000),),
            (QuotaMetric("累计 Token 数", "1万"),),
        )
        refreshed_activity = (
            (("2026-08-12", 4_000),),
            (QuotaMetric("累计 Token 数", "2万"),),
        )
        provider = CodexProvider()
        provider._profile_activity = Mock(
            side_effect=[first_activity, refreshed_activity]
        )
        provider._local_activity = Mock(return_value=((), ()))
        try:
            with patch("api.providers.codex.time.monotonic", return_value=100.0):
                initial = provider._activity_snapshot(cache_key, {})
            with patch("api.providers.codex.time.monotonic", return_value=3_699.0):
                cached = provider._activity_snapshot(cache_key, {})
            with patch("api.providers.codex.time.monotonic", return_value=3_700.0):
                refreshed = provider._activity_snapshot(cache_key, {})
        finally:
            provider.close()
            with CodexProvider._session_cache_lock:
                CodexProvider._activity_cache.pop(cache_key, None)

        expected_initial = (first_activity[0], first_activity[0], first_activity[1])
        expected_refreshed = (
            refreshed_activity[0],
            refreshed_activity[0],
            refreshed_activity[1],
        )
        self.assertEqual(initial, expected_initial)
        self.assertEqual(cached, expected_initial)
        self.assertEqual(refreshed, expected_refreshed)
        self.assertEqual(provider._profile_activity.call_count, 2)
        self.assertEqual(provider._local_activity.call_count, 2)

    def test_codex_persisted_snapshot_identity_is_stable_and_non_secret(self):
        provider = CodexProvider()
        provider._credentials = Mock(
            return_value=("access-secret", "account-123", {"email": "a@example.com"})
        )
        try:
            first = provider.snapshot_identity()
            second = provider.snapshot_identity()
            provider._credentials = Mock(
                return_value=("other-secret", "account-456", {"email": "b@example.com"})
            )
            different = provider.snapshot_identity()
        finally:
            provider.close()

        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)
        self.assertNotIn("account-123", first)
        self.assertNotIn("a@example.com", first)
        self.assertNotEqual(first, different)

    def test_codex_activity_failure_keeps_the_last_server_snapshot(self):
        cache_key = "account:stale-activity-cache"
        server_activity = (
            (("2026-08-12", 3_000),),
            (("2026-08-12", 3_000),),
            (QuotaMetric("累计 Token 数", "1万", "来自 Codex 账号统计"),),
        )
        local_activity = (
            (("2026-08-12", 9_000),),
            (QuotaMetric("累计 Token 数", "9万", "本机估算"),),
        )
        provider = CodexProvider()
        provider._profile_activity = Mock(return_value=None)
        provider._local_activity = Mock(return_value=local_activity)
        with CodexProvider._session_cache_lock:
            CodexProvider._activity_cache[cache_key] = (100.0, server_activity)
        try:
            with patch("api.providers.codex.time.monotonic", return_value=3_701.0):
                result = provider._activity_snapshot(cache_key, {})
        finally:
            provider.close()
            with CodexProvider._session_cache_lock:
                CodexProvider._activity_cache.pop(cache_key, None)

        self.assertEqual(result, server_activity)
        provider._profile_activity.assert_called_once()
        provider._local_activity.assert_called_once_with()

    def test_codex_subscription_metadata_failure_does_not_discard_quota(self):
        provider = CodexProvider()
        provider._credentials = Mock(return_value=("access", "failed-account", {}))
        provider._local_activity = Mock(return_value=((), ()))
        provider._session.get = Mock(
            return_value=response({"plan_type": "plus", "rate_limit": {}})
        )
        provider._metadata_session.get = Mock(
            side_effect=[
                response({}, status=500),
                requests.Timeout(),
            ]
        )
        try:
            quota, error = provider.fetch_quota()
        finally:
            provider.close()

        self.assertIsNone(error)
        self.assertEqual(quota.plan, "plus")
        self.assertIsNone(quota.account_plan_active_until)

    def test_codex_local_sessions_build_activity_and_five_statistics(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            sessions = home / "sessions" / "2026" / "08" / "09"
            sessions.mkdir(parents=True)
            today = datetime.now().astimezone().date()
            yesterday = today - timedelta(days=1)

            def line(timestamp, event_type, payload):
                return json.dumps(
                    {"timestamp": timestamp, "type": event_type, "payload": payload},
                    separators=(",", ":"),
                )

            def token_event(total, cached=0):
                return {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "total_tokens": total,
                            "cached_input_tokens": cached,
                        }
                    },
                }
            first = [
                line(f"{today.isoformat()}T08:00:00+08:00", "session_meta", {}),
                line(f"{today.isoformat()}T08:10:00+08:00", "event_msg", token_event(10_000, 4_000)),
                line(f"{today.isoformat()}T08:20:00+08:00", "event_msg", token_event(15_000, 6_000)),
                line(f"{today.isoformat()}T08:52:35+08:00", "event_msg", {"type": "task_complete"}),
            ]
            second = [
                line(f"{yesterday.isoformat()}T09:00:00+08:00", "session_meta", {}),
                line(f"{yesterday.isoformat()}T09:01:00+08:00", "event_msg", token_event(6_000)),
            ]
            resumed = [
                line(f"{yesterday.isoformat()}T10:00:00+08:00", "session_meta", {}),
                line(
                    f"{yesterday.isoformat()}T10:00:01+08:00",
                    "event_msg",
                    {"type": "task_started"},
                ),
                line(
                    f"{yesterday.isoformat()}T10:12:01+08:00",
                    "event_msg",
                    {"type": "task_complete"},
                ),
                # Resuming the same session file a day later must start a new task;
                # the idle gap between tasks is not chat duration.
                line(
                    f"{today.isoformat()}T10:00:01+08:00",
                    "event_msg",
                    {"type": "task_started"},
                ),
                line(
                    f"{today.isoformat()}T10:08:01+08:00",
                    "event_msg",
                    {"type": "task_complete"},
                ),
            ]
            (sessions / "first.jsonl").write_text("\n".join(first) + "\n", encoding="utf-8")
            (sessions / "second.jsonl").write_text("\n".join(second) + "\n", encoding="utf-8")
            (sessions / "resumed.jsonl").write_text(
                "\n".join(resumed) + "\n", encoding="utf-8"
            )

            provider = CodexProvider({"CODEX_HOME": str(home)})
            try:
                activity, statistics = provider._local_activity()
            finally:
                provider.close()

        self.assertEqual(dict(activity)[today.isoformat()], 15_000)
        self.assertEqual(dict(activity)[yesterday.isoformat()], 6_000)
        self.assertEqual([item.title for item in statistics], [
            "累计 Token 数",
            "峰值 Token 数",
            "最长任务时长",
            "当前连续天数",
            "最长连续天数",
        ])
        self.assertEqual([item.value for item in statistics[:3]], ["2.1万", "1.5万", "52分 35秒"])
        self.assertTrue(all("本机 Codex 会话日志估算" in item.detail for item in statistics))

    def test_codex_profile_stats_error_falls_back_to_local_activity(self):
        today = datetime.now().astimezone().date().isoformat()
        local_statistics = (
            QuotaMetric("累计 Token 数", "1万", "本机估算"),
        )
        provider = CodexProvider()
        provider._credentials = Mock(return_value=("access", None, {}))
        provider._local_activity = Mock(
            return_value=(((today, 10_000),), local_statistics)
        )
        provider._session.get = Mock(
            return_value=response({"plan_type": "plus", "rate_limit": {}})
        )
        provider._metadata_session.get = Mock(
            return_value=response(
                {
                    "stats": {"lifetime_tokens": 99_000},
                    "metadata": {"stats_error": "temporarily unavailable"},
                }
            )
        )
        try:
            quota, error = provider.fetch_quota()
        finally:
            provider.close()

        self.assertIsNone(error)
        self.assertEqual(quota.activity, ())
        self.assertEqual(quota.weekly_activity, ((today, 10_000),))
        self.assertEqual(quota.statistics, ())
        self.assertEqual(quota.weekly_activity_source, "local")
        self.assertEqual(quota.activity_source, "")
        self.assertEqual(quota.statistics_source, "")
        provider._local_activity.assert_called_once_with()


class CursorProviderTests(unittest.TestCase):
    def setUp(self):
        with CursorProvider._activity_cache_lock:
            CursorProvider._activity_cache.clear()

    def storage(self, rows: dict[str, str]) -> Path:
        temp_root = Path.cwd() / ".test-appdata" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        with sqlite3.connect(directory / "state.vscdb") as connection:
            connection.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
            connection.executemany(
                "INSERT INTO ItemTable(key, value) VALUES (?, ?)", rows.items()
            )
        return directory

    @staticmethod
    def usage_payload(**overrides):
        payload = {
            "billingCycleStart": "1785542400000",
            "billingCycleEnd": "1788220800000",
            "planUsage": {
                "includedSpend": 840,
                "limit": 2000,
                "bonusSpend": 0,
                "autoSpend": 620,
                "apiSpend": 220,
                "autoPercentUsed": 33.0,
                "apiPercentUsed": 15.0,
                "totalPercentUsed": 42.0,
            },
            "spendLimitUsage": {
                "individualUsed": 210,
                "individualLimit": 5000,
            },
        }
        payload.update(overrides)
        return payload

    @staticmethod
    def plan_payload(**overrides):
        info = {
            "planName": "Pro",
            "includedAmountCents": 2000,
            "billingCycleEnd": "1788220800000",
        }
        info.update(overrides)
        return {"planInfo": info}

    @staticmethod
    def activity_payload():
        first = str(int(datetime(2026, 8, 13, 12, tzinfo=timezone.utc).timestamp() * 1000))
        second = str(int(datetime(2026, 8, 14, 12, tzinfo=timezone.utc).timestamp() * 1000))
        return {
            "dailySpend": [
                {"day": first, "category": "included", "totalTokens": "100"},
                {"day": first, "category": "on_demand", "totalTokens": "200"},
                {"day": second, "category": "included", "totalTokens": "75"},
            ]
        }

    def test_paths_and_read_only_access_token_query(self):
        appdata = Path("C:/Users/example/AppData/Roaming")
        with patch.dict(os.environ, {"APPDATA": str(appdata)}):
            provider = CursorProvider()
            try:
                self.assertEqual(
                    provider._global_storage_dir(),
                    appdata / "Cursor" / "User" / "globalStorage",
                )
            finally:
                provider.close()

        storage = self.storage(
            {
                "cursorAuth/accessToken": "synthetic-access-value",
                "cursorAuth/refreshToken": "must-not-be-read",
            }
        )
        traces: list[str] = []
        real_connect = sqlite3.connect

        def connect_read_only(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            connection.set_trace_callback(traces.append)
            return connection

        provider = CursorProvider({"CURSOR_GLOBAL_STORAGE": str(storage)})
        try:
            with patch("api.providers.cursor.sqlite3.connect", side_effect=connect_read_only) as connect:
                self.assertEqual(provider._access_token(), "synthetic-access-value")
            self.assertEqual(provider._state_db_path(), storage / "state.vscdb")
            self.assertIn("mode=ro", connect.call_args.args[0])
            self.assertTrue(connect.call_args.kwargs["uri"])
            self.assertTrue(any("cursorAuth/accessToken" in sql for sql in traces))
            self.assertTrue(all("refreshToken" not in sql for sql in traces))
        finally:
            provider.close()

    def test_missing_database_token_and_unreadable_database_are_not_configured(self):
        temp_root = Path.cwd() / ".test-appdata" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(temporary.cleanup)
        missing = Path(temporary.name)
        provider = CursorProvider({"CURSOR_GLOBAL_STORAGE": str(missing)})
        provider._session.post = Mock()
        try:
            self.assertFalse(provider.is_configured())
            quota, error = provider.fetch_quota()
            self.assertIsNone(quota)
            self.assertEqual(error.code, "NOT_CONFIGURED")
            provider._session.post.assert_not_called()
        finally:
            provider.close()

        empty = self.storage({})
        provider = CursorProvider({"CURSOR_GLOBAL_STORAGE": str(empty)})
        try:
            self.assertFalse(provider.is_configured())
            with patch(
                "api.providers.cursor.sqlite3.connect",
                side_effect=sqlite3.OperationalError("unreadable"),
            ):
                self.assertFalse(provider.is_configured())
        finally:
            provider.close()

    def test_configuration_probe_and_snapshot_identity_are_non_secret(self):
        first = self.storage({"cursorAuth/accessToken": "synthetic-first"})
        second = self.storage({"cursorAuth/accessToken": "synthetic-second"})
        provider = CursorProvider({"CURSOR_GLOBAL_STORAGE": str(first)})
        try:
            provider._session.post = Mock()
            self.assertTrue(provider.is_configured())
            provider._session.post.assert_not_called()
            identity = provider.snapshot_identity()
            self.assertEqual(len(identity), 64)
            self.assertNotIn("synthetic-first", identity)
            self.assertEqual(identity, provider.snapshot_identity())
        finally:
            provider.close()

        other = CursorProvider({"CURSOR_GLOBAL_STORAGE": str(second)})
        try:
            self.assertNotEqual(identity, other.snapshot_identity())
        finally:
            other.close()

        with (
            patch.object(DeepSeekProvider, "is_configured", return_value=False),
            patch.object(MiMoProvider, "is_configured", return_value=False),
            patch.object(CodexProvider, "is_configured", return_value=False),
        ):
            self.assertEqual(
                configured_provider_ids({"CURSOR_GLOBAL_STORAGE": str(first)}),
                ["cursor"],
            )
        self.assertEqual(
            list_providers(),
            [
                ("deepseek", "DeepSeek"),
                ("mimo", "小米 MiMo"),
                ("codex", "Codex"),
                ("cursor", "Cursor"),
            ],
        )

    def test_rpc_contract_amount_mapping_and_timestamp_units(self):
        provider = CursorProvider()
        provider._credentials = Mock(return_value="synthetic-access-value")
        provider._session.post = Mock(
            side_effect=[response(self.usage_payload()), response(self.plan_payload())]
        )
        provider._activity_session.post = Mock(return_value=response(self.activity_payload()))
        try:
            quota, error = provider.fetch_quota()
        finally:
            provider.close()

        self.assertIsNone(error)
        self.assertEqual(quota.windows[0].id, "cursor-monthly")
        self.assertEqual(quota.windows[0].title, "每月额度")
        self.assertEqual(quota.windows[0].used_percent, 33)
        self.assertEqual(quota.windows[0].resets_at, datetime(2026, 9, 1, tzinfo=timezone.utc))
        self.assertEqual(
            [(item.title, item.value) for item in quota.metrics],
            [("套餐用量", "$8.40 / $20.00"), ("额外消费", "$2.10 / $50.00")],
        )
        self.assertEqual(
            [(item.title, item.value) for item in quota.statistics[:4]],
            [("套餐", "Pro"), ("Bonus", "$0.00"), ("Auto", "$6.20"), ("指定模型", "$2.20")],
        )
        self.assertEqual(quota.statistics[4].title, "账期")
        self.assertEqual(quota.plan, "Pro")
        self.assertEqual(sorted(tokens for _, tokens in quota.activity), [75, 300])
        self.assertEqual(quota.weekly_activity, quota.activity)
        self.assertEqual(quota.activity_source, "interface")
        self.assertEqual(quota.weekly_activity_source, "interface")

        self.assertEqual(provider._timestamp("1788220800"), provider._timestamp("1788220800000"))
        self.assertEqual(provider._usage_percent({"includedSpend": 2500, "limit": 2000}), 100)
        self.assertEqual(provider._usage_percent({"totalPercentUsed": 37.5}), 37.5)
        self.assertEqual(
            provider._usage_percent(
                {"autoPercentUsed": 33, "totalPercentUsed": 42, "limit": 2000}
            ),
            33,
        )

        calls = provider._session.post.call_args_list
        self.assertTrue(calls[0].args[0].endswith("/GetCurrentPeriodUsage"))
        self.assertTrue(calls[1].args[0].endswith("/GetPlanInfo"))
        for call in calls:
            self.assertEqual(call.kwargs["json"], {})
            self.assertEqual(call.kwargs["timeout"], (3, 10))
            self.assertEqual(call.kwargs["headers"]["Connect-Protocol-Version"], "1")
            self.assertEqual(call.kwargs["headers"]["Content-Type"], "application/json")
            self.assertEqual(
                call.kwargs["headers"]["Authorization"], "Bearer synthetic-access-value"
            )
        activity_call = provider._activity_session.post.call_args
        self.assertTrue(activity_call.args[0].endswith("/GetDailySpendByCategory"))
        self.assertEqual(
            activity_call.kwargs["json"]["groupBy"],
            "SPEND_GROUP_BY_CATEGORY_USAGE_TYPE",
        )
        self.assertEqual(activity_call.kwargs["json"]["spendType"], "SPEND_TYPE_ALL")
        self.assertEqual(len(activity_call.kwargs["json"]["periodStartMs"]), 13)
        self.assertEqual(len(activity_call.kwargs["json"]["periodEndMs"]), 13)

    def test_optional_fields_display_placeholders(self):
        provider = CursorProvider()
        provider._credentials = Mock(return_value="synthetic-access-value")
        usage = self.usage_payload(
            planUsage={"limit": 0, "totalPercentUsed": 37.5},
            spendLimitUsage={"limitType": "none"},
        )
        provider._session.post = Mock(
            side_effect=[response(usage), response(self.plan_payload(planName=""))]
        )
        provider._activity_session.post = Mock(return_value=response({"dailySpend": []}))
        try:
            quota, error = provider.fetch_quota()
        finally:
            provider.close()

        self.assertIsNone(error)
        self.assertEqual(quota.windows[0].used_percent, 37.5)
        self.assertEqual([metric.value for metric in quota.metrics], ["--", "--"])
        self.assertEqual(
            [metric.value for metric in quota.statistics[:4]], ["--", "--", "--", "--"]
        )

    def test_live_shape_uses_separate_usage_percentages_when_spend_fields_are_absent(self):
        provider = CursorProvider()
        provider._credentials = Mock(return_value="synthetic-live-shape")
        usage = self.usage_payload(
            planUsage={
                "includedSpend": 2000,
                "limit": 2000,
                "bonusSpend": 8570,
                "autoPercentUsed": 32.913333333333334,
                "apiPercentUsed": 15.466666666666667,
                "totalPercentUsed": 30.63768115942029,
            },
            spendLimitUsage={"limitType": "user"},
        )
        provider._session.post = Mock(
            side_effect=[response(usage), response(self.plan_payload())]
        )
        provider._activity_session.post = Mock(return_value=response({"dailySpend": []}))
        try:
            quota, error = provider.fetch_quota()
        finally:
            provider.close()

        self.assertIsNone(error)
        self.assertAlmostEqual(quota.windows[0].used_percent, 32.913333333333334)
        self.assertEqual([metric.value for metric in quota.metrics], ["$20.00 / $20.00", "--"])
        self.assertEqual(
            [metric.value for metric in quota.statistics[:4]],
            ["Pro", "$85.70", "33%", "15%"],
        )

    def test_activity_failure_keeps_last_successful_non_secret_cache(self):
        token = "synthetic-activity-cache"
        provider = CursorProvider()
        provider._credentials = Mock(return_value=token)
        provider._session.post = Mock(
            side_effect=[response(self.usage_payload()), response(self.plan_payload())]
        )
        provider._activity_session.post = Mock(return_value=response(self.activity_payload()))
        try:
            first, first_error = provider.fetch_quota()
        finally:
            provider.close()
        self.assertIsNone(first_error)
        self.assertEqual(first.activity_source, "interface")

        cache_key = CursorProvider._activity_cache_key(token)
        with CursorProvider._activity_cache_lock:
            _, rows = CursorProvider._activity_cache[cache_key]
            CursorProvider._activity_cache[cache_key] = (0, rows)
        provider = CursorProvider()
        provider._credentials = Mock(return_value=token)
        provider._session.post = Mock(
            side_effect=[response(self.usage_payload()), response(self.plan_payload())]
        )
        provider._activity_session.post = Mock(return_value=response({}, 503))
        try:
            cached, cached_error = provider.fetch_quota()
        finally:
            provider.close()

        self.assertIsNone(cached_error)
        self.assertEqual(cached.activity, first.activity)
        self.assertEqual(cached.activity_source, "cache")

    def test_error_mapping_and_messages_exclude_access_token(self):
        cases = ((401, "AUTH_EXPIRED"), (403, "AUTH_EXPIRED"), (429, "RATE_LIMITED"), (503, "SERVER_ERROR"), (400, "UNKNOWN_ERROR"))
        for status, expected in cases:
            with self.subTest(status=status):
                provider = CursorProvider()
                provider._credentials = Mock(return_value="synthetic-access-value")
                provider._session.post = Mock(return_value=response({}, status))
                try:
                    quota, error = provider.fetch_quota()
                finally:
                    provider.close()
                self.assertIsNone(quota)
                self.assertEqual(error.code, expected)
                self.assertNotIn("synthetic-access-value", error.message)

        exceptions = (
            (requests.Timeout(), "NETWORK_TIMEOUT"),
            (requests.ConnectionError(), "NETWORK_ERROR"),
        )
        for exception, expected in exceptions:
            with self.subTest(exception=expected):
                provider = CursorProvider()
                provider._credentials = Mock(return_value="synthetic-access-value")
                provider._session.post = Mock(side_effect=exception)
                try:
                    quota, error = provider.fetch_quota()
                finally:
                    provider.close()
                self.assertIsNone(quota)
                self.assertEqual(error.code, expected)

        invalid = response({})
        invalid.json.side_effect = ValueError("not json")
        provider = CursorProvider()
        provider._credentials = Mock(return_value="synthetic-access-value")
        provider._session.post = Mock(return_value=invalid)
        try:
            quota, error = provider.fetch_quota()
        finally:
            provider.close()
        self.assertIsNone(quota)
        self.assertEqual(error.code, "INVALID_RESPONSE")
        self.assertNotIn("synthetic-access-value", error.message)


class MiMoProviderTests(unittest.TestCase):
    def test_base_session_retries_only_idempotent_get_requests(self):
        session = build_session()
        read_only_post_session = build_session(retry_post=True)
        try:
            retry = session.get_adapter("https://").max_retries
            self.assertEqual(retry.allowed_methods, frozenset({"GET"}))
            self.assertTrue(retry.is_retry("GET", 503))
            self.assertFalse(retry.is_retry("POST", 503))
            self.assertTrue(
                read_only_post_session.get_adapter("https://").max_retries.is_retry(
                    "POST", 503
                )
            )
        finally:
            session.close()
            read_only_post_session.close()

    def config(self, key, default=None):
        return {
            "MIMO_COOKIE": "api-platform_serviceToken=test; userId=1",
            "MIMO_API_PLATFORM_PH": "",
            "MIMO_BASE": "https://platform.xiaomimimo.com",
        }.get(key, default)

    @patch("api.providers.mimo.config_manager.get")
    def test_real_usage_shape_reports_month_and_remaining_tokens(self, get):
        get.side_effect = self.config
        provider = MiMoProvider()

        def balance_response(url, **kwargs):
            return response({
                "code": 0,
                "data": {
                    "balance": "124.07",
                    "frozenBalance": "0.00",
                    "currency": "CNY",
                    "giftBalance": "124.07",
                    "cashBalance": "0.00",
                },
            })

        def usage_response(url, **kwargs):
            return response({
                "code": 0,
                "data": {
                    "tokenUsage": {
                        "inputToken": 551046842,
                        "outputToken": 1503444,
                        "cacheToken": 544156672,
                        "totalToken": 552550286,
                    },
                    "costUsage": {
                        "totalCost": "43.30",
                        "currentMonthCost": "16.17",
                    },
                },
            })

        def detail_response(url, **kwargs):
            return response({
                "code": 0,
                "data": [
                    {
                        "date": "2026-07-04",
                        "model": "mimo-v2.5-pro",
                        "consumedAmount": "9.280356",
                        "inputHitToken": 62931840,
                        "inputMissToken": 2216402,
                        "outputToken": 176309,
                        "totalToken": 65324551,
                    },
                    {
                        "date": "2026-07-03",
                        "model": "mimo-v2.5-pro",
                        "consumedAmount": "6.647988",
                        "inputHitToken": 107101312,
                        "inputMissToken": 991771,
                        "outputToken": 165857,
                        "totalToken": 108258940,
                    },
                ],
            })

        def dispatcher(url, **kwargs):
            if "/api/v1/balance" in url:
                return balance_response(url, **kwargs)
            if "/api/v1/usage/detail/list" in url:
                return detail_response(url, **kwargs)
            return usage_response(url, **kwargs)

        provider._session.get = Mock(side_effect=dispatcher)
        provider._session.post = Mock(side_effect=dispatcher)

        balance, balance_error = provider.fetch_balance()
        summary, summary_error = provider.fetch_summary()
        payloads, payload_errors = provider.fetch_payloads([(7, 2026)])

        self.assertIsNone(balance_error)
        self.assertIsNone(summary_error)
        self.assertEqual(payload_errors, [])
        # 余额：账户余额来自 balance.balance，单位 CNY
        self.assertEqual(str(balance.amount), "124.07")
        self.assertEqual(balance.currency, "CNY")
        # 月度用量：来自 tokenUsage.totalToken
        self.assertEqual(summary.month_tokens, 552550286)
        self.assertEqual(str(summary.month_cost), "16.17")
        # 日明细：确认拿到了 2 天数据且 token/费用字段齐全
        self.assertTrue(payloads and payloads[0]["days"])
        first_day = payloads[0]["days"][0]
        usage_types = {u["type"] for u in first_day["data"][0]["usage"]}
        self.assertIn("PROMPT_CACHE_HIT_TOKEN", usage_types)
        self.assertIn("PROMPT_CACHE_MISS_TOKEN", usage_types)
        self.assertIn("RESPONSE_TOKEN", usage_types)
        self.assertIn("cost_cny", usage_types)

    @patch("api.providers.mimo.config_manager.get")
    def test_body_auth_error_is_not_reported_as_zero(self, get):
        get.side_effect = self.config
        provider = MiMoProvider()
        provider._session.get = Mock(return_value=response({"code": 401, "data": None}))
        with patch.object(
            MiMoProvider, "_fetch_browser_context", side_effect=RuntimeError("BROWSER_NOT_READY")
        ):
            balance, error = provider.fetch_balance()
        self.assertIsNone(balance)
        self.assertEqual(error.code, "AUTH_EXPIRED")

    @patch("api.providers.mimo.config_manager.get")
    def test_auth_error_uses_verified_browser_context_without_persisting_it(self, get):
        get.side_effect = self.config
        provider = MiMoProvider()
        provider._session.get = Mock(return_value=response({"code": 401, "data": None}))
        browser_context = Mock(
            data={"balance": "12.5", "currency": "CNY"},
            cookie="session=fresh; api-platform_ph=ph",
            api_platform_ph="ph",
        )

        with patch.object(MiMoProvider, "_fetch_browser_context", return_value=browser_context) as recover:
            balance, error = provider.fetch_balance()

        self.assertIsNone(error)
        self.assertEqual(str(balance.amount), "12.5")
        recover.assert_called_once_with(path="/api/v1/balance", body=None, base_url="https://platform.xiaomimimo.com")
        self.assertEqual(provider._browser_cookie, "session=fresh; api-platform_ph=ph")
        self.assertEqual(provider._browser_api_platform_ph, "ph")

    def test_manual_mimo_collection_keeps_all_first_party_cookie_names(self):
        with patch("api.providers.mimo.browser_cookie.acquire_cookie_via_chrome") as acquire:
            acquire.return_value = "session=fresh"
            MiMoProvider.acquire_cookie_via_chrome(threading.Event())

        self.assertIsNone(acquire.call_args.kwargs["cookie_names"])

    @patch("api.providers.mimo.config_manager.get", return_value="")
    def test_missing_cookie_does_not_send_request(self, _get):
        provider = MiMoProvider()
        provider._session.get = Mock()
        provider._session.post = Mock()
        self.assertFalse(provider.is_configured())
        balance, _ = provider.fetch_balance()
        self.assertIsNone(balance)
        provider._session.get.assert_not_called()
        provider._session.post.assert_not_called()


class DeepSeekProviderTests(unittest.TestCase):
    def provider_config(self):
        return {
            "DEEPSEEK_API_KEY": "",
            "DEEPSEEK_AUTH": "Bearer test",
            "DEEPSEEK_COOKIE": "session=test",
        }

    @patch("api.providers.deepseek.official_api.build_session")
    @patch("api.providers.deepseek.platform_api.build_session")
    def test_settings_provider_instances_do_not_share_sessions(
        self, build_platform_session, build_official_session
    ):
        platform_sessions = [Mock(), Mock()]
        official_sessions = [Mock(), Mock()]
        build_platform_session.side_effect = platform_sessions
        build_official_session.side_effect = official_sessions

        first = DeepSeekProvider({"ACTIVE_PROVIDER": "deepseek"})
        second = DeepSeekProvider({"ACTIVE_PROVIDER": "deepseek"})

        self.assertIs(first._platform_session, platform_sessions[0])
        self.assertIs(second._platform_session, platform_sessions[1])
        self.assertIsNot(first._platform_session, second._platform_session)
        self.assertIsNot(first._official_session, second._official_session)

    @patch("api.providers.deepseek.official_api.build_session")
    @patch("api.providers.deepseek.platform_api.build_session")
    def test_close_releases_both_sessions(
        self, build_platform_session, build_official_session
    ):
        platform_session = Mock()
        official_session = Mock()
        build_platform_session.return_value = platform_session
        build_official_session.return_value = official_session

        provider = DeepSeekProvider()
        provider.close()

        platform_session.close.assert_called_once_with()
        official_session.close.assert_called_once_with()

    @patch("api.providers.deepseek.platform_api.get_user_summary")
    def test_summary_cache_is_shared_only_within_one_refresh(self, get_summary):
        get_summary.return_value = {
            "normal_wallets": [
                {"currency": "CNY", "balance": "8", "token_estimation": 10}
            ],
            "monthly_costs": [{"amount": "2"}],
            "monthly_token_usage": 30,
        }
        provider = DeepSeekProvider(self.provider_config())
        try:
            provider.reset_refresh_cache()
            provider.fetch_balance()
            provider.fetch_summary()
            self.assertEqual(get_summary.call_count, 1)

            provider.reset_refresh_cache()
            provider.fetch_summary()
            self.assertEqual(get_summary.call_count, 2)
        finally:
            provider.close()

    @patch("api.providers.deepseek.platform_api.get_user_summary")
    def test_previous_summary_error_does_not_pollute_next_refresh(self, get_summary):
        get_summary.side_effect = [
            APIError("NETWORK_TIMEOUT", "summary", "timeout"),
            {"monthly_costs": [], "monthly_token_usage": 7},
        ]
        provider = DeepSeekProvider(self.provider_config())
        try:
            provider.reset_refresh_cache()
            first, first_error = provider.fetch_summary()
            provider.reset_refresh_cache()
            second, second_error = provider.fetch_summary()
        finally:
            provider.close()

        self.assertIsNone(first)
        self.assertEqual(first_error.code, "NETWORK_TIMEOUT")
        self.assertEqual(second.month_tokens, 7)
        self.assertIsNone(second_error)

    @patch("api.providers.deepseek.config_manager.logger")
    @patch("api.providers.deepseek.platform_api.get_user_summary")
    @patch("api.providers.deepseek.official_api.get_balance")
    def test_official_balance_failure_returns_web_fallback_warning(
        self, get_official_balance, get_summary, logger
    ):
        config = self.provider_config()
        config["DEEPSEEK_API_KEY"] = "sk-test"
        get_official_balance.side_effect = APIError(
            "AUTH_EXPIRED", "balance", "expired"
        )
        get_summary.return_value = {
            "normal_wallets": [
                {"currency": "CNY", "balance": "12.5", "token_estimation": 4}
            ]
        }
        provider = DeepSeekProvider(config)
        try:
            balance, warning = provider.fetch_balance()
        finally:
            provider.close()

        self.assertEqual(str(balance.amount), "12.5")
        self.assertEqual(warning.code, "OFFICIAL_BALANCE_FALLBACK")
        logged = str(logger.mock_calls)
        self.assertNotIn("sk-test", logged)
        self.assertNotIn("Bearer test", logged)
        self.assertNotIn("session=test", logged)

    @patch("api.providers.deepseek.config_manager.get")
    @patch("api.providers.deepseek.platform_api.get_usage_cost")
    @patch("api.providers.deepseek.platform_api.get_usage_amount")
    def test_cost_failure_preserves_token_payload(self, amount, cost, get):
        get.side_effect = lambda key, default=None: {
            "DEEPSEEK_AUTH": "Bearer test",
            "DEEPSEEK_COOKIE": "",
        }.get(key, default)
        amount.return_value = {
            "days": [{"date": "2026-07-05", "data": [{
                "model": "deepseek-v4-pro",
                "usage": [
                    {"type": "PROMPT_CACHE_HIT_TOKEN", "amount": "2"},
                    {"type": "PROMPT_CACHE_MISS_TOKEN", "amount": "3"},
                    {"type": "RESPONSE_TOKEN", "amount": "4"},
                ],
            }]}],
        }
        cost.side_effect = APIError("NETWORK_TIMEOUT", "cost", "连接超时")
        payloads, errors = DeepSeekProvider().fetch_payloads([(7, 2026)])
        usages = payloads[0]["days"][0]["data"][0]["usage"]
        self.assertEqual(sum(row["amount"] for row in usages), 9)
        self.assertFalse(payloads[0]["_complete"])
        self.assertEqual(errors[0].code, "NETWORK_TIMEOUT")

    @patch("api.providers.deepseek.config_manager.get")
    @patch("api.providers.deepseek.platform_api.get_usage_cost")
    @patch("api.providers.deepseek.platform_api.get_usage_amount")
    def test_cost_response_is_mapped_to_cost_not_tokens(self, amount, cost, get):
        get.side_effect = lambda key, default=None: {
            "DEEPSEEK_AUTH": "Bearer test",
            "DEEPSEEK_COOKIE": "",
        }.get(key, default)
        amount.return_value = {"days": []}
        cost.return_value = {
            "days": [{"date": "2026-07-05", "data": [{
                "model": "deepseek-v4-pro",
                "usage": [
                    {"type": "PROMPT_CACHE_HIT_TOKEN", "amount": "0.01"},
                    {"type": "RESPONSE_TOKEN", "amount": "0.02"},
                ],
            }]}],
        }
        payloads, errors = DeepSeekProvider().fetch_payloads([(7, 2026)])
        self.assertEqual(errors, [])
        usage = payloads[0]["days"][0]["data"][0]["usage"]
        self.assertEqual(usage, [{"type": "cost_cny", "amount": "0.03"}])


    @patch("api.providers.mimo.config_manager.get")
    def test_ph_is_extracted_from_cookie_and_appended_to_url(self, get):
        ph = "kmi9pTH8JkU4%2FTg3Yjo8Yw%3D%3D"
        cookie = f"api-platform_serviceToken=test; userId=1; api-platform_ph={ph}"

        def cfg(key, default=None):
            return {
                "MIMO_COOKIE": cookie,
                "MIMO_API_PLATFORM_PH": "",
                "MIMO_BASE": "https://platform.xiaomimimo.com",
            }.get(key, default)

        get.side_effect = cfg
        provider = MiMoProvider()
        self.assertEqual(provider.extract_cookie_value(cookie, "api-platform_ph"), ph)
        # 请求头里 cookie 保持原样，不会重复注入 ph
        headers = provider._platform_headers()
        self.assertIn(f"api-platform_ph={ph}", headers["cookie"])
        self.assertIn("api-platform_ph=" + ph, provider._url("/api/v1/balance"))

    @patch("api.providers.mimo.config_manager.get")
    def test_ph_falls_back_to_credential_when_missing_from_cookie(self, get):
        ph = "fallback-ph=="

        def cfg(key, default=None):
            return {
                "MIMO_COOKIE": "api-platform_serviceToken=test; userId=1",
                "MIMO_API_PLATFORM_PH": ph,
                "MIMO_BASE": "https://platform.xiaomimimo.com",
            }.get(key, default)

        get.side_effect = cfg
        provider = MiMoProvider()
        self.assertEqual(provider.extract_cookie_value("xxx", "api-platform_ph"), "")
        headers = provider._platform_headers()
        # 注入后的 cookie 中应包含 ``api-platform_ph``
        self.assertIn("api-platform_ph=", headers["cookie"])
        # URL 同样会带上 ph
        query = parse_qs(urlsplit(provider._url("/api/v1/usage")).query)
        self.assertEqual(query["api-platform_ph"], [ph])

    def test_cookie_normalization_squeezes_whitespace(self):
        raw = "a=1;\n b=2;\nc=3"
        normalized = MiMoProvider.normalize_cookie(raw)
        self.assertEqual(normalized, "a=1; b=2; c=3")
        # 双引号包裹的值会被正确抽取
        self.assertEqual(
            MiMoProvider.extract_cookie_value('api-platform_ph="abc=="; userId=1', "api-platform_ph"),
            "abc==",
        )


    @patch("api.browser_cookie.find_chrome_executable")
    def test_acquire_cookie_fails_gracefully_when_no_chrome(self, find_executable: Mock) -> None:
        find_executable.return_value = ""
        import threading

        with self.assertRaises(RuntimeError):
            MiMoProvider.acquire_cookie_via_chrome(threading.Event())

    def test_cookie_helper_handles_multiple_formats(self) -> None:
        # 1) 单行 name=value; name2=value2
        self.assertEqual(
            MiMoProvider.extract_cookie_value(
                "a=1; api-platform_ph=some-token; c=3", "api-platform_ph"
            ),
            "some-token",
        )
        # 2) 换行与多余空白
        self.assertEqual(
            MiMoProvider.extract_cookie_value("a=1;\n api-platform_ph=xxx;\nc=3", "api-platform_ph"),
            "xxx",
        )
        # 3) 被双引号括住
        self.assertEqual(
            MiMoProvider.extract_cookie_value('api-platform_ph="abc=="; userId=1', "api-platform_ph"),
            "abc==",
        )
        # 4) 不包含字段时返回空串
        self.assertEqual(
            MiMoProvider.extract_cookie_value("userId=1; other=2", "api-platform_ph"),
            "",
        )

    def test_normalize_cookie_squeezes_whitespace(self) -> None:
        self.assertEqual(
            MiMoProvider.normalize_cookie("a=1; \n b=2 ; \n c=3"),
            "a=1; b=2; c=3",
        )
        self.assertEqual(MiMoProvider.normalize_cookie(""), "")

    @patch("api.providers.mimo.MiMoProvider.acquire_cookie_via_chrome")
    def test_error_message_lookup(self, acquire_mock: Mock) -> None:
        acquire_mock.side_effect = RuntimeError("CHROME_NOT_FOUND")
        try:
            acquire_mock()
        except RuntimeError as exc:
            message = MiMoProvider.describe_acquire_error(exc)
            self.assertIn("Chrome", message)

    def test_cdp_prefers_mimo_url_over_others(self) -> None:
        """``_pick_websocket_endpoint`` 应优先选择 MiMo 域名下的 target。"""
        fake_response = [
            {
                "type": "background_page",
                "url": "chrome-extension://abc/background.html",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9288/devtools/page/A",
            },
            {
                "type": "page",
                "url": "https://platform.xiaomimimo.com/console/usage",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9288/devtools/page/B",
            },
            {
                "type": "other",
                "url": "https://example.com",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9288/devtools/page/C",
            },
        ]

        captured: list[str] = []

        def fake_http(host: str, port: int, path: str, timeout: float) -> object:  # noqa: ARG001
            captured.append(path)
            # 只有 ``/json`` 返回目标列表；其他路径 ``/json/version`` 也应返回合法 JSON。
            if path == "/json":
                return fake_response
            return {"Browser": "Chrome/149"}

        with patch.object(MiMoProvider, "_http_json", side_effect=fake_http):
            got = MiMoProvider._pick_websocket_endpoint(9288)
        self.assertEqual(got, "ws://127.0.0.1:9288/devtools/page/B")
        self.assertIn("/json", captured)

    def test_cdp_falls_back_to_first_acceptable_target(self) -> None:
        """如果列表中没有 MiMo 域名，回退到第一个可用 target 而不是直接报错。"""
        fake_response = [
            {
                "type": "background_page",
                "url": "chrome-extension://abc/background.html",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9288/devtools/page/A",
            },
            {"type": "service_worker", "webSocketDebuggerUrl": "ws://127.0.0.1:9288/devtools/page/SW"},
        ]

        def fake_http(host: str, port: int, path: str, timeout: float) -> object:  # noqa: ARG001
            return fake_response

        with patch.object(MiMoProvider, "_http_json", side_effect=fake_http):
            got = MiMoProvider._pick_websocket_endpoint(9288)
        # ``service_worker`` 不在可接受集合内，只能取 background_page。
        self.assertEqual(got, "ws://127.0.0.1:9288/devtools/page/A")

    def test_cdp_raises_when_no_suitable_target(self) -> None:
        """所有条目都没有 webSocketDebuggerUrl 时，应抛 ``CDP_NO_PAGE_TARGET``。"""
        fake_response = [
            {"type": "page", "url": "https://example.com"},
            {"type": "other", "url": "about:blank"},
        ]

        def fake_http(host: str, port: int, path: str, timeout: float) -> object:  # noqa: ARG001
            return fake_response

        with patch.object(MiMoProvider, "_http_json", side_effect=fake_http):
            with self.assertRaises(RuntimeError) as ctx:
                MiMoProvider._pick_websocket_endpoint(9288)
        self.assertEqual(str(ctx.exception), "CDP_NO_PAGE_TARGET")

    def test_cdp_send_text_handles_broken_ws_url(self) -> None:
        """``_cdp_send_text`` 不应因非法 URL 或 socket 问题抛异常。"""
        # 非法 scheme 不会建立连接；方法应安静返回。
        MiMoProvider._cdp_send_text("http://127.0.0.1:1", {"id": 1, "method": "Browser.close"})
        # 端口不可用；``socket.create_connection`` 抛 OSError 被内部吞掉。
        MiMoProvider._cdp_send_text(
            "ws://127.0.0.1:1/devtools/page/0000", {"id": 1, "method": "Browser.close"}
        )

    def test_format_cookie_string_relaxes_domain_and_strips_quotes(self) -> None:
        """``_format_cookie_string`` 必须能识别 ``xiaomimimo.com`` 任意子域，
        并把 ``api-platform_ph`` 引号去掉；否则会导致请求头/URL 里
        出现 ``""...""`` 或被 domain 过滤掉。
        """
        cookies = [
            {"name": "api-platform_ph", "value": '"abc%2Fdef%3D123"', "domain": ".xiaomimimo.com"},
            {"name": "api-platform_serviceToken", "value": "token-value", "domain": "platform.xiaomimimo.com"},
            {"name": "userId", "value": "12345678", "domain": "xiaomimimo.com"},
            {"name": "_ga", "value": "GA1.2.0", "domain": "xiaomimimo.com"},  # 非关键字段，忽略
            {"name": "other_session", "value": "x", "domain": "other.example.com"},
        ]
        got = MiMoProvider._format_cookie_string(cookies)
        # api-platform_ph 必须去引号，并保留原本的百分编码。
        self.assertIn('api-platform_ph=abc%2Fdef%3D123', got)
        # serviceToken 和 userId 必须被包含（落在 ``xiaomimimo.com`` 子域上）。
        self.assertIn("api-platform_serviceToken=token-value", got)
        self.assertIn("userId=12345678", got)
        # 非关键字段不会出现在 cookie 中。
        self.assertNotIn("_ga=", got)
        self.assertNotIn("other_session=", got)

    def test_url_safely_encodes_api_platform_ph(self) -> None:
        provider = MiMoProvider()

        class _ConfigCache(dict):
            def get(self, key, default=""):  # type: ignore[override]
                return super().get(key, default)

        fake = _ConfigCache(MIMO_COOKIE='a=1; api-platform_ph="xx/yy==zz"; userId=8')
        # 临时替换 ``config_manager`` 的读接口。
        original = config_manager.get
        try:
            config_manager.get = fake.get  # type: ignore[method-assign]
            url = provider._url("/api/v1/balance")
        finally:
            config_manager.get = original  # type: ignore[method-assign]
        self.assertTrue(url.startswith("https://platform.xiaomimimo.com/api/v1/balance?api-platform_ph="))
        # 已编码值先统一解码再编码，既不重复转义，也不会把保留字符留在查询结构中。
        self.assertNotIn('"', url)
        self.assertIn("xx%2Fyy%3D%3Dzz", url)

    def test_append_ph_preserves_query_and_encodes_reserved_characters(self) -> None:
        values = ("plain", "a b", "中文", "a&b", "a#b", "a%b", "a%2Fb")
        for value in values:
            with self.subTest(value=value):
                url = MiMoProvider._append_ph(
                    "https://platform.xiaomimimo.com/api?keep=1#result",
                    value,
                )
                parsed = urlsplit(url)
                query = parse_qs(parsed.query)
                self.assertEqual(query["keep"], ["1"])
                self.assertEqual(query["api-platform_ph"], [unquote(value)])
                self.assertEqual(parsed.fragment, "result")


if __name__ == "__main__":
    unittest.main()
