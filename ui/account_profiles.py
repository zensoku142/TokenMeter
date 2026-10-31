"""Independent account monitoring built on the existing providers and overview."""

from __future__ import annotations

import time
from dataclasses import replace

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from api.providers import PROVIDERS
from api.providers.base import QuotaMetric
from config import account_profiles as store
from config import runtime as config_manager
from config.defaults import DEFAULT_CONFIG
from data.store import TokenData
from ui.formatting import format_money
from ui.i18n import add_item, bind_text, tr
from ui.provider_overview import ProviderOverview
from ui.qt_settings import _SettingsComboBox
from ui.quota_details import QuotaDetailsDialog


class ProfileEditor(QDialog):
    def __init__(self, parent=None, profile=None):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self.profile = profile
        self.setWindowModality(Qt.WindowModality.WindowModal)
        bind_text(self, "编辑账户档案" if profile else "添加账户档案", method="setWindowTitle")
        self.resize(580, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 18)
        form = QFormLayout()
        self.name = QLineEdit(profile["name"] if profile else "")
        form.addRow(bind_text(QLabel(), "账户名称"), self.name)
        self.provider = _SettingsComboBox()
        for provider_id, provider in PROVIDERS.items():
            add_item(self.provider, provider.name, provider_id)
        if profile:
            self.provider.setCurrentIndex(self.provider.findData(profile["provider"]))
            self.provider.setEnabled(False)
        form.addRow(bind_text(QLabel(), "数据来源"), self.provider)
        layout.addLayout(form)
        notice = bind_text(QLabel(), "档案需明确指定凭据或登录路径；凭据保存在 Windows 凭据管理器，不修改 CLI 登录。")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll, 1)
        self.fields = {}
        self.provider.currentIndexChanged.connect(self._rebuild_fields)
        self._rebuild_fields()
        footer = QHBoxLayout()
        if profile:
            delete = bind_text(QPushButton(), "删除账户档案")
            delete.clicked.connect(self._delete)
            footer.addWidget(delete)
        footer.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)

    def _rebuild_fields(self, *_args):
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        content = QWidget()
        form = QFormLayout(content)
        self.fields = {}
        provider_id = str(self.provider.currentData())
        values = store.profile_fields(self.profile) if self.profile else {}
        for field, metadata in PROVIDERS[provider_id].credential_fields.items():
            edit = QLineEdit(values.get(field, str(DEFAULT_CONFIG.get(f"{provider_id.upper()}_{field}", ""))))
            if metadata.get("secret"):
                edit.setEchoMode(QLineEdit.EchoMode.Password)
            bind_text(edit, metadata.get("hint", ""), method="setToolTip")
            bind_text(edit, metadata["label"], method="setAccessibleName")
            self.fields[field] = edit
            if metadata.get("directory") or field.endswith("_FILE"):
                row = QHBoxLayout()
                row.addWidget(edit, 1)
                browse = bind_text(QPushButton(), "选择目录" if metadata.get("directory") else "选择文件")
                browse.clicked.connect(lambda _checked=False, target=edit, directory=metadata.get("directory", False): self._browse(target, directory))
                row.addWidget(browse)
                form.addRow(bind_text(QLabel(), metadata["label"]), row)
            else:
                form.addRow(bind_text(QLabel(), metadata["label"]), edit)
        self.scroll.setWidget(content)

    def _browse(self, edit, directory):
        value = QFileDialog.getExistingDirectory(self, tr("选择目录"), edit.text()) if directory else QFileDialog.getOpenFileName(self, tr("选择文件"), edit.text())[0]
        if value:
            edit.setText(value)

    def _save(self):
        try:
            store.save_profile(str(self.provider.currentData()), self.name.text(),
                               {field: edit.text() for field, edit in self.fields.items()},
                               self.profile["id"] if self.profile else None)
        except ValueError:
            QMessageBox.warning(self, tr("账户档案"), tr("请填写账户名称，并明确提供凭据或登录路径。"))
            return
        except OSError:
            QMessageBox.warning(self, tr("账户档案"), tr("账户档案保存失败，请检查数据目录和凭据管理器。"))
            return
        self.accept()

    def _delete(self):
        if QMessageBox.question(self, tr("删除账户档案"), tr("删除此档案？本机 CLI 登录和历史记录会保留。"),
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            store.delete_profile(self.profile["id"])
        except OSError:
            QMessageBox.warning(self, tr("账户档案"), tr("账户档案保存失败，请检查数据目录和凭据管理器。"))
            return
        self.accept()


class _ProfileSignals(QObject):
    finished = Signal(str, str, object)


class _ProfileTask(QRunnable):
    def __init__(self, profile, config):
        super().__init__()
        self.profile_id, self.revision = profile["id"], profile["revision"]
        self.profile = profile
        self.config = config
        self.signals = _ProfileSignals()

    def run(self):
        from ui.qt_widget import _fetch_tokens_safely

        # 线程池中的任务可能尚未开始；关闭后仍回传完成信号释放忙碌状态，但不再请求账号。
        result = (_fetch_tokens_safely(self.config, lightweight=True)
                  if store.is_profile_enabled(self.profile) else TokenData())
        self.signals.finished.emit(self.profile_id, self.revision, result)


class AccountProfilesPage(ProviderOverview):
    quota_observed = Signal(str, str, str, object)
    profiles_changed = Signal()

    def __init__(self, parent=None, *, embedded_settings=False, monitor=None):
        super().__init__(parent)
        self._embedded_settings = embedded_settings
        self._monitor = monitor
        self.profiles = []
        self.results = {}
        self._queue = []
        self._active = None
        self._due = {}
        self._failures = {}
        self._details = None
        self._detail_profile = None
        bind_text(self.hint, "同一平台可添加多个账户档案；各账户独立采集和缓存。")
        bind_text(self.findChild(QLabel, "sectionTitle"), "账户档案")
        bind_text(self.empty, "尚无账户档案")
        bind_text(self.manage_button, "添加账户")
        self.refresh_requested.connect(self.refresh_profiles)
        self.auto_refresh_requested.connect(self.refresh_profiles)
        self.auto_refresh_stopped.connect(self._queue.clear)
        self.connection_requested.connect(self.edit_profile)
        self.provider_selected.connect(self.show_details)
        self.reload_profiles()

    def reload_profiles(self):
        try:
            # CC 已配置账号自动加入采集列表；重复刷新不新增重复档案或改变默认连接。
            store.import_cc_switch_profiles()
            profiles = store.load_profiles(enabled_only=True)
        except (OSError, ValueError):
            bind_text(self.hint, "账户档案无法读取，请检查数据文件。")
            self.hint.show()
            self._queue.clear()
            return False
        bind_text(self.hint, "同一平台可添加多个账户档案；各账户独立采集和缓存。")
        self.hint.setVisible(not self._embedded_settings)
        previous = {profile["id"]: profile["revision"] for profile in self.profiles}
        current = {profile["id"]: profile["revision"] for profile in profiles}
        for profile_id in set(previous) | set(current):
            if previous.get(profile_id) != current.get(profile_id):
                self.results.pop(profile_id, None)
                self._due.pop(profile_id, None)
                self._failures.pop(profile_id, None)
                if self._detail_profile == profile_id and self._details is not None:
                    self._details.reject()
        self.profiles = profiles
        self._queue = [profile_id for profile_id in self._queue if profile_id in current]
        enabled = bool(config_manager.get("ACCOUNT_PROFILES_ENABLED", True))
        self.setEnabled(enabled)
        bind_text(self.empty, "尚无账户档案" if enabled else "多账户已关闭，已有配置已保留。")
        if self._monitor is not None:
            for profile in profiles:
                if profile["provider"] == "codex" and profile["fields"].get("CC_SWITCH_ACCOUNT_ID"):
                    cached = self._monitor._vpet_profile_results.get(profile["id"])
                    if (cached is not None and cached[0] == profile["revision"]
                            and cached[1].account_key == TokenData.account_key_for_config(store.profile_config(profile))):
                        self.results[profile["id"]] = cached[1]
                    else:
                        self.results.pop(profile["id"], None)
        self._render()
        if previous != current:
            self.profiles_changed.emit()
        return True

    def _render(self):
        self.set_data({profile["id"]: self.results.get(profile["id"]) for profile in self.profiles},
                      providers={profile["id"]: profile["provider"] for profile in self.profiles},
                      labels={profile["id"]: profile["name"] for profile in self.profiles})
        in_flight = self._monitor._vpet_profile_in_flight if self._monitor is not None else {}
        for profile in self.profiles:
            if profile["provider"] == "codex" and profile["fields"].get("CC_SWITCH_ACCOUNT_ID"):
                button = self.cards[profile["id"]].findChild(QPushButton, "overviewConnection")
                bind_text(button, "刷新")
                button.setEnabled((profile["id"], profile["revision"]) != self._active
                                  and profile["id"] not in self._queue and profile["id"] not in in_flight)
        self.set_refreshing(bool(self._active or self._queue or any(
            profile["id"] in in_flight for profile in self.profiles)))

    def refresh_profiles(self):
        if self._monitor is not None:
            self._monitor._update_vpet_source_options()
        if not self.reload_profiles():
            return
        if self._monitor is not None:
            self._monitor._refresh_vpet_bound_sources()
        now = time.monotonic()
        self._queue = [profile["id"] for profile in self.profiles
                       if (profile["id"], profile["revision"]) != self._active and now >= self._due.get(profile["id"], 0)
                       and not (self._monitor is not None and profile["provider"] == "codex"
                                and profile["fields"].get("CC_SWITCH_ACCOUNT_ID"))]
        if self._active is None:
            self._advance()

    def _advance(self):
        if self._active is not None:
            return
        while self._queue:
            profile_id = self._queue.pop(0)
            profile = next((item for item in self.profiles if item["id"] == profile_id), None)
            if profile is None or not store.is_profile_enabled(profile):
                continue
            self._active = (profile_id, profile["revision"])
            self._due[profile_id] = time.monotonic() + 60
            task = _ProfileTask(profile, store.profile_config(profile))
            task.signals.finished.connect(self._finished)
            QThreadPool.globalInstance().start(task)
            break
        self._render()

    def _finished(self, profile_id, revision, result):
        if self._active != (profile_id, revision):
            return
        self._active = None
        if not self.reload_profiles():
            return
        profile = next((item for item in self.profiles if item["id"] == profile_id and item["revision"] == revision), None)
        if profile is not None:
            current_key = TokenData.account_key_for_config(store.profile_config(profile))
            # 编辑/删除档案或 CLI 换号后的迟到结果不能重新出现；默认连接的缓存也不参与回退。
            if result.account_key == current_key:
                previous = self.results.get(profile_id)
                if previous is not None and previous.account_key == current_key and result.last_success_at is None:
                    result = replace(previous, status="error", is_stale=True, errors=result.errors, refresh_error_codes=result.refresh_error_codes)
                self.results[profile_id] = result
                self.quota_observed.emit(profile_id, profile["name"], profile["provider"], result)
                if result.errors or result.refresh_error_codes:
                    failures = min(6, self._failures.get(profile_id, 0) + 1)
                    self._failures[profile_id] = failures
                    self._due[profile_id] = time.monotonic() + min(900, 60 * 2 ** (failures - 1))
                else:
                    self._failures.pop(profile_id, None)
            else:
                self.results.pop(profile_id, None)
        self._render()
        QTimer.singleShot(0, self._advance)

    def edit_profile(self, profile_id=""):
        if not config_manager.get("ACCOUNT_PROFILES_ENABLED", True):
            return
        profile = next((item for item in self.profiles if item["id"] == profile_id), None)
        if profile and profile["provider"] == "codex" and profile["fields"].get("CC_SWITCH_ACCOUNT_ID"):
            # CC 是账号配置的唯一来源；卡片操作只刷新该账号，不再打开本地凭据编辑器。
            if self._monitor is not None:
                if profile_id not in self._monitor._vpet_profile_in_flight:
                    self._monitor._vpet_profile_due.pop(profile_id, None)
                    self._monitor._refresh_vpet_bound_sources()
                self._render()
                return
            if (profile_id, profile["revision"]) != self._active and profile_id not in self._queue:
                self._queue.append(profile_id)
                self._advance()
                self._render()
            return
        editor = ProfileEditor(self, profile)
        if editor.exec() == QDialog.DialogCode.Accepted:
            self.refresh_profiles()

    def show_details(self, profile_id):
        data = self.results.get(profile_id)
        profile = next((item for item in self.profiles if item["id"] == profile_id), None)
        if data is None or profile is None:
            return
        display = data
        if not PROVIDERS[profile["provider"]].supports_subscription_quota:
            display = replace(data, quota_metrics=[
                QuotaMetric(PROVIDERS[profile["provider"]].balance_label, format_money(data.balance_cny, data.currency)),
                QuotaMetric("今日使用金额", format_money(data.today_cost_cny, data.currency)),
                QuotaMetric("本月累计", format_money(data.monthly_cost_cny, data.currency)),
            ])
        if self._details is None:
            self._details = QuotaDetailsDialog(self, title_source="账户详情")
        if display.per_provider:
            display = replace(display, per_provider=[replace(display.per_provider[0], provider_name=f"{PROVIDERS[profile['provider']].name} · {profile['name']}")])
        self._detail_profile = profile_id
        self._details.set_data(display)
        self._details.show()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self._details is not None:
            self._details.reject()
