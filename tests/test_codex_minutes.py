import json
from dataclasses import astuple
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication

from api.providers.base import FetchError, ProviderQuota, QuotaWindow
from api.providers.codex import CodexProvider
from data import history
from data.local_usage import LocalUsage, LocalUsageScanner, load_local_report, save_local_report
from data.store import PerProviderData, TokenData
from ui.i18n import configure_language
from ui.qt_panel import MainPanel


def write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def event(stamp, total, last=None):
    info = {"total_token_usage": dict(zip(
        ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens"), total,
    ))}
    if last is not None:
        info["last_token_usage"] = dict(zip(
            ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens"), last,
        ))
    return {"timestamp": stamp.isoformat(), "type": "event_msg",
            "payload": {"type": "token_count", "info": info}}


def session_records(stamp):
    return [
        {"type": "session_meta", "payload": {"id": "session", "cwd": "C:/work/project"}},
        {"type": "turn_context", "payload": {"model": "test-model"}},
        event(stamp, (98488, 97792, 167, 98655), (98488, 97792, 167, 98655)),
    ]


def test_scanner_keeps_request_time_and_does_not_recount_inherited_or_repeated_usage(tmp_path):
    stamp = datetime.now().astimezone().replace(hour=11, minute=12, second=0, microsecond=0)
    records = session_records(stamp)
    # 续写文件继承历史累计数，但 last 是当前请求；额度刷新重发时不能重复累计。
    records[2] = event(stamp, (198488, 177792, 1167, 199655), (98488, 97792, 167, 98655))
    records.append(event(stamp + timedelta(seconds=2), (198488, 177792, 1167, 199655),
                         (98488, 97792, 167, 98655)))
    write(tmp_path / "sessions/log.jsonl", records)
    scanner = LocalUsageScanner()
    rows = scanner.scan({"codex": tmp_path})
    assert len(rows) == 1
    assert rows[0].timestamp == stamp.isoformat()
    assert rows[0].total == 98655
    assert rows[0].input - rows[0].cache_read == 696
    assert rows[0].output == 167
    assert scanner.scan({"codex": tmp_path}) is rows


def test_timestamp_cache_round_trip_and_legacy_snapshot(tmp_path):
    stamp = datetime.now(timezone.utc).isoformat()
    row = LocalUsage("codex", "s", "p", date.today().isoformat(), "m", 10, 2, 3, 0, 12, stamp)
    save_local_report("scope", [row], 0)
    assert load_local_report("scope") == ([row], 0)
    # 旧的固定列快照缺少时间列，仍须能作为每日统计缓存读取。
    with history._connect() as connection:
        connection.execute("UPDATE local_usage_report SET payload=?", (json.dumps([astuple(row)[:10]]),))
    assert load_local_report("scope")[0][0].timestamp == ""


def test_minutes_follow_local_time_deduplicate_archives_and_incrementally_append(tmp_path):
    stamp = datetime.now().astimezone().replace(hour=11, minute=12, second=0, microsecond=0)
    path = tmp_path / "sessions/log.jsonl"
    records = session_records(stamp.astimezone(timezone.utc))
    yesterday = stamp - timedelta(days=1)
    records.insert(2, event(yesterday, (1000, 500, 100, 1100)))
    records[3] = event(stamp.astimezone(timezone.utc), (99488, 98292, 267, 99755),
                       (98488, 97792, 167, 98655))
    write(path, records)
    write(tmp_path / "archived_sessions/copy.jsonl", records)
    provider = CodexProvider({"CODEX_HOME": str(tmp_path)})
    try:
        snapshot = provider.exact_minute_usage()
        today_rows = [row for row in snapshot.token_rows if row["usage_date"] == stamp.date()]
        assert {row["minute"] for row in today_rows} == {672}
        assert {row["token_type"]: row["token_amount"] for row in today_rows} == {
            "PROMPT_CACHE_HIT_TOKEN": 97792, "PROMPT_CACHE_MISS_TOKEN": 696, "RESPONSE_TOKEN": 167,
        }
        assert snapshot.cost_rows == ()
        assert snapshot.model_rows[-1]["model"] == "test-model"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event(stamp + timedelta(minutes=1),
                                         (99588, 98342, 277, 99865), (100, 50, 10, 110))) + "\n")
        updated = provider.exact_minute_usage()
        assert sum(row["token_amount"] for row in updated.token_rows) == 1100 + 98655 + 110
        assert provider.exact_minute_usage() == updated
    finally:
        provider.close()


def test_store_loads_local_minutes_without_payloads_and_keeps_official_quota(tmp_path, monkeypatch):
    stamp = datetime.now().astimezone().replace(hour=11, minute=12, second=0, microsecond=0)
    write(tmp_path / "sessions/log.jsonl", session_records(stamp))
    provider = CodexProvider({"CODEX_HOME": str(tmp_path)})
    provider.is_configured = Mock(return_value=True)
    provider.snapshot_identity = Mock(return_value="test-account")
    quota = ProviderQuota(
        windows=(QuotaWindow("primary", "5 小时额度", 25),),
        activity=((stamp.date().isoformat(), 500000),), activity_source="interface",
    )
    provider.fetch_quota = Mock(return_value=(quota, None))
    monkeypatch.setattr(TokenData, "_provider_snapshots", {})
    monkeypatch.setattr(TokenData, "_last_snapshot", None)
    try:
        data = TokenData._fetch_with_provider(provider, stamp.date(), False)
        assert data.minute_usage_status == "recorded"
        assert data.minute_usage_source == "local"
        assert data.history_provider == provider.local_usage_scope
        assert sum(row["token_amount"] for row in data.minute_usage) == 98655
        assert data.minute_model_usage[0]["model"] == "test-model"
        assert data.minute_cost_usage == []
        assert data.daily_usage[0]["tokens"] == 500000
        assert data.quota_windows == list(quota.windows)
        provider.fetch_quota.return_value = (None, FetchError("NETWORK_TIMEOUT", "Codex", "timeout"))
        offline = TokenData._fetch_with_provider(provider, stamp.date(), False)
        assert offline.minute_usage == data.minute_usage
        assert offline.quota_windows == list(quota.windows)
        other = CodexProvider({"CODEX_HOME": str(tmp_path / "other")})
        try:
            assert other.local_usage_scope != provider.local_usage_scope
        finally:
            other.close()
    finally:
        provider.close()


@pytest.mark.parametrize("width", [640, 775, 800, 820])
@pytest.mark.parametrize("language", ["zh-cn", "en", "ja"])
def test_codex_panel_shows_local_minutes_and_keeps_quota_cards_on_refresh(width, language):
    app = QApplication.instance() or QApplication([])
    controller = configure_language(app, "zh-cn")
    today = date.today().isoformat()
    data = TokenData(
        per_provider=[PerProviderData("codex", "Codex")],
        quota_windows=[QuotaWindow("primary", "5 小时额度", 25)],
        activity_source="interface", minute_usage_source="local", minute_usage_status="recorded",
        minute_usage_date=today, minute_usage_days=[today],
        minute_usage=[{"minute": 672, "token_type": "PROMPT_CACHE_MISS_TOKEN", "token_amount": 696}],
        minute_model_usage=[{"minute": 672, "model": "test-model", "cache_miss_tokens": 696}],
    )
    panel = MainPanel()
    panel.resize(width, 550)
    panel.update_data(data)
    try:
        assert not panel.activity_mode_segment.isHidden()
        panel.minute_activity_button.click()
        panel.show()
        controller.set_language(language)
        app.processEvents()
        assert panel.width() == width
        controls = [panel.activity_mode_segment, panel.minute_date_edit, panel.minute_estimate_label,
                    *panel.minute_legend_buttons.values(), panel.activity_summary, panel.quota_details_button]
        bounds = [QRect(control.mapTo(panel, QPoint()), control.size())
                  for control in controls if control.isVisible()]
        assert all(rect.left() >= 0 and rect.right() < panel.width() for rect in bounds)
        assert all(not left.intersects(right)
                   for index, left in enumerate(bounds) for right in bounds[index + 1:])
        controller.set_language("zh-cn")
        assert panel._activity_view == "minute"
        assert panel.minute_estimate_label.text() == "本机日志"
        assert "账号归属未知" in panel.minute_estimate_label.toolTip()
        assert "test-model" in panel.minute_chart.tooltip_text(672)
        assert "消耗金额　--" in panel.minute_chart.tooltip_text(672)
        assert panel.today_card.title_label.text() == "5 小时额度"
        assert panel.today_card.value.text() == "剩余 75%"
        panel.update_data(data)
        assert panel._activity_view == "minute"
        assert panel.today_card.value.text() == "剩余 75%"
        panel.annual_activity_button.click()
        assert panel.activity_summary.toolTip() == "来自 Codex 账号统计，不含本机估算"
        data.per_provider = [PerProviderData("cursor", "Cursor")]
        data.minute_usage_source = ""
        data.minute_usage_status = "unavailable"
        panel.update_data(data)
        assert panel.activity_mode_segment.isHidden()
    finally:
        controller.set_language("zh-cn")
        panel.close()
