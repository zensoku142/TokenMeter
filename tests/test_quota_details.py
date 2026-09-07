"""Quota details use complete synthetic snapshots and native Qt layout/input."""

import os
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLabel
from shiboken6 import isValid

from api.providers import PROVIDERS
from api.providers.base import FetchError, QuotaMetric, QuotaWindow
from data.store import PerProviderData, TokenData
from ui.i18n import configure_language, current_language, tr
from ui.qt_theme import configure_theme, current_theme
from ui.quota_details import QuotaDetailsDialog

APP = QApplication.instance() or QApplication([])


@pytest.fixture
def dialog():
    language = current_language()
    theme = current_theme().name
    configure_language(APP, "zh-cn")
    configure_theme(APP, "dark")
    widget = QuotaDetailsDialog()
    yield widget
    widget.close()
    configure_language(APP, language)
    configure_theme(APP, theme)


def snapshot(**values):
    return TokenData(**{
        "account_key": "synthetic-account",
        "per_provider": [PerProviderData("codex", "Codex")],
        "status": "ok",
        "quota_source": "interface",
        "last_success_at": datetime(2026, 9, 5, 10, 20, 30),
        **values,
    })


def row_text(row):
    return "\n".join(label.text() for label in row.findChildren(QLabel))


def test_complete_snapshot_includes_every_window_metric_statistic_and_plan(dialog):
    data = snapshot(
        quota_windows=[QuotaWindow(str(index), f"window-{index}", index) for index in range(12)],
        quota_metrics=[QuotaMetric(f"metric-{index}", str(index)) for index in range(5)],
        quota_statistics=[QuotaMetric(f"statistic-{index}", str(index)) for index in range(4)],
        statistics_source="cache",
        account_plan="Pro",
        account_label="synthetic@example.test",
    )
    dialog.set_data(data)
    assert len(dialog.rows) == 22
    assert len(dialog.progress_bars) == 12
    assert "window-11" in row_text(dialog.rows[11])
    assert "metric-4" in row_text(dialog.rows[16])
    assert "Pro" in row_text(dialog.rows[17])
    assert "statistic-3" in row_text(dialog.rows[-1])
    assert "缓存数据" in row_text(dialog.rows[-1])
    assert "接口数据" in dialog.updated.text()
    assert "2026-09-05 10:20:30" in dialog.updated.text()
    assert dialog._data is data


def test_unlimited_metrics_do_not_invent_percentage_bars(dialog):
    dialog.set_data(snapshot(quota_metrics=[QuotaMetric("Chat", "不限量"), QuotaMetric("Audio", "不限额")]))
    assert len(dialog.rows) == 2
    assert dialog.progress_bars == []
    assert "不限量" in row_text(dialog.rows[0])
    assert "不限额" in row_text(dialog.rows[1])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), None, True, "invalid", -1])
def test_invalid_percentages_remain_unknown_without_fabricated_progress(dialog, value):
    dialog.set_data(snapshot(quota_windows=[QuotaWindow("invalid", "Unknown", value)]))
    assert dialog.progress_bars == []
    assert "--" in row_text(dialog.rows[0])
    assert "平台未提供重置时间" in row_text(dialog.rows[0])


def test_overage_retains_numeric_value_while_bar_remains_bounded(dialog):
    dialog.set_data(snapshot(quota_windows=[QuotaWindow("overage", "Overage", 125.5)]))
    assert "125.5%" in row_text(dialog.rows[0])
    assert dialog.progress_bars[0].value() == 1000
    assert dialog.progress_bars[0].accessibleName() == "Overage"
    assert "125.5%" in dialog.progress_bars[0].accessibleDescription()


def test_cached_and_partial_sources_are_distinguished(dialog):
    data = snapshot(quota_windows=[QuotaWindow("test", "Window", 25)], is_stale=True)
    dialog.set_data(data)
    assert "部分数据未能更新" in dialog.notice.text()
    assert "接口数据" in dialog.updated.text()
    dialog.set_data(replace(data, quota_source="cache"))
    assert "当前显示缓存额度" in dialog.notice.text()
    assert "缓存数据" in dialog.updated.text()
    dialog.set_data(replace(data, is_stale=False, quota_source="local_snapshot"))
    assert "本机快照" in dialog.updated.text()
    assert dialog.notice.isHidden()


def test_empty_first_refresh_has_clear_status_and_no_stale_rows_or_brand(dialog):
    dialog.set_data(snapshot(quota_metrics=[QuotaMetric("Chat", "不限量")]))
    dialog.set_data(TokenData(errors=[FetchError("NOT_CONFIGURED", "Quota", "test")]))
    assert dialog.rows == []
    assert dialog.progress_bars == []
    assert "等待首次更新" in dialog.updated.text()
    assert "暂无可用额度" in dialog.notice.text()
    assert dialog.dashboard_button.isHidden()
    assert dialog.brand.pixmap().isNull()
    assert "Codex" not in dialog.windowTitle()


@pytest.mark.parametrize("language", ["en", "zh-tw", "ja", "ko"])
def test_language_change_reformats_live_values_and_every_bound_label(dialog, language):
    dialog.set_data(snapshot(
        quota_windows=[QuotaWindow("week", "每周额度", 25, detail="以平台实际策略为准")],
        quota_metrics=[QuotaMetric("累计 Token 数", "1.2万", raw_value=12345, value_kind="tokens")],
        quota_statistics=[QuotaMetric("活跃天数", "2 天", raw_value=2, value_kind="days")],
        statistics_source="cache",
    ))
    original_rows = tuple(dialog.rows)
    configure_language(APP, language)
    assert tuple(dialog.rows) == original_rows
    assert dialog.windowTitle() == f"Codex · {tr('全部额度')}"
    if language != "zh-tw":
        assert "全部额度" not in dialog.windowTitle()
    assert tr("平台未提供重置时间") in row_text(dialog.rows[0])
    assert tr("以平台实际策略为准") in row_text(dialog.rows[0])
    assert tr("缓存数据") in row_text(dialog.rows[-1])
    assert dialog.progress_bars[0].accessibleDescription() == tr("已用 25%")
    if language == "en":
        assert "12.3K" in row_text(dialog.rows[1])
        assert "2 days" in row_text(dialog.rows[2])


def test_reset_countdown_translates_and_remains_attached_to_its_window(dialog):
    dialog.set_data(snapshot(quota_windows=[
        QuotaWindow("past", "Past", 10, resets_at=datetime.now() - timedelta(minutes=5)),
        QuotaWindow("unknown", "Unknown", 20),
    ]))
    configure_language(APP, "en")
    assert tr("即将重置") in row_text(dialog.rows[0])
    assert tr("平台未提供重置时间") in row_text(dialog.rows[1])


def test_refresh_replaces_old_account_rows_and_releases_old_widgets(dialog):
    dialog.set_data(snapshot(quota_windows=[QuotaWindow("old", "Old account", 15)]))
    old_row = dialog.rows[0]
    dialog.set_data(snapshot(account_key="new-account", quota_metrics=[QuotaMetric("New account", "不限量")]))
    APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(old_row)
    assert len(dialog.rows) == 1
    assert "Old account" not in row_text(dialog.rows[0])
    assert "New account" in row_text(dialog.rows[0])
    assert dialog.progress_bars == []


def test_many_windows_scroll_without_horizontal_overflow_and_refresh_keeps_position(dialog):
    data = snapshot(quota_windows=[QuotaWindow(str(index), f"Window {index}", index % 100) for index in range(60)])
    dialog.set_data(data)
    dialog.resize(420, 380)
    dialog.show()
    APP.processEvents()
    bar = dialog.scroll_area.verticalScrollBar()
    assert bar.maximum() > 0
    assert dialog.scroll_area.horizontalScrollBar().maximum() == 0
    bar.setValue(bar.maximum() // 2)
    position = bar.value()
    dialog.set_data(data)
    APP.processEvents()
    assert bar.value() == position
    dialog.set_data(replace(data, account_key="another-account"))
    APP.processEvents()
    assert bar.value() == 0


def test_long_provider_details_wrap_within_minimum_dialog_width(dialog):
    dialog.set_data(snapshot(
        per_provider=[PerProviderData("gemini", "Gemini CLI / Google Code Assist")],
        quota_metrics=[QuotaMetric("gemini-model-" + "x" * 100, "value-" + "x" * 100, "detail-" + "x" * 150)],
    ))
    dialog.resize(380, 350)
    dialog.show()
    APP.processEvents()
    assert dialog.scroll_area.horizontalScrollBar().maximum() == 0
    assert dialog.rows[0].width() <= dialog.scroll_area.viewport().width()


def test_provider_identity_and_icon_follow_theme_and_clear_on_empty_snapshot(dialog):
    dialog.set_data(snapshot(quota_metrics=[QuotaMetric("Chat", "不限量")]))
    dark = dialog.brand.pixmap().toImage()
    assert not dialog.windowIcon().isNull()
    assert dialog.windowTitle().startswith("Codex")
    configure_theme(APP, "light")
    assert current_theme().window in dialog.styleSheet()
    assert dialog.brand.pixmap().toImage() != dark
    dialog.set_data(snapshot(per_provider=[PerProviderData("claude", "Claude Code")]))
    assert dialog.windowTitle().startswith("Claude Code")
    assert not dialog.brand.pixmap().isNull()


def test_narrow_dialog_close_button_keeps_text_padding_after_language_change(dialog):
    dialog.set_data(snapshot(quota_metrics=[QuotaMetric("Chat", "不限量")]))
    configure_language(APP, "en")
    configure_theme(APP, "light")
    dialog.resize(380, 350)
    dialog.show()
    APP.processEvents()
    close = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Close)
    assert close.width() >= close.fontMetrics().horizontalAdvance(close.text()) + 24


def test_external_fields_render_as_plain_text(dialog):
    dialog.set_data(snapshot(quota_metrics=[QuotaMetric("<b>Title</b>", "<img src='https://invalid.test/image'>", "<i>Detail</i>")]))
    labels = dialog.rows[0].findChildren(QLabel)
    assert all(label.textFormat() == Qt.TextFormat.PlainText for label in labels)
    assert "<b>Title</b>" in row_text(dialog.rows[0])


def test_official_page_action_uses_registered_provider_url_and_escape_closes(dialog, monkeypatch):
    opened = Mock()
    monkeypatch.setattr("ui.quota_details.QDesktopServices.openUrl", opened)
    dialog.set_data(snapshot(
        per_provider=[PerProviderData("claude", "Claude Code")],
        quota_metrics=[QuotaMetric("Chat", "不限量")],
    ))
    dialog.show()
    APP.processEvents()
    QTest.mouseClick(dialog.dashboard_button, Qt.MouseButton.LeftButton)
    assert opened.call_args.args[0].toString() == PROVIDERS["claude"].dashboard_url
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert not dialog.isVisible()


def test_same_theme_refresh_does_not_reapply_stylesheet_to_every_row(dialog, monkeypatch):
    apply_style = Mock(wraps=dialog.setStyleSheet)
    monkeypatch.setattr(dialog, "setStyleSheet", apply_style)
    data = snapshot(quota_windows=[QuotaWindow("week", "Weekly", 25)])
    dialog.set_data(data)
    dialog.set_data(replace(data, quota_source="cache"))
    apply_style.assert_not_called()
    configure_theme(APP, "light")
    assert apply_style.call_count == 1
    assert current_theme().window in dialog.styleSheet()


def test_unchanged_rows_keep_widgets_but_update_timestamp_source_and_warning(dialog):
    data = snapshot(quota_windows=[QuotaWindow("week", "Weekly", 25)])
    dialog.set_data(data)
    content = dialog.scroll_area.widget()
    rows = tuple(dialog.rows)
    updated = replace(data, last_success_at=datetime(2026, 9, 5, 11, 30), quota_source="cache")
    dialog.set_data(updated)
    assert dialog.scroll_area.widget() is content
    assert tuple(dialog.rows) == rows
    assert dialog._data is updated
    assert "11:30:00" in dialog.updated.text()
    assert "缓存数据" in dialog.updated.text()
    assert "当前显示缓存额度" in dialog.notice.text()
    dialog.set_data(replace(updated, quota_source="interface", is_stale=True))
    assert tuple(dialog.rows) == rows
    assert "部分数据未能更新" in dialog.notice.text()


def test_unchanged_rows_refresh_countdown_and_still_translate_without_rebuild(dialog, monkeypatch):
    countdown = Mock(return_value="2 分钟后重置")
    monkeypatch.setattr("ui.quota_details.format_reset_countdown", countdown)
    data = snapshot(quota_windows=[QuotaWindow("week", "每周额度", 25, resets_at=datetime(2026, 9, 6))])
    dialog.set_data(data)
    row = dialog.rows[0]
    assert "2 分钟后重置" in row_text(row)
    countdown.return_value = "1 分钟后重置"
    dialog.set_data(replace(data, last_success_at=datetime(2026, 9, 5, 11)))
    assert dialog.rows[0] is row
    assert "1 分钟后重置" in row_text(row)
    configure_language(APP, "en")
    assert dialog.rows[0] is row
    assert tr("1 分钟后重置") in row_text(row)
    assert "每周额度" not in row_text(row)


@pytest.mark.parametrize("field,replacement,expected", [
    ("quota_windows", [QuotaWindow("new", "New quota", 80)], "New quota"),
    ("quota_metrics", [QuotaMetric("New credit", "33")], "New credit"),
    ("quota_statistics", [QuotaMetric("New statistic", "44")], "New statistic"),
    ("account_plan", "Max", "Max"),
    ("account_label", "new@example.test", "new@example.test"),
    ("statistics_source", "cache", "缓存数据"),
])
def test_mutating_original_snapshot_invalidates_row_cache(dialog, field, replacement, expected):
    data = snapshot(
        quota_windows=[QuotaWindow("week", "Weekly", 25)],
        quota_metrics=[QuotaMetric("Credit", "11")],
        quota_statistics=[QuotaMetric("Statistic", "22")],
        account_plan="Pro", account_label="old@example.test", statistics_source="interface",
    )
    dialog.set_data(data)
    old_row = dialog.rows[0]
    original = getattr(data, field)
    if isinstance(original, list):
        original[:] = replacement
    else:
        setattr(data, field, replacement)
    dialog.set_data(data)
    APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(old_row)
    assert expected in "\n".join(row_text(row) for row in dialog.rows)


@pytest.mark.parametrize("change", [
    {"account_key": "other-account"},
    {"per_provider": [PerProviderData("claude", "Claude Code")]},
])
def test_equal_quota_values_from_new_scope_do_not_reuse_previous_account_rows(dialog, change):
    data = snapshot(quota_windows=[QuotaWindow("week", "Weekly", 25)])
    dialog.set_data(data)
    old_row = dialog.rows[0]
    dialog.set_data(replace(data, **change))
    APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(old_row)
    assert "Weekly" in row_text(dialog.rows[0])


@pytest.mark.parametrize("before,after", [(0, False), (1, True), (False, 0), (True, 1)])
def test_boolean_percentage_does_not_reuse_equal_numeric_quota_rows(dialog, before, after):
    data = snapshot(quota_windows=[QuotaWindow("same", "Quota", before)])
    dialog.set_data(data)
    data.quota_windows[0] = QuotaWindow("same", "Quota", after)
    dialog.set_data(data)
    if isinstance(after, bool):
        assert dialog.progress_bars == []
        assert "--" in row_text(dialog.rows[0])
    else:
        assert len(dialog.progress_bars) == 1
        assert dialog.progress_bars[0].value() == after * 10
