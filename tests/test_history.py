import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

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

    def test_non_finite_usage_cannot_poison_valid_history(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            with patch.object(history, "DB_PATH", Path(directory) / "usage.db"):
                for amount in ("NaN", "sNaN", "Infinity", "-Infinity"):
                    with self.subTest(amount=amount):
                        history.save_usage(
                            [payload("2099-01-01", amount), payload("2099-01-01", 7)],
                            [payload("2099-01-01", amount), payload("2099-01-01", ".25")],
                            provider="synthetic",
                        )
                        rows = history.recent_daily(30_000, "synthetic")
                        self.assertEqual(rows[0]["tokens"], 7)
                        self.assertEqual(rows[0]["cost_cny"], Decimal(".25"))
                        self.assertEqual(history.total_cost("synthetic"), Decimal(".25"))

    def test_nonfinite_legacy_cached_costs_do_not_poison_aggregated_views(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            with patch.object(history, "DB_PATH", Path(directory) / "usage.db"):
                cost = payload("2099-01-01", ".25")
                cost["days"][0]["data"][0]["usage"][0]["type"] = "cost_cny"
                history.save_usage([payload("2099-01-01", 7)], [cost])
                with history._connect() as connection:
                    for index, value in enumerate(("NaN", "Infinity", "-Infinity", "sNaN")):
                        connection.execute(
                            "INSERT INTO daily_usage VALUES (?, ?, ?, ?, ?, ?, ?)",
                            ("2099-01-01", f"old-{index}", "cost_cny", 0, value, "2099-01-01", "deepseek"),
                        )
                self.assertEqual(history.total_cost("deepseek"), Decimal(".25"))
                rows = history.recent_daily(30_000, "deepseek")
                self.assertEqual(rows[0]["tokens"], 7)
                self.assertEqual(rows[0]["cost_cny"], Decimal(".25"))
                models = history.recent_daily_model_usage("deepseek", date(2099, 1, 1), 1)[0]["models"]
                self.assertEqual(sum(row["cost_cny"] for row in models), Decimal(".25"))

    def test_normalized_cost_replaces_legacy_token_cost_without_double_counting(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            with patch.object(history, "DB_PATH", Path(directory) / "usage.db"):
                history.save_usage([payload("2099-01-01", 7)], [payload("2099-01-01", ".25")])
                cost = payload("2099-01-01", ".3")
                cost["days"][0]["data"][0]["usage"][0]["type"] = "cost_cny"
                history.save_usage([], [cost])
                self.assertEqual(history.total_cost("deepseek"), Decimal(".3"))
                rows = history.recent_daily(30_000, "deepseek")
                self.assertEqual(rows[0]["tokens"], 7)
                self.assertEqual(rows[0]["cost_cny"], Decimal(".3"))

    def test_existing_mixed_cost_rows_are_read_once_before_resync(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            with patch.object(history, "DB_PATH", Path(directory) / "usage.db"):
                history.save_usage([payload("2099-01-01", 7)], [payload("2099-01-01", ".25")])
                with history._connect() as connection:
                    connection.execute(
                        "INSERT INTO daily_usage VALUES (?, ?, ?, ?, ?, ?, ?)",
                        ("2099-01-01", "deepseek-test", "cost_cny", 0, ".3", "2099-01-01", "deepseek"),
                    )
                self.assertEqual(history.total_cost("deepseek"), Decimal(".3"))
                self.assertEqual(history.recent_daily(30_000, "deepseek")[0]["cost_cny"], Decimal(".3"))
                models = history.recent_daily_model_usage("deepseek", date(2099, 1, 1), 1)[0]["models"]
                self.assertEqual(models[0]["cost_cny"], Decimal(".3"))
                exported = history.provider_daily_payloads("deepseek", date(2099, 1, 1), date(2099, 1, 1))
                history.save_usage([{"days": exported}], [{"days": exported}], provider="copy")
                self.assertEqual(history.total_cost("copy"), Decimal(".3"))

    def test_failed_normalized_cost_write_preserves_previous_cost_and_tokens(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            with patch.object(history, "DB_PATH", Path(directory) / "usage.db"):
                history.save_usage([payload("2099-01-01", 7)], [payload("2099-01-01", ".25")])
                with history._connect() as connection:
                    connection.execute("""CREATE TRIGGER reject_normalized BEFORE INSERT ON daily_usage
                        WHEN NEW.token_type = 'cost_cny' BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END""")
                cost = payload("2099-01-01", ".3")
                cost["days"][0]["data"][0]["usage"][0]["type"] = "cost_cny"
                with self.assertRaises(sqlite3.DatabaseError):
                    history.save_usage([], [cost])
                self.assertEqual(history.total_cost("deepseek"), Decimal(".25"))
                self.assertEqual(history.recent_daily(30_000, "deepseek")[0]["tokens"], 7)

    def test_normalized_payload_never_counts_tokens_as_money_when_cost_is_missing(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            with patch.object(history, "DB_PATH", Path(directory) / "usage.db"):
                combined = [payload("2099-01-01", 700)]
                history.save_usage(combined, combined, costs_are_normalized=True)
                self.assertEqual(history.total_cost("deepseek"), Decimal("0"))
                self.assertEqual(history.recent_daily(30_000, "deepseek")[0]["tokens"], 700)

    def test_mixed_normalized_models_do_not_turn_missing_model_cost_into_tokens(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            with patch.object(history, "DB_PATH", Path(directory) / "usage.db"):
                combined = payload("2099-01-01", 700)
                combined["days"][0]["data"].append({"model": "priced", "usage": [{"type": "cost_cny", "amount": ".25"}]})
                history.save_usage([combined], [combined])
                self.assertEqual(history.total_cost("deepseek"), Decimal(".25"))

    def test_daily_models_preserve_legacy_costs_without_normalized_rows(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            with patch.object(history, "DB_PATH", Path(directory) / "usage.db"):
                history.save_usage([payload("2099-01-01", 7)], [payload("2099-01-01", ".25")])
                models = history.recent_daily_model_usage("deepseek", date(2099, 1, 1), 1)[0]["models"]
                self.assertEqual(models[0]["cost_cny"], Decimal(".25"))

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
                self.assertIn("minute_cost_usage", tables)
                self.assertIn("minute_cost_snapshot", tables)
                with closing(sqlite3.connect(db_path)) as schema_connection:
                    self.assertEqual(
                        [
                            row[1]
                            for row in schema_connection.execute(
                                "PRAGMA table_info(minute_usage)"
                            )
                            if row[5]
                        ],
                        ["provider", "usage_date", "minute_index", "token_type"],
                    )

    def test_failed_schema_upgrade_rolls_back_legacy_data_and_can_retry(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            db_path = Path(directory) / "usage.db"
            with closing(sqlite3.connect(db_path)) as connection:
                connection.executescript("""
                    CREATE TABLE daily_usage (
                        usage_date TEXT, model TEXT, token_type TEXT, token_amount INTEGER,
                        cost_cny TEXT, fetched_at TEXT,
                        PRIMARY KEY (usage_date, model, token_type)
                    );
                    INSERT INTO daily_usage VALUES ('2099-01-01', 'model', 'RESPONSE_TOKEN', 12, '.125', '2099-01-01');
                """)
            connect = sqlite3.connect

            def fail_late_in_upgrade(*args, **kwargs):
                connection = connect(*args, **kwargs)
                connection.set_authorizer(lambda action, name, *_rest: (
                    sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_INDEX
                    and name == "idx_minute_usage_provider_date" else sqlite3.SQLITE_OK
                ))
                return connection

            with patch.object(history, "DB_PATH", db_path):
                with patch.object(history.sqlite3, "connect", side_effect=fail_late_in_upgrade):
                    with self.assertRaises(sqlite3.DatabaseError):
                        history.recent_daily(30_000)
                with closing(connect(db_path)) as connection:
                    self.assertNotIn("provider", [row[1] for row in connection.execute("PRAGMA table_info(daily_usage)")])
                    self.assertEqual(connection.execute("SELECT token_amount FROM daily_usage").fetchone()[0], 12)
                    self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
                rows = history.recent_daily(30_000)
                self.assertEqual(rows[0]["tokens"], 12)
                self.assertEqual(rows[0]["cost_cny"], Decimal(".125"))

    def test_backup_usage_database_creates_verified_pre_update_snapshot(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            db_path = Path(directory) / "usage.db"
            with patch.object(history, "DB_PATH", db_path):
                history.save_usage(
                    [payload("2026-08-10", 12)],
                    [payload("2026-08-10", ".125")],
                    provider="deepseek",
                )
                backup_path = history.backup_usage_database(
                    "1.12.0", datetime(2026, 8, 10, 18, 30)
                )

            self.assertIsNotNone(backup_path)
            assert backup_path is not None
            self.assertEqual(backup_path.parent, db_path.parent / "backups")
            self.assertEqual(
                backup_path.name,
                "usage-before-update-v1.12.0-20260810183000000000.db",
            )
            with closing(sqlite3.connect(backup_path)) as connection:
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(
                    connection.execute(
                        "SELECT SUM(token_amount) FROM daily_usage WHERE provider = 'deepseek'"
                    ).fetchone()[0],
                    12,
                )
            self.assertFalse(
                backup_path.with_suffix(backup_path.suffix + ".tmp").exists()
            )

    def test_backup_usage_database_skips_missing_database(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            db_path = Path(directory) / "usage.db"
            with patch.object(history, "DB_PATH", db_path):
                self.assertIsNone(history.backup_usage_database("1.12.0"))
            self.assertFalse((db_path.parent / "backups").exists())

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

    def test_long_same_day_gap_distributes_all_token_and_cost_delta(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            with patch.object(history, "DB_PATH", Path(directory) / "usage.db"):
                usage_day = date(2026, 8, 13)
                first = datetime(2026, 8, 13, 14, 28)
                second = datetime(2026, 8, 13, 17, 1)
                initial = {
                    "PROMPT_CACHE_HIT_TOKEN": 1_000_000,
                    "PROMPT_CACHE_MISS_TOKEN": 2_000_000,
                    "RESPONSE_TOKEN": 3_000_000,
                }
                current = {
                    "PROMPT_CACHE_HIT_TOKEN": 1_400_000,
                    "PROMPT_CACHE_MISS_TOKEN": 2_300_000,
                    "RESPONSE_TOKEN": 3_126_700,
                }
                history.save_estimated_minute_usage(
                    "mimo", usage_day, initial, first, cost_cny=Decimal("10")
                )
                self.assertEqual(
                    history.save_estimated_minute_usage(
                        "mimo", usage_day, current, second, cost_cny=Decimal("12.53")
                    ),
                    "recorded",
                )

                token_rows = history.minute_usage_for_day("mimo", usage_day)
                self.assertEqual({row["minute"] for row in token_rows}, set(range(869, 1022)))
                self.assertEqual(sum(row["token_amount"] for row in token_rows), 826_700)
                cost_rows = history.minute_cost_usage_for_day("mimo", usage_day)
                self.assertEqual([row["minute"] for row in cost_rows], list(range(869, 1022)))
                self.assertEqual(sum(row["cost_cny"] for row in cost_rows), Decimal("2.53"))

    def test_same_day_recovery_keeps_persisted_baseline_and_provider_isolation(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            db_path = Path(directory) / "usage.db"
            usage_day = date(2026, 8, 13)
            totals = {token_type: 0 for token_type in history.MINUTE_TOKEN_TYPES}
            with patch.object(history, "DB_PATH", db_path):
                history.save_estimated_minute_usage(
                    "mimo", usage_day, totals, datetime(2026, 8, 13, 10, 0)
                )
                # A later call uses a fresh SQLite connection, matching restart,
                # sleep, network recovery and authentication recovery semantics.
                recovered = dict(totals)
                recovered["RESPONSE_TOKEN"] = 20
                self.assertEqual(
                    history.save_estimated_minute_usage(
                        "mimo", usage_day, recovered, datetime(2026, 8, 13, 10, 20)
                    ),
                    "recorded",
                )
                history.save_estimated_minute_usage(
                    "deepseek", usage_day, totals, datetime(2026, 8, 13, 10, 0)
                )
                deepseek = dict(totals)
                deepseek["RESPONSE_TOKEN"] = 7
                history.save_estimated_minute_usage(
                    "deepseek", usage_day, deepseek, datetime(2026, 8, 13, 10, 1)
                )

                self.assertEqual(
                    sum(
                        row["token_amount"]
                        for row in history.minute_usage_for_day("mimo", usage_day)
                    ),
                    20,
                )
                self.assertEqual(
                    sum(
                        row["token_amount"]
                        for row in history.minute_usage_for_day("deepseek", usage_day)
                    ),
                    7,
                )

    def test_concurrent_provider_first_access_initializes_shared_database_safely(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            with patch.object(history, "DB_PATH", Path(directory) / "usage.db"):
                usage_day = date(2026, 8, 13)
                totals = {token_type: 0 for token_type in history.MINUTE_TOKEN_TYPES}
                barrier = threading.Barrier(2)
                errors = []

                def save(provider):
                    try:
                        barrier.wait()
                        history.save_estimated_minute_usage(
                            provider,
                            usage_day,
                            totals,
                            datetime(2026, 8, 13, 10, 0),
                        )
                    except Exception as exc:
                        errors.append(exc)

                threads = [
                    threading.Thread(target=save, args=(provider,))
                    for provider in ("deepseek", "mimo")
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()

                self.assertEqual(errors, [])
                self.assertEqual(history.minute_usage_dates("deepseek"), ["2026-08-13"])
                self.assertEqual(history.minute_usage_dates("mimo"), ["2026-08-13"])

    def test_zero_delta_cross_day_and_adjustment_only_rebuild_baseline(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            with patch.object(history, "DB_PATH", Path(directory) / "usage.db"):
                usage_day = date(2026, 8, 13)
                totals = {token_type: 10 for token_type in history.MINUTE_TOKEN_TYPES}
                first = datetime(2026, 8, 13, 10, 0)
                self.assertEqual(
                    history.save_estimated_minute_usage("mimo", usage_day, totals, first),
                    "baseline",
                )
                self.assertEqual(
                    history.save_estimated_minute_usage(
                        "mimo", usage_day, totals, first + timedelta(minutes=1)
                    ),
                    "unchanged",
                )
                adjusted = dict(totals)
                adjusted["RESPONSE_TOKEN"] = 9
                self.assertEqual(
                    history.save_estimated_minute_usage(
                        "mimo", usage_day, adjusted, first + timedelta(minutes=2)
                    ),
                    "adjusted",
                )
                self.assertEqual(
                    history.save_estimated_minute_usage(
                        "mimo", usage_day, adjusted, first + timedelta(days=1)
                    ),
                    "cross_day",
                )
                self.assertEqual(history.minute_usage_for_day("mimo", usage_day), [])

    def test_minute_cleanup_keeps_daily_history_and_rolls_back_on_failure(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            with patch.object(history, "DB_PATH", Path(directory) / "usage.db"):
                old_day = date(2026, 7, 11)
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

    def test_minute_cleanup_uses_double_retention_grace_and_logs_deleted_rows(self):
        for retention_days in (15, 30):
            with self.subTest(retention_days=retention_days):
                with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
                    with patch.object(history, "DB_PATH", Path(directory) / "usage.db"):
                        current_day = date(2026, 8, 10)
                        effective_days = retention_days * 2
                        expired_day = current_day - timedelta(days=effective_days)
                        retained_day = current_day - timedelta(days=effective_days - 1)
                        totals = {
                            token_type: 0 for token_type in history.MINUTE_TOKEN_TYPES
                        }
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
                                datetime.combine(usage_day, datetime.min.time())
                                + timedelta(minutes=1),
                                retention_days=365,
                            )

                        logger = Mock()
                        with patch.object(
                            history.config_manager, "logger", return_value=logger
                        ):
                            deleted_rows = history.clear_expired_minute_usage(
                                "deepseek", current_day, retention_days
                            )

                        self.assertGreater(deleted_rows, 0)
                        self.assertEqual(
                            history.minute_usage_for_day("deepseek", expired_day), []
                        )
                        self.assertTrue(
                            history.minute_usage_for_day("deepseek", retained_day)
                        )
                        logger.info.assert_called_once()
                        log_args = logger.info.call_args.args
                        self.assertEqual(log_args[1], "deepseek")
                        self.assertEqual(log_args[2], retained_day.isoformat())
                        self.assertEqual(log_args[3], retention_days)
                        self.assertEqual(log_args[4], effective_days)
                        self.assertEqual(log_args[5], deleted_rows)

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

    def test_estimated_minute_cost_usage_tracks_baselines_and_exact_distribution(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            with patch.object(history, "DB_PATH", Path(directory) / "usage.db"):
                usage_day = date(2026, 7, 13)
                totals = {token_type: 0 for token_type in history.MINUTE_TOKEN_TYPES}
                first = datetime(2026, 7, 13, 10, 0)
                second = datetime(2026, 7, 13, 10, 3)
                history.save_estimated_minute_usage(
                    "mimo", usage_day, totals, first, cost_cny=Decimal("1.00")
                )
                totals["RESPONSE_TOKEN"] = 3
                history.save_estimated_minute_usage(
                    "mimo", usage_day, totals, second, cost_cny=Decimal("2.00")
                )
                rows = history.minute_cost_usage_for_day("mimo", usage_day)
                self.assertEqual([row["minute"] for row in rows], [601, 602, 603])
                self.assertEqual(sum(row["cost_cny"] for row in rows), Decimal("1.00"))
                self.assertEqual(
                    rows[-1]["cost_cny"],
                    Decimal("1.00") - rows[0]["cost_cny"] - rows[1]["cost_cny"],
                )

                zero_totals = {token_type: 0 for token_type in history.MINUTE_TOKEN_TYPES}
                history.save_estimated_minute_usage(
                    "deepseek", usage_day, zero_totals, first, cost_cny=Decimal(".50")
                )
                history.save_estimated_minute_usage(
                    "deepseek", usage_day, zero_totals, second, cost_cny=Decimal(".50")
                )
                self.assertEqual(
                    history.minute_cost_usage_for_day("deepseek", usage_day), []
                )

                history.save_estimated_minute_usage(
                    "missing", usage_day, zero_totals, first, cost_cny=Decimal(".50")
                )
                history.save_estimated_minute_usage(
                    "missing", usage_day, zero_totals, second, cost_cny=None
                )
                history.save_estimated_minute_usage(
                    "missing", usage_day, zero_totals, second + timedelta(minutes=1), cost_cny=Decimal("1.00")
                )
                self.assertEqual(history.minute_cost_usage_for_day("missing", usage_day), [])

                history.save_estimated_minute_usage(
                    "adjusted", usage_day, zero_totals, first, cost_cny=Decimal(".50")
                )
                history.save_estimated_minute_usage(
                    "adjusted", usage_day, zero_totals, second, cost_cny=Decimal(".40")
                )
                self.assertEqual(history.minute_cost_usage_for_day("adjusted", usage_day), [])
                history.save_estimated_minute_usage(
                    "adjusted", usage_day + timedelta(days=1), zero_totals,
                    first + timedelta(days=1), cost_cny=Decimal(".40"),
                )
                self.assertEqual(
                    history.minute_cost_usage_for_day("adjusted", usage_day + timedelta(days=1)), []
                )
                self.assertEqual(
                    history.minute_usage_dates("mimo"), [usage_day.isoformat()]
                )

                history.save_estimated_minute_usage(
                    "cross-day", usage_day, zero_totals, first, cost_cny=Decimal(".50")
                )
                self.assertEqual(
                    history.save_estimated_minute_usage(
                        "cross-day", usage_day, zero_totals,
                        first + timedelta(days=1), cost_cny=Decimal("1.00"),
                    ),
                    "cross_day",
                )
                self.assertEqual(history.minute_cost_usage_for_day("cross-day", usage_day), [])

    def test_minute_cost_cleanup_and_transaction_are_provider_scoped(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as directory:
            with patch.object(history, "DB_PATH", Path(directory) / "usage.db"):
                old_day = date(2026, 7, 11)
                current_day = date(2026, 7, 13)
                totals = {token_type: 0 for token_type in history.MINUTE_TOKEN_TYPES}
                for provider, usage_day in (("mimo", old_day), ("deepseek", old_day)):
                    history.save_estimated_minute_usage(
                        provider, usage_day, totals, datetime.combine(usage_day, datetime.min.time()),
                        cost_cny=Decimal("1"),
                    )
                    history.save_estimated_minute_usage(
                        provider, usage_day, totals,
                        datetime.combine(usage_day, datetime.min.time()) + timedelta(minutes=1),
                        cost_cny=Decimal("2"),
                    )
                history.clear_expired_minute_usage("mimo", current_day, 1)
                self.assertEqual(history.minute_cost_usage_for_day("mimo", old_day), [])
                self.assertTrue(history.minute_cost_usage_for_day("deepseek", old_day))

                history.save_estimated_minute_usage(
                    "mimo", current_day, totals, datetime(2026, 7, 13, 10, 0), cost_cny=Decimal("1")
                )
                totals["RESPONSE_TOKEN"] = 4
                connection = sqlite3.connect(history.DB_PATH)
                try:
                    connection.execute(
                        """CREATE TRIGGER abort_minute_cost_write
                             BEFORE INSERT ON minute_cost_usage
                             WHEN NEW.provider = 'mimo'
                             BEGIN SELECT RAISE(ABORT, 'test rollback'); END"""
                    )
                    connection.commit()
                finally:
                    connection.close()
                with self.assertRaises(sqlite3.DatabaseError):
                    history.save_estimated_minute_usage(
                        "mimo", current_day, totals, datetime(2026, 7, 13, 10, 1), cost_cny=Decimal("2")
                    )
                self.assertEqual(history.minute_usage_for_day("mimo", current_day), [])
                self.assertEqual(history.minute_cost_usage_for_day("mimo", current_day), [])


if __name__ == "__main__":
    unittest.main()
