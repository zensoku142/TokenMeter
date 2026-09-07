import csv
import json
from datetime import date
from pathlib import Path

from data.local_usage import LocalUsage, LocalUsageScanner, export_usage, filter_usage


def write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")


def codex(total, timestamp="2026-09-07T01:00:00Z"):
    return {"timestamp": timestamp, "type": "event_msg", "payload": {"type": "token_count", "info": {
        "total_token_usage": {"input_tokens": total - 10, "output_tokens": 10, "cached_input_tokens": 20, "total_tokens": total},
    }}}


def claude(identity="m1", output=10):
    return {"type": "assistant", "timestamp": "2026-09-07T01:00:00Z", "sessionId": "s1", "cwd": "C:/work/project",
            "message": {"id": identity, "model": "model-a", "content": "private prompt not retained", "usage": {
                "input_tokens": 100, "output_tokens": output, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 20,
            }}}


def test_codex_incremental_append_archives_and_duplicate_events(tmp_path):
    path = tmp_path / "sessions/a.jsonl"
    records = [{"type": "session_meta", "payload": {"id": "s1", "cwd": "C:/work/project"}},
               {"type": "turn_context", "payload": {"model": "model-a"}}, codex(100), codex(100)]
    write(path, records)
    scanner = LocalUsageScanner()
    rows = scanner.scan({"codex": tmp_path})
    assert sum(row.total for row in rows) == 100
    assert rows[0].model == "model-a"
    assert rows[0].project == "project"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(codex(150, "2026-09-07T02:00:00Z")) + "\n")
    assert sum(row.total for row in scanner.scan({"codex": tmp_path})) == 150
    archive = tmp_path / "archived_sessions/a.jsonl"
    archive.parent.mkdir()
    archive.write_bytes(path.read_bytes())
    assert sum(row.total for row in scanner.scan({"codex": tmp_path})) == 150
    path.unlink()
    assert sum(row.total for row in scanner.scan({"codex": tmp_path})) == 150


def test_claude_cache_semantics_streaming_updates_and_copied_session(tmp_path):
    path = tmp_path / "projects/a/log.jsonl"
    write(path, [claude(), claude(output=15), claude("m2")])
    write(tmp_path / "projects/b/copy.jsonl", [claude(), claude(output=15), claude("m2")])
    scanner = LocalUsageScanner()
    rows = scanner.scan({"claude": tmp_path})
    assert len(rows) == 2
    assert sum(row.total for row in rows) == 365
    assert all(row.input == 170 and row.cache_read == 50 for row in rows)
    assert "private prompt" not in repr(scanner.files)


def test_half_line_truncation_and_unchanged_file_cache(tmp_path, monkeypatch):
    path = tmp_path / "projects/a/log.jsonl"
    write(path, [claude()])
    scanner = LocalUsageScanner()
    scanner.scan({"claude": tmp_path})
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unchanged file read")))
        assert len(scanner.scan({"claude": tmp_path})) == 1
    record = json.dumps(claude("m2"))
    with path.open("a", encoding="utf-8") as stream:
        stream.write(record[:25])
    assert len(scanner.scan({"claude": tmp_path})) == 1
    with path.open("a", encoding="utf-8") as stream:
        stream.write(record[25:] + "\n")
    assert len(scanner.scan({"claude": tmp_path})) == 2
    write(path, [claude("m3")])
    assert len(scanner.scan({"claude": tmp_path})) == 1


def test_malformed_usage_and_warning_survives_cached_scan(tmp_path):
    record = claude()
    record["timestamp"] = "bad"
    write(tmp_path / "projects/a/log.jsonl", [record, claude()])
    scanner = LocalUsageScanner()
    assert len(scanner.scan({"claude": tmp_path})) == 1
    assert scanner.issues == 1
    scanner.scan({"claude": tmp_path})
    assert scanner.issues == 1


def test_filters_exports_metadata_and_formula_escape(tmp_path):
    row = LocalUsage("claude", "s1", "=HYPERLINK(1)", "2026-09-07", "model-a", 170, 10, 50, 20, 180)
    older = LocalUsage("codex", "s2", "old", "2026-08-01", "model-b", 10, 10, 0, 0, 20)
    assert filter_usage([row, older], 7, today=date(2026, 9, 7)) == [row]
    assert filter_usage([row, older], 0, "OLD", today=date(2026, 9, 7)) == [older]
    export_usage(tmp_path / "report.json", [row], issues=1)
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert payload["account"] == "unknown"
    assert payload["incomplete_records"] == 1
    assert payload["records"][0]["total"] == 180
    export_usage(tmp_path / "report.csv", [row])
    with (tmp_path / "report.csv").open(encoding="utf-8-sig", newline="") as stream:
        record = next(csv.DictReader(stream))
    assert record["project"].startswith("'=")
    assert record["source"] == "local_logs"


def test_local_dialog_filters_and_exports_same_records(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication

    from ui.i18n import configure_language
    from ui.local_analytics import LocalAnalyticsDialog

    app = QApplication.instance() or QApplication([])
    configure_language(app, "en")
    dialog = LocalAnalyticsDialog()
    dialog.period.setCurrentIndex(3)
    dialog._finished([LocalUsage("claude", "s1", "demo", date.today().isoformat(), "model-a", 1, 2, 0, 0, 3)], 0, False)
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 2).toolTip() == "3 Token"
    dialog._finished([LocalUsage("claude", "s1", "demo", date.today().isoformat(), "model-a", 100000000, 25000000, 0, 0, 125000000)], 0, False)
    assert dialog.table.item(0, 2).text() == "125M"
    configure_language(app, "zh-cn")
    assert dialog.table.item(0, 2).text() == "1.25亿"
    assert dialog.table.item(0, 2).toolTip() == "125,000,000 Token"
    dialog.project.setText("missing")
    assert dialog.table.rowCount() == 0
    assert not dialog.export_button.isEnabled()
    configure_language(app, "zh-cn")
