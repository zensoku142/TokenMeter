import os
import sqlite3
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
