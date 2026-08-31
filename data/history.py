"""SQLite cache for normalized per-provider daily usage history."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing, contextmanager, nullcontext
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator

from config import runtime as config_manager

DB_PATH = config_manager.CONFIG_DIR / "usage.db"
MINUTE_USAGE_CLEANUP_GRACE_MULTIPLIER = 2
_SCHEMA_LOCK = threading.Lock()

MINUTE_TOKEN_TYPES = (
    "PROMPT_CACHE_HIT_TOKEN",
    "PROMPT_CACHE_MISS_TOKEN",
    "RESPONSE_TOKEN",
)

_DAILY_USAGE_DDL = """
CREATE TABLE daily_usage (
    usage_date TEXT NOT NULL,
    model TEXT NOT NULL,
    token_type TEXT NOT NULL,
    token_amount INTEGER NOT NULL DEFAULT 0,
    cost_cny TEXT NOT NULL DEFAULT '0',
    fetched_at TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'deepseek',
    PRIMARY KEY (usage_date, model, token_type, provider)
)
"""


def _ensure_daily_usage_schema(connection: sqlite3.Connection) -> None:
    columns = connection.execute("PRAGMA table_info(daily_usage)").fetchall()
    if not columns:
        connection.execute(_DAILY_USAGE_DDL)
        return
    names = {str(row[1]) for row in columns}
    primary_key = [
        str(row[1]) for row in sorted(columns, key=lambda row: int(row[5])) if int(row[5])
    ]
    expected_key = ["usage_date", "model", "token_type", "provider"]
    if "provider" in names and primary_key == expected_key:
        return

    # SQLite 不能直接扩展主键；迁移时保留旧账单，并把 v1.0 数据归到 DeepSeek。
    connection.execute("ALTER TABLE daily_usage RENAME TO daily_usage_legacy")
    connection.execute(_DAILY_USAGE_DDL)
    provider_expr = "COALESCE(provider, 'deepseek')" if "provider" in names else "'deepseek'"
    connection.execute(
        f"""INSERT OR REPLACE INTO daily_usage
               (usage_date, model, token_type, token_amount, cost_cny, fetched_at, provider)
             SELECT usage_date, model, token_type, token_amount, cost_cny, fetched_at,
                    {provider_expr}
             FROM daily_usage_legacy"""
    )
    connection.execute("DROP TABLE daily_usage_legacy")


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=5)
    try:
        # 多 Provider 首次并发访问时只串行化建表/旧结构检查；实际读写事务
        # 仍由各自连接独立执行。
        with _SCHEMA_LOCK, connection:
            # 版本仅在建表/迁移成功后写入；普通查询不再反复执行整套 DDL。
            if connection.execute("PRAGMA user_version").fetchone()[0] < 1:
                connection.execute("PRAGMA journal_mode=WAL")
                _ensure_daily_usage_schema(connection)
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS sync_state (
                        provider TEXT PRIMARY KEY,
                        last_success_at TEXT,
                        last_error TEXT
                    );
                    CREATE TABLE IF NOT EXISTS monthly_sync (
                        provider TEXT NOT NULL,
                        year INTEGER NOT NULL,
                        month INTEGER NOT NULL,
                        last_success_at TEXT NOT NULL,
                        PRIMARY KEY (provider, year, month)
                    );
                    CREATE TABLE IF NOT EXISTS minute_usage (
                        provider TEXT NOT NULL,
                        usage_date TEXT NOT NULL,
                        minute_index INTEGER NOT NULL,
                        token_type TEXT NOT NULL,
                        token_amount INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (provider, usage_date, minute_index, token_type)
                    );
                    CREATE INDEX IF NOT EXISTS idx_minute_usage_provider_date
                        ON minute_usage(provider, usage_date);
                    CREATE TABLE IF NOT EXISTS minute_usage_snapshot (
                        provider TEXT NOT NULL,
                        usage_date TEXT NOT NULL,
                        token_type TEXT NOT NULL,
                        token_amount INTEGER NOT NULL DEFAULT 0,
                        observed_at TEXT NOT NULL,
                        PRIMARY KEY (provider, usage_date, token_type)
                    );
                    CREATE INDEX IF NOT EXISTS idx_minute_snapshot_provider_date
                        ON minute_usage_snapshot(provider, usage_date);
                    CREATE TABLE IF NOT EXISTS minute_cost_usage (
                        provider TEXT NOT NULL,
                        usage_date TEXT NOT NULL,
                        minute_index INTEGER NOT NULL,
                        cost_cny TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (provider, usage_date, minute_index)
                    );
                    CREATE INDEX IF NOT EXISTS idx_minute_cost_usage_provider_date
                        ON minute_cost_usage(provider, usage_date);
                    CREATE TABLE IF NOT EXISTS minute_model_usage (
                        provider TEXT NOT NULL,
                        usage_date TEXT NOT NULL,
                        minute_index INTEGER NOT NULL,
                        model TEXT NOT NULL,
                        cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
                        cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
                        output_tokens INTEGER NOT NULL DEFAULT 0,
                        cost_cny TEXT NOT NULL DEFAULT '0',
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (provider, usage_date, minute_index, model)
                    );
                    CREATE INDEX IF NOT EXISTS idx_minute_model_usage_provider_date
                        ON minute_model_usage(provider, usage_date);
                    CREATE TABLE IF NOT EXISTS minute_cost_snapshot (
                        provider TEXT NOT NULL,
                        usage_date TEXT NOT NULL,
                        cost_cny TEXT,
                        observed_at TEXT NOT NULL,
                        PRIMARY KEY (provider, usage_date)
                    );
                    CREATE INDEX IF NOT EXISTS idx_minute_cost_snapshot_provider_date
                        ON minute_cost_snapshot(provider, usage_date);
                    """
                )
                connection.execute("PRAGMA user_version = 1")
        with connection:
            yield connection
    finally:
        # sqlite Connection 的上下文只处理事务，文件句柄仍需显式关闭。
        connection.close()


def _ensure_provider_quota_snapshot_schema(connection: sqlite3.Connection) -> None:
    # 额度快照是新增的可选缓存，按需建表，避免只读历史查询被一次迁移阻断。
    connection.execute(
        """CREATE TABLE IF NOT EXISTS provider_quota_snapshot (
               provider TEXT NOT NULL,
               account_key TEXT NOT NULL,
               payload TEXT NOT NULL,
               saved_at TEXT NOT NULL,
               PRIMARY KEY (provider, account_key)
           )"""
    )


def save_provider_quota_snapshot(
    provider: str,
    account_key: str,
    payload: dict[str, Any],
    saved_at: datetime,
) -> None:
    if not provider or not account_key:
        return
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with _connect() as connection:
        _ensure_provider_quota_snapshot_schema(connection)
        connection.execute(
            """INSERT INTO provider_quota_snapshot(provider, account_key, payload, saved_at)
                 VALUES (?, ?, ?, ?)
                 ON CONFLICT(provider, account_key) DO UPDATE SET
                   payload = excluded.payload,
                   saved_at = excluded.saved_at""",
            (provider, account_key, serialized, saved_at.isoformat()),
        )


def load_provider_quota_snapshot(
    provider: str, account_key: str
) -> tuple[dict[str, Any], datetime] | None:
    if not provider or not account_key:
        return None
    with _connect() as connection:
        _ensure_provider_quota_snapshot_schema(connection)
        row = connection.execute(
            """SELECT payload, saved_at FROM provider_quota_snapshot
                 WHERE provider = ? AND account_key = ?""",
            (provider, account_key),
        ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(str(row[0]))
        saved_at = datetime.fromisoformat(str(row[1]))
    except (TypeError, ValueError, json.JSONDecodeError):
        config_manager.logger().warning(
            "Skipped malformed provider quota snapshot: provider=%s", provider
        )
        return None
    return (payload, saved_at) if isinstance(payload, dict) else None


def backup_usage_database(
    release_version: str, observed_at: datetime | None = None
) -> Path | None:
    """Create a consistent pre-update SQLite snapshot outside the update cache."""

    source_path = DB_PATH.resolve(strict=False)
    if not source_path.is_file():
        return None
    safe_version = "".join(
        character if character.isalnum() or character in {".", "-", "_"} else "_"
        for character in str(release_version).strip()
    ) or "unknown"
    timestamp = (observed_at or datetime.now()).strftime("%Y%m%d%H%M%S%f")
    backup_dir = source_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"usage-before-update-v{safe_version}-{timestamp}.db"
    temporary_path = backup_path.with_suffix(backup_path.suffix + ".tmp")
    source_uri = source_path.as_uri() + "?mode=ro"
    try:
        with (
            closing(sqlite3.connect(source_uri, uri=True, timeout=5)) as source,
            closing(sqlite3.connect(temporary_path, timeout=5)) as target,
        ):
            source.backup(target)
            result = target.execute("PRAGMA quick_check").fetchone()
            if not result or result[0] != "ok":
                raise sqlite3.DatabaseError("usage.db backup integrity check failed")
        temporary_path.replace(backup_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return backup_path


def needs_initial_sync(provider: str = "deepseek") -> bool:
    with _connect() as connection:
        row = connection.execute(
            "SELECT last_success_at FROM sync_state WHERE provider = ?", (provider,)
        ).fetchone()
    return not row or not row[0]


def scoped_provider(provider: str, account_key: str) -> str:
    if not account_key:
        return provider
    scoped = f"{provider}:{account_key}"
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS history_account (provider TEXT PRIMARY KEY, account_key TEXT NOT NULL)"
        )
        inserted = connection.execute(
            "INSERT OR IGNORE INTO history_account VALUES (?, ?)", (provider, account_key)
        ).rowcount
        if inserted:
            # 旧历史没有账号标识，只在首次升级时归给当前凭据；随后换号保留旧记录但不混用。
            for table in (
                "daily_usage", "sync_state", "monthly_sync", "minute_usage",
                "minute_usage_snapshot", "minute_cost_usage", "minute_model_usage",
                "minute_cost_snapshot",
            ):
                connection.execute(f"UPDATE {table} SET provider = ? WHERE provider = ?", (scoped, provider))
    return scoped


def _ensure_nayuto_cache(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS nayuto_records (
            provider TEXT NOT NULL, record_key TEXT NOT NULL,
            usage_date TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY (provider, record_key)
        );
        CREATE INDEX IF NOT EXISTS idx_nayuto_records_date ON nayuto_records(provider, usage_date);
        CREATE TABLE IF NOT EXISTS nayuto_sync (
            provider TEXT PRIMARY KEY, payload TEXT NOT NULL
        );
    """)


def load_nayuto_sync(provider: str) -> dict[str, Any]:
    with _connect() as connection:
        _ensure_nayuto_cache(connection)
        row = connection.execute("SELECT payload FROM nayuto_sync WHERE provider = ?", (provider,)).fetchone()
    return json.loads(row[0]) if row else {}


def save_nayuto_page(
    provider: str, rows: list[tuple[str, str, dict[str, Any]]], state: dict[str, Any],
) -> None:
    # 页面与断点同一事务提交；中断后重复读取重叠页可按记录 ID 幂等覆盖。
    with _connect() as connection:
        _ensure_nayuto_cache(connection)
        connection.executemany(
            "INSERT INTO nayuto_records VALUES (?, ?, ?, ?) ON CONFLICT(provider, record_key) "
            "DO UPDATE SET usage_date = excluded.usage_date, payload = excluded.payload",
            ((provider, key, day, json.dumps(payload, separators=(",", ":"))) for key, day, payload in rows),
        )
        connection.execute(
            "INSERT INTO nayuto_sync VALUES (?, ?) ON CONFLICT(provider) DO UPDATE SET payload = excluded.payload",
            (provider, json.dumps(state, separators=(",", ":"))),
        )


def nayuto_records(provider: str, earliest: date) -> Iterator[dict[str, Any]]:
    with _connect() as connection:
        _ensure_nayuto_cache(connection)
        for (payload,) in connection.execute(
            "SELECT payload FROM nayuto_records WHERE provider = ? AND usage_date >= ? ORDER BY usage_date",
            (provider, earliest.isoformat()),
        ):
            yield json.loads(payload)


def prune_nayuto_records(provider: str, earliest_needed: date) -> None:
    # 请求级缓存只支持年度明细重算；保留日聚合账单，不能无限积累逐请求 JSON。
    cutoff = min(earliest_needed, date.today() - timedelta(days=400)).isoformat()
    with _connect() as connection:
        connection.execute("DELETE FROM nayuto_records WHERE provider = ? AND usage_date < ?", (provider, cutoff))


def unsynced_months(
    months: list[tuple[int, int]], provider: str = "deepseek"
) -> list[tuple[int, int]]:
    if not months:
        return []
    with _connect() as connection:
        rows = connection.execute(
            "SELECT month, year FROM monthly_sync WHERE provider = ?", (provider,)
        ).fetchall()
    synced = {(int(month), int(year)) for month, year in rows}
    return [item for item in months if item not in synced]


def _rows(payloads: list[dict[str, Any]]):
    for payload in payloads:
        days = payload.get("days", [])
        if not isinstance(days, list):
            continue
        for day in days:
            if not isinstance(day, dict):
                continue
            usage_date = str(day.get("date", ""))
            try:
                date.fromisoformat(usage_date)
            except ValueError:
                continue
            items = day.get("data", [])
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                model = str(item.get("model", "unknown")).strip() or "unknown"
                usages = item.get("usage", [])
                if not isinstance(usages, list):
                    continue
                for usage in usages:
                    if not isinstance(usage, dict):
                        continue
                    token_type = str(usage.get("type", "")).strip()
                    try:
                        amount = Decimal(str(usage.get("amount", "0")))
                    except (InvalidOperation, ValueError):
                        continue
                    if token_type:
                        yield usage_date, model, token_type, amount


def _aggregated_rows(payloads: list[dict[str, Any]]):
    totals: dict[tuple[str, str, str], Decimal] = {}
    for usage_date, model, token_type, amount in _rows(payloads):
        key = (usage_date, model, token_type)
        totals[key] = totals.get(key, Decimal("0")) + amount
    for (usage_date, model, token_type), amount in totals.items():
        yield usage_date, model, token_type, amount


def save_usage(
    amount_payloads: list[dict[str, Any]],
    cost_payloads: list[dict[str, Any]],
    synced_months: list[tuple[int, int]] | None = None,
    provider: str = "deepseek",
) -> None:
    fetched_at = datetime.now().isoformat(timespec="seconds")
    amount_rows = list(_aggregated_rows(amount_payloads))
    cost_rows = list(_aggregated_rows(cost_payloads))
    normalized_costs = any(row[2] == "cost_cny" for row in cost_rows)
    with _connect() as connection:
        for usage_date, model, token_type, amount in amount_rows:
            if token_type == "cost_cny":
                continue
            connection.execute(
                """INSERT INTO daily_usage
                       (usage_date, model, token_type, token_amount, fetched_at, provider)
                     VALUES (?, ?, ?, ?, ?, ?)
                     ON CONFLICT(usage_date, model, token_type, provider) DO UPDATE SET
                       token_amount = excluded.token_amount,
                       fetched_at = excluded.fetched_at""",
                (usage_date, model, token_type, int(amount), fetched_at, provider),
            )
        for usage_date, model, token_type, amount in cost_rows:
            if normalized_costs and token_type != "cost_cny":
                continue
            connection.execute(
                """INSERT INTO daily_usage
                       (usage_date, model, token_type, cost_cny, fetched_at, provider)
                     VALUES (?, ?, ?, ?, ?, ?)
                     ON CONFLICT(usage_date, model, token_type, provider) DO UPDATE SET
                       cost_cny = excluded.cost_cny,
                       fetched_at = excluded.fetched_at""",
                (usage_date, model, token_type, str(amount), fetched_at, provider),
            )
        connection.execute(
            """INSERT INTO sync_state(provider, last_success_at, last_error)
                 VALUES (?, ?, NULL)
                 ON CONFLICT(provider) DO UPDATE SET
                   last_success_at = excluded.last_success_at, last_error = NULL""",
            (provider, fetched_at),
        )
        for month, year in dict.fromkeys(synced_months or []):
            connection.execute(
                """INSERT INTO monthly_sync(provider, year, month, last_success_at)
                     VALUES (?, ?, ?, ?)
                     ON CONFLICT(provider, year, month) DO UPDATE SET
                       last_success_at = excluded.last_success_at""",
                (provider, year, month, fetched_at),
            )


def _minute_index(value: datetime) -> int:
    return value.hour * 60 + value.minute


def _snapshot_rows(
    connection: sqlite3.Connection, provider: str, usage_date: str
) -> tuple[dict[str, int], datetime | None]:
    rows = connection.execute(
        """SELECT token_type, token_amount, observed_at
             FROM minute_usage_snapshot
            WHERE provider = ? AND usage_date = ?""",
        (provider, usage_date),
    ).fetchall()
    totals: dict[str, int] = {}
    observed_at: datetime | None = None
    for token_type, token_amount, raw_observed_at in rows:
        if str(token_type) not in MINUTE_TOKEN_TYPES:
            continue
        totals[str(token_type)] = int(token_amount or 0)
        if observed_at is None:
            try:
                observed_at = datetime.fromisoformat(str(raw_observed_at))
            except ValueError:
                return {}, None
    if set(totals) != set(MINUTE_TOKEN_TYPES):
        return {}, None
    return totals, observed_at


def _save_minute_snapshot(
    connection: sqlite3.Connection,
    provider: str,
    usage_date: str,
    totals: dict[str, int],
    observed_at: datetime,
) -> None:
    observed_text = observed_at.isoformat(timespec="seconds")
    for token_type in MINUTE_TOKEN_TYPES:
        connection.execute(
            """INSERT INTO minute_usage_snapshot
                   (provider, usage_date, token_type, token_amount, observed_at)
                 VALUES (?, ?, ?, ?, ?)
                 ON CONFLICT(provider, usage_date, token_type) DO UPDATE SET
                   token_amount = excluded.token_amount,
                   observed_at = excluded.observed_at""",
            (provider, usage_date, token_type, int(totals.get(token_type, 0)), observed_text),
        )


def _cost_snapshot_row(
    connection: sqlite3.Connection, provider: str, usage_date: str
) -> tuple[Decimal | None, datetime | None, bool]:
    row = connection.execute(
        """SELECT cost_cny, observed_at FROM minute_cost_snapshot
             WHERE provider = ? AND usage_date = ?""",
        (provider, usage_date),
    ).fetchone()
    if row is None:
        return None, None, False
    raw_cost, raw_observed_at = row
    try:
        observed_at = datetime.fromisoformat(str(raw_observed_at))
    except ValueError:
        return None, None, False
    if raw_cost is None:
        return None, observed_at, True
    try:
        cost = Decimal(str(raw_cost))
    except (InvalidOperation, ValueError):
        return None, None, False
    return (cost, observed_at, True) if cost.is_finite() else (None, None, False)


def _save_minute_cost_snapshot(
    connection: sqlite3.Connection,
    provider: str,
    usage_date: str,
    cost_cny: Decimal | None,
    observed_at: datetime,
) -> None:
    connection.execute(
        """INSERT INTO minute_cost_snapshot
               (provider, usage_date, cost_cny, observed_at)
             VALUES (?, ?, ?, ?)
             ON CONFLICT(provider, usage_date) DO UPDATE SET
               cost_cny = excluded.cost_cny,
               observed_at = excluded.observed_at""",
        (
            provider,
            usage_date,
            str(cost_cny) if cost_cny is not None else None,
            observed_at.isoformat(timespec="seconds"),
        ),
    )


def _minute_indexes(previous_at: datetime, observed_at: datetime) -> list[int]:
    start_minute = _minute_index(previous_at) + 1
    end_minute = _minute_index(observed_at)
    indexes = list(range(start_minute, end_minute + 1))
    return indexes or [end_minute]


def _add_minute_cost(
    connection: sqlite3.Connection,
    provider: str,
    usage_date: str,
    minute: int,
    amount: Decimal,
    updated_at: str,
) -> None:
    row = connection.execute(
        """SELECT cost_cny FROM minute_cost_usage
             WHERE provider = ? AND usage_date = ? AND minute_index = ?""",
        (provider, usage_date, minute),
    ).fetchone()
    if row is None:
        stored_amount = amount
    elif amount == 0:
        # 已有同一分钟的实际费用时，后续零增量不能覆盖它。
        stored_amount = Decimal(str(row[0]))
    else:
        stored_amount = Decimal(str(row[0])) + amount
    connection.execute(
        """INSERT INTO minute_cost_usage
               (provider, usage_date, minute_index, cost_cny, updated_at)
             VALUES (?, ?, ?, ?, ?)
             ON CONFLICT(provider, usage_date, minute_index) DO UPDATE SET
               cost_cny = excluded.cost_cny,
               updated_at = excluded.updated_at""",
        (provider, usage_date, minute, str(stored_amount), updated_at),
    )


def _save_estimated_minute_cost_usage(
    connection: sqlite3.Connection,
    provider: str,
    usage_date: str,
    cost_cny: Decimal | None,
    observed_at: datetime,
) -> None:
    previous, previous_at, has_snapshot = _cost_snapshot_row(
        connection, provider, usage_date
    )
    _save_minute_cost_snapshot(connection, provider, usage_date, cost_cny, observed_at)
    if (
        cost_cny is None
        or not has_snapshot
        or previous is None
        or previous_at is None
        or previous_at == observed_at
        or previous_at.date() != observed_at.date()
        or previous_at > observed_at
    ):
        return
    delta = cost_cny - previous
    if delta <= 0:
        return
    indexes = _minute_indexes(previous_at, observed_at)
    share = delta / len(indexes)
    allocated = Decimal("0")
    updated_at = observed_at.isoformat(timespec="seconds")
    for index, minute in enumerate(indexes):
        amount = delta - allocated if index == len(indexes) - 1 else share
        allocated += amount
        _add_minute_cost(
            connection, provider, usage_date, minute, amount, updated_at
        )


def _minute_usage_retention_threshold(current_day: date, retention_days: int) -> str:
    if retention_days < 1:
        raise ValueError("分时数据保存天数至少为 1 天")
    return (current_day - timedelta(days=retention_days - 1)).isoformat()


def _delete_expired_minute_usage(
    connection: sqlite3.Connection,
    provider: str,
    current_day: date,
    retention_days: int,
) -> tuple[str, dict[str, int]]:
    effective_days = retention_days * MINUTE_USAGE_CLEANUP_GRACE_MULTIPLIER
    threshold = _minute_usage_retention_threshold(current_day, effective_days)
    deleted_rows: dict[str, int] = {}
    for table in (
        "minute_usage",
        "minute_usage_snapshot",
        "minute_cost_usage",
        "minute_model_usage",
        "minute_cost_snapshot",
    ):
        cursor = connection.execute(
            f"DELETE FROM {table} WHERE provider = ? AND usage_date < ?",
            (provider, threshold),
        )
        deleted_rows[table] = max(0, int(cursor.rowcount))
    return threshold, deleted_rows


def _log_minute_usage_cleanup(
    provider: str,
    retention_days: int,
    threshold: str,
    deleted_rows: dict[str, int],
) -> None:
    total_deleted = sum(deleted_rows.values())
    if not total_deleted:
        return
    details = ", ".join(
        f"{table}={count}" for table, count in deleted_rows.items()
    )
    config_manager.logger().info(
        "Minute usage cleanup: provider=%s cutoff=%s configured_days=%s "
        "effective_days=%s deleted_rows=%s (%s)",
        provider,
        threshold,
        retention_days,
        retention_days * MINUTE_USAGE_CLEANUP_GRACE_MULTIPLIER,
        total_deleted,
        details,
    )


def clear_expired_minute_usage(
    provider: str, current_day: date, retention_days: int = 3
) -> int:
    """超过配置天数的双倍宽限期后，删除指定提供商的分时缓存。"""
    with _connect() as connection:
        threshold, deleted_rows = _delete_expired_minute_usage(
            connection, provider, current_day, retention_days
        )
    _log_minute_usage_cleanup(provider, retention_days, threshold, deleted_rows)
    return sum(deleted_rows.values())


def save_estimated_minute_usage(
    provider: str,
    usage_day: date,
    totals: dict[str, int],
    observed_at: datetime,
    retention_days: int = 3,
    cost_cny: Decimal | None = None,
) -> str:
    """保存一次按刷新间隔均摊的 Token 与费用差额。

    返回值用于界面状态：``baseline``、``recorded``、``unchanged``、
    ``adjusted`` 或 ``cross_day``。首次采样只保存累计快照，不能把此前
    已发生的使用量虚构到某一分钟。
    """
    usage_date = usage_day.isoformat()
    normalized = {
        token_type: max(0, int(totals.get(token_type, 0) or 0))
        for token_type in MINUTE_TOKEN_TYPES
    }
    if cost_cny is not None:
        try:
            cost_cny = Decimal(str(cost_cny))
        except (InvalidOperation, ValueError):
            cost_cny = None
        else:
            if not cost_cny.is_finite():
                cost_cny = None
    with _connect() as connection:
        cleanup_threshold, deleted_rows = _delete_expired_minute_usage(
            connection, provider, usage_day, retention_days
        )
        previous, previous_at = _snapshot_rows(connection, provider, usage_date)
        if previous_at is None:
            _save_minute_snapshot(connection, provider, usage_date, normalized, observed_at)
            status = "baseline"
        else:
            deltas = {
                token_type: normalized[token_type] - previous[token_type]
                for token_type in MINUTE_TOKEN_TYPES
            }
            if previous_at == observed_at and not any(deltas.values()):
                status = "unchanged"
            # 同一平台日以外的刷新间隔不能可靠地落到某一日的分钟上。
            elif previous_at.date() != observed_at.date() or previous_at > observed_at:
                _save_minute_snapshot(connection, provider, usage_date, normalized, observed_at)
                status = "cross_day"
            else:
                _save_minute_snapshot(connection, provider, usage_date, normalized, observed_at)
                if any(amount < 0 for amount in deltas.values()):
                    # 平台修正累计值时只重置基线，不能回写负数破坏已有估算。
                    status = "adjusted"
                elif not any(deltas.values()):
                    status = "unchanged"
                else:
                    minute_indexes = _minute_indexes(previous_at, observed_at)
                    updated_at = observed_at.isoformat(timespec="seconds")
                    for token_type, delta in deltas.items():
                        quotient, remainder = divmod(delta, len(minute_indexes))
                        for index, minute in enumerate(minute_indexes):
                            amount = quotient + (1 if index < remainder else 0)
                            if not amount:
                                continue
                            connection.execute(
                                """INSERT INTO minute_usage
                                       (provider, usage_date, minute_index, token_type, token_amount, updated_at)
                                     VALUES (?, ?, ?, ?, ?, ?)
                                     ON CONFLICT(provider, usage_date, minute_index, token_type) DO UPDATE SET
                                       token_amount = minute_usage.token_amount + excluded.token_amount,
                                       updated_at = excluded.updated_at""",
                                (provider, usage_date, minute, token_type, amount, updated_at),
                            )
                    config_manager.logger().debug(
                        "Minute delta distributed: provider=%s gap_minutes=%s token_total=%s",
                        provider,
                        len(minute_indexes),
                        sum(deltas.values()),
                    )
                    status = "recorded"
        _save_estimated_minute_cost_usage(
            connection, provider, usage_date, cost_cny, observed_at
        )
    _log_minute_usage_cleanup(
        provider, retention_days, cleanup_threshold, deleted_rows
    )
    return status


def replace_exact_minute_usage(
    provider: str,
    usage_dates: set[date],
    token_rows: list[dict[str, Any]],
    cost_rows: list[dict[str, Any]],
    current_day: date,
    retention_days: int = 3,
    model_rows: list[dict[str, Any]] | None = None,
) -> str:
    """Atomically replace provider-authored minute rows for complete dates."""

    normalized_dates = {value.isoformat() for value in usage_dates}
    token_totals: dict[tuple[str, int, str], int] = {}
    for row in token_rows:
        raw_date = row.get("usage_date")
        usage_date = raw_date if isinstance(raw_date, date) else date.fromisoformat(str(raw_date))
        date_text = usage_date.isoformat()
        minute = int(row.get("minute"))
        token_type = str(row.get("token_type") or "")
        amount = int(row.get("token_amount"))
        if (
            date_text not in normalized_dates
            or not 0 <= minute < 1440
            or token_type not in MINUTE_TOKEN_TYPES
            or amount < 0
        ):
            raise ValueError("invalid exact minute token row")
        key = (date_text, minute, token_type)
        token_totals[key] = token_totals.get(key, 0) + amount

    cost_totals: dict[tuple[str, int], Decimal] = {}
    for row in cost_rows:
        raw_date = row.get("usage_date")
        usage_date = raw_date if isinstance(raw_date, date) else date.fromisoformat(str(raw_date))
        date_text = usage_date.isoformat()
        minute = int(row.get("minute"))
        amount = Decimal(str(row.get("cost_cny")))
        if (
            date_text not in normalized_dates
            or not 0 <= minute < 1440
            or not amount.is_finite()
            or amount < 0
        ):
            raise ValueError("invalid exact minute cost row")
        key = (date_text, minute)
        cost_totals[key] = cost_totals.get(key, Decimal("0")) + amount

    model_totals: dict[tuple[str, int, str], dict[str, Any]] = {}
    if model_rows is not None:
        for row in model_rows:
            raw_date = row.get("usage_date")
            usage_date = (
                raw_date if isinstance(raw_date, date) else date.fromisoformat(str(raw_date))
            )
            date_text = usage_date.isoformat()
            minute = int(row.get("minute"))
            model = str(row.get("model") or "unknown").strip() or "unknown"
            hit = int(row.get("cache_hit_tokens", 0) or 0)
            miss = int(row.get("cache_miss_tokens", 0) or 0)
            output = int(row.get("output_tokens", 0) or 0)
            cost = Decimal(str(row.get("cost_cny", "0")))
            if (
                date_text not in normalized_dates
                or not 0 <= minute < 1440
                or min(hit, miss, output) < 0
                or not cost.is_finite()
                or cost < 0
            ):
                raise ValueError("invalid exact minute model row")
            values = model_totals.setdefault(
                (date_text, minute, model),
                {
                    "cache_hit_tokens": 0,
                    "cache_miss_tokens": 0,
                    "output_tokens": 0,
                    "cost_cny": Decimal("0"),
                },
            )
            values["cache_hit_tokens"] += hit
            values["cache_miss_tokens"] += miss
            values["output_tokens"] += output
            values["cost_cny"] += cost

        model_token_totals: dict[tuple[str, int, str], int] = {}
        model_cost_totals: dict[tuple[str, int], Decimal] = {}
        for (usage_date, minute, _model), values in model_totals.items():
            for token_type, field in (
                ("PROMPT_CACHE_HIT_TOKEN", "cache_hit_tokens"),
                ("PROMPT_CACHE_MISS_TOKEN", "cache_miss_tokens"),
                ("RESPONSE_TOKEN", "output_tokens"),
            ):
                key = (usage_date, minute, token_type)
                model_token_totals[key] = model_token_totals.get(key, 0) + int(
                    values[field]
                )
            cost_key = (usage_date, minute)
            model_cost_totals[cost_key] = model_cost_totals.get(
                cost_key, Decimal("0")
            ) + Decimal(values["cost_cny"])
        if any(
            token_totals.get(key, 0) != model_token_totals.get(key, 0)
            for key in set(token_totals) | set(model_token_totals)
        ) or any(
            cost_totals.get(key, Decimal("0"))
            != model_cost_totals.get(key, Decimal("0"))
            for key in set(cost_totals) | set(model_cost_totals)
        ):
            raise ValueError("exact minute model totals do not match minute totals")

    updated_at = datetime.now().isoformat(timespec="seconds")
    with _connect() as connection:
        cleanup_threshold, deleted_rows = _delete_expired_minute_usage(
            connection, provider, current_day, retention_days
        )
        for usage_date in normalized_dates:
            connection.execute(
                "DELETE FROM minute_usage WHERE provider = ? AND usage_date = ?",
                (provider, usage_date),
            )
            connection.execute(
                "DELETE FROM minute_cost_usage WHERE provider = ? AND usage_date = ?",
                (provider, usage_date),
            )
            if model_rows is not None:
                connection.execute(
                    "DELETE FROM minute_model_usage WHERE provider = ? AND usage_date = ?",
                    (provider, usage_date),
                )
            # Exact providers do not use delta baselines. Removing any old snapshot
            # prevents a future capability change from mixing the two semantics.
            connection.execute(
                "DELETE FROM minute_usage_snapshot WHERE provider = ? AND usage_date = ?",
                (provider, usage_date),
            )
            connection.execute(
                "DELETE FROM minute_cost_snapshot WHERE provider = ? AND usage_date = ?",
                (provider, usage_date),
            )
        for (usage_date, minute, token_type), amount in token_totals.items():
            if amount:
                connection.execute(
                    """INSERT INTO minute_usage
                           (provider, usage_date, minute_index, token_type, token_amount, updated_at)
                         VALUES (?, ?, ?, ?, ?, ?)""",
                    (provider, usage_date, minute, token_type, amount, updated_at),
                )
        for (usage_date, minute), amount in cost_totals.items():
            connection.execute(
                """INSERT INTO minute_cost_usage
                       (provider, usage_date, minute_index, cost_cny, updated_at)
                     VALUES (?, ?, ?, ?, ?)""",
                (provider, usage_date, minute, str(amount), updated_at),
            )
        for (usage_date, minute, model), values in model_totals.items():
            connection.execute(
                """INSERT INTO minute_model_usage
                       (provider, usage_date, minute_index, model,
                        cache_hit_tokens, cache_miss_tokens, output_tokens,
                        cost_cny, updated_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    provider,
                    usage_date,
                    minute,
                    model,
                    values["cache_hit_tokens"],
                    values["cache_miss_tokens"],
                    values["output_tokens"],
                    str(values["cost_cny"]),
                    updated_at,
                ),
            )
    _log_minute_usage_cleanup(
        provider, retention_days, cleanup_threshold, deleted_rows
    )
    return "recorded" if token_totals or cost_totals or model_totals else "empty"


def minute_usage_for_day(provider: str, usage_day: date, *, _connection: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """读取指定日期的临时估算数据；稀疏行由界面补齐为 1,440 个分钟点。"""
    result: list[dict[str, Any]] = []
    with (nullcontext(_connection) if _connection is not None else _connect()) as connection:
        rows = connection.execute(
            """SELECT minute_index, token_type, token_amount
                 FROM minute_usage
                WHERE provider = ? AND usage_date = ?
                ORDER BY minute_index, token_type""",
            (provider, usage_day.isoformat()),
        )
        # 直接流式归一化，避免原始 SQLite 元组与最终字典同时占用一整天内存。
        for minute_index, token_type, token_amount in rows:
            result.append(
                {
                    "minute": int(minute_index),
                    "token_type": str(token_type),
                    "token_amount": int(token_amount or 0),
                }
            )
    return result


def minute_cost_usage_for_day(provider: str, usage_day: date, *, _connection: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """读取指定日期的分钟费用估算；缺失行表示该分钟尚无可靠费用。"""
    result: list[dict[str, Any]] = []
    with (nullcontext(_connection) if _connection is not None else _connect()) as connection:
        rows = connection.execute(
            """SELECT minute_index, cost_cny
                 FROM minute_cost_usage
                WHERE provider = ? AND usage_date = ?
                ORDER BY minute_index""",
            (provider, usage_day.isoformat()),
        )
        for minute_index, raw_cost in rows:
            try:
                cost = Decimal(str(raw_cost))
            except (InvalidOperation, ValueError):
                config_manager.logger().warning("Skipped malformed cached minute cost")
                continue
            if cost.is_finite():
                result.append({"minute": int(minute_index), "cost_cny": cost})
    return result


def minute_model_usage_for_day(provider: str, usage_day: date, *, _connection: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """读取指定日期按分钟和模型保存的服务商原始明细。"""
    result: list[dict[str, Any]] = []
    with (nullcontext(_connection) if _connection is not None else _connect()) as connection:
        rows = connection.execute(
            """SELECT minute_index, model, cache_hit_tokens, cache_miss_tokens,
                      output_tokens, cost_cny
                 FROM minute_model_usage
                WHERE provider = ? AND usage_date = ?
                ORDER BY minute_index, model""",
            (provider, usage_day.isoformat()),
        )
        for minute, model, hit, miss, output, raw_cost in rows:
            try:
                cost = Decimal(str(raw_cost or "0"))
            except (InvalidOperation, ValueError):
                config_manager.logger().warning("Skipped malformed cached minute model cost")
                continue
            if not cost.is_finite():
                continue
            result.append(
                {
                    "minute": int(minute),
                    "model": str(model or "unknown"),
                    "cache_hit_tokens": int(hit or 0),
                    "cache_miss_tokens": int(miss or 0),
                    "output_tokens": int(output or 0),
                    "cost_cny": cost,
                }
            )
    return result


def minute_history_for_day(
    provider: str, usage_day: date, include_models: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    # 同一天的三个视图共享连接和读事务，避免重复开库及读到不同轮次的数据。
    with _connect() as connection:
        connection.execute("BEGIN")
        return (
            minute_usage_for_day(provider, usage_day, _connection=connection),
            minute_cost_usage_for_day(provider, usage_day, _connection=connection),
            minute_model_usage_for_day(provider, usage_day, _connection=connection) if include_models else [],
        )


def minute_usage_dates(provider: str, *, populated_only: bool = False) -> list[str]:
    """读取指定提供商仍保留分时缓存的日期，按日期升序返回。"""
    with _connect() as connection:
        if populated_only:
            return [str(row[0]) for row in connection.execute(
                "SELECT DISTINCT usage_date FROM minute_usage WHERE provider = ? ORDER BY usage_date",
                (provider,),
            )]
        rows = connection.execute(
            """SELECT usage_date
                 FROM (
                     SELECT usage_date FROM minute_usage WHERE provider = ?
                     UNION
                     SELECT usage_date FROM minute_usage_snapshot WHERE provider = ?
                     UNION
                     SELECT usage_date FROM minute_cost_usage WHERE provider = ?
                     UNION
                     SELECT usage_date FROM minute_model_usage WHERE provider = ?
                     UNION
                     SELECT usage_date FROM minute_cost_snapshot WHERE provider = ?
                 )
                 ORDER BY usage_date""",
            (provider, provider, provider, provider, provider),
        ).fetchall()
    return [str(usage_date) for (usage_date,) in rows]


def total_cost(provider: str | None = None) -> Decimal:
    query = "SELECT cost_cny FROM daily_usage"
    params: tuple[Any, ...] = ()
    if provider:
        query += " WHERE provider = ?"
        params = (provider,)
    total = Decimal("0")
    with _connect() as connection:
        # 保留 Decimal 累加精度，同时流式读取以免大历史表产生额外结果列表。
        for (cost,) in connection.execute(query, params):
            try:
                total += Decimal(str(cost or "0"))
            except (InvalidOperation, ValueError):
                config_manager.logger().warning("Skipped malformed cached cost")
    return total


def recent_daily(days: int = 371, provider: str | None = None) -> list[dict[str, Any]]:
    start = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
    query = """SELECT usage_date, token_amount, cost_cny
               FROM daily_usage WHERE usage_date >= ?"""
    params: list[Any] = [start]
    if provider:
        query += " AND provider = ?"
        params.append(provider)
    query += " ORDER BY usage_date"
    daily: dict[str, dict[str, Any]] = {}
    with _connect() as connection:
        for usage_date, tokens, cost in connection.execute(query, params):
            item = daily.setdefault(
                str(usage_date),
                {"date": str(usage_date), "tokens": 0, "cost_cny": Decimal("0")},
            )
            item["tokens"] += int(tokens or 0)
            try:
                item["cost_cny"] += Decimal(str(cost or "0"))
            except (InvalidOperation, ValueError):
                config_manager.logger().warning("Skipped malformed cached daily cost")
    return list(daily.values())


def recent_daily_model_usage(
    provider: str, current_day: date, days: int = 7
) -> list[dict[str, Any]]:
    """读取连续自然日内的按模型 Token 构成和十进制金额。"""
    day_count = max(1, int(days))
    start = current_day - timedelta(days=day_count - 1)
    ordered_days = [start + timedelta(days=offset) for offset in range(day_count)]
    by_day: dict[str, dict[str, dict[str, Any]]] = {
        usage_day.isoformat(): {} for usage_day in ordered_days
    }
    with _connect() as connection:
        rows = connection.execute(
            """SELECT usage_date, model, token_type, token_amount, cost_cny
                 FROM daily_usage
                WHERE provider = ? AND usage_date BETWEEN ? AND ?
                ORDER BY usage_date, model, token_type""",
            (provider, start.isoformat(), current_day.isoformat()),
        )
        for usage_date, raw_model, token_type, token_amount, raw_cost in rows:
            model = str(raw_model or "unknown")
            values = by_day[str(usage_date)].setdefault(
                model,
                {
                    "date": str(usage_date),
                    "model": model,
                    "cache_hit_tokens": 0,
                    "cache_miss_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cost_cny": Decimal("0"),
                },
            )
            field = {
                "PROMPT_CACHE_HIT_TOKEN": "cache_hit_tokens",
                "PROMPT_CACHE_MISS_TOKEN": "cache_miss_tokens",
                "RESPONSE_TOKEN": "output_tokens",
            }.get(str(token_type))
            if field:
                amount = int(token_amount or 0)
                values[field] += amount
                values["total_tokens"] += amount
            if str(token_type) == "cost_cny":
                try:
                    values["cost_cny"] += Decimal(str(raw_cost or "0"))
                except (InvalidOperation, ValueError):
                    config_manager.logger().warning(
                        "Skipped malformed cached daily model cost"
                    )
    return [
        {
            "date": usage_day.isoformat(),
            "models": [
                by_day[usage_day.isoformat()][model]
                for model in sorted(by_day[usage_day.isoformat()])
            ],
        }
        for usage_day in ordered_days
    ]


def provider_daily_payloads(provider: str, start: date, end: date) -> list[dict[str, Any]]:
    with _connect() as connection:
        rows = connection.execute(
            """SELECT usage_date, model, token_type, token_amount, cost_cny
                 FROM daily_usage
                WHERE provider = ? AND usage_date BETWEEN ? AND ?
                ORDER BY usage_date""",
            (provider, start.isoformat(), end.isoformat()),
        ).fetchall()
    by_day: dict[str, list[dict[str, Any]]] = {}
    for usage_date, model, token_type, token_amount, cost_cny in rows:
        usages: list[dict[str, Any]] = []
        if int(token_amount or 0):
            usages.append({"type": str(token_type), "amount": int(token_amount)})
        if Decimal(str(cost_cny or "0")):
            usages.append({"type": "cost_cny", "amount": str(cost_cny)})
        if usages:
            by_day.setdefault(str(usage_date), []).append(
                {"model": str(model or "unknown"), "usage": usages}
            )
    return [{"date": day, "data": items} for day, items in by_day.items()]


def provider_monthly_payload(provider: str, year: int, month: int) -> dict[str, Any] | None:
    start = date(year, month, 1)
    next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    days = provider_daily_payloads(provider, start, next_month - timedelta(days=1))
    return {"days": days, "total": []} if days else None


def provider_per_model(provider: str, year: int, month: int) -> list[tuple[str, int, Decimal]]:
    payload = provider_monthly_payload(provider, year, month)
    if payload is None:
        return []
    totals: dict[str, tuple[int, Decimal]] = {}
    for day in payload["days"]:
        for item in day["data"]:
            tokens, cost = totals.get(item["model"], (0, Decimal("0")))
            for usage in item["usage"]:
                if usage["type"] == "cost_cny":
                    cost += Decimal(str(usage["amount"]))
                else:
                    tokens += int(usage["amount"])
            totals[item["model"]] = tokens, cost
    return sorted(
        [(model, tokens, cost) for model, (tokens, cost) in totals.items()],
        key=lambda item: item[1],
        reverse=True,
    )
