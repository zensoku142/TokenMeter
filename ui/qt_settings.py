"""Qt settings dialog built from the provider registry.

Provider selection is now a simple dropdown, and only the credentials of the
selected provider are shown — keeping the dialog small and focused.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Union

from PySide6.QtCore import QRectF, QSignalBlocker, QSize, Qt, QThread, QTime, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStyle,
    QTabBar,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from api.providers import PROVIDERS, list_providers
from api.providers.base import FetchError
from config import runtime as config_manager
from core import pet_extension
from core.autostart import AutostartError, sync_autostart
from core.identity import APP_DISPLAY_NAME, GITHUB_REPOSITORY_URL
from data.store import TokenData
from ui.i18n import (
    LANGUAGES,
    add_item,
    add_tab,
    bind_text,
    configure_language,
    language_controller,
    tr,
)
from ui.qt_theme import DARK_THEME, LIGHT_THEME, current_theme, fluent_icon, theme_controller
from ui.qt_update import AppUpdateController
from updater.client import (
    DownloadCancelled, GitHubReleaseClient, PetReleaseInfo, compare_versions, format_bytes,
)

_CARD_PADDING = 18


class _SettingsComboBox(QComboBox):
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        # 去掉原生箭头按钮的直角底板后，复用项目的 Fluent 箭头保留清晰的下拉提示。
        icon = fluent_icon("chevron-down", 14)
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown)
        painter = QPainter(self)
        if not self.isEnabled():
            painter.setOpacity(0.45)
        icon.paint(painter, self.width() - 24, (self.height() - 14) // 2, 14, 14)


class _SettingsTabBar(QTabBar):
    HEIGHT = 42
    INSET = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDrawBase(False)
        self.setExpanding(True)
        self.setFixedHeight(self.HEIGHT)

    def tabSizeHint(self, index: int) -> QSize:
        return QSize(self.fontMetrics().horizontalAdvance(self.tabText(index)) + 24, self.HEIGHT)

    def paintEvent(self, event) -> None:
        # 参考 NutriTime 的完整轨道和内缩胶囊；统一绘制可避免原生页签残留直角底板。
        tokens = current_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        border = QColor(tokens.border)
        border.setAlpha(45)
        painter.setPen(QPen(border, 1))
        painter.setBrush(QColor(tokens.surface))
        track = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)
        for index in range(self.count()):
            rect = QRectF(self.tabRect(index)).adjusted(self.INSET, self.INSET, -self.INSET, -self.INSET)
            selected = index == self.currentIndex()
            if selected:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(tokens.accent_soft))
                painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
                if self.hasFocus():
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.setPen(QPen(QColor(tokens.accent), 1))
                    painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
            painter.setPen(QColor(tokens.accent_text if selected else tokens.subtext))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, tr(self.tabText(index)))


class _SettingsSwitch(QCheckBox):
    def __init__(self, text: str):
        super().__init__(text)
        # 保留复选框的键盘和无障碍语义，仅把重复的二态控件绘制成开关。
        bind_text(self, text, method='setAccessibleName')
        self.setFixedSize(50, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, event) -> None:
        tokens = current_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.45)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens.accent if self.isChecked() else tokens.disabled))
        painter.drawRoundedRect(QRectF(2, 3, 46, 22), 11, 11)
        # on_accent 是文字对比色，浅色主色会变成黑色；开关圆钮保持白色才能维持一致外观。
        painter.setBrush(QColor("#FFFFFF"))
        thumb_border = QColor(tokens.border)
        thumb_border.setAlpha(45)
        painter.setPen(QPen(thumb_border, 0.5))
        painter.drawEllipse(QRectF(28 if self.isChecked() else 4, 5, 18, 18))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(tokens.accent), 1))
            painter.drawRoundedRect(QRectF(0.5, 0.5, 49, 27), 13, 13)


class ConnectionWorker(QThread):
    finished_with_data = Signal(object)

    def __init__(self, config: Mapping[str, Any], parent=None):
        super().__init__(parent)
        self._config = config

    def run(self) -> None:
        try:
            result = TokenData.test_connection(self._config)
        except Exception as exc:
            config_manager.logger().exception("Connection test failed")
            result = TokenData(
                status="error",
                errors=[FetchError("UNKNOWN_ERROR", "连接测试", str(exc))],
            )
        self.finished_with_data.emit(result)


@dataclass(frozen=True)
class _AcquiredCookie:
    cookie_text: str
    direct_usable: bool = True


class _CookieAcquireWorker(QThread):
    """Run the selected provider's browser collection away from the UI thread."""

    success = Signal(object)
    error = Signal(str)

    def __init__(self, provider_cls, parent=None):
        super().__init__(parent)
        self._stop_event = threading.Event()
        self._provider_cls = provider_cls

    def stop_and_collect(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        try:
            acquire = getattr(
                self._provider_cls, "acquire_credentials_via_chrome", None
            )
            if not callable(acquire):
                acquire = self._provider_cls.acquire_cookie_via_chrome
            cookie = acquire(self._stop_event)
        except RuntimeError as exc:
            self.error.emit(self._provider_cls.describe_acquire_error(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.error.emit(self._provider_cls.describe_acquire_error(exc))
            return
        direct_usable = True
        if getattr(self._provider_cls, "id", "") == "mimo":
            # The visible browser only proves that cookies exist. Re-open the
            # retained profile headlessly and require a real MiMo API success
            # before any draft credentials are changed.
            try:
                cookie = self._provider_cls.recover_verified_cookie_via_chrome(
                    threading.Event(),
                    headless=True,
                )
            except RuntimeError as exc:
                self.error.emit(self._provider_cls.describe_acquire_error(exc))
                return
            direct_usable = self._provider_cls.is_direct_cookie_usable(cookie)
        self.success.emit(_AcquiredCookie(cookie, direct_usable))


class _PetExtensionWorker(QThread):
    progress_changed = Signal(object)

    def __init__(self, operation: str, parent=None, *, release: PetReleaseInfo | None = None):
        super().__init__(parent)
        self.operation = operation
        self.error: Exception | None = None
        self.release = release

    def run(self) -> None:
        try:
            if self.operation == "check":
                client = GitHubReleaseClient()
                try:
                    self.release = client.latest_pet_release(cancel_requested=self.isInterruptionRequested)
                finally:
                    client._session.close()
            elif self.operation in {"install", "update"}:
                pet_extension.download_and_install(
                    self.progress_changed.emit, self.isInterruptionRequested,
                    release=self.release, replace_existing=self.operation == "update",
                )
            else:
                pet_extension.uninstall()
        except Exception as exc:
            self.error = exc


class SettingsWindow(QDialog):
    theme_requested = Signal(str)
    appearance_preview_requested = Signal(str, str, int)
    appearance_requested = Signal(str, str, int)
    save_state_changed = Signal(str, str)
    pet_update_started = Signal()
    pet_update_finished = Signal()

    def __init__(
        self,
        parent=None,
        on_saved: Callable[[], None] | None = None,
        update_controller: AppUpdateController | None = None,
        embedded: bool = False,
    ):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        bind_text(self, f"{APP_DISPLAY_NAME} 设置", method='setWindowTitle')
        self.setModal(False)
        if embedded:
            # 在主面板内作为普通控件显示，避免生成继承悬浮球置顶状态的独立窗口。
            self.setWindowFlags(Qt.WindowType.Widget)
            self.setStyleSheet("QDialog#settingsPage { background: transparent; }")
        else:
            self.setMinimumWidth(560)
            self.setMaximumWidth(720)
        self.on_saved = on_saved
        self.update_controller = update_controller
        self._pet_worker: _PetExtensionWorker | None = None
        self._pet_release: PetReleaseInfo | None = None
        QApplication.instance().aboutToQuit.connect(self.stop_pet_task)
        self._worker: ConnectionWorker | None = None
        self._cookie_acquire_worker: "_CookieAcquireWorker | None" = None
        self._cookie_acquire_provider_id = ""
        self._credential_acquire_label = "Cookie"
        self._credential_acquire_automatic = False
        self._rendered_provider_id = ""
        self._provider_widgets: dict[str, Union[QLineEdit, QPlainTextEdit]] = {}
        self._provider_drafts: dict[str, dict[str, str]] = {}
        self._resolved_theme = theme_controller().resolved
        self._syncing_appearance = False
        self._autosave_ready = False
        self._saving = False
        self._save_pending = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(650)
        self._save_timer.timeout.connect(self._auto_save)
        self._appearance_save_timer = QTimer(self)
        self._appearance_save_timer.setSingleShot(True)
        self._appearance_save_timer.setInterval(200)
        self._appearance_save_timer.timeout.connect(self._commit_appearance)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 14, 24, 18)
        root.setSpacing(8)
        self.save_feedback = bind_text(QLabel(self), "自动保存")
        self.save_feedback.setObjectName("settingsSaveStatus")
        self.save_feedback.setMaximumWidth(260)
        self.save_feedback.setProperty("tone", "muted")
        # 内嵌页的返回和保存状态由共用标题栏承载，不再占用一整行内容空间。
        if embedded:
            self.save_feedback.hide()
        else:
            root.addWidget(self.save_feedback, 0, Qt.AlignmentFlag.AlignRight)
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("settingsTabs")
        self.tabs.setTabBar(_SettingsTabBar(self.tabs))
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setExpanding(True)

        # 每个分类独立滚动，长凭据和目录信息不会挤压主面板的导航。
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QWidget()
        self.scroll_area.setWidget(self.content)
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 20, 0, 8)
        content_layout.setSpacing(14)
        title = bind_text(QLabel(), "连接数据平台；凭据仅保存在本机，修改后自动保存。")
        title.setProperty("tone", "muted")
        title.setWordWrap(True)

        # Provider picker — single dropdown.
        picker_row = QHBoxLayout()
        picker_row.setContentsMargins(0, 0, 0, 0)
        picker_row.setSpacing(8)
        picker_label = bind_text(QLabel(), "数据来源")
        picker_label.setStyleSheet("font-size: 13px; font-weight: 500;")
        self.provider_combo = _SettingsComboBox()
        for provider_id, provider_name in list_providers():
            display_name = (
                provider_name
                if provider_id == "nayuto"
                else f"{provider_name} ({provider_id})"
            )
            add_item(self.provider_combo, display_name, provider_id)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        picker_row.addWidget(picker_label)
        picker_row.addWidget(self.provider_combo, 1)

        # Credentials card — rebuild when the selected provider changes.
        self.credentials_card = QFrame()
        self.credentials_card.setObjectName("settingsCard")
        self.credentials_layout = QVBoxLayout(self.credentials_card)
        self.credentials_layout.setContentsMargins(_CARD_PADDING, 14, _CARD_PADDING, 14)
        self.credentials_layout.setSpacing(10)
        self._provider_widgets: dict[str, QLineEdit] = {}

        connection_actions = QHBoxLayout()
        self.test_button = bind_text(QPushButton(), "测试连接")
        self.test_button.clicked.connect(self._test_connection)
        connection_actions.addWidget(self.test_button)
        connection_actions.addStretch(1)
        self.connection_feedback = QLabel()
        self.connection_feedback.setWordWrap(True)
        self.connection_feedback.setProperty("tone", "muted")
        self.connection_feedback.setStyleSheet("font-size: 12px;")

        content_layout.addWidget(title)
        content_layout.addLayout(picker_row)
        content_layout.addWidget(self.credentials_card)

        self.deepseek_peak_pricing_card = QFrame()
        self.deepseek_peak_pricing_card.setObjectName("settingsCard")
        peak_layout = QVBoxLayout(self.deepseek_peak_pricing_card)
        peak_layout.setContentsMargins(_CARD_PADDING, 14, _CARD_PADDING, 14)
        peak_layout.setSpacing(9)
        peak_title = bind_text(QLabel(), "峰谷计价提示")
        peak_title.setStyleSheet("font-size: 14px; font-weight: 600;")
        peak_layout.addWidget(peak_title)
        self.deepseek_peak_pricing_enabled = bind_text(QCheckBox(), "显示峰谷计价状态")
        self.deepseek_peak_pricing_enabled.toggled.connect(
            self._set_peak_pricing_inputs_enabled
        )
        peak_layout.addWidget(self.deepseek_peak_pricing_enabled)
        peak_form = QFormLayout()
        peak_form.setHorizontalSpacing(16)
        peak_form.setVerticalSpacing(8)
        self.deepseek_peak_period_1_start = self._peak_time_edit()
        self.deepseek_peak_period_1_end = self._peak_time_edit()
        self.deepseek_peak_period_2_start = self._peak_time_edit()
        self.deepseek_peak_period_2_end = self._peak_time_edit()
        peak_form.addRow(
            bind_text(QLabel(), "高峰时段 1"),
            self._peak_period_row(
                self.deepseek_peak_period_1_start, self.deepseek_peak_period_1_end
            ),
        )
        peak_form.addRow(
            bind_text(QLabel(), "高峰时段 2"),
            self._peak_period_row(
                self.deepseek_peak_period_2_start, self.deepseek_peak_period_2_end
            ),
        )
        peak_layout.addLayout(peak_form)
        peak_hint = bind_text(QLabel(), "按北京时间判断；高峰时所有计费项按平时价格 2 倍计费。")
        peak_hint.setWordWrap(True)
        peak_hint.setProperty("tone", "muted")
        peak_hint.setStyleSheet("font-size: 12px;")
        peak_layout.addWidget(peak_hint)
        content_layout.addLayout(connection_actions)
        content_layout.addWidget(self.connection_feedback)
        content_layout.addStretch(1)
        add_tab(self.tabs, self.scroll_area, "账户连接")

        appearance_layout = self._add_settings_page("外观", "调整主题与面板外观，修改立即应用并保存。")

        appearance_card = QFrame()
        appearance_card.setObjectName("settingsSection")
        appearance_form = QFormLayout(appearance_card)
        appearance_form.setContentsMargins(_CARD_PADDING, 14, _CARD_PADDING, 14)
        appearance_form.setHorizontalSpacing(16)
        appearance_form.setVerticalSpacing(10)
        self.language_combo = _SettingsComboBox()
        for language, label in LANGUAGES:
            # 语言自称保持原文，误选语言后仍能找到熟悉的选项。
            if language == "system":
                add_item(self.language_combo, label, language)
            else:
                self.language_combo.addItem(label, language)
        self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(
            config_manager.get("UI_LANGUAGE", "system")
        )))
        bind_text(self.language_combo, "选择后立即生效并保存，不影响其他未提交的设置。", method="setToolTip")
        appearance_form.addRow("Language / 语言", self.language_combo)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        self.theme_combo = _SettingsComboBox()
        add_item(self.theme_combo, "跟随系统", "system")
        add_item(self.theme_combo, "浅色", "light")
        add_item(self.theme_combo, "深色", "dark")
        bind_text(self.theme_combo, "主题与调色盘会立即应用并保存", method='setToolTip')
        appearance_form.addRow(bind_text(QLabel(), "外观主题"), self.theme_combo)

        accent_row = QWidget()
        accent_layout = QHBoxLayout(accent_row)
        accent_layout.setContentsMargins(0, 0, 0, 0)
        accent_layout.setSpacing(8)
        self.accent_color_edit = QLineEdit()
        bind_text(self.accent_color_edit, "#RRGGBB", method='setPlaceholderText')
        self.accent_color_edit.setMaxLength(7)
        bind_text(self.accent_color_edit, "输入完整的十六进制主题主色", method='setToolTip')
        self.accent_color_button = QPushButton()
        self.accent_color_button.setFixedWidth(42)
        bind_text(self.accent_color_button, "打开颜色选择器", method='setToolTip')
        bind_text(self.accent_color_button, "选择主题主色", method='setAccessibleName')
        accent_layout.addWidget(self.accent_color_edit, 1)
        accent_layout.addWidget(self.accent_color_button)
        appearance_form.addRow(bind_text(QLabel(), "主题主色"), accent_row)

        self.sync_accent_check = bind_text(QCheckBox(), "深浅模式使用相同主题色")
        bind_text(
            self.sync_accent_check,
            "默认同步主色；取消勾选后可分别设置，面板透明度始终独立。",
            method="setToolTip",
        )
        appearance_form.addRow("", self.sync_accent_check)

        opacity_row = QWidget()
        opacity_layout = QHBoxLayout(opacity_row)
        opacity_layout.setContentsMargins(0, 0, 0, 0)
        opacity_layout.setSpacing(10)
        self.panel_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.panel_opacity_slider.setRange(70, 100)
        self.panel_opacity_slider.setSingleStep(1)
        self.panel_opacity_slider.setPageStep(5)
        bind_text(self.panel_opacity_slider, "仅调整展开面板背景，不降低文字和控件清晰度", method='setToolTip')
        self.panel_opacity_label = bind_text(QLabel(), "100%")
        self.panel_opacity_label.setFixedWidth(38)
        self.panel_opacity_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        opacity_layout.addWidget(self.panel_opacity_slider, 1)
        opacity_layout.addWidget(self.panel_opacity_label)
        appearance_form.addRow(bind_text(QLabel(), "面板透明度"), opacity_row)

        self.ball_size_hint = bind_text(QLabel(), "悬停在悬浮球上时，滚动鼠标滚轮即可调整大小。")
        self.ball_size_hint.setWordWrap(True)
        self.ball_size_hint.setProperty("tone", "muted")
        self.ball_size_hint.setStyleSheet("font-size: 12px;")
        appearance_form.addRow(bind_text(QLabel(), "悬浮球大小"), self.ball_size_hint)

        self.reset_appearance_button = bind_text(QPushButton(), "恢复当前主题默认配置")
        bind_text(
            self.reset_appearance_button,
            "重置当前主题；开启主色同步时同时同步另一模式的主色。",
            method='setToolTip',
        )
        appearance_form.addRow(bind_text(QLabel(), ""), self.reset_appearance_button)
        appearance_layout.addWidget(appearance_card)
        appearance_layout.addStretch(1)

        behavior_layout = self._add_settings_page(
            "悬浮与启动", "控制悬浮球的显示行为以及随系统启动的自动运行选项。"
        )
        self.edge_hide_check = _SettingsSwitch("贴边自动隐藏")
        self._add_switch_row(
            behavior_layout, "贴边自动隐藏", "靠近屏幕边缘时隐藏悬浮球", self.edge_hide_check
        )
        self.panel_auto_collapse_check = _SettingsSwitch("失焦自动收起")
        self._add_switch_row(
            behavior_layout, "失焦自动收起", "点击其他应用时，面板和设置一起收起",
            self.panel_auto_collapse_check,
        )
        self.autostart_check = _SettingsSwitch(f"开机后自动运行 {APP_DISPLAY_NAME}")
        self._add_switch_row(
            behavior_layout, "开机自动运行", f"登录 Windows 后启动 {APP_DISPLAY_NAME}",
            self.autostart_check,
        )
        behavior_layout.addStretch(1)

        pet_layout = self._add_settings_page(
            "桌宠", "下载安装桌宠扩展包后可启用；扩展独立更新，不影响主程序。"
        )
        self.vpet_check = _SettingsSwitch("启用 VPet 精简桌宠")
        self._add_switch_row(
            pet_layout, "启用 VPet 精简桌宠",
            "启用后替代悬浮球，面板和主题保持不变。", self.vpet_check
        )
        self.pet_version_label = QLabel()
        self.pet_version_label.setWordWrap(True)
        self.pet_version_label.setProperty("tone", "muted")
        pet_layout.addWidget(self.pet_version_label)
        self.pet_status_label = QLabel()
        self.pet_status_label.setWordWrap(True)
        self.pet_status_label.setProperty("tone", "muted")
        pet_layout.addWidget(self.pet_status_label)
        pet_actions = QHBoxLayout()
        pet_actions.setSpacing(6)
        self.pet_install_button = bind_text(QPushButton(), "下载桌宠扩展包")
        self.pet_uninstall_button = bind_text(QPushButton(), "卸载桌宠扩展包")
        self.pet_cancel_button = bind_text(QPushButton(), "取消下载")
        self.pet_check_button = bind_text(QPushButton(), "检查桌宠更新")
        self.pet_update_button = bind_text(QPushButton(), "更新桌宠扩展包")
        self.pet_install_button.clicked.connect(self._install_pet)
        self.pet_uninstall_button.clicked.connect(self._uninstall_pet)
        self.pet_cancel_button.clicked.connect(self._cancel_pet_download)
        self.pet_check_button.clicked.connect(lambda: self._start_pet_task("check"))
        self.pet_update_button.clicked.connect(self._update_pet)
        for button in (self.pet_install_button, self.pet_update_button, self.pet_uninstall_button,
                       self.pet_check_button, self.pet_cancel_button):
            pet_actions.addWidget(button)
        pet_actions.addStretch(1)
        pet_layout.addLayout(pet_actions)
        self.pet_source_label = bind_text(QLabel(), lambda: tr(
            "桌宠源码来源：{link}",
            link='<a href="https://github.com/LorisYounger/VPet">LorisYounger/VPet</a>',
        ))
        self.pet_source_label.setWordWrap(True)
        self.pet_source_label.setOpenExternalLinks(True)
        pet_layout.addWidget(self.pet_source_label)
        pet_layout.addStretch(1)

        runtime_layout = self._add_settings_page(
            "采集与统计", "设置数据刷新频率、后台采集平台与分时统计方式。"
        )

        runtime_card = QFrame()
        runtime_card.setObjectName("settingsSection")
        runtime_layout.addWidget(runtime_card)
        runtime_form = QFormLayout(runtime_card)
        runtime_form.setContentsMargins(_CARD_PADDING, 14, _CARD_PADDING, 14)
        runtime_form.setHorizontalSpacing(16)
        runtime_form.setVerticalSpacing(10)
        self.refresh_seconds = QSpinBox()
        self.refresh_seconds.setRange(5, 3600)
        bind_text(self.refresh_seconds, " 秒", method='setSuffix')
        runtime_form.addRow(bind_text(QLabel(), "刷新间隔"), self.refresh_seconds)
        background_provider_widget = QWidget()
        background_provider_layout = QHBoxLayout(background_provider_widget)
        background_provider_layout.setContentsMargins(0, 0, 0, 0)
        background_provider_layout.setSpacing(10)
        self.background_provider_checks: dict[str, QCheckBox] = {}
        for provider_id, provider_name in list_providers():
            check = bind_text(QCheckBox(), provider_name)
            bind_text(check, "勾选后，即使不是当前数据来源也会在后台定时获取", method='setToolTip')
            self.background_provider_checks[provider_id] = check
            background_provider_layout.addWidget(check)
        runtime_form.addRow(bind_text(QLabel(), "同时获取"), background_provider_widget)
        self.minute_usage_interval_minutes = QSpinBox()
        self.minute_usage_interval_minutes.setRange(1, 60)
        bind_text(self.minute_usage_interval_minutes, " 分钟", method='setSuffix')
        bind_text(self.minute_usage_interval_minutes, "仅合并分时图的展示粒度，底层分钟数据和刷新频率保持不变", method='setToolTip')
        runtime_form.addRow(bind_text(QLabel(), "分时统计间隔"), self.minute_usage_interval_minutes)
        self.minute_usage_chart_type = _SettingsComboBox()
        add_item(self.minute_usage_chart_type, "柱状图", "bar")
        add_item(self.minute_usage_chart_type, "折线图", "line")
        bind_text(self.minute_usage_chart_type, "切换今日分时主图和全天导航的展示样式", method='setToolTip')
        runtime_form.addRow(bind_text(QLabel(), "分时图表样式"), self.minute_usage_chart_type)
        runtime_layout.addWidget(self.deepseek_peak_pricing_card)
        runtime_layout.addStretch(1)

        storage_layout = self._add_settings_page(
            "数据存储", "管理本地记录与应用数据目录；目录迁移在下次启动时执行。"
        )
        storage_form = QFormLayout()
        storage_form.setHorizontalSpacing(16)
        storage_form.setVerticalSpacing(14)
        storage_layout.addLayout(storage_form)
        self.minute_usage_retention_days = QSpinBox()
        self.minute_usage_retention_days.setRange(1, 365)
        bind_text(self.minute_usage_retention_days, " 天", method='setSuffix')
        bind_text(self.minute_usage_retention_days, "界面展示最近 N 天分时估算数据；本地数据保留双倍宽限期，超过 2N 天后才自动清理", method='setToolTip')
        storage_form.addRow(bind_text(QLabel(), "分时数据保留天数"), self.minute_usage_retention_days)
        data_dir_row = QWidget()
        data_dir_layout = QHBoxLayout(data_dir_row)
        data_dir_layout.setContentsMargins(0, 0, 0, 0)
        data_dir_layout.setSpacing(8)
        self.data_dir_edit = QLineEdit()
        self.data_dir_edit.setReadOnly(True)
        bind_text(self.data_dir_edit, "配置、数据库、日志、更新缓存和专用浏览器会话的保存目录", method='setToolTip')
        self.data_dir_browse_button = bind_text(QPushButton(), "选择…")
        self.data_dir_browse_button.clicked.connect(self._choose_data_dir)
        self.data_dir_default_button = bind_text(QPushButton(), "恢复默认")
        self.data_dir_default_button.clicked.connect(self._restore_default_data_dir)
        data_dir_layout.addWidget(self.data_dir_edit, 1)
        data_dir_layout.addWidget(self.data_dir_browse_button)
        data_dir_layout.addWidget(self.data_dir_default_button)
        storage_form.addRow(bind_text(QLabel(), "应用数据目录"), data_dir_row)
        self.data_dir_status = QLabel()
        self.data_dir_status.setWordWrap(True)
        self.data_dir_status.setProperty("tone", "muted")
        self.data_dir_status.setStyleSheet("font-size: 12px;")
        storage_form.addRow(bind_text(QLabel(), ""), self.data_dir_status)
        storage_layout.addStretch(1)

        update_page_layout = self._add_settings_page(
            "更新与关于", "管理版本更新，查看项目主页与反馈入口。"
        )
        self.update_card = QFrame()
        self.update_card.setObjectName("settingsSection")
        update_layout = QVBoxLayout(self.update_card)
        update_layout.setContentsMargins(_CARD_PADDING, 14, _CARD_PADDING, 14)
        update_layout.setSpacing(10)
        update_title = bind_text(QLabel(), "软件更新")
        update_title.setStyleSheet("font-size: 14px; font-weight: 600;")
        update_layout.addWidget(update_title)

        update_form = QFormLayout()
        update_form.setHorizontalSpacing(16)
        update_form.setVerticalSpacing(8)
        self.current_version_label = QLabel()
        self.auto_check_updates = _SettingsSwitch("启动后自动检查")
        self.update_channel_combo = _SettingsComboBox()
        add_item(self.update_channel_combo, "正式版", "stable")
        add_item(self.update_channel_combo, "预发布版", "prerelease")
        self.update_status_label = QLabel()
        self.update_status_label.setWordWrap(True)
        self.update_status_label.setProperty("tone", "muted")
        self.update_status_label.setStyleSheet("font-size: 12px;")
        update_form.addRow(bind_text(QLabel(), "当前版本"), self.current_version_label)
        update_form.addRow(bind_text(QLabel(), "自动检查"), self.auto_check_updates)
        update_form.addRow(bind_text(QLabel(), "更新通道"), self.update_channel_combo)
        update_form.addRow(bind_text(QLabel(), "检查状态"), self.update_status_label)
        update_layout.addLayout(update_form)

        update_actions = QHBoxLayout()
        update_actions.setContentsMargins(0, 0, 0, 0)
        update_actions.setSpacing(8)
        self.check_updates_button = bind_text(QPushButton(), "检查更新")
        self.check_updates_button.clicked.connect(self._check_updates)
        self.skip_update_button = bind_text(QPushButton(), "跳过当前版本")
        self.skip_update_button.clicked.connect(self._skip_current_update)
        self.project_homepage_button = bind_text(QPushButton(), "GitHub 项目主页")
        self.project_homepage_button.clicked.connect(self._open_project_homepage)
        update_actions.addWidget(self.check_updates_button)
        update_actions.addWidget(self.skip_update_button)
        update_actions.addWidget(self.project_homepage_button)
        update_actions.addStretch(1)
        update_layout.addLayout(update_actions)
        # 常驻入口比弹窗索要 Star 更克制，也让安装版用户能随时找到源码和反馈渠道。
        project_hint = bind_text(QLabel(), "如果 TokenMeter 对你有帮助，欢迎在 GitHub 点 Star，帮助更多人发现它。")
        project_hint.setWordWrap(True)
        project_hint.setProperty("tone", "muted")
        project_hint.setStyleSheet("font-size: 12px;")
        update_layout.addWidget(project_hint)

        update_page_layout.addWidget(self.update_card)
        update_page_layout.addStretch(1)

        root.addWidget(self.tabs, 1)
        self.tabs.currentChanged.connect(lambda _index: self._sync_window_size())
        self._load_values()
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        self.sync_accent_check.toggled.connect(self._on_accent_sync_changed)
        self.accent_color_edit.textChanged.connect(self._on_appearance_edited)
        self.accent_color_edit.editingFinished.connect(self._finish_accent_edit)
        self.accent_color_button.clicked.connect(self._choose_accent_color)
        self.panel_opacity_slider.valueChanged.connect(self._on_appearance_edited)
        self.reset_appearance_button.clicked.connect(self._reset_appearance)
        theme_controller().changed.connect(self._on_theme_state_changed)
        self._bind_update_controller()
        self._refresh_pet_controls()
        self._sync_window_size()
        self._connect_autosave()
        self._autosave_ready = True
        controller = language_controller()
        if controller is not None:
            controller.changed.connect(self._on_language_state_changed)

    def _refresh_pet_controls(self, message: str = "") -> None:
        busy = self._pet_worker is not None
        manifest = pet_extension.installed_manifest()
        removable = any(path.exists() for path in pet_extension.removable_directories())
        # 只有校验完整的已安装扩展包才允许启用，不能把开发构建或旧安装目录当成下载完成。
        available = manifest is not None
        self.vpet_check.setEnabled(available and not busy)
        if not available:
            # 状态刷新只纠正显示，不触发自动保存去改写其他设置。
            with QSignalBlocker(self.vpet_check):
                self.vpet_check.setChecked(False)
        self.pet_install_button.setEnabled(not busy and not removable)
        self.pet_uninstall_button.setEnabled(not busy and removable)
        cancellable = busy and self._pet_worker.operation != "uninstall"
        self.pet_cancel_button.setVisible(cancellable)
        self.pet_cancel_button.setEnabled(True)
        self.pet_check_button.setVisible(not cancellable)
        self.pet_check_button.setEnabled(not busy and removable)
        version = str(manifest.get("version") or "") if manifest else ""
        update_available = removable and self._pet_release is not None and (
            not version or compare_versions(version, self._pet_release.version) < 0
        )
        self.pet_update_button.setVisible(update_available)
        self.pet_update_button.setEnabled(not busy and removable and update_available)
        # 更新替换下载、取消替换检查，避免任务状态变化后把同一行挤成四五个按钮。
        self.pet_install_button.setVisible(not update_available)
        bind_text(self.pet_version_label, f"桌宠版本：v{version}" if version else (
            "桌宠版本：旧版（无版本号）" if available else
            "桌宠版本：不可用" if removable else "桌宠版本：未安装"
        ))
        if not message:
            message = (
                "桌宠可用；启用后替代悬浮球，原有面板保持不变。" if available else
                "桌宠扩展包不完整，请卸载后重新下载。" if removable else
                "未安装桌宠扩展包；默认使用原有悬浮球和面板，按需下载。"
            )
        bind_text(self.pet_status_label, message)

    def _update_pet(self) -> None:
        if self._pet_worker is not None or self._pet_release is None:
            return
        answer = QMessageBox.question(
            self, tr("更新桌宠扩展包"),
            tr("将更新桌宠至 v{version}，期间暂停桌宠；主程序和主题保持不变。是否继续？",
               version=self._pet_release.version),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start_pet_task("update")

    def _install_pet(self) -> None:
        if self._pet_worker is not None:
            return
        answer = QMessageBox.question(
            self, tr("下载桌宠扩展包"),
            tr("将从 GitHub 下载独立桌宠资源和运行时，安装完成后可手动启用。是否继续？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes and self._pet_worker is None:
            self._start_pet_task("install")

    def _uninstall_pet(self) -> None:
        if self._pet_worker is not None:
            return
        answer = QMessageBox.question(
            self, tr("卸载桌宠扩展包"),
            tr("卸载将关闭桌宠并恢复悬浮球；保留账户、用量数据和桌宠偏好。是否继续？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes or self._pet_worker is not None:
            return
        try:
            # 先持久化关闭状态并通知主窗口停止子进程，再删除文件，避免占用和重启后反复报错。
            config_manager.save_config({"VPET_ENABLED": False})
            self.vpet_check.setChecked(False)
            if self.on_saved:
                self.on_saved()
        except Exception as exc:
            self._refresh_pet_controls(f"桌宠扩展操作失败：{exc}")
            return
        self._start_pet_task("uninstall")

    def _start_pet_task(self, operation: str) -> None:
        if self._pet_worker is not None:
            return
        worker = _PetExtensionWorker(operation, self, release=self._pet_release)
        self._pet_worker = worker
        worker.progress_changed.connect(self._pet_progress)
        worker.finished.connect(self._pet_task_finished)
        if operation == "update":
            # 只暂停宿主，不改 VPET_ENABLED；失败后也能按原偏好恢复旧桌宠。
            self.pet_update_started.emit()
        self._refresh_pet_controls({
            "install": "正在下载桌宠扩展包…", "update": "正在更新桌宠扩展包…",
            "uninstall": "正在卸载桌宠扩展包…", "check": "正在检查桌宠更新…",
        }[operation])
        worker.start()

    def _pet_progress(self, payload: dict) -> None:
        bind_text(self.pet_status_label,
                  f"正在下载桌宠：{format_bytes(int(payload.get('downloaded') or 0))} / "
                  f"{format_bytes(int(payload.get('total') or 0))}")

    def _cancel_pet_download(self) -> None:
        if self._pet_worker is not None and self._pet_worker.operation != "uninstall":
            self._pet_worker.requestInterruption()
            self.pet_cancel_button.setEnabled(False)
            bind_text(self.pet_status_label, "正在取消下载…")

    def _pet_task_finished(self) -> None:
        worker = self._pet_worker
        if worker is None:
            return
        # finished 到达后再释放线程引用，设置页关闭或后台任务结束都不会销毁运行中的 QThread。
        self._pet_worker = None
        if isinstance(worker.error, DownloadCancelled):
            message = "已取消下载，继续使用原有面板。"
        elif worker.error is not None:
            message = f"桌宠扩展操作失败：{worker.error}"
        elif worker.operation == "check":
            self._pet_release = worker.release
            manifest = pet_extension.installed_manifest() or {}
            version = manifest.get("version")
            if self._pet_release is not None and (not version or compare_versions(version, self._pet_release.version) < 0):
                message = f"发现桌宠新版本 v{self._pet_release.version}，可单独更新。"
            else:
                message = "桌宠已是当前主程序可用的最新版本。"
        elif worker.operation == "install":
            self._pet_release = None
            message = "桌宠扩展包已安装，可打开上方开关启用。"
        elif worker.operation == "update":
            self._pet_release = None
            message = "桌宠扩展已更新，主程序和主题保持不变。"
        else:
            self._pet_release = None
            message = "桌宠扩展包已卸载，已恢复悬浮球，原有面板保持不变。"
        if worker.operation == "update":
            self.pet_update_finished.emit()
        worker.deleteLater()
        self._refresh_pet_controls(message)

    def stop_pet_task(self) -> None:
        if self._pet_worker is not None:
            # 网络有超时，解压逐块响应取消；退出前等临时文件清理完，防止留下半安装目录。
            self._pet_worker.requestInterruption()
            self._pet_worker.wait()

    def _add_settings_page(self, title: str, description: str) -> QVBoxLayout:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 20, 0, 8)
        layout.setSpacing(12)
        hint = bind_text(QLabel(), description)
        hint.setWordWrap(True)
        hint.setProperty("tone", "muted")
        layout.addWidget(hint)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        add_tab(self.tabs, scroll, title)
        return layout

    @staticmethod
    def _add_switch_row(layout, title: str, hint: str, control: QCheckBox) -> None:
        row = QFrame()
        row.setObjectName("settingsSwitchRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 16, 8, 16)
        row_layout.setSpacing(20)
        labels = QVBoxLayout()
        labels.setSpacing(8)
        label = bind_text(QLabel(), title)
        label.setObjectName("settingsRowTitle")
        label.setBuddy(control)
        detail = bind_text(QLabel(), hint)
        detail.setWordWrap(True)
        detail.setProperty("tone", "muted")
        labels.addWidget(label)
        labels.addWidget(detail)
        row_layout.addLayout(labels, 1)
        row_layout.addWidget(control)
        bind_text(control, hint, method='setToolTip')
        layout.addWidget(row)

    def _connect_autosave(self) -> None:
        # 只监听用户操作，加载配置、切换草稿和后台同步凭据不能再次触发写盘。
        for control in self.findChildren(QCheckBox):
            if control is not self.sync_accent_check:
                control.clicked.connect(self._schedule_save)
        for control in self.findChildren(QComboBox):
            if control not in (self.theme_combo, self.language_combo):
                control.activated.connect(self._schedule_save)
        for control in self.findChildren(QSpinBox):
            control.setKeyboardTracking(False)
            control.valueChanged.connect(
                lambda _value, target=control: self._schedule_save() if target.hasFocus() else None
            )
            control.editingFinished.connect(self._schedule_save)
        for control in self.findChildren(QTimeEdit):
            control.setKeyboardTracking(False)
            control.timeChanged.connect(
                lambda _value, target=control: self._schedule_save() if target.hasFocus() else None
            )
            control.editingFinished.connect(self._schedule_save)

    def _schedule_save(self, *_args) -> None:
        if not self._autosave_ready:
            return
        self._save_pending = True
        self._set_feedback(self.save_feedback, "等待保存…", "muted")
        self._save_timer.start()

    def _auto_save(self) -> None:
        # 确认地址等模态对话框会启动嵌套事件循环，避免定时器重入并重复弹窗。
        if self._saving:
            self._save_timer.start()
            return
        self._save_timer.stop()
        base_editor = self._provider_widgets.get("BASE")
        if isinstance(base_editor, QLineEdit) and base_editor.hasFocus() and base_editor.isModified():
            # 地址尚在输入时不触发信任确认，等 editingFinished 再保存完整地址。
            return
        self._save_pending = False
        self._saving = True
        try:
            values = self._values()
            current = config_manager.all_config()
            scheduled_dir = config_manager.pending_data_dir() or config_manager.CONFIG_DIR
            if (
                all(current.get(key) == value for key, value in values.items())
                and Path(self._selected_data_dir).resolve(strict=False)
                == Path(scheduled_dir).resolve(strict=False)
            ):
                self._set_feedback(self.save_feedback, "已自动保存", "success")
                return
            self._save()
        except Exception as exc:
            self._set_feedback(self.save_feedback, f"自动保存失败：{exc}", "danger")
        finally:
            self._saving = False

    def flush_pending_saves(self) -> None:
        # 返回或退出时不能丢失防抖窗口内的最后一次修改。
        focused = self.focusWidget()
        if focused is not None:
            # 先完成输入框的失焦提交，避免关闭后才排入新的保存定时器。
            focused.clearFocus()
        if self._appearance_save_timer.isActive():
            self._commit_appearance()
        if self._save_pending:
            self._auto_save()

    def reject(self) -> None:
        self.flush_pending_saves()
        super().reject()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_window_size()

    def _bind_update_controller(self) -> None:
        if self.update_controller is None:
            bind_text(self.current_version_label, "v开发模式")
            bind_text(self.update_status_label, "当前窗口未接入更新控制器。")
            self.skip_update_button.setEnabled(False)
            return
        bind_text(self.current_version_label, self.update_controller.version_text())
        self.update_controller.status_changed.connect(self._set_update_status)
        self.update_controller.latest_release_changed.connect(self._on_latest_release_changed)
        self._set_update_status(self.update_controller.status_text())
        self._on_latest_release_changed(self.update_controller.latest_release())

    def _set_update_status(self, text: str) -> None:
        bind_text(self.update_status_label, text)

    def _on_latest_release_changed(self, release) -> None:
        self.skip_update_button.setEnabled(release is not None)

    def _on_provider_changed(self, _index: int) -> None:
        self._remember_visible_credentials()
        provider_id = self.provider_combo.currentData()
        self._render_credentials(provider_id)

    @staticmethod
    def _peak_time_edit() -> QTimeEdit:
        editor = QTimeEdit()
        editor.setDisplayFormat("HH:mm")
        editor.setTime(QTime(0, 0))
        editor.setFixedWidth(92)
        return editor

    @staticmethod
    def _peak_period_row(start: QTimeEdit, end: QTimeEdit) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(start)
        layout.addWidget(bind_text(QLabel(), "至"))
        layout.addWidget(end)
        layout.addStretch(1)
        return row

    def _set_peak_pricing_inputs_enabled(self, enabled: bool) -> None:
        for editor in (
            self.deepseek_peak_period_1_start,
            self.deepseek_peak_period_1_end,
            self.deepseek_peak_period_2_start,
            self.deepseek_peak_period_2_end,
        ):
            editor.setEnabled(enabled)

    def _on_theme_changed(self, _index: int) -> None:
        if self._appearance_save_timer.isActive():
            self._commit_appearance()
        mode = str(self.theme_combo.currentData() or "dark")
        self.theme_requested.emit(mode)

    def _on_language_changed(self, _index: int) -> None:
        from PySide6.QtWidgets import QApplication

        controller = language_controller()
        if controller is None:
            controller = configure_language(QApplication.instance(), config_manager.get("UI_LANGUAGE", "system"))
            controller.changed.connect(self._on_language_state_changed)
        previous = controller.preference
        requested = str(self.language_combo.currentData() or "system")
        try:
            config_manager.save_ui_language(requested)
        except Exception:
            config_manager.logger().exception("Language preference could not be saved")
            blocker = QSignalBlocker(self.language_combo)
            self.language_combo.setCurrentIndex(self.language_combo.findData(previous))
            del blocker
            self.set_theme_feedback("语言切换失败，已恢复原设置。", "danger")
            return
        controller.set_language(requested)
        self.set_theme_feedback("语言已切换。", "success")

    def _on_language_state_changed(self, preference: str, _resolved: str) -> None:
        blocker = QSignalBlocker(self.language_combo)
        self.language_combo.setCurrentIndex(self.language_combo.findData(preference))
        del blocker

    def _on_theme_state_changed(self, mode: str, resolved: str) -> None:
        self.set_theme_mode(mode, resolved)

    def _on_accent_sync_changed(self, enabled: bool) -> None:
        if self._appearance_save_timer.isActive():
            # 切联动策略前提交当前颜色，避免防抖中的编辑按新策略保存到错误的模式。
            self._commit_appearance()
        controller = theme_controller()
        color, opacity = controller.appearance(self._resolved_theme)
        try:
            config_manager.save_ui_appearance(
                self._resolved_theme, color, opacity, sync_accent=enabled
            )
        except Exception:
            config_manager.logger().exception("Accent sync preference could not be saved")
            blocker = QSignalBlocker(self.sync_accent_check)
            self.sync_accent_check.setChecked(controller.sync_accent)
            del blocker
            self.set_theme_feedback("主题外观保存失败，已恢复原设置。", "danger")
            return
        controller.set_accent_sync(enabled)
        self.set_theme_feedback("主题外观已保存。", "success")

    def set_theme_mode(self, mode: str, resolved: str | None = None) -> None:
        """Synchronize the selector without requesting the same change again."""

        if resolved in {"light", "dark"}:
            self._resolved_theme = resolved
        index = self.theme_combo.findData(mode)
        if index < 0:
            index = self.theme_combo.findData("dark")
        blocker = QSignalBlocker(self.theme_combo)
        self.theme_combo.setCurrentIndex(index)
        del blocker
        blocker = QSignalBlocker(self.sync_accent_check)
        self.sync_accent_check.setChecked(theme_controller().sync_accent)
        del blocker
        self._set_appearance_controls(self._resolved_theme)

    def _set_appearance_controls(
        self,
        theme_name: str,
        color: str | None = None,
        opacity: int | None = None,
    ) -> None:
        if color is None or opacity is None:
            color, opacity = theme_controller().appearance(theme_name)
        normalized = QColor(color).name(QColor.NameFormat.HexRgb).upper()
        self._syncing_appearance = True
        try:
            color_blocker = QSignalBlocker(self.accent_color_edit)
            opacity_blocker = QSignalBlocker(self.panel_opacity_slider)
            self.accent_color_edit.setText(normalized)
            self.panel_opacity_slider.setValue(int(opacity))
            del color_blocker, opacity_blocker
            bind_text(self.panel_opacity_label, f"{int(opacity)}%")
            self._refresh_accent_swatch(normalized)
        finally:
            self._syncing_appearance = False

    @staticmethod
    def _valid_accent_color(value: str) -> bool:
        return re.fullmatch(r"#[0-9A-Fa-f]{6}", value.strip()) is not None

    def _refresh_accent_swatch(self, color: str) -> None:
        self.accent_color_button.setStyleSheet(
            f"background-color: {color}; border: 1px solid palette(mid);"
        )

    def _on_appearance_edited(self, _value=None) -> None:
        bind_text(self.panel_opacity_label, f"{self.panel_opacity_slider.value()}%")
        if self._syncing_appearance:
            return
        color = self.accent_color_edit.text().strip()
        if not self._valid_accent_color(color):
            return
        normalized = color.upper()
        self._refresh_accent_swatch(normalized)
        opacity = self.panel_opacity_slider.value()
        self.appearance_preview_requested.emit(
            self._resolved_theme, normalized, opacity
        )
        # 拖动滑块时先连续预览，停顿后再写盘，避免高频替换配置文件。
        self._appearance_save_timer.start()

    def _finish_accent_edit(self) -> None:
        if not self._valid_accent_color(self.accent_color_edit.text()):
            self._appearance_save_timer.stop()
            self._set_appearance_controls(self._resolved_theme)
            self.set_theme_feedback("请输入 #RRGGBB 格式的主题色。", "danger")
            return
        self._commit_appearance()

    def _commit_appearance(self) -> None:
        self._appearance_save_timer.stop()
        color = self.accent_color_edit.text().strip().upper()
        if not self._valid_accent_color(color):
            return
        self.appearance_requested.emit(
            self._resolved_theme,
            color,
            self.panel_opacity_slider.value(),
        )

    def _choose_accent_color(self) -> None:
        # Qt 色板是进程级状态；按槽位恢复，旧配置缺少的槽位保持默认白色。
        saved_colors = config_manager.get("UI_CUSTOM_COLORS", [])
        previous_colors = []
        for index in range(QColorDialog.customCount()):
            color = QColor(saved_colors[index] if index < len(saved_colors) else "#FFFFFF")
            QColorDialog.setCustomColor(index, color)
            previous_colors.append(color.name(QColor.NameFormat.HexRgb).upper())
        initial = QColor(self.accent_color_edit.text().strip())
        selected = QColorDialog.getColor(initial, self, "选择主题主色")
        if selected.isValid():
            self.accent_color_edit.setText(
                selected.name(QColor.NameFormat.HexRgb).upper()
            )
            self._commit_appearance()

        # “添加到自定义颜色”独立于选择主题色，即使取消选色也要保留新增色板。
        custom_colors = [
            QColorDialog.customColor(index).name(QColor.NameFormat.HexRgb).upper()
            for index in range(QColorDialog.customCount())
        ]
        if custom_colors != previous_colors:
            try:
                config_manager.save_ui_custom_colors(custom_colors)
            except Exception as exc:
                self.set_theme_feedback(f"自定义颜色保存失败：{exc}", "danger")
            else:
                if not selected.isValid():
                    self.set_theme_feedback("自定义颜色已保存。", "success")

    def _reset_appearance(self) -> None:
        base = LIGHT_THEME if self._resolved_theme == "light" else DARK_THEME
        self._set_appearance_controls(self._resolved_theme, base.accent, 100)
        self.appearance_preview_requested.emit(
            self._resolved_theme, base.accent, 100
        )
        self.appearance_requested.emit(self._resolved_theme, base.accent, 100)

    def set_theme_feedback(self, message: str, tone: str = "muted") -> None:
        self._set_feedback(self.save_feedback, message, tone)

    def _set_feedback(self, label: QLabel, message: str, tone: str) -> None:
        label.setProperty("tone", tone)
        bind_text(label, message)
        bind_text(label, message, method='setToolTip')
        label.style().unpolish(label)
        label.style().polish(label)
        label.update()
        if label is self.save_feedback:
            self.save_state_changed.emit(message, tone)

    def open_provider(self, provider_id: str, start_cookie_acquisition: bool = False) -> None:
        """Focus a provider and optionally begin the browser flow from a tray alert."""

        index = self.provider_combo.findData(provider_id)
        if index >= 0:
            self.provider_combo.setCurrentIndex(index)
        self.tabs.setCurrentIndex(0)
        if start_cookie_acquisition:
            # Let the provider switch finish rendering before the worker reads its controls.
            QTimer.singleShot(0, self._begin_cookie_acquire)

    def _sync_window_size(self) -> None:
        # 内嵌设置由主面板布局分配空间，切换页签或平台时不能自行调整窗口尺寸。
        if not self.isWindow():
            return
        self.content.adjustSize()
        self.tabs.adjustSize()
        content_size = self.tabs.sizeHint()
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            # Win10 在高缩放或任务栏较高时，可用工作区会明显变小；这里限制
            # 对话框高度并保留滚动，避免底部按钮被裁到屏幕外却无法操作。
            max_height = max(360, screen.availableGeometry().height() - 80)
        else:
            max_height = content_size.height()
        target_width = min(self.maximumWidth(), max(self.minimumWidth(), content_size.width()))
        target_height = min(max(440, content_size.height() + 82), max_height)
        self.resize(target_width, target_height)

    def _begin_cookie_acquire(self) -> None:
        provider_cls = PROVIDERS.get(self._rendered_provider_id)
        if not provider_cls or not (
            getattr(provider_cls, "supports_cookie_acquisition", False)
            or getattr(provider_cls, "supports_browser_credential_acquisition", False)
        ):
            return
        if self._cookie_acquire_worker is not None:
            return
        self._cookie_acquire_provider_id = self._rendered_provider_id
        self._cookie_acquire_button.setEnabled(False)
        bind_text(self._cookie_acquire_button, "正在打开浏览器…")
        bind_text(self._cookie_acquire_status, "正在打开浏览器，请在浏览器中完成登录。")
        worker = _CookieAcquireWorker(provider_cls, self)
        self._cookie_acquire_worker = worker
        worker.success.connect(
            lambda cookie, provider_id=self._rendered_provider_id: self._apply_acquired_cookie(
                provider_id, cookie
            )
        )
        worker.error.connect(self._cookie_acquire_failed)
        worker.finished.connect(self._cleanup_cookie_acquire_worker)
        worker.start()

        def _after_browser_open() -> None:
            if self._cookie_acquire_worker is worker and worker.isRunning():
                self._cookie_finish_button.setVisible(
                    not self._credential_acquire_automatic
                )
                if self._credential_acquire_automatic:
                    bind_text(self._cookie_acquire_status, f"浏览器已打开，请登录；程序将自动捕获并验证 {self._credential_acquire_label}。")
                else:
                    self._cookie_finish_button.setEnabled(True)
                    bind_text(self._cookie_acquire_status, "浏览器已打开，请登录后回到本窗口点击“完成采集”。")

        QTimer.singleShot(500, _after_browser_open)

    def _finish_cookie_acquire(self) -> None:
        if self._cookie_acquire_worker is None:
            return
        bind_text(self._cookie_acquire_status, f"正在读取 {self._credential_acquire_label}…")
        self._cookie_acquire_worker.stop_and_collect()

    def _apply_acquired_cookie(
        self,
        provider_id: str,
        acquired: _AcquiredCookie | str,
    ) -> None:
        provider_cls = PROVIDERS.get(provider_id)
        if not provider_cls:
            return
        if isinstance(acquired, str):
            # Preserve the existing direct-call seam used by older UI paths and
            # tests; worker-originated results carry explicit validation state.
            acquired = _AcquiredCookie(acquired)
        cookie_text = acquired.cookie_text
        mapper = getattr(provider_cls, "acquired_credential_values", None)
        values = (
            mapper(cookie_text)
            if callable(mapper)
            else provider_cls.acquired_cookie_values(cookie_text)
        )
        if not values:
            return
        if not acquired.direct_usable:
            # A valid Chromium session may rely on storage-backed refresh state.
            # Do not replace a usable persisted Cookie with a value that requests
            # cannot replay; the provider will use the retained browser instead.
            self._cookie_acquire_button.setEnabled(True)
            bind_text(self._cookie_acquire_button, f"一键获取 {self._credential_acquire_label}")
            self._cookie_finish_button.setVisible(False)
            bind_text(self._cookie_acquire_status, "专用浏览器会话已验证；Cookie 无法由程序直连，当前凭据未覆盖。")
            return
        # Save the fresh browser session immediately so changing tabs cannot restore stale drafts.
        self._provider_drafts.setdefault(provider_id, {}).update(values)
        saved_automatically = False
        if bool(getattr(provider_cls, "credential_acquisition_automatic", False)):
            secure_values = {
                f"{provider_id.upper()}_{field.upper()}": value
                for field, value in values.items()
            }
            try:
                config_manager.save_config(secure_values)
            except Exception:
                config_manager.logger().error(
                    "Browser credential could not be saved: provider=%s",
                    provider_id,
                )
                if self._rendered_provider_id == provider_id:
                    self._cookie_acquire_button.setEnabled(True)
                    bind_text(self._cookie_acquire_status, f"{self._credential_acquire_label} 已验证，但安全保存失败。")
                return
            saved_automatically = True
        if self._rendered_provider_id != provider_id:
            if saved_automatically and self.on_saved:
                self.on_saved()
            elif not saved_automatically:
                self._schedule_save()
            return
        for field, value in values.items():
            widget = self._provider_widgets.get(field)
            if isinstance(widget, QPlainTextEdit):
                widget.setPlainText(value)
            elif isinstance(widget, QLineEdit):
                widget.setText(value)
        self._cookie_acquire_button.setEnabled(True)
        bind_text(self._cookie_acquire_button, f"一键获取 {self._credential_acquire_label}")
        self._cookie_finish_button.setVisible(False)
        bind_text(self._cookie_acquire_status, f"{self._credential_acquire_label} 已验证并安全保存。"
            if saved_automatically
            else f"{self._credential_acquire_label} 已自动填入，正在保存。")
        if saved_automatically and self.on_saved:
            self.on_saved()
        elif not saved_automatically:
            self._schedule_save()

    def sync_persisted_cookie(self, provider_id: str, cookie_text: str) -> None:
        """Keep an open settings draft aligned with an externally renewed cookie."""

        provider_cls = PROVIDERS.get(provider_id)
        if not provider_cls:
            return
        values = provider_cls.acquired_cookie_values(cookie_text)
        if not values:
            return
        self._provider_drafts.setdefault(provider_id, {}).update(values)
        if self._rendered_provider_id != provider_id:
            return
        for field, value in values.items():
            widget = self._provider_widgets.get(field)
            if isinstance(widget, QPlainTextEdit):
                widget.setPlainText(value)
            elif isinstance(widget, QLineEdit):
                widget.setText(value)
        if self._cookie_acquire_status is not None:
            bind_text(self._cookie_acquire_status, "Cookie 已在后台自动续期并保存。")

    def _cookie_acquire_failed(self, message: str) -> None:
        if self._rendered_provider_id == getattr(self, "_cookie_acquire_provider_id", ""):
            self._cookie_acquire_button.setEnabled(True)
            bind_text(self._cookie_acquire_button, f"重试获取 {self._credential_acquire_label}")
            self._cookie_finish_button.setVisible(False)
            bind_text(self._cookie_acquire_status, str(message))
        config_manager.logger().warning("cookie acquire failed: %s", str(message))

    def _cleanup_cookie_acquire_worker(self) -> None:
        worker = self._cookie_acquire_worker
        if worker is not None:
            worker.deleteLater()
        self._cookie_acquire_worker = None

    def _remember_visible_credentials(self) -> None:
        if not self._rendered_provider_id:
            return
        draft: dict[str, str] = {}
        for field, widget in self._provider_widgets.items():
            if isinstance(widget, QPlainTextEdit):
                draft[field] = widget.toPlainText().strip()
            else:
                draft[field] = widget.text().strip()
        # 小米 MiMo：若 Cookie 中已自带 ``api-platform_ph`` 且用户没有显式填
        # 写过对应的输入框，则自动回填，避免用户重复复制相同内容。
        if self._rendered_provider_id == "mimo":
            cookie_value = draft.get("COOKIE", "") or ""
            ph_widget = self._provider_widgets.get("API_PLATFORM_PH")
            if cookie_value and ph_widget is not None:
                ph_in_cookie = _extract_cookie_value(cookie_value, "api-platform_ph")
                if ph_in_cookie:
                    current_ph = (
                        ph_widget.toPlainText().strip()
                        if isinstance(ph_widget, QPlainTextEdit)
                        else ph_widget.text().strip()
                    )
                    if not current_ph:
                        if isinstance(ph_widget, QPlainTextEdit):
                            ph_widget.setPlainText(ph_in_cookie)
                        else:
                            ph_widget.setText(ph_in_cookie)
                        draft["API_PLATFORM_PH"] = ph_in_cookie
        self._provider_drafts[self._rendered_provider_id] = draft

    def _render_credentials(self, provider_id: str) -> None:
        # Clear any existing widgets.
        while self.credentials_layout.count():
            item = self.credentials_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        self._provider_widgets = {}
        self._cookie_acquire_button = None
        self._cookie_finish_button = None
        self._cookie_acquire_status = None

        provider_cls = PROVIDERS.get(provider_id)
        if not provider_cls:
            return
        provider_instance = provider_cls()
        # Read from the in-memory cache (already populated by a prior
        # `load_config()` call) to avoid touching Win32 credential APIs
        # from potentially non-main threads.
        cached = config_manager.all_config()
        draft = self._provider_drafts.get(provider_id, {})
        upper_id = provider_id.upper()

        header = bind_text(QLabel(), f"{provider_instance.name} 凭据")
        header.setStyleSheet("font-size: 14px; font-weight: 600;")
        self.credentials_layout.addWidget(header)

        for field, meta in (provider_instance.credential_fields or {}).items():
            label = str(meta.get("label") or field)
            hint = str(meta.get("hint") or "")
            secret = bool(meta.get("secret"))
            multiline = bool(meta.get("multiline"))
            directory = bool(meta.get("directory"))
            row_widget, edit = self._build_credential_row(
                label, hint, secret, multiline, directory
            )
            key = f"{upper_id}_{field.upper()}"
            initial = draft.get(field, str(cached.get(key, "")))
            if directory and initial:
                initial_path = Path(initial).expanduser()
                if initial_path.name.lower() == "auth.json":
                    initial = str(initial_path.parent)
            if isinstance(edit, QPlainTextEdit):
                edit.setPlainText(initial)
            else:
                edit.setText(initial)
            self._provider_widgets[field] = edit
            self.credentials_layout.addWidget(row_widget)
            supports_cookie = (
                field == "COOKIE"
                and getattr(provider_cls, "supports_cookie_acquisition", False)
            )
            supports_credential = (
                bool(meta.get("browser_acquisition"))
                and getattr(
                    provider_cls, "supports_browser_credential_acquisition", False
                )
            )
            if supports_cookie or supports_credential:
                self._add_cookie_acquire_row(
                    provider_instance.name,
                    str(getattr(provider_cls, "credential_acquisition_label", "Cookie")),
                    bool(
                        getattr(
                            provider_cls, "credential_acquisition_automatic", False
                        )
                    ),
                )
        # 小米 MiMo：若 Cookie 中已含 ``api-platform_ph`` 则自动回填，
        # 避免用户再去 URL 里复制一次；若用户此前已经填写过
        # ``api-platform_ph`` 或 cookie 里没有，则保持原样。
        if provider_id == "mimo":
            cookie_widget = self._provider_widgets.get("COOKIE")
            ph_widget = self._provider_widgets.get("API_PLATFORM_PH")
            if cookie_widget is not None and ph_widget is not None:
                cookie_text = (
                    cookie_widget.toPlainText().strip()
                    if isinstance(cookie_widget, QPlainTextEdit)
                    else cookie_widget.text().strip()
                )
                ph_text = (
                    ph_widget.toPlainText().strip()
                    if isinstance(ph_widget, QPlainTextEdit)
                    else ph_widget.text().strip()
                )
                if cookie_text and not ph_text:
                    ph_in_cookie = _extract_cookie_value(cookie_text, "api-platform_ph")
                    if ph_in_cookie:
                        if isinstance(ph_widget, QPlainTextEdit):
                            ph_widget.setPlainText(ph_in_cookie)
                        else:
                            ph_widget.setText(ph_in_cookie)
        self._rendered_provider_id = provider_id
        self.deepseek_peak_pricing_card.setVisible(provider_id == "deepseek")
        for editor in self._provider_widgets.values():
            if isinstance(editor, QPlainTextEdit):
                editor.textChanged.connect(
                    lambda target=editor: self._schedule_save() if target.document().isModified() else None
                )
            else:
                editor.textEdited.connect(self._schedule_save)
                editor.editingFinished.connect(lambda target=editor: self._finish_credential_edit(target))
        self._sync_window_size()

    def _finish_credential_edit(self, editor: QLineEdit) -> None:
        # setText 也可能引发失焦信号；只提交真实输入，避免回填草稿或测试数据时误保存。
        if editor.isModified():
            editor.setModified(False)
            self._schedule_save()

    def _add_cookie_acquire_row(
        self, provider_name: str, credential_label: str = "Cookie", automatic: bool = False
    ) -> None:
        self._credential_acquire_label = credential_label
        self._credential_acquire_automatic = automatic
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)
        self._cookie_acquire_button = bind_text(QPushButton(), f"一键获取 {credential_label}")
        bind_text(self._cookie_acquire_button, f"打开浏览器登录 {provider_name} 后读取 {credential_label}", method='setToolTip')
        self._cookie_acquire_button.clicked.connect(self._begin_cookie_acquire)
        self._cookie_finish_button = bind_text(QPushButton(), "完成采集")
        self._cookie_finish_button.setVisible(False)
        self._cookie_finish_button.clicked.connect(self._finish_cookie_acquire)
        self._cookie_acquire_status = bind_text(QLabel(), f"通过独立浏览器登录后，可将 {credential_label} 自动填回此处。")
        self._cookie_acquire_status.setWordWrap(True)
        self._cookie_acquire_status.setProperty("tone", "muted")
        self._cookie_acquire_status.setStyleSheet("font-size: 12px;")
        layout.addWidget(self._cookie_acquire_button)
        layout.addWidget(self._cookie_finish_button)
        layout.addWidget(self._cookie_acquire_status, 1)
        self.credentials_layout.addWidget(row)

    def _build_credential_row(
        self,
        label: str,
        hint: str,
        secret: bool,
        multiline: bool,
        directory: bool = False,
    ) -> tuple[QWidget, Union[QLineEdit, QPlainTextEdit]]:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        label_widget = bind_text(QLabel(), label)
        label_widget.setStyleSheet("font-size: 13px;")
        layout.addWidget(label_widget)
        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(8)
        if multiline:
            editor: Union[QLineEdit, QPlainTextEdit] = QPlainTextEdit()
            bind_text(editor, "未填写" if not hint else hint, method='setPlaceholderText')
            editor.setFixedHeight(96)
        else:
            editor = QLineEdit()
            bind_text(editor, "未填写" if not hint else hint, method='setPlaceholderText')
        if secret and isinstance(editor, QLineEdit):
            editor.setEchoMode(QLineEdit.EchoMode.Password)
        if directory and isinstance(editor, QLineEdit):
            editor.setReadOnly(True)
            browse_button = bind_text(QPushButton(), "选择…")
            browse_button.setObjectName("credentialDirectoryBrowseButton")
            browse_button.clicked.connect(
                lambda _checked=False, target=editor, title=label: self._choose_credential_directory(
                    target, title
                )
            )
            default_button = bind_text(QPushButton(), "使用默认")
            default_button.setObjectName("credentialDirectoryDefaultButton")
            default_button.clicked.connect(
                lambda _checked=False, target=editor: target.clear()
            )
            default_button.clicked.connect(self._schedule_save)
        input_row.addWidget(editor, 1)
        if directory and isinstance(editor, QLineEdit):
            input_row.addWidget(browse_button)
            input_row.addWidget(default_button)
        layout.addLayout(input_row)
        return wrapper, editor

    def _choose_credential_directory(self, editor: QLineEdit, label: str) -> None:
        current = editor.text().strip()
        initial = Path(current).expanduser() if current else Path.home() / ".codex"
        if initial.name.lower() == "auth.json":
            initial = initial.parent
        selected = QFileDialog.getExistingDirectory(
            self, tr(f"选择{label}"), str(initial)
        )
        if selected:
            editor.setText(selected)
            self._schedule_save()

    def _load_values(self) -> None:
        values = config_manager.load_config()
        self.refresh_seconds.setValue(max(5, int(values.get("REFRESH_INTERVAL", 60_000)) // 1000))
        self.minute_usage_interval_minutes.setValue(
            int(values.get("MINUTE_USAGE_INTERVAL_MINUTES", 5))
        )
        chart_type = str(values.get("MINUTE_USAGE_CHART_TYPE", "bar"))
        self.minute_usage_chart_type.setCurrentIndex(
            max(0, self.minute_usage_chart_type.findData(chart_type))
        )
        self.minute_usage_retention_days.setValue(
            int(values.get("MINUTE_USAGE_RETENTION_DAYS", 3))
        )
        background_provider_ids = set(values.get("BACKGROUND_PROVIDER_IDS", []))
        for provider_id, check in self.background_provider_checks.items():
            check.setChecked(provider_id in background_provider_ids)
        self.set_theme_mode(
            str(values.get("UI_THEME", "dark")), theme_controller().resolved
        )
        color_key = f"UI_{self._resolved_theme.upper()}_ACCENT_COLOR"
        opacity_key = f"UI_{self._resolved_theme.upper()}_PANEL_OPACITY"
        base = LIGHT_THEME if self._resolved_theme == "light" else DARK_THEME
        self._set_appearance_controls(
            self._resolved_theme,
            str(values.get(color_key, base.accent)),
            int(values.get(opacity_key, 100)),
        )
        self.sync_accent_check.setChecked(bool(values.get("UI_SYNC_ACCENT_COLOR", True)))
        self.edge_hide_check.setChecked(bool(values.get("EDGE_HIDE_ENABLED", True)))
        self.vpet_check.setChecked(bool(values.get("VPET_ENABLED", False)))
        self.panel_auto_collapse_check.setChecked(
            bool(values.get("PANEL_AUTO_COLLAPSE_ON_DEACTIVATE", True))
        )
        self.autostart_check.setChecked(bool(values.get("AUTO_START_ENABLED", False)))
        self.deepseek_peak_pricing_enabled.setChecked(
            bool(values.get("DEEPSEEK_PEAK_PRICING_ENABLED", False))
        )
        for editor, key, fallback in (
            (
                self.deepseek_peak_period_1_start,
                "DEEPSEEK_PEAK_PERIOD_1_START",
                "09:00",
            ),
            (
                self.deepseek_peak_period_1_end,
                "DEEPSEEK_PEAK_PERIOD_1_END",
                "12:00",
            ),
            (
                self.deepseek_peak_period_2_start,
                "DEEPSEEK_PEAK_PERIOD_2_START",
                "14:00",
            ),
            (
                self.deepseek_peak_period_2_end,
                "DEEPSEEK_PEAK_PERIOD_2_END",
                "18:00",
            ),
        ):
            parsed = QTime.fromString(str(values.get(key, fallback)), "HH:mm")
            editor.setTime(
                parsed if parsed.isValid() else QTime.fromString(fallback, "HH:mm")
            )
        self._set_peak_pricing_inputs_enabled(
            self.deepseek_peak_pricing_enabled.isChecked()
        )
        selected_data_dir = config_manager.pending_data_dir() or config_manager.CONFIG_DIR
        self._selected_data_dir = selected_data_dir
        self.data_dir_edit.setText(str(selected_data_dir))
        migration_error = config_manager.data_dir_migration_error()
        if migration_error:
            self._set_feedback(
                self.data_dir_status,
                f"上次迁移失败，仍在使用原目录：{migration_error}",
                "danger",
            )
        elif config_manager.pending_data_dir() is not None:
            self._set_feedback(
                self.data_dir_status, "目录变更将在重启后执行。", "muted"
            )
        else:
            self._set_feedback(
                self.data_dir_status, "更改后需重启；迁移完成前不会删除原目录。", "muted"
            )
        self.auto_check_updates.setChecked(bool(values.get("UPDATE_AUTO_CHECK_ENABLED", True)))
        update_channel = str(values.get("UPDATE_CHANNEL", "stable"))
        update_index = max(0, self.update_channel_combo.findData(update_channel))
        self.update_channel_combo.setCurrentIndex(update_index)
        active_id = str(values.get("ACTIVE_PROVIDER", "")).lower()
        target_index = 0
        for index in range(self.provider_combo.count()):
            if self.provider_combo.itemData(index) == active_id:
                target_index = index
                break
        self.provider_combo.setCurrentIndex(target_index)
        self._render_credentials(self.provider_combo.currentData())

    def _values(self) -> dict[str, Any]:
        self._remember_visible_credentials()
        values: dict[str, Any] = {
            "REFRESH_INTERVAL": self.refresh_seconds.value() * 1000,
            "MINUTE_USAGE_CHART_TYPE": str(
                self.minute_usage_chart_type.currentData() or "bar"
            ),
            "MINUTE_USAGE_INTERVAL_MINUTES": self.minute_usage_interval_minutes.value(),
            "MINUTE_USAGE_RETENTION_DAYS": self.minute_usage_retention_days.value(),
            "ACTIVE_PROVIDER": str(self.provider_combo.currentData() or ""),
            "BACKGROUND_PROVIDER_IDS": [
                provider_id
                for provider_id, check in self.background_provider_checks.items()
                if check.isChecked()
            ],
            "UI_THEME": str(self.theme_combo.currentData() or "dark"),
            "EDGE_HIDE_ENABLED": self.edge_hide_check.isChecked(),
            "VPET_ENABLED": self.vpet_check.isChecked(),
            "PANEL_AUTO_COLLAPSE_ON_DEACTIVATE": self.panel_auto_collapse_check.isChecked(),
            "AUTO_START_ENABLED": self.autostart_check.isChecked(),
            "UPDATE_AUTO_CHECK_ENABLED": self.auto_check_updates.isChecked(),
            "UPDATE_CHANNEL": str(self.update_channel_combo.currentData() or "stable"),
            "DEEPSEEK_PEAK_PRICING_ENABLED": self.deepseek_peak_pricing_enabled.isChecked(),
            "DEEPSEEK_PEAK_PERIOD_1_START": self.deepseek_peak_period_1_start.time().toString(
                "HH:mm"
            ),
            "DEEPSEEK_PEAK_PERIOD_1_END": self.deepseek_peak_period_1_end.time().toString(
                "HH:mm"
            ),
            "DEEPSEEK_PEAK_PERIOD_2_START": self.deepseek_peak_period_2_start.time().toString(
                "HH:mm"
            ),
            "DEEPSEEK_PEAK_PERIOD_2_END": self.deepseek_peak_period_2_end.time().toString(
                "HH:mm"
            ),
        }
        # Persist credentials for all registered providers. The currently
        # selected provider is read from the on-screen inputs; other
        # providers keep their existing in-memory values so switching
        # between providers does not wipe credentials.
        existing = config_manager.all_config()
        for provider_id, _provider_name in list_providers():
            upper_id = provider_id.upper()
            provider_cls = PROVIDERS[provider_id]
            fields = list((getattr(provider_cls(), "credential_fields", {}) or {}).keys())
            for field in fields:
                key = f"{upper_id}_{field.upper()}"
                if key in values:
                    continue
                if field in self._provider_drafts.get(provider_id, {}):
                    values[key] = self._provider_drafts[provider_id][field]
                else:
                    values[key] = str(existing.get(key, ""))
        return values

    def _check_updates(self) -> None:
        if self.update_controller is None:
            self._set_update_status("当前运行环境未启用在线更新。")
            return
        self.update_controller.check_for_updates(manual=True, parent=self)

    def _skip_current_update(self) -> None:
        if self.update_controller is None:
            self._set_update_status("当前运行环境未启用在线更新。")
            return
        self.update_controller.skip_available_version(self)

    def _open_project_homepage(self) -> None:
        QDesktopServices.openUrl(QUrl(GITHUB_REPOSITORY_URL))

    def _choose_data_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            tr("选择应用数据目录"),
            str(self._selected_data_dir),
        )
        if not selected:
            return
        self._selected_data_dir = selected
        self.data_dir_edit.setText(str(self._selected_data_dir))
        self._set_feedback(
            self.data_dir_status, "将在下次启动时迁移全部应用数据。", "muted"
        )
        self._schedule_save()

    def _restore_default_data_dir(self) -> None:
        self._selected_data_dir = config_manager.DEFAULT_CONFIG_DIR.resolve(strict=False)
        self.data_dir_edit.setText(str(self._selected_data_dir))
        self._set_feedback(
            self.data_dir_status, "将在下次启动时恢复默认目录。", "muted"
        )
        self._schedule_save()

    def _save(self) -> None:
        self._save_timer.stop()
        self._save_pending = False
        values = self._values()
        for key, value in values.items():
            if (
                key.endswith("_BASE") and value
                and not config_manager.is_official_base_url(value, key.removesuffix("_BASE").lower())
                and value != config_manager.get(key, "")
            ):
                # 自动保存仅对新地址询问信任，避免修改无关开关时重复弹窗。
                result = QMessageBox.question(
                    self,
                    tr("非官方 API 地址"),
                    tr(f"{key} 会接收当前平台凭据，确认信任并继续吗？"),
                )
                if result != QMessageBox.StandardButton.Yes:
                    self._set_feedback(self.save_feedback, "未保存：请确认或恢复 API 地址。", "danger")
                    return
        try:
            selected_data_dir = config_manager.validate_data_dir_target(
                self._selected_data_dir
            )
        except (OSError, ValueError) as exc:
            self._set_feedback(self.save_feedback, f"应用数据目录不可用：{exc}", "danger")
            return
        previous_autostart = bool(config_manager.get("AUTO_START_ENABLED", False))
        requested_autostart = bool(values.get("AUTO_START_ENABLED", False))
        autostart_changed = previous_autostart != requested_autostart
        try:
            # 启动项可能被系统或安全软件移除；每次保存都按当前偏好重新对账。
            sync_autostart(requested_autostart)
        except AutostartError as exc:
            self._set_feedback(self.save_feedback, f"开机自启设置失败：{exc}", "danger")
            return
        try:
            config_manager.save_config(values)
        except Exception as exc:
            if autostart_changed:
                try:
                    # 配置未保存时恢复原启动行为，避免开关与持久化状态不一致。
                    sync_autostart(previous_autostart)
                except AutostartError:
                    config_manager.logger().warning(
                        "Windows autostart state could not be rolled back"
                    )
            self._set_feedback(self.save_feedback, f"保存失败，配置已回滚：{exc}", "danger")
            return
        scheduled_data_dir = (
            config_manager.pending_data_dir() or config_manager.CONFIG_DIR
        ).resolve(strict=False)
        data_dir_changed = selected_data_dir != scheduled_data_dir
        if data_dir_changed:
            try:
                config_manager.schedule_data_dir_change(selected_data_dir)
            except (OSError, ValueError) as exc:
                self._set_feedback(
                    self.save_feedback,
                    f"配置已保存，但应用数据目录变更失败：{exc}",
                    "danger",
                )
                return
        self._set_feedback(
            self.save_feedback,
            "已自动保存",
            "success",
        )
        if self.update_controller is not None:
            self.update_controller.reload_cached_release()
        if self.on_saved:
            self.on_saved()
        if data_dir_changed:
            QMessageBox.information(
                self,
                tr("重启后迁移"),
                tr("全部应用数据将在下次启动时迁移。迁移成功后才会切换目录，并清理原数据目录。"),
            )

    def _test_connection(self) -> None:
        try:
            candidate = config_manager.validate_config(self._values())
        except Exception as exc:
            self._set_feedback(
                self.connection_feedback, f"请先修正配置：{exc}", "danger"
            )
            return
        for key, value in candidate.items():
            if key.endswith("_BASE") and value and not config_manager.is_official_base_url(
                value, key.removesuffix("_BASE").lower()
            ):
                result = QMessageBox.question(
                    self,
                    tr("非官方 API 地址"),
                    tr(f"{key} 会接收当前平台凭据，确认信任并测试连接吗？"),
                )
                if result != QMessageBox.StandardButton.Yes:
                    return
        self.test_button.setEnabled(False)
        bind_text(self.test_button, "测试中…")
        self._set_feedback(
            self.connection_feedback, "正在使用当前输入的凭据测试连接…", "muted"
        )
        # MappingProxyType makes accidental mutation in the worker fail immediately;
        # validation has already produced an independent merged configuration copy.
        self._worker = ConnectionWorker(MappingProxyType(candidate), self)
        self._worker.finished_with_data.connect(self._connection_result)
        self._worker.start()

    def _connection_result(self, data: TokenData) -> None:
        self.test_button.setEnabled(True)
        bind_text(self.test_button, "测试连接")
        if data.status in {"ok", "partial"}:
            if data.status == "ok":
                self._set_feedback(self.connection_feedback, "连接成功。", "success")
            else:
                # Collect all error messages from providers that had issues.
                error_messages: list[str] = []
                for per in data.per_provider:
                    for err in per.errors:
                        error_messages.append(f"[{per.provider_name}] {err.message}")
                detail = "\n".join(error_messages) if error_messages else "未知错误"
                self._set_feedback(
                    self.connection_feedback, f"连接失败：\n{detail}", "danger"
                )
        else:
            message = data.errors[0].message if data.errors else "连接失败"
            self._set_feedback(self.connection_feedback, message, "danger")
        self._worker = None


def _normalize_cookie(raw: str) -> str:
    """规范粘贴的 Cookie: 去掉换行/多乙空白, 并以 ``; `` 连接."""
    tokens = [
        token.strip()
        for token in " ".join(str(raw).splitlines()).split(";")
        if token.strip()
    ]
    return "; ".join(tokens)


def _extract_cookie_value(raw: str, name: str) -> str:
    """在 ``k=v; k2=v2`` 字符串中定位 ``name`` 的值.

    会去掉值周围的双引号；未找到返回空字符串.
    """
    for token in " ".join(str(raw).splitlines()).split(";"):
        token = token.strip()
        if not token or "=" not in token:
            continue
        key, _, value = token.partition("=")
        if key.strip() == name:
            return value.strip().strip('"')
    return ""
