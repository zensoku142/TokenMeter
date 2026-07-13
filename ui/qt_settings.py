"""Qt settings dialog built from the provider registry.

Provider selection is now a simple dropdown, and only the credentials of the
selected provider are shown — keeping the dialog small and focused.
"""

from __future__ import annotations

from typing import Any, Callable, Union

from PySide6.QtCore import QSignalBlocker, QThread, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import config_manager
from api.providers import PROVIDERS, list_providers
from api.providers.base import FetchError
from data.store import TokenData
from ui.qt_theme import theme_controller
from ui.qt_update import AppUpdateController

_CARD_PADDING = 18


class ConnectionWorker(QThread):
    finished_with_data = Signal(object)

    def run(self) -> None:
        try:
            result = TokenData.fetch()
        except Exception as exc:
            config_manager.logger().exception("Connection test failed")
            result = TokenData(
                status="error",
                errors=[FetchError("UNKNOWN_ERROR", "连接测试", str(exc))],
            )
        self.finished_with_data.emit(result)


class _CookieAcquireWorker(QThread):
    """Run the selected provider's browser collection away from the UI thread."""

    success = Signal(str)
    error = Signal(str)

    def __init__(self, provider_cls, parent=None):
        super().__init__(parent)
        import threading

        self._stop_event = threading.Event()
        self._provider_cls = provider_cls

    def stop_and_collect(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        try:
            cookie = self._provider_cls.acquire_cookie_via_chrome(self._stop_event)
        except RuntimeError as exc:
            self.error.emit(self._provider_cls.describe_acquire_error(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.error.emit(self._provider_cls.describe_acquire_error(exc))
            return
        self.success.emit(cookie)


class SettingsWindow(QDialog):
    theme_requested = Signal(str)

    def __init__(
        self,
        parent=None,
        on_saved: Callable[[], None] | None = None,
        update_controller: AppUpdateController | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("TokenSpider 设置")
        self.setModal(False)
        self.setMinimumWidth(560)
        self.setMaximumWidth(720)
        self.on_saved = on_saved
        self.update_controller = update_controller
        self._worker: ConnectionWorker | None = None
        self._cookie_acquire_worker: "_CookieAcquireWorker | None" = None
        self._cookie_acquire_provider_id = ""
        self._rendered_provider_id = ""
        self._provider_widgets: dict[str, Union[QLineEdit, QPlainTextEdit]] = {}
        self._provider_drafts: dict[str, dict[str, str]] = {}
        self._test_config_backup: dict[str, Any] | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        self.tabs = QTabWidget(self)

        # The account tab is the only long page. Keeping the footer outside it
        # prevents Save/Cancel from scrolling away while users edit Cookies.
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QWidget()
        self.scroll_area.setWidget(self.content)
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(6, 4, 6, 8)
        content_layout.setSpacing(14)
        title = QLabel("账户与凭据")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")

        # Provider picker — single dropdown.
        picker_row = QHBoxLayout()
        picker_row.setContentsMargins(0, 0, 0, 0)
        picker_row.setSpacing(8)
        picker_label = QLabel("数据来源")
        picker_label.setStyleSheet("font-size: 13px; font-weight: 500;")
        self.provider_combo = QComboBox()
        for provider_id, provider_name in list_providers():
            self.provider_combo.addItem(f"{provider_name} ({provider_id})", provider_id)
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
        self.test_button = QPushButton("测试连接")
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
        content_layout.addLayout(connection_actions)
        content_layout.addWidget(self.connection_feedback)
        content_layout.addStretch(1)
        self.tabs.addTab(self.scroll_area, "账户与凭据")

        runtime_page = QWidget()
        runtime_layout = QVBoxLayout(runtime_page)
        runtime_layout.setContentsMargins(6, 8, 6, 8)
        runtime_layout.setSpacing(14)
        runtime_title = QLabel("运行行为")
        runtime_title.setStyleSheet("font-size: 18px; font-weight: 700;")
        runtime_layout.addWidget(runtime_title)
        runtime_hint = QLabel("控制数据刷新，以及悬浮球和面板在桌面的显示方式。")
        runtime_hint.setWordWrap(True)
        runtime_hint.setProperty("tone", "muted")
        runtime_hint.setStyleSheet("font-size: 12px;")
        runtime_layout.addWidget(runtime_hint)
        runtime_card = QFrame()
        runtime_card.setObjectName("settingsCard")
        runtime_layout.addWidget(runtime_card)
        runtime_form = QFormLayout(runtime_card)
        runtime_form.setContentsMargins(_CARD_PADDING, 14, _CARD_PADDING, 14)
        runtime_form.setHorizontalSpacing(16)
        runtime_form.setVerticalSpacing(10)
        self.refresh_seconds = QSpinBox()
        self.refresh_seconds.setRange(5, 3600)
        self.refresh_seconds.setSuffix(" 秒")
        runtime_form.addRow("刷新间隔", self.refresh_seconds)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("跟随系统", "system")
        self.theme_combo.addItem("浅色", "light")
        self.theme_combo.addItem("深色", "dark")
        self.theme_combo.setToolTip("主题会立即应用并保存，取消设置不会回滚主题")
        runtime_form.addRow("外观主题", self.theme_combo)
        self.edge_hide_check = QCheckBox("贴边自动隐藏")
        self.edge_hide_check.setToolTip("拖拽悬浮球到屏幕边缘后自动隐藏，鼠标移入时显示")
        runtime_form.addRow("贴边隐藏", self.edge_hide_check)
        self.panel_auto_collapse_check = QCheckBox("点击面板外部时自动收起")
        self.panel_auto_collapse_check.setToolTip(
            "点击其它应用使面板失焦时收起面板并显示悬浮球"
        )
        runtime_form.addRow("面板自动收起", self.panel_auto_collapse_check)
        runtime_layout.addStretch(1)
        self.tabs.addTab(runtime_page, "运行行为")

        self.update_card = QFrame()
        self.update_card.setObjectName("settingsCard")
        update_layout = QVBoxLayout(self.update_card)
        update_layout.setContentsMargins(_CARD_PADDING, 14, _CARD_PADDING, 14)
        update_layout.setSpacing(10)
        update_title = QLabel("软件更新")
        update_title.setStyleSheet("font-size: 14px; font-weight: 600;")
        update_layout.addWidget(update_title)

        update_form = QFormLayout()
        update_form.setHorizontalSpacing(16)
        update_form.setVerticalSpacing(8)
        self.current_version_label = QLabel()
        self.auto_check_updates = QCheckBox("启动后自动检查")
        self.update_channel_combo = QComboBox()
        self.update_channel_combo.addItem("正式版", "stable")
        self.update_channel_combo.addItem("预发布版", "prerelease")
        self.update_status_label = QLabel()
        self.update_status_label.setWordWrap(True)
        self.update_status_label.setProperty("tone", "muted")
        self.update_status_label.setStyleSheet("font-size: 12px;")
        update_form.addRow("当前版本", self.current_version_label)
        update_form.addRow("自动检查", self.auto_check_updates)
        update_form.addRow("更新通道", self.update_channel_combo)
        update_form.addRow("检查状态", self.update_status_label)
        update_layout.addLayout(update_form)

        update_actions = QHBoxLayout()
        update_actions.setContentsMargins(0, 0, 0, 0)
        update_actions.setSpacing(8)
        self.check_updates_button = QPushButton("检查更新")
        self.check_updates_button.clicked.connect(self._check_updates)
        self.skip_update_button = QPushButton("跳过当前版本")
        self.skip_update_button.clicked.connect(self._skip_current_update)
        update_actions.addWidget(self.check_updates_button)
        update_actions.addWidget(self.skip_update_button)
        update_actions.addStretch(1)
        update_layout.addLayout(update_actions)

        update_page = QWidget()
        update_page_layout = QVBoxLayout(update_page)
        update_page_layout.setContentsMargins(6, 8, 6, 8)
        update_page_layout.setSpacing(14)
        update_title = QLabel("软件更新")
        update_title.setStyleSheet("font-size: 18px; font-weight: 700;")
        update_page_layout.addWidget(update_title)
        update_page_layout.addWidget(self.update_card)
        update_page_layout.addStretch(1)
        self.tabs.addTab(update_page, "软件更新")

        root.addWidget(self.tabs, 1)
        self.save_feedback = QLabel()
        self.save_feedback.setWordWrap(True)
        self.save_feedback.setProperty("tone", "muted")
        self.save_feedback.setStyleSheet("font-size: 12px;")
        root.addWidget(self.save_feedback)
        actions = QHBoxLayout()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存并生效")
        save.setObjectName("primaryButton")
        save.clicked.connect(self._save)
        actions.addStretch(1)
        actions.addWidget(cancel)
        actions.addWidget(save)
        root.addLayout(actions)
        self.tabs.currentChanged.connect(lambda _index: self._sync_window_size())
        self._load_values()
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        theme_controller().changed.connect(self._on_theme_state_changed)
        self._bind_update_controller()
        self._sync_window_size()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_window_size()

    def _bind_update_controller(self) -> None:
        if self.update_controller is None:
            self.current_version_label.setText("v开发模式")
            self.update_status_label.setText("当前窗口未接入更新控制器。")
            self.skip_update_button.setEnabled(False)
            return
        self.current_version_label.setText(self.update_controller.version_text())
        self.update_controller.status_changed.connect(self._set_update_status)
        self.update_controller.latest_release_changed.connect(self._on_latest_release_changed)
        self._set_update_status(self.update_controller.status_text())
        self._on_latest_release_changed(self.update_controller.latest_release())

    def _set_update_status(self, text: str) -> None:
        self.update_status_label.setText(text)

    def _on_latest_release_changed(self, release) -> None:
        self.skip_update_button.setEnabled(release is not None)

    def _on_provider_changed(self, _index: int) -> None:
        self._remember_visible_credentials()
        provider_id = self.provider_combo.currentData()
        self._render_credentials(provider_id)

    def _on_theme_changed(self, _index: int) -> None:
        mode = str(self.theme_combo.currentData() or "dark")
        self.theme_requested.emit(mode)

    def _on_theme_state_changed(self, mode: str, _resolved: str) -> None:
        self.set_theme_mode(mode)

    def set_theme_mode(self, mode: str) -> None:
        """Synchronize the selector without requesting the same change again."""

        index = self.theme_combo.findData(mode)
        if index < 0:
            index = self.theme_combo.findData("dark")
        blocker = QSignalBlocker(self.theme_combo)
        self.theme_combo.setCurrentIndex(index)
        del blocker

    def set_theme_feedback(self, message: str, tone: str = "muted") -> None:
        self._set_feedback(self.save_feedback, message, tone)

    @staticmethod
    def _set_feedback(label: QLabel, message: str, tone: str) -> None:
        label.setProperty("tone", tone)
        label.setText(message)
        label.style().unpolish(label)
        label.style().polish(label)
        label.update()

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
        if not provider_cls or not getattr(provider_cls, "supports_cookie_acquisition", False):
            return
        if self._cookie_acquire_worker is not None:
            return
        self._cookie_acquire_provider_id = self._rendered_provider_id
        self._cookie_acquire_button.setEnabled(False)
        self._cookie_acquire_button.setText("正在打开浏览器…")
        self._cookie_acquire_status.setText("正在打开浏览器，请在浏览器中完成登录。")
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
                self._cookie_finish_button.setVisible(True)
                self._cookie_finish_button.setEnabled(True)
                self._cookie_acquire_status.setText(
                    "浏览器已打开，请登录后回到本窗口点击“完成采集”。"
                )

        QTimer.singleShot(500, _after_browser_open)

    def _finish_cookie_acquire(self) -> None:
        if self._cookie_acquire_worker is None:
            return
        self._cookie_acquire_status.setText("正在读取 Cookie…")
        self._cookie_acquire_worker.stop_and_collect()

    def _apply_acquired_cookie(self, provider_id: str, cookie_text: str) -> None:
        provider_cls = PROVIDERS.get(provider_id)
        if not provider_cls:
            return
        values = provider_cls.acquired_cookie_values(cookie_text)
        if not values:
            return
        # Save the fresh browser session immediately so changing tabs cannot restore stale drafts.
        self._provider_drafts.setdefault(provider_id, {}).update(values)
        if self._rendered_provider_id != provider_id:
            return
        for field, value in values.items():
            widget = self._provider_widgets.get(field)
            if isinstance(widget, QPlainTextEdit):
                widget.setPlainText(value)
            elif isinstance(widget, QLineEdit):
                widget.setText(value)
        self._cookie_acquire_button.setEnabled(True)
        self._cookie_acquire_button.setText("一键获取 Cookie")
        self._cookie_finish_button.setVisible(False)
        self._cookie_acquire_status.setText("Cookie 已自动填入，请保存设置。")

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
            self._cookie_acquire_status.setText("Cookie 已在后台自动续期并保存。")

    def _cookie_acquire_failed(self, message: str) -> None:
        if self._rendered_provider_id == getattr(self, "_cookie_acquire_provider_id", ""):
            self._cookie_acquire_button.setEnabled(True)
            self._cookie_acquire_button.setText("重试获取 Cookie")
            self._cookie_finish_button.setVisible(False)
            self._cookie_acquire_status.setText(str(message))
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

        header = QLabel(f"{provider_instance.name} 凭据")
        header.setStyleSheet("font-size: 14px; font-weight: 600;")
        self.credentials_layout.addWidget(header)

        for field, meta in (provider_instance.credential_fields or {}).items():
            label = str(meta.get("label") or field)
            hint = str(meta.get("hint") or "")
            secret = bool(meta.get("secret"))
            multiline = bool(meta.get("multiline"))
            row_widget, edit = self._build_credential_row(label, hint, secret, multiline)
            key = f"{upper_id}_{field.upper()}"
            initial = draft.get(field, str(cached.get(key, "")))
            if isinstance(edit, QPlainTextEdit):
                edit.setPlainText(initial)
            else:
                edit.setText(initial)
            self._provider_widgets[field] = edit
            self.credentials_layout.addWidget(row_widget)
            if field == "COOKIE" and getattr(provider_cls, "supports_cookie_acquisition", False):
                self._add_cookie_acquire_row(provider_instance.name)
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
        self._sync_window_size()

    def _add_cookie_acquire_row(self, provider_name: str) -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)
        self._cookie_acquire_button = QPushButton("一键获取 Cookie")
        self._cookie_acquire_button.setToolTip(f"打开浏览器登录 {provider_name} 后读取 Cookie")
        self._cookie_acquire_button.clicked.connect(self._begin_cookie_acquire)
        self._cookie_finish_button = QPushButton("完成采集")
        self._cookie_finish_button.setVisible(False)
        self._cookie_finish_button.clicked.connect(self._finish_cookie_acquire)
        self._cookie_acquire_status = QLabel("通过独立浏览器登录后，可将 Cookie 自动填回此处。")
        self._cookie_acquire_status.setWordWrap(True)
        self._cookie_acquire_status.setProperty("tone", "muted")
        self._cookie_acquire_status.setStyleSheet("font-size: 12px;")
        layout.addWidget(self._cookie_acquire_button)
        layout.addWidget(self._cookie_finish_button)
        layout.addWidget(self._cookie_acquire_status, 1)
        self.credentials_layout.addWidget(row)

    @staticmethod
    def _build_credential_row(
        label: str, hint: str, secret: bool, multiline: bool
    ) -> tuple[QWidget, Union[QLineEdit, QPlainTextEdit]]:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        label_widget = QLabel(label)
        label_widget.setStyleSheet("font-size: 13px;")
        layout.addWidget(label_widget)
        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(8)
        if multiline:
            editor: Union[QLineEdit, QPlainTextEdit] = QPlainTextEdit()
            editor.setPlaceholderText("未填写" if not hint else hint)
            editor.setFixedHeight(96)
        else:
            editor = QLineEdit()
            editor.setPlaceholderText("未填写" if not hint else hint)
        if secret and isinstance(editor, QLineEdit):
            editor.setEchoMode(QLineEdit.EchoMode.Password)
        input_row.addWidget(editor, 1)
        layout.addLayout(input_row)
        return wrapper, editor

    def _load_values(self) -> None:
        values = config_manager.load_config()
        self.refresh_seconds.setValue(max(5, int(values.get("REFRESH_INTERVAL", 60_000)) // 1000))
        self.set_theme_mode(str(values.get("UI_THEME", "dark")))
        self.edge_hide_check.setChecked(bool(values.get("EDGE_HIDE_ENABLED", True)))
        self.panel_auto_collapse_check.setChecked(
            bool(values.get("PANEL_AUTO_COLLAPSE_ON_DEACTIVATE", True))
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
            "ACTIVE_PROVIDER": str(self.provider_combo.currentData() or ""),
            "UI_THEME": str(self.theme_combo.currentData() or "dark"),
            "EDGE_HIDE_ENABLED": self.edge_hide_check.isChecked(),
            "PANEL_AUTO_COLLAPSE_ON_DEACTIVATE": self.panel_auto_collapse_check.isChecked(),
            "UPDATE_AUTO_CHECK_ENABLED": self.auto_check_updates.isChecked(),
            "UPDATE_CHANNEL": str(self.update_channel_combo.currentData() or "stable"),
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

    def _save(self) -> None:
        values = self._values()
        for key, value in values.items():
            if key.endswith("_BASE") and value and not config_manager.is_official_base_url(value):
                result = QMessageBox.question(
                    self,
                    "非官方 API 地址",
                    f"{key} 会接收当前平台凭据，确认信任并继续吗？",
                )
                if result != QMessageBox.StandardButton.Yes:
                    return
        try:
            config_manager.save_config(values)
        except Exception as exc:
            self._set_feedback(self.save_feedback, f"保存失败，配置已回滚：{exc}", "danger")
            return
        active_id = str(values.get("ACTIVE_PROVIDER", ""))
        self._set_feedback(
            self.save_feedback,
            f"已使用 {active_id or '默认'} 作为数据来源，配置已保存。",
            "success",
        )
        if self.update_controller is not None:
            self.update_controller.reload_cached_release()
        if self.on_saved:
            self.on_saved()

    def _test_connection(self) -> None:
        try:
            candidate = self._values()
            self._test_config_backup = config_manager.all_config()
            config_manager._config.update(candidate)
        except Exception as exc:
            self._set_feedback(
                self.connection_feedback, f"请先修正配置：{exc}", "danger"
            )
            return
        self.test_button.setEnabled(False)
        self.test_button.setText("测试中…")
        self._set_feedback(
            self.connection_feedback, "正在使用当前输入的凭据测试连接…", "muted"
        )
        self._worker = ConnectionWorker(self)
        self._worker.finished_with_data.connect(self._connection_result)
        self._worker.start()

    def _connection_result(self, data: TokenData) -> None:
        if self._test_config_backup is not None:
            # Theme changes are saved immediately while the connection worker is
            # running, so restoring credential drafts must preserve that newer mode.
            active_theme = config_manager._config.get(
                "UI_THEME", self._test_config_backup.get("UI_THEME", "dark")
            )
            restored = self._test_config_backup.copy()
            restored["UI_THEME"] = active_theme
            config_manager._config = restored
            self._test_config_backup = None
        self.test_button.setEnabled(True)
        self.test_button.setText("测试连接")
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
