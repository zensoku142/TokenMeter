"""Dependency-free Excel export for the desktop runtime's local usage report."""

from __future__ import annotations

import os
import re
import tempfile
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _cell(index: int, row: int, value, *, header=False) -> ET.Element:
    column = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        column = chr(65 + remainder) + column
    cell = ET.Element("c", r=f"{column}{row}")
    if header:
        cell.set("s", "1")
    if isinstance(value, date):
        cell.set("s", "3")
        ET.SubElement(cell, "v").text = str((value - date(1899, 12, 30)).days)
    elif type(value) is int and abs(value) < 10 ** 15:
        cell.set("s", "2")
        ET.SubElement(cell, "v").text = str(value)
    else:
        # inlineStr 永远是文字而非公式；超出 Excel 15 位精度的整数也按文字保留精确值。
        cell.set("t", "inlineStr")
        text = ET.SubElement(ET.SubElement(cell, "is"), "t")
        text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        text.text = _INVALID_XML.sub("", str(value or ""))[:32767]
    return cell


def _write_sheet(archive: ZipFile, number: int, headers, records, widths) -> None:
    with archive.open(f"xl/worksheets/sheet{number}.xml", "w") as stream:
        stream.write(f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{NS}">'.encode())
        stream.write(b'<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="18"/>')
        columns = ET.Element("cols")
        for index, width in enumerate(widths, 1):
            ET.SubElement(columns, "col", min=str(index), max=str(index), width=str(width), customWidth="1")
        stream.write(ET.tostring(columns))
        stream.write(b"<sheetData>")
        last = 1
        for index, values in enumerate(_with_header(headers, records), 1):
            if index > 1048576:
                raise ValueError("Too many rows for one Excel sheet")
            row = ET.Element("row", r=str(index), ht="30" if index == 1 else "18", customHeight="1")
            for column, value in enumerate(values, 1):
                row.append(_cell(column, index, value, header=index == 1))
            stream.write(ET.tostring(row, encoding="utf-8"))
            last = index
            if index % 256 == 0:
                # 逐行写入并让出执行权，导出大日志时不构造数十万 XML 节点常驻内存。
                time.sleep(0)
        stream.write(b"</sheetData>")
        end = _cell(len(headers), last, "").get("r")
        stream.write(f'<autoFilter ref="A1:{end}"/></worksheet>'.encode())


def _with_header(headers, rows):
    yield headers
    yield from rows


_STYLES = f'''<?xml version="1.0" encoding="UTF-8"?>
<styleSheet xmlns="{NS}">
<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd"/></numFmts>
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF2F72E8"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>
<xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''


def export_local_usage(path: Path, rows, *, issues: int = 0, dimension: str = "model") -> None:
    names = {"model": "模型", "project": "项目", "session": "会话", "day": "日期", "daily_model": "日期 / 模型"}
    if dimension not in names:
        raise ValueError("Unknown report dimension")
    groups = defaultdict(lambda: [0, 0, 0, 0, 0])
    for row in rows:
        key = (row.day, row.model) if dimension == "daily_model" else (getattr(row, dimension),)
        values = groups[(row.provider, *key)]
        for index, value in enumerate((row.total, row.input, row.output, row.cache_read, row.cache_write)):
            values[index] += value
    summary_headers = ["平台", *( ("日期", "模型") if dimension == "daily_model" else (names[dimension],)),
                       "总 Token", "输入（含缓存）", "输出", "缓存读取", "缓存写入"]
    summary = []
    for key, counts in sorted(groups.items()):
        parts = list(key)
        if dimension in {"day", "daily_model"}:
            parts[1] = date.fromisoformat(parts[1])
        summary.append([*parts, *counts])
    metadata = [
        ("数据来源", "所选目录的本机日志"), ("账户归属", "未知"),
        ("说明", "静态导出；不代表订阅额度或实际账单。输入已包含缓存，计算总量时不重复累加缓存。"),
        ("统计分组", names[dimension]), ("时区", str(datetime.now().astimezone().tzinfo)),
        ("记录数", len(rows)), ("读取异常数", issues),
        ("最早记录日期", min((row.day for row in rows), default="")),
        ("最晚记录日期", max((row.day for row in rows), default="")),
        ("导出时间", datetime.now().isoformat(timespec="seconds")),
    ]
    sheets = ("统计汇总", "用量明细", "说明")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".xlsx", delete=False) as handle:
            temporary = Path(handle.name)
        with ZipFile(temporary, "w", ZIP_DEFLATED, compresslevel=6) as archive:
            types = ET.Element("Types", xmlns="http://schemas.openxmlformats.org/package/2006/content-types")
            ET.SubElement(types, "Default", Extension="rels", ContentType="application/vnd.openxmlformats-package.relationships+xml")
            ET.SubElement(types, "Default", Extension="xml", ContentType="application/xml")
            for part, kind in [("/xl/workbook.xml", "sheet.main"), ("/xl/styles.xml", "styles"),
                               *[(f"/xl/worksheets/sheet{i}.xml", "worksheet") for i in range(1, 4)]]:
                ET.SubElement(types, "Override", PartName=part, ContentType=f"application/vnd.openxmlformats-officedocument.spreadsheetml.{kind}+xml")
            archive.writestr("[Content_Types].xml", ET.tostring(types, encoding="utf-8"))
            rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
            office_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            root = ET.Element("Relationships", xmlns=rel_ns)
            ET.SubElement(root, "Relationship", Id="rId1", Type=f"{office_ns}/officeDocument", Target="xl/workbook.xml")
            archive.writestr("_rels/.rels", ET.tostring(root))
            workbook = ET.Element("workbook", xmlns=NS, attrib={"xmlns:r": office_ns})
            sheet_list = ET.SubElement(workbook, "sheets")
            relations = ET.Element("Relationships", xmlns=rel_ns)
            for index, name in enumerate(sheets, 1):
                ET.SubElement(sheet_list, "sheet", name=name, sheetId=str(index), attrib={"r:id": f"rId{index}"})
                ET.SubElement(relations, "Relationship", Id=f"rId{index}", Type=f"{office_ns}/worksheet", Target=f"worksheets/sheet{index}.xml")
            ET.SubElement(relations, "Relationship", Id="rIdStyles", Type=f"{office_ns}/styles", Target="styles.xml")
            archive.writestr("xl/workbook.xml", ET.tostring(workbook, encoding="utf-8"))
            archive.writestr("xl/_rels/workbook.xml.rels", ET.tostring(relations))
            archive.writestr("xl/styles.xml", _STYLES)
            _write_sheet(archive, 1, summary_headers, summary, [18, *([16, 28] if dimension == "daily_model" else [32]), *([20] * 5)])
            details = ((row.provider, row.session, row.project, date.fromisoformat(row.day), row.model,
                        row.input, row.output, row.cache_read, row.cache_write, row.total) for row in rows)
            _write_sheet(archive, 2, ("平台", "会话", "项目", "日期", "模型", "输入（含缓存）", "输出", "缓存读取", "缓存写入", "总 Token"),
                         details, [18, 28, 28, 16, 28, *([20] * 5)])
            _write_sheet(archive, 3, ("项目", "说明"), metadata, [24, 100])
        # 写完整工作簿后原子替换；文件被 Excel 占用或导出失败时保留用户已有文件。
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
