"""Qt settings dialog built from the provider registry.

Provider selection is now a simple dropdown, and only the credentials of the
selected provider are shown — keeping the dialog small and focused.
"""

from __future__ import annotations

from typing import Any, Callable, Union

from PySide6.QtCore import QThread, Signal
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
    QVBoxLayout,
    QWidget,
)

import config_manager
from api.providers import PROVIDERS, list_providers
from api.providers.base import FetchError
from data.store import TokenData
from ui.qt_theme import C_GREEN, C_PANEL, C_RED, C_SUBTEXT
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


class SettingsWindow(QDialog):
    def __init__(
        self,
        parent=None,
        on_saved: Callable[[], None] | None = None,
        update_controller: AppUpdateController | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("TokenSpider 设置")
        self.setModal(False)
        self.setMinimumWidth(520)
        self.setMaximumWidth(680)
        self.on_saved = on_saved
        self.update_controller = update_controller
        self._worker: ConnectionWorker | None = None
        self._rendered_provider_id = ""
        self._provider_drafts: dict[str, dict[str, str]] = {}
        self._test_config_backup: dict[str, Any] | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        # 1.3.1 改成滚动容器后，Qt 会让 viewport/content 回退到系统默认底色；
        # 这里显式继承深色面板背景，避免设置页在 Windows 上出现整块白底。
        self.scroll_area.viewport().setStyleSheet(f"background: {C_PANEL};")
        root.addWidget(self.scroll_area)
        self.content = QWidget()
        self.content.setStyleSheet(f"background: {C_PANEL};")
        self.scroll_area.setWidget(self.content)
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(24, 20, 24, 20)
        content_layout.setSpacing(14)
        title = QLabel("运行配置")
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
        self.credentials_card.setObjectName("credentialsCard")
        self.credentials_card.setStyleSheet(
            "QFrame#credentialsCard { border: 1px solid #e5e7eb; border-radius: 10px; }"
        )
        self.credentials_layout = QVBoxLayout(self.credentials_card)
        self.credentials_layout.setContentsMargins(_CARD_PADDING, 14, _CARD_PADDING, 14)
        self.credentials_layout.setSpacing(10)
        self._provider_widgets: dict[str, QLineEdit] = {}

        # Global: refresh interval + edge-hide toggle.
        global_form = QFormLayout()
        global_form.setHorizontalSpacing(16)
        global_form.setVerticalSpacing(8)
        self.refresh_seconds = QSpinBox()
        self.refresh_seconds.setRange(5, 3600)
        self.refresh_seconds.setSuffix(" 秒")
        global_form.addRow("刷新间隔", self.refresh_seconds)
        self.edge_hide_check = QCheckBox("贴边自动隐藏")
        self.edge_hide_check.setToolTip("拖拽悬浮球到屏幕边缘后自动隐藏，鼠标移入时显示")
        global_form.addRow("贴边隐藏", self.edge_hide_check)

        self.update_card = QFrame()
        self.update_card.setObjectName("updateCard")
        self.update_card.setStyleSheet(
            "QFrame#updateCard { border: 1px solid #e5e7eb; border-radius: 10px; }"
        )
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
        self.update_status_label.setStyleSheet(f"color: {C_SUBTEXT}; font-size: 12px;")
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

        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        self.feedback.setStyleSheet(f"color: {C_SUBTEXT}; font-size: 12px;")

        actions = QHBoxLayout()
        self.test_button = QPushButton("测试连接")
        self.test_button.clicked.connect(self._test_connection)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存并生效")
        save.setObjectName("primaryButton")
        save.clicked.connect(self._save)
        actions.addWidget(self.test_button)
        actions.addStretch(1)
        actions.addWidget(cancel)
        actions.addWidget(save)

        content_layout.addWidget(title)
        content_layout.addLayout(picker_row)
        content_layout.addWidget(self.credentials_card, 1)
        content_layout.addLayout(global_form)
        content_layout.addWidget(self.update_card)
        content_layout.addWidget(self.feedback)
        content_layout.addLayout(actions)
        self._load_values()
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

    def _sync_window_size(self) -> None:
        self.content.adjustSize()
        content_size = self.content.sizeHint()
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            # Win10 在高缩放或任务栏较高时，可用工作区会明显变小；这里限制
            # 对话框高度并保留滚动，避免底部按钮被裁到屏幕外却无法操作。
            max_height = max(360, screen.availableGeometry().height() - 80)
        else:
            max_height = content_size.height()
        target_width = min(self.maximumWidth(), max(self.minimumWidth(), content_size.width()))
        target_height = min(content_size.height(), max_height)
        self.resize(target_width, target_height)

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
        self.edge_hide_check.setChecked(bool(values.get("EDGE_HIDE_ENABLED", True)))
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
            "EDGE_HIDE_ENABLED": self.edge_hide_check.isChecked(),
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
            self.feedback.setStyleSheet(f"color: {C_RED};")
            self.feedback.setText(f"保存失败，配置已回滚：{exc}")
            return
        self.feedback.setStyleSheet(f"color: {C_GREEN};")
        active_id = str(values.get("ACTIVE_PROVIDER", ""))
        self.feedback.setText(f"已使用 {active_id or '默认'} 作为数据来源，配置已保存。")
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
            self.feedback.setStyleSheet(f"color: {C_RED};")
            self.feedback.setText(f"请先修正配置：{exc}")
            return
        self.test_button.setEnabled(False)
        self.test_button.setText("测试中…")
        self.feedback.setStyleSheet(f"color: {C_SUBTEXT};")
        self.feedback.setText("正在使用当前输入的凭据测试连接…")
        self._worker = ConnectionWorker(self)
        self._worker.finished_with_data.connect(self._connection_result)
        self._worker.start()

    def _connection_result(self, data: TokenData) -> None:
        if self._test_config_backup is not None:
            config_manager._config = self._test_config_backup
            self._test_config_backup = None
        self.test_button.setEnabled(True)
        self.test_button.setText("测试连接")
        if data.status in {"ok", "partial"}:
            if data.status == "ok":
                self.feedback.setStyleSheet(f"color: {C_GREEN};")
                self.feedback.setText("连接成功。")
            else:
                # Collect all error messages from providers that had issues.
                error_messages: list[str] = []
                for per in data.per_provider:
                    for err in per.errors:
                        error_messages.append(f"[{per.provider_name}] {err.message}")
                detail = "\n".join(error_messages) if error_messages else "未知错误"
                self.feedback.setStyleSheet(f"color: {C_RED};")
                self.feedback.setText(f"连接失败：\n{detail}")
        else:
            message = data.errors[0].message if data.errors else "连接失败"
            self.feedback.setStyleSheet(f"color: {C_RED};")
            self.feedback.setText(message)
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
