"""Qt settings dialog built from the provider registry.

Provider selection is now a simple dropdown, and only the credentials of the
selected provider are shown — keeping the dialog small and focused.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QThread, Signal
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
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import config_manager
from api.providers import PROVIDERS, list_providers
from api.providers.base import FetchError
from data.store import TokenData
from ui.qt_theme import C_GREEN, C_RED, C_SUBTEXT

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
    def __init__(self, parent=None, on_saved: Callable[[], None] | None = None):
        super().__init__(parent)
        self.setWindowTitle("TokenSpider 设置")
        self.setModal(False)
        self.setMinimumWidth(520)
        self.setMaximumWidth(680)
        self.on_saved = on_saved
        self._worker: ConnectionWorker | None = None
        self._rendered_provider_id = ""
        self._provider_drafts: dict[str, dict[str, str]] = {}
        self._test_config_backup: dict[str, Any] | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
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

        root.addWidget(title)
        root.addLayout(picker_row)
        root.addWidget(self.credentials_card, 1)
        root.addLayout(global_form)
        root.addWidget(self.feedback)
        root.addLayout(actions)
        self._load_values()

    def _on_provider_changed(self, _index: int) -> None:
        self._remember_visible_credentials()
        provider_id = self.provider_combo.currentData()
        self._render_credentials(provider_id)

    def _remember_visible_credentials(self) -> None:
        if not self._rendered_provider_id:
            return
        self._provider_drafts[self._rendered_provider_id] = {
            field: widget.text().strip()
            for field, widget in self._provider_widgets.items()
        }

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
            row_widget, edit = self._build_credential_row(label, hint, secret)
            key = f"{upper_id}_{field.upper()}"
            edit.setText(draft.get(field, str(cached.get(key, ""))))
            self._provider_widgets[field] = edit
            self.credentials_layout.addWidget(row_widget)
        self._rendered_provider_id = provider_id

    @staticmethod
    def _build_credential_row(label: str, hint: str, secret: bool) -> tuple[QWidget, QLineEdit]:
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
        edit = QLineEdit()
        edit.setPlaceholderText("未填写" if not hint else hint)
        if secret:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
        input_row.addWidget(edit, 1)
        layout.addLayout(input_row)
        return wrapper, edit

    def _load_values(self) -> None:
        values = config_manager.load_config()
        self.refresh_seconds.setValue(max(5, int(values.get("REFRESH_INTERVAL", 60_000)) // 1000))
        self.edge_hide_check.setChecked(bool(values.get("EDGE_HIDE_ENABLED", True)))
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
