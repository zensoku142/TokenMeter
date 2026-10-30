"""Read-only public Codex reset news from the documented TIBO status API."""

from datetime import datetime, timezone

from api.http import HttpsSession

STATUS_URL = "https://tibo.cc/api/v1/status"


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


def fetch_status():
    with HttpsSession() as session:
        response = session.get(STATUS_URL, timeout=(5, 10))
        response.raise_for_status()
        if len(response.content) > 1024 * 1024:
            raise ValueError("Reset status is too large")
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise ValueError("Reset status is unavailable")
        if any(payload["data"].get(key) is not None and not isinstance(payload["data"][key], dict)
               for key in ("latest_reset", "scheduled_reset", "active_watch")):
            raise ValueError("Invalid reset record")
        return payload


def reset_news(payload, now=None):
    """Keep source timestamps distinct from estimates and personal quota resets."""
    now = now or datetime.now(timezone.utc)
    data = payload.get("data") or {}
    latest = data.get("latest_reset") or {}
    scheduled = data.get("scheduled_reset") or {}
    assessment = payload.get("time_assessment") or {}
    expiry = timestamp(payload.get("fresh_until"))
    fresh = payload.get("stale") is False and expiry is not None and expiry > now
    retired = (assessment.get("timeline_relation") or {}).get("retires_current_schedule") is True
    notice_id = str(scheduled.get("id") or "")
    record_id = str(latest.get("id") or "")
    notice_id = notice_id if len(notice_id) <= 128 else ""
    record_id = record_id if len(record_id) <= 128 else ""
    announced = timestamp(latest.get("announced_at"))
    reference = timestamp(scheduled.get("scheduled_for"))
    estimated_start = timestamp(assessment.get("window_start"))
    estimated_end = timestamp(assessment.get("window_end"))
    active = bool(scheduled) and not retired and assessment.get("state") not in {
        "newer_record", "time_passed", "window_passed", "stale",
    }
    if reference is not None and reference <= now:
        active = False
    lines = []
    if active:
        if reference is not None and reference > now:
            lines = ["重置预告 · 参考时间", reference.astimezone().strftime("%m-%d %H:%M")]
            countdown = reference
        elif (assessment.get("mode") == "estimated" and estimated_start is not None
              and estimated_end is not None and estimated_end > now and not assessment.get("stale")):
            lines = ["预计重置窗口（社区预测）",
                     estimated_start.astimezone().strftime("%m-%d %H:%M") + " — " + estimated_end.astimezone().strftime("%m-%d %H:%M")]
            countdown = estimated_start if estimated_start > now else None
        else:
            lines = ["有重置预告，具体时间待确认"]
            countdown = None
        if fresh and countdown is not None:
            minutes = max(1, int(((countdown - now).total_seconds() + 59) // 60))
            days, rest = divmod(minutes, 1440)
            hours, minutes = divmod(rest, 60)
            lines.append(f"距参考时间还有 {days} 天 {hours} 小时 {minutes} 分钟")
    elif announced is not None and announced <= now:
        minutes = max(0, int((now - announced).total_seconds() // 60))
        days, rest = divmod(minutes, 1440)
        hours, minutes = divmod(rest, 60)
        lines = ["上次公开重置：" + announced.astimezone().strftime("%m-%d %H:%M"),
                 f"已过去 {days} 天 {hours} 小时 {minutes} 分钟"]
    else:
        lines = ["暂无可用的公开重置记录"]
    if not fresh:
        lines.append("数据待更新")
    record = scheduled if active else latest
    source_url = (record.get("source") or {}).get("url")
    return {"lines": lines, "fresh": fresh, "active": active,
            "notice_key": "notice:" + notice_id + ":" + str(scheduled.get("scheduled_for") or assessment.get("window_end") or "") if active and notice_id else "",
            "record_key": "reset:" + record_id if record_id and announced is not None and announced <= now else "",
            "source_url": source_url if isinstance(source_url, str) and source_url.startswith("https://") else ""}
