"""SQLite cache for normalized per-provider daily usage history."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Iterator

import config_manager

DB_PATH = config_manager.CONFIG_DIR / "usage.db"

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
        connection.execute("PRAGMA journal_mode=WAL")
        with connection:
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
                """
            )
            yield connection
    finally:
        # sqlite Connection 的上下文只处理事务，文件句柄仍需显式关闭。
        connection.close()


def needs_initial_sync(provider: str = "deepseek") -> bool:
    with _connect() as connection:
        row = connection.execute(
            "SELECT last_success_at FROM sync_state WHERE provider = ?", (provider,)
        ).fetchone()
    return not row or not row[0]


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


def total_cost(provider: str | None = None) -> Decimal:
    query = "SELECT cost_cny FROM daily_usage"
    params: tuple[Any, ...] = ()
    if provider:
        query += " WHERE provider = ?"
        params = (provider,)
    with _connect() as connection:
        rows = connection.execute(query, params).fetchall()
    total = Decimal("0")
    for (cost,) in rows:
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
    with _connect() as connection:
        rows = connection.execute(query, params).fetchall()
    daily: dict[str, dict[str, Any]] = {}
    for usage_date, tokens, cost in rows:
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
