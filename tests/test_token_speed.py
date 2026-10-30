import json
from dataclasses import astuple
from datetime import datetime, timedelta
from unittest.mock import Mock

import pyqtgraph as pg
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QComboBox

from api.providers.base import ExactMinuteUsage, ProviderQuota
from api.providers.codex import CodexProvider
from api.providers.nayuto import NayutoProvider
from data import history
from data.local_usage import LocalUsage, LocalUsageScanner, load_local_report, save_local_report
from data.store import PerProviderData, TokenData
from ui import token_speed as speed_ui
from ui.i18n import configure_language
from ui.qt_panel import MainPanel, MinuteUsageTooltip
from ui.qt_theme import configure_theme
from ui.token_speed import TokenSpeedChart

# 与现有 UI 用例保持一致：整个模块持有应用，避免回收后重建 QApplication 导致 Qt 原生崩溃。
APP = QApplication.instance() or QApplication([])


def line(stamp, kind, payload):
    return {"timestamp": stamp.isoformat(), "type": kind, "payload": payload}


def request(start, *, output=400, seconds=10, baseline=0, model="model-a", delayed=False, usage_record=True):
    end = start + timedelta(seconds=seconds)
    usage = {"input_tokens": 1000, "cached_input_tokens": 500, "output_tokens": output, "total_tokens": 1000 + output}
    cumulative = {**usage, "input_tokens": 1000 + baseline, "total_tokens": 1000 + output + baseline}
    rows = [line(start, "turn_context", {"model": model}),
            line(start, "response_item", {"type": "message", "role": "user"}),
            line(end - timedelta(milliseconds=1), "response_item", {"type": "message", "role": "assistant"})]
    if usage_record:
        rows.append(line(end, "token_usage_record", {"usage": usage}))
    if delayed:
        end += timedelta(seconds=30)
        rows.append(line(end, "response_item", {"type": "function_call_output"}))
    rows.append(line(end, "event_msg", {"type": "token_count", "info": {
        "last_token_usage": usage, "total_token_usage": cumulative,
    }}))
    return rows


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


@pytest.mark.parametrize("usage_record", [False, True])
def test_history_backfills_request_timing_without_tool_wait_and_deduplicates(tmp_path, usage_record):
    start = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0) - timedelta(days=1)
    rows = [line(start, "session_meta", {"id": "s", "cwd": "C:/project"}),
            *request(start, delayed=True, usage_record=usage_record)]
    rows.append({**rows[-1], "timestamp": (start + timedelta(seconds=45)).isoformat()})
    path = tmp_path / "sessions/session.jsonl"
    write(path, rows)
    write(tmp_path / "archived_sessions/copy.jsonl", rows)
    scanner = LocalUsageScanner()
    first = scanner.scan({"codex": tmp_path})
    assert len(first) == 1
    assert first[0].day == start.date().isoformat()
    assert first[0].duration_ms == pytest.approx(10000, abs=1)
    with path.open("a", encoding="utf-8") as stream:
        for row in request(start + timedelta(hours=1), model="model-b", baseline=1400):
            stream.write(json.dumps(row) + "\n")
    updated = scanner.scan({"codex": tmp_path})
    assert len(updated) == 2
    assert {row.model for row in updated} == {"model-a", "model-b"}
    assert all(row.duration_ms == pytest.approx(10000, abs=1) for row in updated)
    provider = CodexProvider({"CODEX_HOME": str(tmp_path)})
    try:
        snapshot = provider.exact_minute_usage()
        assert len(snapshot.speed_rows) == 2
        assert {row["usage_date"] for row in snapshot.speed_rows} == {start.date()}
    finally:
        provider.close()


def test_aborted_request_does_not_inherit_idle_time_and_missing_timing_stays_unknown(tmp_path):
    stamp = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    old = stamp - timedelta(days=1)
    rows = [line(old, "turn_context", {"model": "old"}),
            line(old + timedelta(seconds=1), "response_item", {"type": "reasoning"}),
            line(old + timedelta(seconds=2), "event_msg", {"type": "turn_aborted"}),
            line(stamp, "event_msg", {"type": "task_started"}), *request(stamp, seconds=5)]
    write(tmp_path / "sessions/a.jsonl", rows)
    write(tmp_path / "sessions/b.jsonl", [request(stamp)[-1]])
    values = LocalUsageScanner().scan({"codex": tmp_path})
    assert sorted((row.duration_ms or 0) for row in values) == [0, 5000]


@pytest.mark.parametrize("columns", [10, 11, 12, 13])
def test_optional_duration_reads_each_legacy_cache_format(tmp_path, columns):
    row = LocalUsage("codex", "s", "p", "2026-10-28", "model", 100, 400, 20, 0, 500,
                     "2026-10-28T10:00:00+08:00", 10000, 1200)
    save_local_report("speed", [row], 0)
    with history._connect() as connection:
        connection.execute("UPDATE local_usage_report SET payload=?", (json.dumps([astuple(row)[:columns]]),))
    cached, issues = load_local_report("speed")
    assert issues == 0
    assert cached[0].duration_ms == (10000 if columns >= 12 else None)
    assert cached[0].first_token_ms == (1200 if columns >= 13 else None)


def speed_row(stamp, output, duration, model="model-a"):
    return {"usage_date": stamp.date(), "timestamp": stamp.isoformat(), "model": model,
            "output_tokens": output, "duration_ms": duration}


def test_chart_uses_weighted_model_buckets_and_clickable_legend_without_zero_samples():
    app = APP
    configure_theme(app, "dark")
    stamp = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    chart = TokenSpeedChart()
    chart.set_rows([speed_row(stamp, 1000, 10000), speed_row(stamp, 200, 20000),
                    speed_row(stamp, 500, 10000, "model-b"),
                    speed_row(stamp, 199, 10000, "too-short"), speed_row(stamp, 400, 999, "bad-duration"),
                    speed_row(stamp + timedelta(hours=1), 600, 10000)], stamp.date().isoformat())
    assert len(chart._points) == 2
    assert chart._points[0]["speed"] == 10
    assert {point["model"] for point in chart._points} == {"model-a"}
    assert set(chart.legend_buttons) == {"model-a", "model-b"}
    assert chart.legend_buttons["model-a"].isChecked()
    assert not chart.legend_buttons["model-b"].isChecked()
    assert [point["x"] for point in chart._points] == [0, 1]
    assert chart._points[0]["open"] == 100
    assert chart._points[0]["high"] == 100 and chart._points[0]["low"] == 10
    assert chart._points[-1]["open"] == 10 and chart._points[-1]["speed"] == 60
    assert chart.plot.getAxis("bottom").tickStrings([0, 1], 1, 1) == ["10:00", "11:00"]
    assert "均速 ≈45.0" in chart.summary.text()
    assert not chart.findChildren(QComboBox)
    chart.legend_buttons["model-b"].click()
    assert len(chart._points) == 1
    assert chart._points[0]["model"] == "model-b"
    assert chart._points[0]["output"] == 500
    assert chart.legend_buttons["model-b"].isChecked()
    assert not chart.legend_buttons["model-a"].isChecked()
    assert "均速 ≈50.0" in chart.summary.text()
    chart.legend_buttons["model-b"].click()
    assert chart.legend_buttons["model-b"].isChecked()
    assert len(chart._points) == 1
    chart._show_hover(chart._points[0], QPoint(100, 50))
    assert "≈50.0" in chart.tooltip.total_label.text()
    chart.legend_buttons["model-a"].click()
    assert len(chart._points) == 2
    assert {point["model"] for point in chart._points} == {"model-a"}
    assert not chart.tooltip.isVisible()
    assert len(chart._rows) == 4
    chart.close()


def test_model_switch_keeps_kline_open_and_hover_within_selected_model():
    stamp = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    chart = TokenSpeedChart()
    chart.set_rows([speed_row(stamp, 400, 10000, "model-a"),
                    speed_row(stamp + timedelta(minutes=5), 600, 10000, "model-b"),
                    speed_row(stamp + timedelta(minutes=10), 300, 10000, "model-a"),
                    speed_row(stamp + timedelta(minutes=10), 200, 10000, "model-b")],
                   stamp.date().isoformat())
    primary = list(chart._hover_primary.values())
    assert [point["model"] for point in primary] == ["model-b", "model-b"]
    assert [point["open"] for point in primary] == [60, 60]
    assert [point["speed"] for point in primary] == [60, 20]
    chart.legend_buttons["model-a"].click()
    assert all(point["model"] == "model-a" for point in chart._points)
    assert list(chart._hover_primary) == [0, 1]
    assert [point["open"] for point in chart._points] == [40, 40]
    assert [point["speed"] for point in chart._points] == [40, 30]
    assert chart.plot.getAxis("bottom").minutes == [600, 610]
    nav_bars = next(item for item in chart.navigator.items() if isinstance(item, pg.BarGraphItem))
    assert nav_bars.opts["height"] == [40, 30]
    chart._show_hover(chart._points[1], QPoint(100, 50))
    assert chart.tooltip.model_label.text() == "model-a"
    assert chart.tooltip.total_label.text() == "≈30.0 token/s"
    chart.close()


def test_model_selection_survives_refresh_and_falls_back_when_selected_model_disappears():
    stamp = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    today = stamp.date().isoformat()
    chart = TokenSpeedChart()
    rows = [speed_row(stamp, 400, 10000), speed_row(stamp, 300, 10000, "model-b")]
    chart.set_rows(rows, today)
    chart.legend_buttons["model-b"].click()
    chart.set_rows([*rows, speed_row(stamp + timedelta(minutes=5), 800, 10000)], today)
    assert {point["model"] for point in chart._points} == {"model-b"}
    assert chart.legend_buttons["model-b"].isChecked()
    chart.set_rows(rows, today, status="failed")
    assert {point["model"] for point in chart._points} == {"model-b"}
    assert chart.legend_buttons["model-b"].isChecked()
    chart.set_rows([rows[0]], today)
    assert {point["model"] for point in chart._points} == {"model-a"}
    assert set(chart.legend_buttons) == {"model-a"}
    assert chart.legend_buttons["model-a"].isChecked()
    chart.set_rows([], today)
    assert not chart._points and not chart.legend_buttons
    assert "暂无可计速请求" in chart.state_label.text()
    chart.set_rows([rows[1]], today)
    assert {point["model"] for point in chart._points} == {"model-b"}
    assert chart.legend_buttons["model-b"].isChecked()
    chart.close()


def test_dense_speed_chart_navigates_without_permanent_main_line_dots():
    stamp = datetime.now().astimezone().replace(hour=8, minute=0, second=0, microsecond=0)
    chart = TokenSpeedChart()
    chart.resize(640, 230)
    rows = [speed_row(stamp + timedelta(minutes=5 * index), 400 + index, 10000)
            for index in range(72)]
    rows.extend(speed_row(stamp + timedelta(minutes=15 * index), 300, 10000, "model-b")
                for index in range(8))
    chart.set_rows(rows, stamp.date().isoformat())
    chart.show()
    APP.processEvents()
    assert len(chart._points) == 72
    dots = [item for item in chart._items if isinstance(item, pg.ScatterPlotItem) and item is not chart._hover_marker]
    assert sum(len(item.data) for item in dots) == 0
    assert not chart.navigator.isHidden()
    assert chart.region.getRegion() == pytest.approx((47.5, 71.5))
    chart.region.setRegion((10, 20))
    assert chart.plot.getViewBox().viewRange()[0] == pytest.approx([10, 20])
    zoom_width = chart._candles[0].opts["width"]
    chart.region.setRegion((-0.5, 71.5))
    dense_width = chart._candles[0].opts["width"]
    assert dense_width <= 0.56
    assert zoom_width < dense_width
    assert len(chart._points) == 72
    chart.plot.getViewBox().setXRange(30, 40, padding=0)
    assert chart.region.getRegion() == pytest.approx((30, 40))
    chart._show_hover(chart._points[35], QPoint(100, 50))
    assert chart._hover_marker.isVisible()
    chart._hide_hover()
    assert not chart._hover_marker.isVisible()
    chart.legend_buttons["model-b"].click()
    assert len(chart._points) == 8
    assert chart.region.getRegion() == pytest.approx((-0.5, 7.5))
    chart.legend_buttons["model-a"].click()
    assert len(chart._points) == 72
    assert chart.region.getRegion() == pytest.approx((47.5, 71.5))
    chart.close()


@pytest.mark.parametrize("first_ms,expected", [(1500, 1500), (-100, None), (11000, None)])
def test_first_token_uses_recorded_model_start_and_never_guesses_completion(tmp_path, first_ms, expected):
    start = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    rows = request(start)
    rows.insert(2, line(start + timedelta(seconds=9), "event_msg", {
        "type": "item_completed", "item": {"type": "Reasoning"},
        "started_at_ms": round(start.timestamp() * 1000) + first_ms,
    }))
    write(tmp_path / "sessions/s.jsonl", rows)
    record = LocalUsageScanner().scan({"codex": tmp_path})[0]
    assert record.duration_ms == 10000
    assert record.first_token_ms == expected


def test_hover_caches_content_tracks_curve_and_does_not_reopen_after_leave(monkeypatch):
    stamp = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    chart = TokenSpeedChart()
    chart.resize(640, 230)
    first = {**speed_row(stamp, 400, 10000), "first_token_ms": 1200}
    chart.set_rows([first, speed_row(stamp + timedelta(minutes=5), 800, 10000)], stamp.date().isoformat())
    chart.show()
    APP.processEvents()
    content_spy = Mock(wraps=chart.tooltip.set_speed_values)
    layout_calls = Mock(wraps=speed_ui.show_usage_tooltip)
    monkeypatch.setattr(chart.tooltip, "set_speed_values", content_spy)
    monkeypatch.setattr("ui.token_speed.show_usage_tooltip", layout_calls)
    for offset in range(100):
        chart._show_hover(chart._points[0], QPoint(130 + offset % 10, 65))
    assert content_spy.call_count == 1
    assert layout_calls.call_args_list[0].kwargs["refresh_layout"]
    assert all(not call.kwargs["refresh_layout"] for call in layout_calls.call_args_list[1:])
    assert isinstance(chart.tooltip, MinuteUsageTooltip)
    assert chart.tooltip.time_label.text() == "10:00–10:04"
    assert chart.tooltip.model_label.text() == "model-a"
    assert chart.tooltip.first_token_label.text() == "≈1.20s"
    assert chart.tooltip.total_label.text() == "≈40.0 token/s"
    APP.processEvents()
    assert chart.tooltip.time_label.mapTo(chart.tooltip, QPoint()).y() < chart.tooltip.model_label.mapTo(chart.tooltip, QPoint()).y()
    model_center = chart.tooltip.model_label.mapTo(chart.tooltip, QPoint()).y() + chart.tooltip.model_label.height() / 2
    speed_center = chart.tooltip.total_label.mapTo(chart.tooltip, QPoint()).y() + chart.tooltip.total_label.height() / 2
    assert abs(model_center - speed_center) <= 1
    assert chart.tooltip.model_label.mapTo(chart.tooltip, QPoint()).x() < chart.tooltip.total_label.mapTo(chart.tooltip, QPoint()).x()
    assert chart.tooltip.first_token_label.mapTo(chart.tooltip, QPoint()).y() > chart.tooltip.model_label.mapTo(chart.tooltip, QPoint()).y()
    assert chart.tooltip.value_labels == chart.tooltip.swatches == []
    point = chart._hover_primary[0]
    scene = chart.plot.getViewBox().mapViewToScene(QPointF(point["x"], point["speed"]))
    chart._pointer_inside = True
    chart._on_mouse_moved((scene,))
    assert chart.tooltip.isVisible()
    dots = [item for item in chart._items if isinstance(item, pg.ScatterPlotItem)]
    assert all(not item.opts["hoverable"] and not item.acceptHoverEvents() and not item.opts["useCache"] for item in dots)
    APP.sendEvent(chart._plot_viewport, QEvent(QEvent.Type.Leave))
    chart._on_mouse_moved((scene,))
    assert not chart.tooltip.isVisible()
    assert chart._hover_key is None
    chart.close()


def test_hover_burst_is_coalesced_and_missing_first_token_stays_unknown():
    stamp = datetime.now().astimezone()
    chart = TokenSpeedChart()
    chart.set_rows([speed_row(stamp, 400, 10000)], stamp.date().isoformat())
    spy = QSignalSpy(chart._mouse_proxy.sigDelayed)
    for _index in range(500):
        chart.plot.scene().sigMouseMoved.emit(QPointF(10, 10))
    QTest.qWait(50)
    assert 1 <= spy.count() < 5
    chart._show_hover(chart._points[0], QPoint(100, 50))
    assert chart.tooltip.first_token_label.text() == "--"
    chart.close()


def test_first_token_follows_last_request_in_bucket():
    stamp = datetime.now().astimezone()
    chart = TokenSpeedChart()
    rows = [{**speed_row(stamp, 400, 10000), "first_token_ms": value} for value in (0, 2000, None)]
    chart.set_rows(rows, stamp.date().isoformat())
    assert chart._points[0]["first_token_ms"] is None
    assert chart._points[0]["speed"] == 40
    chart.close()


def test_nayuto_speed_uses_verified_duration_without_affecting_billing():
    records = [{"id": i, "request_id": str(i), "model": f"model-{i}", "created_at": "2026-10-28T02:00:00Z",
                "input_tokens": 10, "cache_read_tokens": 20, "output_tokens": 400, "actual_cost": "0.1",
                "duration_ms": duration, "status": status}
               for i, (duration, status) in enumerate([(10000, "completed"), (None, "completed"),
                                                       (-1, "completed"), (10000, "failed")])]
    payloads, snapshot, dirty = NayutoProvider._normalize_records(records, {(10, 2026)})
    assert dirty == 0
    assert len(snapshot.speed_rows) == 4
    assert snapshot.speed_rows[0]["duration_ms"] == 10000
    assert sum(row["token_amount"] for row in snapshot.token_rows) == 4 * 430
    assert payloads


def test_latest_valid_request_survives_shorter_request_and_kline_uses_real_samples():
    stamp = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    chart = TokenSpeedChart()
    rows = [speed_row(stamp, 400, 10000),
            speed_row(stamp + timedelta(seconds=10), 600, 10000),
            speed_row(stamp + timedelta(seconds=20), 300, 10000),
            speed_row(stamp + timedelta(minutes=5), 500, 10000),
            speed_row(stamp + timedelta(minutes=6), 100, 10000, "short-model")]
    chart.set_rows(rows, stamp.date().isoformat())
    assert "最近 ≈50.0" in chart.summary.text()
    assert "10:05:00" in chart.summary.toolTip() and "model-a" in chart.summary.toolTip()
    first, second = chart._points
    assert (first["open"], first["high"], first["low"], first["speed"]) == (40, 60, 30, 30)
    assert (second["open"], second["high"], second["low"], second["speed"]) == (30, 50, 30, 50)
    assert len(chart._candles) == 2
    chart.set_rows(rows[:-1], stamp.date().isoformat())
    assert "最近 ≈50.0" in chart.summary.text()
    chart.legend_buttons["model-a"].click()
    assert "最近 ≈50.0" in chart.summary.text()
    chart.close()


def test_kline_ma5_uses_only_five_observed_intervals():
    stamp = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    chart = TokenSpeedChart()
    rows = [speed_row(stamp + timedelta(minutes=5 * index), 200, duration)
            for index, duration in enumerate([10000, 8000, 5000, 4000, 2000, 1000])]
    chart.set_rows(rows, stamp.date().isoformat())
    assert [point["speed"] for point in chart._points] == [20, 25, 40, 50, 100, 200]
    assert list(chart._ma_line.xData) == [4, 5]
    assert list(chart._ma_line.yData) == [47, 83]
    other_rows = [speed_row(stamp + timedelta(minutes=5 * index), 400, 1000, "model-b")
                  for index in range(6)]
    chart.set_rows([*rows, *other_rows], stamp.date().isoformat())
    assert {point["model"] for point in chart._points} == {"model-a"}
    assert list(chart._ma_line.yData) == [47, 83]
    chart.legend_buttons["model-b"].click()
    assert {point["model"] for point in chart._points} == {"model-b"}
    assert list(chart._ma_line.yData) == [400, 400]
    chart.legend_buttons["model-a"].click()
    assert list(chart._ma_line.yData) == [47, 83]
    chart.set_rows(rows[:4], stamp.date().isoformat())
    assert chart._ma_line is None
    assert chart.ma_label.isHidden()
    chart.close()


def test_latest_failed_request_does_not_hide_valid_speed_and_cached_label_is_clear():
    stamp = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    chart = TokenSpeedChart()
    rows = [speed_row(stamp, 400, 10000),
            {**speed_row(stamp + timedelta(seconds=30), 600, 10000, "failed-model"), "status": "failed"}]
    chart.set_rows(rows, stamp.date().isoformat())
    assert "最近 ≈40.0" in chart.summary.text()
    assert "model-a" in chart.summary.toolTip()
    assert len(chart._points) == 1
    chart.set_rows(rows, stamp.date().isoformat(), status="failed")
    assert "缓存 ≈40.0" in chart.summary.text()
    chart.close()


def test_latest_speed_is_chosen_by_timestamp_and_empty_when_no_valid_request():
    stamp = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    chart = TokenSpeedChart()
    rows = [speed_row(stamp + timedelta(minutes=2), 100, 10000, "short"),
            speed_row(stamp + timedelta(minutes=1), 600, 10000, "newest-valid"),
            speed_row(stamp, 400, 10000, "older-valid"),
            {**speed_row(stamp + timedelta(minutes=3), 500, 10000, "failed"), "status": "failed"}]
    chart.set_rows(rows, stamp.date().isoformat())
    assert "最近 ≈60.0" in chart.summary.text()
    assert "10:01:00" in chart.summary.toolTip() and "newest-valid" in chart.summary.toolTip()
    chart.legend_buttons["newest-valid"].click()
    assert "最近 ≈60.0" in chart.summary.text()
    chart.set_rows([rows[0], rows[3]], stamp.date().isoformat())
    assert "最近 --" in chart.summary.text()
    chart.close()


def test_directory_switch_clears_speed_snapshot_but_same_scope_failure_keeps_it(tmp_path, monkeypatch):
    stamp = datetime.now().astimezone()
    row = speed_row(stamp, 400, 10000)
    first = CodexProvider({"CODEX_HOME": str(tmp_path / "first")})
    second = CodexProvider({"CODEX_HOME": str(tmp_path / "second")})
    monkeypatch.setattr(TokenData, "_provider_snapshots", {})
    monkeypatch.setattr(TokenData, "_last_snapshot", None)
    for provider in (first, second):
        provider.is_configured = Mock(return_value=True)
        provider.snapshot_identity = Mock(return_value="same-account")
        provider.fetch_quota = Mock(return_value=(ProviderQuota(), None))
    first.exact_minute_usage = Mock(return_value=ExactMinuteUsage(usage_dates=(stamp.date(),), speed_rows=(row,)))
    second.exact_minute_usage = Mock(return_value=None)
    try:
        initial = TokenData._fetch_with_provider(first, stamp.date(), False)
        assert initial.token_speed_usage == [row]
        first.exact_minute_usage.return_value = None
        fallback = TokenData._fetch_with_provider(first, stamp.date(), False)
        assert fallback.token_speed_usage == [row]
        switched = TokenData._fetch_with_provider(second, stamp.date(), False)
        assert switched.token_speed_usage == []
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("provider,source", [("codex", "local"), ("nayuto", "provider")])
@pytest.mark.parametrize("width,language", [(640, "zh-cn"), (820, "zh-cn"), (640, "en"), (820, "en")])
def test_speed_tab_uses_model_legends_and_keeps_date_refresh_theme_and_other_tabs(provider, source, width, language):
    app = APP
    controller = configure_language(app, "zh-cn")
    configure_theme(app, "dark")
    stamp = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    today = stamp.date().isoformat()
    data = TokenData(per_provider=[PerProviderData(provider, provider)], minute_usage_date=today,
                     minute_usage_days=[today], minute_usage_status="recorded", minute_usage_source=source,
                     token_speed_usage=[speed_row(stamp, 400, 10000), speed_row(stamp, 500, 10000, "model-b")])
    panel = MainPanel()
    panel.resize(width, 550)
    panel.update_data(data)
    assert panel._speed_chart is None
    panel.speed_activity_button.click()
    panel.show()
    controller.set_language(language)
    app.processEvents()
    try:
        assert panel._activity_view == "speed"
        assert panel.activity_stack.currentWidget() is panel.speed_chart
        assert len(panel.speed_chart._points) == 1
        assert panel.speed_chart._points[0]["model"] == "model-b"
        assert not panel.speed_legend_slot.isHidden()
        assert all(button.isHidden() for button in panel.minute_legend_buttons.values())
        assert panel.activity_summary.isHidden()
        buttons = list(panel.speed_chart.legend_buttons.values())
        hint = "Click to switch to this model" if language == "en" else "点击切换到此模型"
        assert all(hint in button.toolTip() for button in buttons)
        legend_center = (buttons[0].geometry().left() + buttons[-1].geometry().right()) / 2
        assert abs(legend_center - panel.speed_chart.legend_content.rect().center().x()) <= 2
        panel.speed_chart.legend_buttons["model-a"].click()
        assert panel.speed_chart._points[0]["model"] == "model-a"
        assert sum(button.isChecked() for button in buttons) == 1
        controls = [panel.activity_mode_segment, panel.minute_date_edit, panel.minute_estimate_label,
                    panel.speed_legend_slot, panel.quota_details_button]
        rects = [QRect(control.mapTo(panel, QPoint()), control.size()) for control in controls if control.isVisible()]
        assert panel.width() == width
        assert all(rect.left() >= 0 and rect.right() < width for rect in rects)
        assert all(not left.intersects(right) for index, left in enumerate(rects) for right in rects[index + 1:])
        panel.update_data(data)
        assert panel._activity_view == "speed"
        assert panel.speed_chart._points[0]["model"] == "model-a"
        assert all(button.isHidden() for button in panel.minute_legend_buttons.values())
        configure_theme(app, "light")
        assert len(panel.speed_chart._points) == 1
        assert panel.speed_chart._points[0]["model"] == "model-a"
        panel.minute_activity_button.click()
        assert all(not button.isHidden() for button in panel.minute_legend_buttons.values())
        assert panel.speed_legend_slot.isHidden()
        panel.annual_activity_button.click()
        assert panel._activity_view == "annual"
        data.per_provider = [PerProviderData("claude", "Claude")]
        data.minute_usage_status = "unavailable"
        panel.update_data(data)
        assert panel.speed_activity_button.isHidden()
    finally:
        controller.set_language("zh-cn")
        configure_theme(app, "dark")
        panel.close()
