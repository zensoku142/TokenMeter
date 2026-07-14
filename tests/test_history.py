import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

os.environ["APPDATA"] = str(Path.cwd() / ".test-appdata")

from data import history


def payload(day, amount):
    return {"days": [{"date": day, "data": [{
        "model": "deepseek-test",
        "usage": [{"type": "RESPONSE_TOKEN", "amount": str(amount)}],
    }]}]}


class HistoryTests(unittest.TestCase):
    @staticmethod
    def temp_root() -> Path:
        root = Path.cwd() / ".test-appdata" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def test_save_and_read_normalized_daily_usage(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            with patch.object(history, "DB_PATH", Path(directory) / "usage.db"):
                self.assertTrue(history.needs_initial_sync())
                self.assertEqual(
                    history.unsynced_months([(5, 2026), (4, 2026)]),
                    [(5, 2026), (4, 2026)],
                )
                history.save_usage(
                    [payload("2099-01-01", 12)],
                    [payload("2099-01-01", ".125")],
                    synced_months=[(5, 2026)],
                )
                self.assertFalse(history.needs_initial_sync())
                self.assertEqual(
                    history.unsynced_months([(5, 2026), (4, 2026)]),
                    [(4, 2026)],
                )
                history.save_usage([], [], synced_months=[(4, 2026)])
                self.assertEqual(
                    history.unsynced_months([(5, 2026), (4, 2026)]), []
                )
                self.assertEqual(history.total_cost(), Decimal(".125"))
                rows = history.recent_daily(30_000)
        self.assertEqual(rows[0]["tokens"], 12)
        self.assertEqual(str(rows[0]["cost_cny"]), "0.125")

    def test_provider_history_is_isolated(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            with patch.object(history, "DB_PATH", Path(directory) / "usage.db"):
                history.save_usage(
                    [payload("2099-01-01", 12)],
                    [payload("2099-01-01", ".125")],
                    provider="deepseek",
                )
                history.save_usage(
                    [payload("2099-01-01", 7)],
                    [payload("2099-01-01", ".5")],
                    provider="mimo",
                )
                deepseek = history.recent_daily(30_000, "deepseek")
                mimo = history.recent_daily(30_000, "mimo")
                self.assertEqual(deepseek[0]["tokens"], 12)
                self.assertEqual(mimo[0]["tokens"], 7)
                self.assertEqual(history.total_cost("deepseek"), Decimal(".125"))
                self.assertEqual(history.total_cost("mimo"), Decimal(".5"))

    def test_legacy_database_is_migrated_without_deleting_history(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            db_path = Path(directory) / "usage.db"
            connection = sqlite3.connect(db_path)
            connection.executescript(
                """
                CREATE TABLE daily_usage (
                    usage_date TEXT NOT NULL,
                    model TEXT NOT NULL,
                    token_type TEXT NOT NULL,
                    token_amount INTEGER NOT NULL DEFAULT 0,
                    cost_cny TEXT NOT NULL DEFAULT '0',
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (usage_date, model, token_type)
                );
                INSERT INTO daily_usage VALUES
                    ('2099-01-01', 'deepseek-test', 'RESPONSE_TOKEN', 12, '.125', '2099-01-01');
                """
            )
            connection.commit()
            connection.close()

            with patch.object(history, "DB_PATH", db_path):
                rows = history.recent_daily(30_000, "deepseek")
                self.assertEqual(rows[0]["tokens"], 12)
                self.assertEqual(rows[0]["cost_cny"], Decimal(".125"))
                connection = sqlite3.connect(db_path)
                try:
                    tables = {
                        row[0]
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'"
                        )
                    }
                finally:
                    connection.close()
                self.assertIn("minute_usage", tables)
                self.assertIn("minute_usage_snapshot", tables)

    def test_estimated_minute_usage_distributes_delta_and_is_idempotent(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            with patch.object(history, "DB_PATH", Path(directory) / "usage.db"):
                usage_day = date(2026, 7, 13)
                first = datetime(2026, 7, 13, 10, 0, 10)
                second = datetime(2026, 7, 13, 10, 3, 10)
                totals = {
                    "PROMPT_CACHE_HIT_TOKEN": 3,
                    "PROMPT_CACHE_MISS_TOKEN": 2,
                    "RESPONSE_TOKEN": 1,
                }
                self.assertEqual(
                    history.save_estimated_minute_usage("mimo", usage_day, totals, first),
                    "baseline",
                )
                totals["PROMPT_CACHE_HIT_TOKEN"] = 8
                totals["PROMPT_CACHE_MISS_TOKEN"] = 5
                self.assertEqual(
                    history.save_estimated_minute_usage("mimo", usage_day, totals, second),
                    "recorded",
                )
                rows = history.minute_usage_for_day("mimo", usage_day)
                by_type = {}
                for row in rows:
                    by_type[row["token_type"]] = by_type.get(row["token_type"], 0) + row["token_amount"]
                self.assertEqual(by_type["PROMPT_CACHE_HIT_TOKEN"], 5)
                self.assertEqual(by_type["PROMPT_CACHE_MISS_TOKEN"], 3)
                self.assertNotIn("RESPONSE_TOKEN", by_type)
                self.assertEqual(
                    history.save_estimated_minute_usage("mimo", usage_day, totals, second),
                    "unchanged",
                )
                self.assertEqual(history.minute_usage_for_day("mimo", usage_day), rows)

    def test_minute_cleanup_keeps_daily_history_and_rolls_back_on_failure(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            with patch.object(history, "DB_PATH", Path(directory) / "usage.db"):
                old_day = date(2026, 7, 12)
                current_day = date(2026, 7, 13)
                totals = {token_type: 0 for token_type in history.MINUTE_TOKEN_TYPES}
                history.save_estimated_minute_usage(
                    "deepseek", old_day, totals, datetime(2026, 7, 12, 10, 0)
                )
                totals["RESPONSE_TOKEN"] = 4
                history.save_estimated_minute_usage(
                    "deepseek", old_day, totals, datetime(2026, 7, 12, 10, 1)
                )
                history.save_usage([payload(old_day.isoformat(), 12)], [])
                current_totals = {token_type: 0 for token_type in history.MINUTE_TOKEN_TYPES}
                history.save_estimated_minute_usage(
                    "deepseek", current_day, current_totals, datetime(2026, 7, 13, 10, 0)
                )
                current_totals["RESPONSE_TOKEN"] = 2
                history.save_estimated_minute_usage(
                    "deepseek", current_day, current_totals, datetime(2026, 7, 13, 10, 1)
                )
                history.clear_expired_minute_usage("deepseek", current_day, 1)
                self.assertEqual(history.minute_usage_for_day("deepseek", old_day), [])
                self.assertTrue(history.minute_usage_for_day("deepseek", current_day))
                self.assertEqual(history.recent_daily(30_000)[0]["tokens"], 12)

                # 第二个 DELETE 触发失败时，第一个 DELETE 也必须由事务回滚。
                history.save_estimated_minute_usage(
                    "deepseek", old_day, totals, datetime(2026, 7, 12, 10, 2)
                )
                totals["RESPONSE_TOKEN"] = 5
                history.save_estimated_minute_usage(
                    "deepseek", old_day, totals, datetime(2026, 7, 12, 10, 3)
                )
                connection = sqlite3.connect(history.DB_PATH)
                try:
                    connection.execute(
                        """CREATE TRIGGER abort_snapshot_cleanup
                             BEFORE DELETE ON minute_usage_snapshot
                             WHEN OLD.provider = 'deepseek'
                             BEGIN SELECT RAISE(ABORT, 'test rollback'); END"""
                    )
                    connection.commit()
                finally:
                    connection.close()
                with self.assertRaises(sqlite3.DatabaseError):
                    history.clear_expired_minute_usage("deepseek", current_day, 1)
                self.assertTrue(history.minute_usage_for_day("deepseek", old_day))

    def test_minute_cleanup_keeps_configured_retention_days(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            with patch.object(history, "DB_PATH", Path(directory) / "usage.db"):
                current_day = date(2026, 7, 13)
                expired_day = current_day - timedelta(days=3)
                retained_day = current_day - timedelta(days=2)
                totals = {token_type: 0 for token_type in history.MINUTE_TOKEN_TYPES}
                for usage_day in (expired_day, retained_day):
                    history.save_estimated_minute_usage(
                        "deepseek",
                        usage_day,
                        totals,
                        datetime.combine(usage_day, datetime.min.time()),
                        retention_days=365,
                    )
                    totals["RESPONSE_TOKEN"] += 1
                    history.save_estimated_minute_usage(
                        "deepseek",
                        usage_day,
                        totals,
                        datetime.combine(usage_day, datetime.min.time()) + timedelta(minutes=1),
                        retention_days=365,
                    )

                history.clear_expired_minute_usage("deepseek", current_day, 3)

                self.assertEqual(history.minute_usage_for_day("deepseek", expired_day), [])
                self.assertTrue(history.minute_usage_for_day("deepseek", retained_day))

    def test_minute_usage_dates_include_snapshot_only_days_and_are_provider_scoped(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            with patch.object(history, "DB_PATH", Path(directory) / "usage.db"):
                totals = {token_type: 0 for token_type in history.MINUTE_TOKEN_TYPES}
                for provider, usage_day in (
                    ("mimo", date(2026, 7, 13)),
                    ("mimo", date(2026, 7, 12)),
                    ("deepseek", date(2026, 7, 11)),
                ):
                    history.save_estimated_minute_usage(
                        provider,
                        usage_day,
                        totals,
                        datetime.combine(usage_day, datetime.min.time()),
                    )

                self.assertEqual(
                    history.minute_usage_dates("mimo"),
                    ["2026-07-12", "2026-07-13"],
                )
                self.assertEqual(history.minute_usage_dates("deepseek"), ["2026-07-11"])


if __name__ == "__main__":
    unittest.main()
