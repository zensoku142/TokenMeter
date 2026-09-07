from datetime import date
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from data.excel_export import NS, export_local_usage
from data.local_usage import LocalUsage


def records():
    return [
        LocalUsage("codex", "s1", "project", "2026-09-01", "=HYPERLINK(1)", 100, 20, 30, 0, 120),
        LocalUsage("codex", "s2", "project", "2026-09-01", "=HYPERLINK(1)", 200, 40, 50, 0, 240),
        LocalUsage("claude", "s3", "project", "2026-09-02", "<model>&\x00", 50, 10, 20, 5, 60),
    ]


def test_excel_contains_typed_summary_details_dates_and_metadata(tmp_path):
    path = tmp_path / "report.xlsx"
    export_local_usage(path, records(), issues=2, dimension="daily_model")
    with ZipFile(path) as archive:
        assert archive.testzip() is None
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        assert [sheet.get("name") for sheet in workbook.findall(f"{{{NS}}}sheets/{{{NS}}}sheet")] == ["统计汇总", "用量明细", "说明"]
        sheets = [ET.fromstring(archive.read(f"xl/worksheets/sheet{index}.xml")) for index in (1, 2, 3)]
        assert all(sheet.find(f"{{{NS}}}sheetViews/{{{NS}}}sheetView/{{{NS}}}pane").get("state") == "frozen" for sheet in sheets)
        summary = sheets[0]
        # 排序后的第一行是 Claude；第二行是两个 Codex 记录合计，保持数值类型。
        cell = summary.find(f".//{{{NS}}}c[@r='D3']")
        assert cell.find(f"{{{NS}}}v").text == "360"
        assert cell.get("t") is None
        assert summary.find(f".//{{{NS}}}c[@r='B3']").get("s") == "3"
        detail = sheets[1]
        assert len(detail.findall(f"{{{NS}}}sheetData/{{{NS}}}row")) == 4
        hostile = detail.find(f".//{{{NS}}}c[@r='E2']")
        assert hostile.get("t") == "inlineStr"
        assert hostile.find(f"{{{NS}}}is/{{{NS}}}t").text == "=HYPERLINK(1)"
        assert not any(sheet.findall(f".//{{{NS}}}f") for sheet in sheets)
        assert "未知" in "".join(sheets[2].itertext())


def test_failed_excel_export_preserves_existing_file(tmp_path, monkeypatch):
    import data.excel_export as module

    path = tmp_path / "existing.xlsx"
    path.write_bytes(b"keep existing file")
    monkeypatch.setattr(module, "_write_sheet", lambda *_args: (_ for _ in ()).throw(OSError("write failure")))
    with pytest.raises(OSError):
        export_local_usage(path, records())
    assert path.read_bytes() == b"keep existing file"
    assert list(tmp_path.iterdir()) == [path]


def test_excel_large_counts_are_preserved_without_float_rounding(tmp_path):
    path = tmp_path / "large.xlsx"
    value = 12345678901234567
    export_local_usage(path, [LocalUsage("codex", "s", "p", "2026-09-01", "m", value, 0, 0, 0, value)])
    with ZipFile(path) as archive:
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet2.xml"))
        cell = sheet.find(f".//{{{NS}}}c[@r='J2']")
        assert cell.get("t") == "inlineStr"
        assert cell.find(f"{{{NS}}}is/{{{NS}}}t").text == str(value)


def test_excel_ui_captures_filter_and_exports_in_worker(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QFileDialog

    from ui import local_analytics

    app = QApplication.instance() or QApplication([])
    dialog = local_analytics.LocalAnalyticsDialog()
    dialog.period.setCurrentIndex(3)
    today = date.today().isoformat()
    dialog._finished([LocalUsage("codex", "s", "keep", today, "m", 1, 0, 0, 0, 1),
                      LocalUsage("codex", "s", "skip", today, "m", 2, 0, 0, 0, 2)], 0, False)
    dialog.project.setText("keep")
    path = tmp_path / "result.xlsx"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: (str(path), "Excel (*.xlsx)"))
    tasks = []
    class Pool:
        def start(self, task):
            tasks.append(task)
        def waitForDone(self):
            pass
    monkeypatch.setattr(local_analytics.QThreadPool, "globalInstance", lambda: Pool())
    dialog.export()
    assert len(tasks) == 1
    assert not path.exists()
    assert len(tasks[0].rows) == 1
    assert tasks[0].rows[0].project == "keep"
    assert dialog._export_busy
    tasks[0].run()
    assert path.exists()
    assert not dialog._export_busy
