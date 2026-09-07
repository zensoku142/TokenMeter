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
    dialog.table_button.click()
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


def test_report_cache_is_scoped_and_rejects_malformed_counts(tmp_path):
    from data import history
    from data.local_usage import load_local_report, local_cache_scope, save_local_report

    scope = local_cache_scope({"codex": tmp_path})
    row = LocalUsage("codex", "demo", "project", "2026-09-07", "model", 1, 1, 0, 0, 2)
    save_local_report(scope, [row], 3)
    assert load_local_report(scope) == ([row], 3)
    assert load_local_report(local_cache_scope({"codex": tmp_path / "another"})) is None
    with history._connect() as connection:
        connection.execute("UPDATE local_usage_report SET payload=?", ('[{"total":true}]',))
    assert load_local_report(scope) is None


def test_worker_delivers_cached_report_before_refresh(monkeypatch, tmp_path):
    from unittest.mock import Mock

    from ui import local_analytics

    cached = LocalUsage("codex", "cached", "p", "2026-09-07", "m", 1, 1, 0, 0, 2)
    fresh = LocalUsage("codex", "fresh", "p", "2026-09-07", "m", 2, 2, 0, 0, 4)
    scanner = Mock(issues=0)
    scanner.scan.return_value = [fresh]
    monkeypatch.setattr(local_analytics, "load_local_report", lambda _: ([cached], 1))
    monkeypatch.setattr(local_analytics, "save_local_report", Mock())
    task = local_analytics._ScanTask(scanner, {"codex": tmp_path}, load_cache=True)
    events = []
    task.signals.cached.connect(lambda rows, issues: events.append(("cached", rows)))
    task.signals.finished.connect(lambda rows, issues, failed: events.append(("fresh", rows)))
    task.run()
    assert events == [("cached", [cached]), ("fresh", [fresh])]


def test_visible_page_auto_refreshes_and_hidden_page_stops_timer(monkeypatch):
    import time
    from unittest.mock import Mock

    from PySide6.QtWidgets import QApplication

    from ui.local_analytics import LocalAnalyticsDialog

    app = QApplication.instance() or QApplication([])
    dialog = LocalAnalyticsDialog()
    monkeypatch.setattr(dialog, "scan", Mock())
    dialog.show()
    app.processEvents()
    dialog.scan.assert_called_once_with()
    dialog.scanned = True
    dialog._last_scan = time.monotonic()
    dialog.hide()
    assert not dialog._auto_timer.isActive()
    dialog.show()
    app.processEvents()
    dialog.scan.assert_called_once_with()


def test_chart_and_table_share_filters_and_switch_without_scanning(monkeypatch):
    from unittest.mock import Mock

    from PySide6.QtWidgets import QApplication

    from ui.local_analytics import LocalAnalyticsDialog

    app = QApplication.instance() or QApplication([])
    dialog = LocalAnalyticsDialog()
    dialog.period.setCurrentIndex(3)
    rows = [LocalUsage("codex", "s", project, date.today().isoformat(), project, n, 0, 0, 0, n)
            for project, n in (("alpha", 100), ("beta", 200))]
    dialog._finished(rows, 0, False)
    monkeypatch.setattr(dialog, "scan", Mock())
    dialog.table_button.click()
    assert dialog.result_stack.currentWidget() is dialog.table
    dialog.project.setText("alpha")
    assert dialog.table.rowCount() == 1
    assert sum(values[0] for values in dialog._groups.values()) == 100
    dialog.chart_button.click()
    assert dialog.result_stack.currentWidget() is dialog.chart
    dialog.scan.assert_not_called()


def test_daily_models_fill_missing_days_and_keep_other_models_total():
    from data.local_usage import daily_model_series

    rows = [LocalUsage("codex", "s", "project", "2026-09-01", f"model-{index}", index + 1, 0, 0, 0, index + 1)
            for index in range(10)]
    rows.append(LocalUsage("codex", "s", "project", "2026-09-03", "model-9", 3, 0, 0, 0, 3))
    days, series = daily_model_series(rows, date(2026, 9, 1), date(2026, 9, 3))
    assert days == ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert ("", "其他") in series
    assert all(values[1] == 0 for values in series.values())
    assert sum(sum(values) for values in series.values()) == 58


def test_daily_chart_custom_dates_match_table_and_export_scope():
    from PySide6.QtCore import QDate
    from PySide6.QtWidgets import QApplication

    from ui.local_analytics import LocalAnalyticsDialog

    app = QApplication.instance() or QApplication([])
    dialog = LocalAnalyticsDialog()
    dialog.period.setCurrentIndex(4)
    dialog.start_date.setDate(QDate(2026, 9, 1))
    dialog.end_date.setDate(QDate(2026, 9, 3))
    rows = [LocalUsage("codex", "s", "p", day, model, total, 0, 0, 0, total) for day, model, total in (
        ("2026-09-01", "a", 100), ("2026-09-01", "b", 200),
        ("2026-09-03", "a", 300), ("2026-09-04", "a", 400),
    )]
    dialog._finished(rows, 0, False)
    dialog.chart_mode.setCurrentIndex(1)
    assert not dialog.group.isEnabled()
    assert dialog._daily_days == ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert dialog._daily_series["codex", "a"] == [100, 0, 300]
    assert dialog._daily_series["codex", "b"] == [200, 0, 0]
    dialog.table_button.click()
    assert dialog.table.rowCount() == 3
    assert sum(row.total for row in dialog.filtered_rows()) == 600


def test_interactive_legend_solo_reset_and_zoom_preservation():
    from PySide6.QtWidgets import QApplication

    from ui.local_analytics import LocalAnalyticsDialog

    app = QApplication.instance() or QApplication([])
    dialog = LocalAnalyticsDialog()
    dialog.period.setCurrentIndex(1)
    dialog._finished([LocalUsage("codex", "s", "p", date.today().isoformat(), model, count, 0, 0, 0, count)
                      for model, count in (("a", 100), ("b", 200))], 0, False)
    dialog.chart_mode.setCurrentIndex(1)
    dialog.legend_buttons["codex", "a"].click()
    assert ("codex", "a") not in dialog._visible_daily_series
    assert ("codex", "b") in dialog._visible_daily_series
    dialog._solo_model(("codex", "a"))
    assert list(dialog._visible_daily_series) == [("codex", "a")]
    dialog.reset_button.click()
    assert len(dialog._visible_daily_series) == 2
    dialog.chart.setXRange(-0.1, 0.1, padding=0)
    dialog._mark_chart_zoomed()
    bounds = dialog.chart.viewRange()[0]
    dialog.render()
    assert dialog.chart.viewRange()[0] == bounds
    dialog.reset_button.click()
    assert dialog.chart.viewRange()[0] != bounds


def test_unchanged_render_and_legend_toggle_do_not_rescan_records(monkeypatch):
    from unittest.mock import Mock

    from PySide6.QtWidgets import QApplication

    from ui.local_analytics import LocalAnalyticsDialog

    app = QApplication.instance() or QApplication([])
    dialog = LocalAnalyticsDialog()
    dialog.period.setCurrentIndex(3)
    dialog._finished([LocalUsage("codex", "s", "p", date.today().isoformat(), "a", 100, 0, 0, 0, 100)], 0, False)
    dialog.chart_mode.setCurrentIndex(1)
    monkeypatch.setattr(dialog, "filtered_rows", Mock(side_effect=AssertionError("unchanged records aggregated again")))
    dialog.render()
    dialog.legend_buttons["codex", "a"].click()
    assert dialog.table.rowCount() == 0


def test_daily_navigator_uses_minute_chart_density_and_synchronizes_range():
    from datetime import timedelta

    from PySide6.QtWidgets import QApplication

    from ui.local_analytics import LocalAnalyticsDialog
    from ui.qt_panel import MinuteUsageChart, adaptive_usage_bar_width

    app = QApplication.instance() or QApplication([])
    dialog = LocalAnalyticsDialog()
    dialog.period.setCurrentIndex(2)
    rows = [LocalUsage("codex", "s", "p", (date.today() - timedelta(days=day)).isoformat(), f"model-{model}", 100, 0, 0, 0, 100)
            for day in range(30) for model in range(6)]
    dialog._finished(rows, 0, False)
    dialog.chart_mode.setCurrentIndex(1)
    dialog.show()
    app.processEvents()
    low, high = dialog.chart.viewRange()[0]
    assert high - low == MinuteUsageChart.DEFAULT_VISIBLE_BUCKETS / 6
    assert dialog.navigator.height() == 34
    dialog.navigator_region.setRegion((5, 9))
    assert dialog.chart.viewRange()[0] == [5, 9]
    expected = adaptive_usage_bar_width(5, 9, dialog.chart.getViewBox().width(), 0.8 / 6)
    assert dialog._daily_bar_width == expected
    dialog.table_button.click()
    assert dialog.navigator.isHidden()


def test_chart_hover_has_exact_counts_and_plain_text_labels(monkeypatch):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtWidgets import QApplication

    from ui.local_analytics import LocalAnalyticsDialog

    app = QApplication.instance() or QApplication([])
    dialog = LocalAnalyticsDialog()
    dialog.period.setCurrentIndex(3)
    dialog._finished([LocalUsage("codex", "s", "p", date.today().isoformat(), "<img src=x>", 12345, 0, 0, 0, 12345)], 0, False)
    dialog.show()
    app.processEvents()
    view = dialog.chart.getPlotItem().getViewBox()
    dialog._chart_hover(view.mapViewToScene(QPointF(100, 0)))
    assert "12,345 Token" in dialog.hover_tooltip.total_label.accessibleDescription()
    from ui.qt_panel import MinuteUsageTooltip

    assert isinstance(dialog.hover_tooltip, MinuteUsageTooltip)
    assert "<img" in dialog.hover_tooltip.model_label.text()
    assert dialog.hover_tooltip.model_label.textFormat() == Qt.TextFormat.PlainText
    assert dialog.hover_tooltip.objectName() == "minuteTooltip"
