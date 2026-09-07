"""Small, account-scoped quota samples and notification state in the existing DB."""

from __future__ import annotations

from datetime import datetime
from math import isfinite

from data import history


def estimate_seconds(samples: list[tuple[float, float]]) -> float | None:
    # 只保留连续且未释放额度的片段；滚动回补、时钟回退与长断档不能线性外推。
    valid = []
    for stamp, used in samples:
        if not isfinite(stamp) or not isfinite(used) or not 0 <= used <= 100:
            valid = []
            continue
        if valid and (stamp <= valid[-1][0] or stamp - valid[-1][0] > 15 * 60 or used < valid[-1][1]):
            valid = []
        valid.append((stamp, used))
    if len(valid) < 3 or valid[-1][0] - valid[0][0] < 600 or valid[-1][1] >= 100:
        return None
    delta = valid[-1][1] - valid[0][1]
    if delta == 0:
        return 0.0
    seconds = (100 - valid[-1][1]) * (valid[-1][0] - valid[0][0]) / delta
    return seconds if isfinite(seconds) else None


def record_quota(provider, data) -> dict[str, float]:
    if (not data.account_key or data.status != "ok" or data.is_stale or data.errors
            or data.refresh_error_codes or data.last_success_at is None
            or data.quota_source not in {"interface", "local_snapshot"}):
        return {}
    stamp = data.last_success_at.timestamp()
    result = {}
    with history._connect() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS quota_samples (
            provider TEXT, account TEXT, window TEXT, period TEXT, stamp REAL, used REAL,
            PRIMARY KEY (provider, account, window, stamp))""")
        connection.execute("CREATE INDEX IF NOT EXISTS quota_samples_stamp ON quota_samples(stamp)")
        connection.execute("DELETE FROM quota_samples WHERE stamp < ?", (stamp - 3600,))
        for window in data.quota_windows:
            used = window.used_percent
            if isinstance(used, bool) or not isinstance(used, (int, float)) or not isfinite(used) or not 0 <= used <= 100:
                continue
            if window.resets_at is not None and window.resets_at.timestamp() <= stamp:
                continue
            period = f"{data.quota_source}:{window.window_minutes}:{window.resets_at.isoformat() if window.resets_at else ''}"
            scope = (provider, data.account_key, window.id)
            # 来源/周期变更后丢弃旧片段；切回原来源也不能把不连续观测拼成趋势。
            connection.execute("DELETE FROM quota_samples WHERE provider=? AND account=? AND window=? AND period<>?", (*scope, period))
            connection.execute("INSERT OR REPLACE INTO quota_samples VALUES (?, ?, ?, ?, ?, ?)", (*scope, period, stamp, used))
            samples = connection.execute("""SELECT stamp, used FROM quota_samples
                WHERE provider=? AND account=? AND window=? AND period=? AND stamp>=? ORDER BY stamp""",
                (*scope, period, stamp - 3600)).fetchall()
            prediction = estimate_seconds(samples)
            if prediction is not None:
                result[window.id] = prediction
    return result


def _alert_schema(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS quota_alerts (
        provider TEXT, account TEXT, window TEXT, reset TEXT, saved REAL,
        PRIMARY KEY(provider, account, window))""")


def load_alerts() -> dict[tuple[str, str, str], datetime | None]:
    with history._connect() as connection:
        _alert_schema(connection)
        rows = connection.execute("SELECT provider, account, window, reset FROM quota_alerts WHERE saved > ?",
                                  (datetime.now().timestamp() - 30 * 86400,)).fetchall()
    result = {}
    for provider, account, window, reset in rows:
        try:
            result[provider, account, window] = datetime.fromisoformat(reset) if reset else None
        except (ValueError, TypeError):
            continue
    return result


def save_alerts(alerts):
    with history._connect() as connection:
        _alert_schema(connection)
        # 单实例程序统一保存小型通知状态，事务替换避免重启后重复提醒。
        connection.execute("DELETE FROM quota_alerts")
        connection.executemany("INSERT INTO quota_alerts VALUES (?, ?, ?, ?, ?)", [
            (*scope, reset.isoformat() if reset else "", datetime.now().timestamp()) for scope, reset in alerts.items()
        ])


def in_quiet_hours(config, now: datetime | None = None) -> bool:
    if config.get("QUOTA_QUIET_ENABLED", False) is not True:
        return False
    now = now or datetime.now()
    try:
        start = datetime.strptime(config.get("QUOTA_QUIET_START", "22:00"), "%H:%M").time()
        end = datetime.strptime(config.get("QUOTA_QUIET_END", "08:00"), "%H:%M").time()
    except (ValueError, TypeError):
        return False
    current = now.time()
    return start <= current < end if start < end else current >= start or current < end
