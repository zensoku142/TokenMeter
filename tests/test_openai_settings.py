"""OpenAI 设置页仅接受当前浏览器任务验证后的成对凭据。"""

import json
import os
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import (
    QApplication, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QWidget,
)

from config import runtime as config_manager
from config.defaults import DEFAULT_CONFIG
from data.store import TokenData
from ui import qt_settings
from ui.qt_settings import SettingsWindow, _AcquiredCookie, _CookieAcquireWorker

APP = QApplication.instance() or QApplication([])
OLD_PAIR = {"AUTH": "Bearer synthetic-old-auth", "ORGANIZATION": "synthetic-old-org"}
NEW_PAIR = {"AUTH": "Bearer synthetic-new-auth", "ORGANIZATION": "synthetic-new-org"}
PACKET = json.dumps(NEW_PAIR)


@pytest.fixture
def settings(monkeypatch, tmp_path):
    values = deepcopy(DEFAULT_CONFIG)
    values.update({
        "ACTIVE_PROVIDER": "deepseek",
        "BACKGROUND_PROVIDER_IDS": ["deepseek"],
        "DEEPSEEK_AUTH": "Bearer synthetic-deepseek-auth",
        **{f"OPENAI_{field}": value for field, value in OLD_PAIR.items()},
    })
    monkeypatch.setattr(config_manager, "_config", values)
    monkeypatch.setattr(config_manager, "load_config", lambda: dict(values))
    monkeypatch.setattr(config_manager, "all_config", lambda: dict(values))
    monkeypatch.setattr(config_manager, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_manager, "pending_data_dir", lambda: None)
    monkeypatch.setattr(config_manager, "data_dir_migration_error", lambda: "")
    monkeypatch.setattr(config_manager, "validate_data_dir_target", lambda _path: tmp_path)
    monkeypatch.setattr(config_manager, "load_panel_layout_state", lambda: {})
    monkeypatch.setattr(qt_settings, "sync_autostart", Mock())
    monkeypatch.setattr(SettingsWindow, "_refresh_pet_controls", Mock())
    # 保留真实 Qt 信号与任务对象，但绝不启动浏览器、HTTP 或原生凭据写入。
    monkeypatch.setattr(_CookieAcquireWorker, "start", Mock())
    monkeypatch.setattr(qt_settings.ConnectionWorker, "start", Mock())
    log = Mock()
    monkeypatch.setattr(config_manager, "logger", lambda: log)

    def persist(changes):
        values.update(changes)
        return dict(values)

    save = Mock(side_effect=persist)
    monkeypatch.setattr(config_manager, "save_config", save)
    monkeypatch.setattr(QMessageBox, "question", Mock(return_value=QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "warning", Mock())
    refreshed = Mock()
    window = SettingsWindow(on_saved=refreshed)
    window.open_provider("openai")
    yield SimpleNamespace(
        window=window, values=values, save=save, log=log, refreshed=refreshed, persist=persist,
    )
    window._save_timer.stop()
    window._save_pending = False
    window.cancel_openai_acquisition()
    window._cookie_acquire_worker = None
    window._worker = None
    window.deleteLater()


def start_acquisition(window, *, finish=True):
    window._begin_cookie_acquire()
    worker = window._cookie_acquire_worker
    assert worker is not None
    if finish:
        window._finish_cookie_acquire()
    return worker


def complete(worker, acquired=PACKET):
    worker.success.emit(_AcquiredCookie(acquired))
    worker.finished.emit()


def visible_text(window):
    parts = []
    for widget in window.findChildren(QWidget):
        parts.extend((widget.toolTip(), widget.accessibleName()))
        if isinstance(widget, (QLabel, QLineEdit, QPushButton)):
            parts.append(widget.text())
        elif isinstance(widget, QPlainTextEdit):
            parts.append(widget.toPlainText())
    return "\n".join(parts)


def test_openai_does_not_offer_unverified_manual_profile_credentials(settings):
    from ui.account_profiles import ProfileEditor

    editor = ProfileEditor()
    assert editor.provider.findData("openai") == -1
    assert editor.provider.findData("deepseek") >= 0
    assert editor.provider.findData("codex") >= 0
    editor.deleteLater()


def test_official_usage_link_is_below_openai_card_and_right_of_connection_test(settings, monkeypatch):
    window = settings.window
    link = window.openai_dashboard_link
    assert link.isFlat() and not link.isHidden()
    assert not window.credentials_card.isAncestorOf(link)
    assert window.credentials_card.findChildren(QPushButton, "providerDashboardButton") == []
    assert link.parentWidget() is window.test_button.parentWidget()
    row = next(window.content.layout().itemAt(index).layout()
               for index in range(window.content.layout().count())
               if window.content.layout().itemAt(index).layout() is not None
               and window.content.layout().itemAt(index).layout().indexOf(link) >= 0)
    assert row.indexOf(window.test_button) == 0 and row.indexOf(link) == 2
    assert row.itemAt(1).spacerItem() is not None
    opened = Mock()
    monkeypatch.setattr(window, "_open_external_url", opened)
    link.click()
    opened.assert_called_once_with(qt_settings.PROVIDERS["openai"].dashboard_url)
    window.show()
    APP.processEvents()
    link_pos = link.mapTo(window.content, QPoint(0, 0))
    test_pos = window.test_button.mapTo(window.content, QPoint(0, 0))
    assert link_pos.x() > test_pos.x() + window.test_button.width()
    assert link_pos.y() >= window.credentials_card.geometry().bottom()
    assert abs(link_pos.y() + link.height() / 2 - test_pos.y() - window.test_button.height() / 2) <= 1


@pytest.mark.parametrize("provider_id", ["deepseek", "mimo", "codex"])
def test_other_provider_official_usage_entry_keeps_existing_card_layout(settings, provider_id):
    window = settings.window
    window.open_provider(provider_id)
    assert window.openai_dashboard_link.isHidden()
    expected = bool(qt_settings.PROVIDERS[provider_id].dashboard_url)
    assert bool(window.credentials_card.findChildren(QPushButton, "providerDashboardButton")) is expected
    window.open_provider("openai")
    assert not window.openai_dashboard_link.isHidden()
    assert len(window.findChildren(QPushButton, "openaiDashboardLink")) == 1
    settings.save.assert_not_called()


def test_official_usage_link_keeps_removed_provider_disabled_state(settings):
    settings.values["DISABLED_PROVIDER_IDS"] = ["openai"]
    settings.window._render_credentials("openai")
    assert not settings.window.openai_dashboard_link.isEnabled()
    assert not settings.window.credentials_card.isEnabled()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_official_usage_text_link_follows_theme(settings, theme):
    from ui.qt_theme import configure_theme, current_theme

    configure_theme(APP, theme)
    settings.window.set_theme_mode(theme, theme)
    style = settings.window.openai_dashboard_link.styleSheet()
    assert current_theme().subtext in style
    assert current_theme().accent in style
    assert "background: transparent" in style
    assert settings.window.openai_dashboard_link.isFlat()


def test_openai_has_browser_entry_without_credential_editors_or_echo(settings):
    window = settings.window
    assert window._provider_widgets == {}
    assert window.credentials_card.findChildren(QLineEdit) == []
    assert window.credentials_card.findChildren(QPlainTextEdit) == []
    assert window._cookie_acquire_button.text() == "重新连接"
    assert window._credential_acquire_automatic is False
    assert window._cookie_finish_button.isHidden()
    text = visible_text(window)
    for value in OLD_PAIR.values():
        assert value not in text
    assert "选好组织" in text
    settings.values.update(OPENAI_AUTH="", OPENAI_ORGANIZATION="")
    window._render_credentials("openai")
    assert window._cookie_acquire_button.text() == "连接账户"
    assert window._provider_widgets == {}


def test_selecting_openai_and_general_autosave_preserve_current_and_background_sources(settings):
    window = settings.window
    window.open_provider("deepseek")
    window.provider_combo.select_provider("openai")
    window.flush_pending_saves()
    assert settings.values["ACTIVE_PROVIDER"] == "deepseek"
    assert settings.values["BACKGROUND_PROVIDER_IDS"] == ["deepseek"]
    assert not window.background_provider_checks["openai"].isChecked()
    assert window._values()["ACTIVE_PROVIDER"] == "deepseek"
    # 其他平台仍沿用原来的来源选择行为。
    window.provider_combo.select_provider("codex")
    assert window._values()["ACTIVE_PROVIDER"] == "codex"


def test_completion_saves_only_the_pair_once_after_validation_and_notifies_refresh(settings):
    window = settings.window
    worker = start_acquisition(window)
    assert worker._collect_requested.is_set()
    assert not worker._cancelled.is_set()
    assert window._cookie_finish_button.isHidden()
    settings.save.assert_not_called()
    assert settings.values["OPENAI_AUTH"] == OLD_PAIR["AUTH"]
    complete(worker)
    settings.save.assert_called_once_with({f"OPENAI_{key}": value for key, value in NEW_PAIR.items()})
    settings.refreshed.assert_called_once_with()
    assert window._cookie_acquire_button.text() == "重新连接"
    assert window._cookie_acquire_button.isEnabled()
    assert window._provider_widgets == {}
    assert "openai" not in window._provider_drafts
    assert not window._save_pending
    assert settings.values["ACTIVE_PROVIDER"] == "deepseek"
    assert settings.values["BACKGROUND_PROVIDER_IDS"] == ["deepseek"]
    assert not window.background_provider_checks["openai"].isChecked()
    for secret in NEW_PAIR.values():
        assert secret not in visible_text(window)
        assert secret not in str(settings.log.mock_calls)
    worker.success.emit(_AcquiredCookie(PACKET))
    assert settings.save.call_count == 1


def test_unrequested_or_unidentified_success_cannot_save(settings):
    window = settings.window
    worker = start_acquisition(window, finish=False)
    worker.success.emit(_AcquiredCookie(PACKET))
    window._apply_acquired_cookie("openai", PACKET)
    settings.save.assert_not_called()
    settings.refreshed.assert_not_called()
    assert settings.values["OPENAI_AUTH"] == OLD_PAIR["AUTH"]
    window.cancel_openai_acquisition()
    worker.finished.emit()


@pytest.mark.parametrize("action", ["close", "reject", "hide", "quit", "remove"])
@pytest.mark.parametrize("finish", [False, True])
def test_cancellation_rejects_success_and_error_already_queued(settings, action, finish):
    window = settings.window
    if action in {"close", "hide"}:
        window.show()
        APP.processEvents()
    worker = start_acquisition(window, finish=finish)
    if action == "close":
        window.close()
    elif action == "reject":
        window.reject()
    elif action == "hide":
        window.hide()
    elif action == "quit":
        window.stop_connection_tasks()
    else:
        settings.values.update(
            OPENAI_AUTH="", OPENAI_ORGANIZATION="", DISABLED_PROVIDER_IDS=["openai"],
        )
        window.reload_provider_configuration("openai", True)
    assert worker._cancelled.is_set()
    assert worker._collect_requested.is_set() is finish
    before = dict(settings.values)
    complete(worker)
    worker.error.emit(PACKET)
    assert settings.values == before
    settings.save.assert_not_called()
    settings.refreshed.assert_not_called()
    assert PACKET not in visible_text(window)
    assert PACKET not in str(settings.log.mock_calls)


@pytest.mark.parametrize("packet", [
    "not-json", "{}", json.dumps({"AUTH": NEW_PAIR["AUTH"]}),
    json.dumps({"ORGANIZATION": NEW_PAIR["ORGANIZATION"]}),
])
def test_incomplete_or_invalid_result_preserves_original_connection(settings, packet):
    before = dict(settings.values)
    worker = start_acquisition(settings.window)
    complete(worker, packet)
    assert settings.values == before
    assert "openai" not in settings.window._provider_drafts
    settings.save.assert_not_called()
    settings.refreshed.assert_not_called()
    assert "连接失败" in settings.window._cookie_acquire_status.text()


def test_unverified_result_is_not_saved(settings):
    worker = start_acquisition(settings.window)
    worker.success.emit(_AcquiredCookie(PACKET, direct_usable=False))
    worker.finished.emit()
    settings.save.assert_not_called()
    assert settings.values["OPENAI_AUTH"] == OLD_PAIR["AUTH"]


def test_provider_error_text_is_neither_echoed_nor_logged(settings):
    worker = start_acquisition(settings.window)
    worker.error.emit(PACKET)
    worker.finished.emit()
    settings.save.assert_not_called()
    settings.refreshed.assert_not_called()
    assert "原有连接未更改" in settings.window._cookie_acquire_status.text()
    for secret in NEW_PAIR.values():
        assert secret not in visible_text(settings.window)
        assert secret not in str(settings.log.mock_calls)


@pytest.mark.parametrize("code,reason", [
    ("BROWSER_REQUEST_EMPTY", "未捕获到"),
    ("BROWSER_NOT_CLOSED", "完全退出"),
    ("CDP_UNEXPECTED_FRAME", "调试消息"),
    ("OPENAI_BROWSER_START_FAILED", "初始化"),
    ("FORBIDDEN", "独立验证"),
    ("NETWORK_ERROR", "独立验证"),
])
def test_fixed_acquisition_code_reaches_ui_and_safe_log_without_losing_cause(settings, code, reason):
    worker = start_acquisition(settings.window)
    worker.error.emit(code)
    worker.finished.emit()
    text = settings.window._cookie_acquire_status.text()
    assert code in text and reason in text and "原有连接未更改" in text
    settings.log.warning.assert_called_with("OpenAI API connection failed: code=%s", code)
    settings.save.assert_not_called()


def test_failed_pair_save_does_not_poison_draft_or_later_autosave(settings):
    window = settings.window
    window._provider_drafts["openai"] = dict(OLD_PAIR)
    drafts = deepcopy(window._provider_drafts)
    before = dict(settings.values)
    settings.save.side_effect = OSError(PACKET)
    worker = start_acquisition(window)
    complete(worker)
    assert settings.values == before
    assert window._provider_drafts == drafts
    settings.refreshed.assert_not_called()
    assert not window._save_pending
    assert "安全保存失败" in window._cookie_acquire_status.text()
    assert PACKET not in visible_text(window)
    assert PACKET not in str(settings.log.mock_calls)
    # 普通保存即使遇到旧的/被污染的草稿，也只能使用已持久化的 OpenAI 字段。
    window._provider_drafts["openai"] = dict(NEW_PAIR)
    settings.save.reset_mock(side_effect=True)
    settings.save.side_effect = settings.persist
    window.quota_alert_check.setChecked(not window.quota_alert_check.isChecked())
    window._schedule_save()
    window.flush_pending_saves()
    assert settings.values["OPENAI_AUTH"] == OLD_PAIR["AUTH"]
    assert settings.values["OPENAI_ORGANIZATION"] == OLD_PAIR["ORGANIZATION"]
    assert settings.values["ACTIVE_PROVIDER"] == "deepseek"


@pytest.mark.parametrize("field", ["OPENAI_AUTH", "OPENAI_ORGANIZATION"])
def test_changing_either_original_credential_invalidates_result_without_reload(settings, field):
    worker = start_acquisition(settings.window)
    settings.values[field] = "synthetic-external-replacement"
    before = dict(settings.values)
    complete(worker)
    settings.save.assert_not_called()
    assert settings.values == before
    assert worker._cancelled.is_set()


def test_reload_invalidates_task_even_when_credential_values_are_identical(settings):
    worker = start_acquisition(settings.window)
    settings.window.reload_provider_configuration("openai", False)
    complete(worker)
    assert worker._cancelled.is_set()
    settings.save.assert_not_called()
    assert settings.values["OPENAI_AUTH"] == OLD_PAIR["AUTH"]


def test_delete_readd_and_new_task_reject_all_old_callbacks(settings):
    window = settings.window
    old = start_acquisition(window)
    window.provider_combo._configured = {"openai", "deepseek"}
    window.provider_combo._remove_provider("openai")
    assert old._cancelled.is_set()
    assert settings.values["OPENAI_AUTH"] == ""
    assert settings.values["OPENAI_ORGANIZATION"] == ""
    window.provider_combo.select_provider("openai")
    assert window.provider_combo.currentData() == "openai"
    assert settings.values["ACTIVE_PROVIDER"] == "deepseek"
    assert "openai" not in settings.values["DISABLED_PROVIDER_IDS"]
    assert not window.background_provider_checks["openai"].isChecked()
    settings.save.reset_mock()
    settings.refreshed.reset_mock()
    complete(old)
    settings.save.assert_not_called()
    new = start_acquisition(window)
    # 旧线程的重复 finished 不能清除新任务，旧 success/error 也不能覆盖新状态。
    old.success.emit(_AcquiredCookie(PACKET))
    old.error.emit(PACKET)
    old.finished.emit()
    assert window._cookie_acquire_worker is new
    assert window._openai_acquire_token is not None
    settings.save.assert_not_called()
    complete(new)
    settings.save.assert_called_once_with({f"OPENAI_{key}": value for key, value in NEW_PAIR.items()})
    settings.refreshed.assert_called_once()


def test_failed_reactivation_does_not_change_source_or_restore_credentials(settings):
    window = settings.window
    settings.values.update(
        OPENAI_AUTH="", OPENAI_ORGANIZATION="", DISABLED_PROVIDER_IDS=["openai"],
    )
    window.reload_provider_configuration("openai", True)
    settings.save.side_effect = OSError(PACKET)
    before = dict(settings.values)
    window.provider_combo.select_provider("openai")
    assert settings.values == before
    assert window.provider_combo.currentData() == "deepseek"
    settings.refreshed.assert_not_called()
    assert PACKET not in str(QMessageBox.warning.call_args)


def test_late_openai_error_does_not_leak_after_another_provider_starts(settings):
    window = settings.window
    old = start_acquisition(window)
    window.cancel_openai_acquisition()
    old.finished.emit()
    window.open_provider("deepseek")
    new = start_acquisition(window, finish=False)
    old.error.emit(PACKET)
    assert window._cookie_acquire_worker is new
    assert PACKET not in visible_text(window)
    assert PACKET not in str(settings.log.mock_calls)
    new.finished.emit()
    settings.save.assert_not_called()


def test_general_save_failure_does_not_echo_openai_credentials(settings):
    settings.save.side_effect = OSError(PACKET)
    window = settings.window
    window.quota_alert_check.setChecked(not window.quota_alert_check.isChecked())
    window._schedule_save()
    window.flush_pending_saves()
    assert "保存失败" in window.save_feedback.text()
    for secret in NEW_PAIR.values():
        assert secret not in visible_text(window)
        assert secret not in str(settings.log.mock_calls)


def test_general_save_reloads_openai_pair_after_reentrant_confirmation(settings, monkeypatch):
    window = settings.window
    window.open_provider("deepseek")
    # 模拟普通保存已经读取旧值后，嵌套事件循环完成了新的浏览器连接。
    def renew_before_save(_enabled):
        settings.values.update({f"OPENAI_{field}": value for field, value in NEW_PAIR.items()})

    monkeypatch.setattr(qt_settings, "sync_autostart", renew_before_save)
    window._save()
    settings.save.assert_called_once()
    changes = settings.save.call_args.args[0]
    assert changes["OPENAI_AUTH"] == NEW_PAIR["AUTH"]
    assert changes["OPENAI_ORGANIZATION"] == NEW_PAIR["ORGANIZATION"]
    assert settings.values["OPENAI_AUTH"] == NEW_PAIR["AUTH"]


def test_switching_form_does_not_write_secrets_into_another_provider(settings):
    window = settings.window
    worker = start_acquisition(window, finish=False)
    window.open_provider("deepseek")
    window.open_provider("openai")
    assert not window._cookie_finish_button.isHidden()
    window._finish_cookie_acquire()
    window.open_provider("deepseek")
    before = {field: editor.text() if isinstance(editor, QLineEdit) else editor.toPlainText()
              for field, editor in window._provider_widgets.items()}
    complete(worker)
    after = {field: editor.text() if isinstance(editor, QLineEdit) else editor.toPlainText()
             for field, editor in window._provider_widgets.items()}
    assert before == after
    assert settings.values["DEEPSEEK_AUTH"] == "Bearer synthetic-deepseek-auth"
    settings.save.assert_called_once()
    assert settings.values["ACTIVE_PROVIDER"] == "deepseek"


def test_openai_connection_test_targets_openai_without_selecting_it_as_current(settings):
    window = settings.window
    window._test_connection()
    worker = window._worker
    assert worker._config["ACTIVE_PROVIDER"] == "openai"
    assert worker._config["OPENAI_AUTH"] == OLD_PAIR["AUTH"]
    window._connection_result(TokenData(status="ok"))
    assert window.connection_feedback.text() == "连接成功。"
    assert settings.values["ACTIVE_PROVIDER"] == "deepseek"
    settings.save.assert_not_called()


def test_deepseek_keeps_manual_fields_and_original_acquisition_draft_behavior(settings):
    window = settings.window
    window.open_provider("deepseek")
    assert "AUTH" in window._provider_widgets
    assert not window._provider_widgets["AUTH"].isReadOnly()
    window._apply_acquired_cookie("deepseek", "synthetic-platform-token")
    assert window._provider_widgets["AUTH"].text() == "Bearer synthetic-platform-token"
    assert window._provider_drafts["deepseek"]["AUTH"] == "Bearer synthetic-platform-token"
    assert window._save_pending
    settings.save.assert_not_called()


@pytest.mark.parametrize("action", ["finish", "cancel", "cancel_after_finish", "none"])
def test_worker_distinguishes_completion_from_cancellation_and_spontaneous_return(action):
    acquire = Mock(return_value=PACKET)
    provider = SimpleNamespace(id="openai", acquire_credentials_via_chrome=acquire)
    worker = _CookieAcquireWorker(provider)
    success, errors = [], []
    worker.success.connect(success.append)
    worker.error.connect(errors.append)
    if action in {"finish", "cancel_after_finish"}:
        worker.stop_and_collect()
    if action in {"cancel", "cancel_after_finish"}:
        worker.cancel()
    worker.run()
    acquire.assert_called_once_with(worker._stop_event, cancel_event=worker._cancelled)
    assert errors == []
    assert success == ([_AcquiredCookie(PACKET)] if action == "finish" else [])


@pytest.mark.parametrize("cancelled", [False, True])
def test_worker_does_not_expose_raw_provider_exception(cancelled):
    provider = SimpleNamespace(
        id="openai", acquire_credentials_via_chrome=Mock(side_effect=RuntimeError(PACKET)),
        describe_acquire_error=Mock(return_value=PACKET),
    )
    worker = _CookieAcquireWorker(provider)
    errors = []
    worker.error.connect(errors.append)
    if cancelled:
        worker.cancel()
    worker.run()
    assert bool(errors) is not cancelled
    assert all(secret not in "\n".join(errors) for secret in NEW_PAIR.values())
    provider.describe_acquire_error.assert_not_called()


def test_worker_keeps_allowlisted_failure_code_instead_of_generic_text():
    provider = SimpleNamespace(
        id="openai", acquire_credentials_via_chrome=Mock(side_effect=RuntimeError("CDP_UNEXPECTED_FRAME")),
    )
    worker = _CookieAcquireWorker(provider)
    errors = []
    worker.error.connect(errors.append)
    worker.run()
    assert errors == ["CDP_UNEXPECTED_FRAME"]


def test_real_config_transaction_rolls_back_pair_and_excludes_secrets_from_files(monkeypatch, tmp_path, caplog):
    import logging

    original = {**DEFAULT_CONFIG, **{f"OPENAI_{key}": value for key, value in OLD_PAIR.items()}}
    secure = {key: original.get(key, "") for key in config_manager.SECRET_KEYS}
    monkeypatch.setattr(config_manager, "_config", dict(original))
    monkeypatch.setattr(config_manager, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_manager, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config_manager, "ensure_config_file", lambda: None)
    monkeypatch.setattr(config_manager, "logger", lambda: logging.getLogger("test.openai.save"))

    def write(key, value):
        if key == "OPENAI_ORGANIZATION" and value == NEW_PAIR["ORGANIZATION"]:
            raise OSError("synthetic secret backend failure")
        secure[key] = value

    monkeypatch.setattr(config_manager, "_write_credential", write)
    changes = {f"OPENAI_{key}": value for key, value in NEW_PAIR.items()}
    with pytest.raises(OSError):
        config_manager.save_config(changes)
    assert config_manager.all_config() == original
    assert all(secure[f"OPENAI_{key}"] == value for key, value in OLD_PAIR.items())
    monkeypatch.setattr(config_manager, "_write_credential", lambda key, value: secure.update({key: value}))
    config_manager.save_config(changes)
    assert all(secure[f"OPENAI_{key}"] == value for key, value in NEW_PAIR.items())
    for file in tmp_path.glob("config.json*"):
        public = file.read_text(encoding="utf-8")
        assert "OPENAI_AUTH" not in public and "OPENAI_ORGANIZATION" not in public
        for value in (*OLD_PAIR.values(), *NEW_PAIR.values()):
            assert value not in public + caplog.text


def test_other_provider_worker_still_accepts_automatic_success_without_finish():
    provider = SimpleNamespace(
        id="deepseek", acquire_credentials_via_chrome=Mock(return_value="synthetic-token"),
    )
    worker = _CookieAcquireWorker(provider)
    success = []
    worker.success.connect(success.append)
    worker.run()
    assert success == [_AcquiredCookie("synthetic-token")]
