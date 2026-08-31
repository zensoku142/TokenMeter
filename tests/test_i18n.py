import json
import os
from datetime import date, datetime
from pathlib import Path
from string import Formatter
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QLocale
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from api.providers.base import QuotaMetric
from config import runtime as config_manager
from config.defaults import DEFAULT_CONFIG, SECRET_KEYS
from data.store import PerProviderData, TokenData
from ui.formatting import format_quota_metric
from ui.i18n import (
    LANGUAGES,
    bind_text,
    configure_language,
    resolve_language,
    startup_running_message,
    tr,
)
from ui.qt_panel import MainPanel, MinuteCalendarWidget, MoneyAxis, TokenAxis
from ui.qt_settings import SettingsWindow
from ui.qt_theme import configure_theme
from ui.qt_update import DownloadProgressDialog
from ui.translations import MESSAGES

APP = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_language(tmp_path, monkeypatch):
    values = DEFAULT_CONFIG.copy()
    monkeypatch.setattr(config_manager, "_config", values)
    monkeypatch.setattr(config_manager, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config_manager, "load_config", lambda: config_manager.all_config())
    monkeypatch.setattr(config_manager, "pending_data_dir", lambda: None)
    monkeypatch.setattr(config_manager, "data_dir_migration_error", lambda: "")
    configure_theme(APP, "dark")
    controller = configure_language(APP, "zh-cn")
    yield controller
    # 其他 UI 用例明确断言中文；同时取消测试草稿的自动保存，避免泄漏到下个用例。
    for widget in APP.allWidgets():
        if isinstance(widget, SettingsWindow):
            widget._autosave_ready = False
            widget._save_pending = False
            widget._save_timer.stop()
            widget._appearance_save_timer.stop()
    controller.set_language("zh-cn")


@pytest.mark.parametrize(
    ("candidates", "expected"),
    [
        (["zh-Hans-CN", "en"], "zh-cn"),
        (["zh-SG"], "zh-cn"),
        (["zh-Hant"], "zh-tw"),
        (["zh-TW"], "zh-tw"),
        (["zh-HK"], "zh-tw"),
        (["zh-MO"], "zh-tw"),
        (["zh-Hans-HK"], "zh-cn"),
        (["en-GB"], "en"),
        (["ja-JP"], "ja"),
        (["ko-KR"], "ko"),
        (["fr-FR", "ja-JP", "en"], "ja"),
        (["fr-FR"], "en"),
        ([], "en"),
    ],
)
def test_system_language_resolution(candidates, expected):
    assert resolve_language("system", candidates) == expected
    assert resolve_language("ko", candidates) == "ko"


def test_language_config_defaults_and_validation():
    assert config_manager.validate_config({})["UI_LANGUAGE"] == "system"
    assert config_manager.validate_value("UI_LANGUAGE", " ZH-TW ") == "zh-tw"
    for code, _label in LANGUAGES:
        assert config_manager.validate_value("UI_LANGUAGE", code) == code
    with pytest.raises(ValueError):
        config_manager.validate_value("UI_LANGUAGE", "fr")


def test_unknown_persisted_language_does_not_discard_other_settings():
    from config.store import load_public_config

    config_manager.CONFIG_PATH.write_text(
        '{"UI_LANGUAGE":"fr","REFRESH_INTERVAL":9500}', encoding="utf-8"
    )
    values = load_public_config(config_manager.CONFIG_PATH)
    assert values["UI_LANGUAGE"] == "system"
    assert values["REFRESH_INTERVAL"] == 9500


def test_startup_language_read_does_not_initialize_or_migrate(tmp_path):
    config_manager.CONFIG_PATH.write_text('{"UI_LANGUAGE":"ja"}', encoding="utf-8")
    with (
        patch.object(config_manager, "_initialized", False),
        patch.object(
            config_manager, "_load_location_state", return_value={"data_dir": str(tmp_path)}
        ),
        patch.object(config_manager, "initialize") as initialize,
    ):
        assert config_manager.read_ui_language_preference() == "ja"
        assert startup_running_message("TokenMeter", "en") == "TokenMeter is already running."
        initialize.assert_not_called()


def test_system_notification_only_changes_automatic_language(isolated_language):
    with patch("ui.i18n.resolve_language", return_value="ja"):
        isolated_language.set_language("system")
    with patch("ui.i18n.resolve_language", return_value="ko"):
        APP.sendEvent(APP, QEvent(QEvent.Type.LocaleChange))
    assert isolated_language.resolved == "ko"
    assert isolated_language.preference == "system"
    isolated_language.set_language("en")
    with patch("ui.i18n.resolve_language", return_value="ja"):
        APP.sendEvent(APP, QEvent(QEvent.Type.LocaleChange))
    assert isolated_language.resolved == "en"


def test_quota_snapshot_preserves_raw_metrics_and_reads_legacy_values():
    provider = Mock(id="codex", name="Codex", default_currency="CNY")
    provider.snapshot_identity.return_value = "synthetic-account"
    metric = QuotaMetric("累计 Token 数", "0.3万", raw_value=2895, value_kind="tokens")
    now = datetime.now()
    data = TokenData(
        account_key="synthetic-account",
        last_success_at=now,
        per_provider=[PerProviderData("codex", "Codex", quota_statistics=[metric])],
    )
    with patch("data.store.history.save_provider_quota_snapshot") as save:
        TokenData._save_persisted_quota_snapshot(provider, data)
    payload = save.call_args.args[2]
    assert payload["statistics"][0]["raw_value"] == 2895
    with patch("data.store.history.load_provider_quota_snapshot", return_value=(payload, now)):
        loaded = TokenData._load_persisted_quota_snapshot(provider)
    assert loaded.quota_statistics[0] == metric
    payload["statistics"][0].pop("raw_value")
    payload["statistics"][0].pop("value_kind")
    with patch("data.store.history.load_provider_quota_snapshot", return_value=(payload, now)):
        legacy = TokenData._load_persisted_quota_snapshot(provider)
    assert legacy.quota_statistics[0].raw_value is None
    assert legacy.quota_statistics[0].value == "0.3万"


def test_packaging_includes_standard_translation_resources():
    from PySide6.QtCore import QLibraryInfo

    root = Path(__file__).resolve().parents[1]
    spec = (root / "packaging/pyinstaller/TokenMeter.spec").read_text(encoding="utf-8")
    for language in ("en", "zh_CN", "zh_TW", "ja", "ko"):
        assert f'"{language}"' in spec
        assert (
            Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
            / f"qtbase_{language}.qm"
        ).is_file()
    assert "PySide6/translations/{filename}" in spec


def test_catalogs_have_all_languages_and_matching_placeholders():
    formatter = Formatter()
    for source, translations in MESSAGES.items():
        assert len(translations) == 4
        fields = {field for _text, field, _spec, _conversion in formatter.parse(source) if field}
        for translated in translations:
            assert translated
            assert {
                field for _text, field, _spec, _conversion in formatter.parse(translated) if field
            } == fields, source


def test_language_save_only_changes_public_preference():
    path = config_manager.CONFIG_PATH
    path.write_text(
        json.dumps(
            {
                "UI_THEME": "light",
                "REFRESH_INTERVAL": 9000,
                "CUSTOM_FIELD": "keep",
                "DEEPSEEK_AUTH": "old-secret",
            }
        ),
        encoding="utf-8",
    )
    config_manager._config["DEEPSEEK_AUTH"] = "draft-secret"
    with patch.object(config_manager, "_write_credential") as write_secret:
        assert config_manager.save_ui_language("en") == "en"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["UI_LANGUAGE"] == "en"
    assert saved["REFRESH_INTERVAL"] == 9000
    assert saved["CUSTOM_FIELD"] == "keep"
    assert not any(key in saved for key in SECRET_KEYS)
    assert config_manager.get("DEEPSEEK_AUTH") == "draft-secret"
    write_secret.assert_not_called()


def test_failed_language_save_preserves_file_and_runtime(monkeypatch):
    config_manager.CONFIG_PATH.write_text('{"UI_LANGUAGE":"zh-cn"}', encoding="utf-8")
    before = config_manager.all_config()
    monkeypatch.setattr(Path, "replace", Mock(side_effect=OSError("read only")))
    with pytest.raises(OSError):
        config_manager.save_ui_language("ja")
    assert config_manager.all_config() == before
    assert json.loads(config_manager.CONFIG_PATH.read_text())["UI_LANGUAGE"] == "zh-cn"
    assert not config_manager.CONFIG_PATH.with_suffix(".json.language.tmp").exists()


def test_live_bindings_keep_source_and_dynamic_states(isolated_language):
    label = bind_text(QLabel(), "设置")
    isolated_language.set_language("en")
    assert label.text() == "Settings"
    bind_text(label, "数据更新于 3 分钟前")
    assert label.text() == "Updated 3 min ago"
    isolated_language.set_language("ja")
    assert label.text() == "3分前に更新"
    isolated_language.set_language("zh-cn")
    assert label.text() == "数据更新于 3 分钟前"
    label.deleteLater()
    APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    isolated_language.set_language("ko")


def test_setting_language_preserves_drafts_and_does_not_save_other_settings(isolated_language):
    window = SettingsWindow()
    credential = window._provider_widgets["AUTH"]
    credential.setText("synthetic-unsaved-token")
    window.refresh_seconds.setValue(125)
    window.tabs.setCurrentIndex(1)
    with (
        patch.object(config_manager, "save_config") as save_all,
        patch.object(config_manager, "_write_credential") as write_secret,
    ):
        window.language_combo.setCurrentIndex(window.language_combo.findData("en"))
        assert isolated_language.resolved == "en"
        assert config_manager.get("UI_LANGUAGE") == "en"
        assert window.tabs.currentIndex() == 1
        assert window._provider_widgets["AUTH"] is credential
        assert credential.text() == "synthetic-unsaved-token"
        assert window.refresh_seconds.value() == 125
        assert window.test_button.text() == "Test connection"
        assert window.minute_usage_chart_type.itemText(0) == "Bars"
        assert not window._save_timer.isActive()
        save_all.assert_not_called()
        write_secret.assert_not_called()
    window.close()


def test_pet_page_and_version_state_follow_language(isolated_language):
    with (
        patch("ui.qt_settings.pet_extension.installed_manifest", return_value=None) as manifest,
        patch("ui.qt_settings.pet_extension.removable_directories", return_value=[]),
    ):
        window = SettingsWindow()
        try:
            isolated_language.set_language("en")
            assert window.tabs.tabText(3) == "Pet"
            assert window.pet_version_label.text() == "Pet version: Not installed"
            assert window.pet_source_label.text().startswith("Pet source code: ")
            assert 'href="https://github.com/LorisYounger/VPet"' in window.pet_source_label.text()
            assert window.pet_source_label.openExternalLinks()
            manifest.return_value = {"version": "0.1.0"}
            window._refresh_pet_controls()
            assert window.pet_version_label.text() == "Pet version: v0.1.0"
            isolated_language.set_language("zh-cn")
            assert window.tabs.tabText(3) == "桌宠"
            assert window.pet_version_label.text() == "桌宠版本：v0.1.0"
            assert window.pet_source_label.text().startswith("桌宠源码来源：")
        finally:
            window.close()


def test_language_switch_failure_restores_selector(isolated_language):
    window = SettingsWindow()
    with patch.object(config_manager, "save_ui_language", side_effect=OSError("failure")):
        window.language_combo.setCurrentIndex(window.language_combo.findData("ko"))
    assert isolated_language.resolved == "zh-cn"
    assert window.language_combo.currentData() == "zh-cn"
    assert "失败" in window.save_feedback.text()
    window.close()


def test_native_calendar_and_axes_follow_language(isolated_language):
    calendar = MinuteCalendarWidget()
    selected = calendar.selectedDate()
    axis = TokenAxis(orientation="left")
    isolated_language.set_language("en")
    assert calendar.locale().language() == QLocale.Language.English
    assert calendar.selectedDate() == selected
    assert axis.tickStrings([1_500, 60_000_000], 1, 1) == ["1.5K", "60M"]
    isolated_language.set_language("ko")
    assert axis.tickStrings([1_500], 1, 1) == ["0.15만"]
    calendar.close()


def test_fixed_trend_ticks_retranslate_without_changing_values(isolated_language):
    import pyqtgraph as pg

    axis = MoneyAxis(orientation="left")
    plot = pg.PlotWidget(axisItems={"left": axis})
    ticks = [[(0, "0万"), (15000, "1.5万")]]
    axis.setTicks(ticks)
    plot.setXRange(1, 3, padding=0)
    bounds = plot.viewRange()
    isolated_language.set_language("en")
    assert axis._tickLevels == [[(0, "0"), (15000, "15K")]]
    assert axis._source_ticks == ticks
    assert plot.viewRange() == bounds
    isolated_language.set_language("zh-cn")
    assert axis._tickLevels == ticks
    plot.close()


def test_dynamic_text_and_raw_metrics_keep_business_values(isolated_language):
    metric = QuotaMetric("累计 Token 数", "0.3万", raw_value=2895, value_kind="tokens")
    value = bind_text(QLabel(), lambda: format_quota_metric(metric))
    isolated_language.set_language("en")
    assert value.text() == "2.9K"
    assert tr("额度/热力图：接口数据") == "Quota/Activity: Live data"
    assert tr("2 天 3 小时后重置") == "Resets in 2d 3h"
    assert tr("4月5日 10:30重置") == "Resets 4/5 10:30"
    assert tr("剩余 75% · 4月5日 10:30重置") == "75% remaining · Resets 4/5 10:30"
    assert tr("26.1亿") == "2.61B"
    assert tr("unknown external detail 日本語") == "unknown external detail 日本語"
    isolated_language.set_language("zh-tw")
    assert value.text() == "0.3萬"
    assert metric.value == "0.3万" and metric.raw_value == 2895


def test_download_keeps_progress_while_language_changes(isolated_language):
    dialog = DownloadProgressDialog()
    dialog.update_progress(
        {
            "stage": "setup.exe",
            "total": 1000,
            "downloaded": 400,
            "current": 400,
            "current_total": 1000,
            "speed": 100,
        }
    )
    isolated_language.set_language("en")
    assert dialog.progress_bar.value() == 40
    assert dialog.status_label.text() == "Downloading: setup.exe"
    assert "File:" in dialog.detail_label.text()
    assert dialog.cancel_button.text() == "Cancel"
    dialog.close()


def test_panel_translation_does_not_reset_chart_state(isolated_language):
    panel = MainPanel()
    data = TokenData(
        status="ok",
        last_success_at=datetime.now(),
        today_tokens=15000,
        daily_usage=[{"date": date.today().isoformat(), "tokens": 15000, "cost_cny": 1}],
        per_provider=[PerProviderData("deepseek", "DeepSeek")],
    )
    panel.update_data(data)
    plot = panel.trend.plot
    plot.setXRange(1, 3, padding=0)
    bounds = plot.viewRange()
    with patch.object(panel, "update_data") as update:
        for code in ("en", "ja", "ko", "zh-tw", "zh-cn"):
            isolated_language.set_language(code)
        update.assert_not_called()
    assert plot.viewRange() == bounds
    assert panel.today_card.title_label.text() == "今日使用金额"
    panel.close()


def test_qt_standard_buttons_are_translated(isolated_language):
    box = QMessageBox()
    box.setStandardButtons(QMessageBox.StandardButton.Cancel)
    isolated_language.set_language("ja")
    APP.sendPostedEvents(None, QEvent.Type.LanguageChange)
    assert box.button(QMessageBox.StandardButton.Cancel).text() == "キャンセル"
    isolated_language.set_language("en")
    APP.sendPostedEvents(None, QEvent.Type.LanguageChange)
    assert box.button(QMessageBox.StandardButton.Cancel).text() == "Cancel"
    box.close()
