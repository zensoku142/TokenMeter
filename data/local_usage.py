"""Incremental, read-only local session statistics; never retain conversation text."""

from __future__ import annotations

import csv
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from data import history

MAX_LINE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class LocalUsage:
    provider: str
    session: str
    project: str
    day: str
    model: str
    input: int
    output: int
    cache_read: int
    cache_write: int
    total: int


@dataclass
class _FileUsage:
    signature: tuple[int, int, int, int]
    offset: int = 0
    session: str = ""
    project: str = ""
    model: str = ""
    issues: int = 0
    previous: dict[str, int] = field(default_factory=dict)
    events: dict[str, LocalUsage] = field(default_factory=dict)


def _count(value) -> int:
    # 异常值不能变成负 Token，也不能把浮点/布尔字段当有效整数。
    return value if type(value) is int and value >= 0 else 0


class LocalUsageScanner:
    """One worker owns a scanner; cached entries contain usage fields only."""

    def __init__(self):
        self.files: dict[tuple[str, Path], _FileUsage] = {}
        self.issues = 0
        self.changed = False
        self._last_rows: list[LocalUsage] | None = None

    def scan(self, roots: dict[str, Path]) -> list[LocalUsage]:
        self.issues = 0
        self.changed = False
        current: set[tuple[str, Path]] = set()
        for provider, root in roots.items():
            if not root.is_dir():
                self.issues += 1
                continue
            # Only user-selected local log directories; no CLI launch or credential discovery.
            directories = (root / "sessions", root / "archived_sessions") if provider == "codex" else (root / "projects",)
            for directory in directories:
                try:
                    for path in directory.rglob("*.jsonl"):
                        key = (provider, path)
                        current.add(key)
                        self._read(provider, path)
                except OSError:
                    self.issues += 1
        self.changed = self.changed or bool(set(self.files) - current)
        self.files = {key: value for key, value in self.files.items() if key in current}
        self.issues += sum(data.issues for data in self.files.values())
        if not self.changed and self._last_rows is not None:
            return self._last_rows
        events: dict[tuple[str, str, str], LocalUsage] = {}
        for (provider, _path), data in self.files.items():
            for event_id, record in data.events.items():
                key = (provider, record.session, event_id)
                previous = events.get(key)
                # 同会话复制/归档及流式重写保留最大完整读数，不能累加同一消息。
                if previous is None or record.total > previous.total:
                    events[key] = record
        self._last_rows = sorted(events.values(), key=lambda row: (row.day, row.provider, row.session))
        return self._last_rows

    def _read(self, provider: str, path: Path) -> None:
        key = (provider, path)
        try:
            stat = path.stat()
            signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            previous = self.files.get(key)
            if previous and previous.signature == signature:
                return
            self.changed = True
            append = previous and previous.signature[:2] == signature[:2] and stat.st_size > previous.signature[2]
            data = previous if append else _FileUsage(signature, session=hashlib.sha256(str(path).encode()).hexdigest())
            with path.open("rb") as stream:
                stream.seek(data.offset)
                lines = 0
                while True:
                    lines += 1
                    if lines % 256 == 0:
                        # 大日志扫描主动让出执行权，避免 CPU 密集 JSON 解析持续挤占 Qt 主线程。
                        time.sleep(0)
                    offset = stream.tell()
                    raw = stream.readline(MAX_LINE_BYTES + 1)
                    if not raw:
                        break
                    oversized = len(raw) > MAX_LINE_BYTES
                    while raw and not raw.endswith(b"\n") and oversized:
                        raw = stream.readline(MAX_LINE_BYTES + 1)
                    if not raw.endswith(b"\n"):
                        # 写入进程尚未提交尾行；下次从该行起点重读，包括分段 UTF-8。
                        stream.seek(offset)
                        break
                    if oversized:
                        data.issues += 1
                        continue
                    # 先过滤记录类型，避免反序列化不参与统计的大段对话和工具输出。
                    if not any(marker in raw for marker in (b'"usage"', b'"token_count"', b'"session_meta"', b'"turn_context"')):
                        continue
                    try:
                        record = json.loads(raw)
                        if isinstance(record, dict):
                            self._record(provider, record, data)
                    except (ValueError, TypeError, AttributeError):
                        data.issues += 1
                data.offset = stream.tell()
            data.signature = signature
            self.files[key] = data
        except OSError:
            # 读到一半出错时丢弃该文件的可变扫描状态，下次从头重建，避免累计值重复差分。
            self.files.pop(key, None)
            self.issues += 1

    def _record(self, provider: str, record: dict, data: _FileUsage) -> None:
        payload = record.get("payload", {})
        if provider == "codex":
            if not isinstance(payload, dict):
                return
            if record.get("type") == "session_meta":
                data.session = str(payload.get("id") or data.session)
                data.project = Path(str(payload.get("cwd") or "")).name
                return
            if record.get("type") == "turn_context":
                data.model = str(payload.get("model") or "")
                return
            if record.get("type") != "event_msg" or payload.get("type") != "token_count":
                return
            info = payload.get("info")
            usage = info.get("total_token_usage") if isinstance(info, dict) else None
            if not isinstance(usage, dict):
                return
            if type(usage.get("total_tokens")) is not int or usage["total_tokens"] < 0:
                data.issues += 1
                return
            if any(name in usage and (type(usage[name]) is not int or usage[name] < 0) for name in (
                "input_tokens", "output_tokens", "cached_input_tokens",
            )):
                data.issues += 1
                return
            stamp = datetime.fromisoformat(str(record.get("timestamp", "")).replace("Z", "+00:00"))
            current = {name: _count(usage.get(name)) for name in (
                "input_tokens", "output_tokens", "cached_input_tokens", "total_tokens",
            )}
            reset = current["total_tokens"] < data.previous.get("total_tokens", 0)
            counts = {name: max(0, value - (0 if reset else data.previous.get(name, 0))) for name, value in current.items()}
            data.previous = current
            event_id = f"{record.get('timestamp')}:{current['total_tokens']}"
            values = (counts["input_tokens"], counts["output_tokens"], counts["cached_input_tokens"], 0, counts["total_tokens"])
        else:
            message = record.get("message")
            if record.get("type") != "assistant" or not isinstance(message, dict):
                return
            usage = message.get("usage")
            if not isinstance(usage, dict):
                return
            if any(name in usage and (type(usage[name]) is not int or usage[name] < 0) for name in (
                "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
            )):
                data.issues += 1
                return
            stamp = datetime.fromisoformat(str(record.get("timestamp", "")).replace("Z", "+00:00"))
            data.session = str(record.get("sessionId") or data.session)
            data.project = Path(str(record.get("cwd") or "")).name or data.project
            data.model = str(message.get("model") or "")
            event_id = str(message.get("id") or record.get("uuid") or "")
            if not event_id:
                # 没有消息标识无法可靠去重；跳过并报告统计不完整，不猜测消息身份。
                data.issues += 1
                return
            uncached, output, read, write = (_count(usage.get(name)) for name in (
                "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
            ))
            # Claude 输入分项互斥；统一 input 为含缓存输入，total 不再二次加缓存。
            values = (uncached + read + write, output, read, write, uncached + output + read + write)
        day = stamp.astimezone().date().isoformat()
        if values[-1] <= 0:
            return
        row = LocalUsage(provider, data.session, data.project, day, data.model, *values)
        existing = data.events.get(event_id)
        if existing is None or row.total > existing.total:
            data.events[event_id] = row


def filter_usage(rows: list[LocalUsage], days: int, project: str = "", *, today=None) -> list[LocalUsage]:
    today = today or datetime.now().date()
    first = (today - timedelta(days=max(0, days - 1))).isoformat() if days else ""
    return [row for row in rows if (not first or row.day >= first) and row.day <= today.isoformat()
            and (not project or project.casefold() in row.project.casefold())]


def daily_model_series(rows: list[LocalUsage], start: date, end: date):
    days = [(start + timedelta(days=index)).isoformat() for index in range(max(0, (end - start).days + 1))]
    totals: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row.provider, row.model)
        totals[key] = totals.get(key, 0) + row.total
    models = sorted(totals, key=lambda key: (-totals[key], key))[:8]
    other = ("", "其他")
    series = {key: [0] * len(days) for key in models}
    if len(totals) > len(models):
        series[other] = [0] * len(days)
    indices = {day: index for index, day in enumerate(days)}
    for row in rows:
        if row.day not in indices:
            continue
        key = (row.provider, row.model)
        # 过多模型合并为“其他”，保持每日总数不丢失，同时避免图例与柱组无限拥挤。
        series[key if key in series else other][indices[row.day]] += row.total
    return days, series


def export_usage(path: Path, rows: list[LocalUsage], *, issues: int = 0) -> None:
    fields = list(LocalUsage.__dataclass_fields__)
    records = [asdict(row) for row in rows]
    metadata = {"source": "local_logs", "account": "unknown", "timezone": str(datetime.now().astimezone().tzinfo),
                "incomplete_records": issues, "input_includes_cache": True}
    if path.suffix.lower() == ".json":
        payload = {**metadata, "records": records}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=[*fields, *metadata])
            writer.writeheader()
            for record in records:
                # Excel 可执行以公式符号开头的项目/模型/会话名；字符串前缀转义，数值仍保留整数。
                writer.writerow({key: "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value
                                 for key, value in {**record, **metadata}.items()})


def local_cache_scope(roots: dict[str, Path]) -> str:
    # 目录与当前时区共同决定统计范围；换目录/时区不能沿用另一范围的快照。
    identity = {provider: str(path.absolute()) for provider, path in roots.items()}
    identity["timezone"] = str(datetime.now().astimezone().tzinfo)
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def load_local_report(scope: str) -> tuple[list[LocalUsage], int] | None:
    with history._connect() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS local_usage_report
            (scope TEXT PRIMARY KEY, payload TEXT, issues INTEGER, saved TEXT)""")
        record = connection.execute("SELECT payload, issues FROM local_usage_report WHERE scope=? AND length(payload)<=?",
                                    (scope, 32 * 1024 * 1024)).fetchone()
    if record is None:
        return None
    try:
        values = json.loads(record[0])
        if not isinstance(values, list):
            return None
        # 新快照按固定列存储，减少重复字段名；仍可读取先前的字典格式。
        rows = [LocalUsage(*value) if isinstance(value, list) else LocalUsage(**value) for value in values]
        for row in rows:
            if any(type(getattr(row, name)) is not int or getattr(row, name) < 0 for name in ("input", "output", "cache_read", "cache_write", "total")):
                return None
            if any(not isinstance(getattr(row, name), str) for name in ("provider", "session", "project", "day", "model")):
                return None
            date.fromisoformat(row.day)
        return rows, max(0, int(record[1]))
    except (ValueError, TypeError):
        return None


def save_local_report(scope: str, rows: list[LocalUsage], issues: int) -> None:
    fields = tuple(LocalUsage.__dataclass_fields__)
    payload = json.dumps([[getattr(row, name) for name in fields] for row in rows], ensure_ascii=False, separators=(",", ":"))
    if len(payload.encode("utf-8")) > 32 * 1024 * 1024:
        return
    with history._connect() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS local_usage_report
            (scope TEXT PRIMARY KEY, payload TEXT, issues INTEGER, saved TEXT)""")
        connection.execute("INSERT OR REPLACE INTO local_usage_report VALUES (?, ?, ?, ?)",
                           (scope, payload, issues, datetime.now().isoformat()))
        # 仅保留最近八个目录范围，避免反复选目录后快照无界增长。
        connection.execute("DELETE FROM local_usage_report WHERE scope NOT IN (SELECT scope FROM local_usage_report ORDER BY saved DESC LIMIT 8)")
