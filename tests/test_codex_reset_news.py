from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from api.codex_resets import reset_news
from config import runtime as config_manager
from ui.codex_reset_news import CodexResetNews


def payload(*, record="old", notice=None, stale=False):
    now = datetime.now(timezone.utc)
    return {"stale": stale, "fresh_until": (now + timedelta(minutes=5)).isoformat(),
            "data": {"latest_reset": {"id": record, "announced_at": (now - timedelta(days=2, hours=3)).isoformat(),
                                      "source": {"url": "https://example.com/reset"}},
                     "scheduled_reset": notice, "active_watch": None}}


def test_latest_record_shows_time_and_elapsed_without_inventing_next_reset():
    result = reset_news(payload())
    assert result["lines"][0].startswith("上次公开重置：")
    assert result["lines"][1].startswith("已过去 2 天 3 小时")
    assert not result["active"]
    assert not result["notice_key"]


def test_schedule_unknown_time_prediction_and_expiry_are_distinct():
    now = datetime.now(timezone.utc)
    value = payload(notice={"id": "notice", "scheduled_for": (now + timedelta(days=1)).isoformat()})
    result = reset_news(value)
    assert result["lines"][0] == "重置预告 · 参考时间"
    value["data"]["scheduled_reset"]["scheduled_for"] = None
    assert reset_news(value)["lines"] == ["有重置预告，具体时间待确认"]
    value["time_assessment"] = {"mode": "estimated", "window_start": now.isoformat(),
                                "window_end": (now + timedelta(days=1)).isoformat()}
    assert reset_news(value)["lines"][0] == "预计重置窗口（社区预测）"
    value["time_assessment"]["state"] = "window_passed"
    assert not reset_news(value)["active"]
    value["time_assessment"]["timeline_relation"] = {"retires_current_schedule": True}
    assert not reset_news(value)["active"]
    value["stale"] = True
    assert "数据待更新" in reset_news(value)["lines"]
    value["data"]["active_watch"] = {"id": "watch", "expires_at": (now + timedelta(days=2)).isoformat()}
    value["data"]["scheduled_reset"] = None
    assert not reset_news(value)["notice_key"]


def test_no_duplicate_or_historical_notifications_after_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(config_manager, "CONFIG_DIR", tmp_path)
    monitor = CodexResetNews()
    monitor._enabled = True
    notified = Mock()
    monitor.notification.connect(notified)
    monitor._finished(payload())
    notified.assert_not_called()
    monitor._finished(payload(record="new"))
    notified.assert_called_once_with("Codex 有新的公开重置记录")
    monitor._finished(payload(record="new"))
    assert notified.call_count == 1
    restarted = CodexResetNews()
    restarted._enabled = True
    restarted.notification.connect(notified)
    restarted._finished(payload(record="new"))
    assert notified.call_count == 1
    restarted._finished(payload(record="not-yet-fresh", stale=True))
    assert notified.call_count == 1
    restarted._finished(None)
    assert restarted.payload["stale"]
    restarted._finished(payload(record="new", notice={"id": "upcoming", "scheduled_for": None}))
    assert notified.call_count == 2
    assert notified.call_args.args[0] == "Codex 有新的公开重置预告"
