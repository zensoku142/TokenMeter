"""Local-only analysis with an explicit scan and no provider requests."""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from config import runtime as config_manager
from data.local_usage import LocalUsageScanner, export_usage, filter_usage
from ui.i18n import add_item, bind_text, language_controller, tr


class _ScanSignals(QObject):
    finished = Signal(object, int, bool)


class _ScanTask(QRunnable):
    def __init__(self, scanner, roots):
        super().__init__()
        self.scanner, self.roots = scanner, roots
        self.signals = _ScanSignals()

    def run(self):
        try:
            rows = self.scanner.scan(self.roots)
            self.signals.finished.emit(rows, self.scanner.issues, False)
        except Exception:
            # 后台扫描失败必须恢复按钮；错误提示不包含路径、原始日志或对话。
            self.signals.finished.emit([], 0, True)


class LocalAnalyticsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        bind_text(self, "本机统计", method="setWindowTitle")
        self.resize(760, 560)
        self.setMinimumSize(480, 360)
        self.scanner = LocalUsageScanner()
        self.rows = []
        self.issues = 0
        self.busy = False
        layout = QVBoxLayout(self)
        notice = bind_text(QLabel(), "仅统计所选目录的本机日志，账户归属未知；不代表订阅额度或实际账单")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        form = QFormLayout()
        self.paths = {}
        self.browse_buttons = []
        defaults = {
            "codex": str(config_manager.get("CODEX_HOME", "") or os.environ.get("CODEX_HOME") or Path.home() / ".codex"),
            "claude": str(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude"),
        }
        for provider, value in defaults.items():
            row = QHBoxLayout()
            edit = QLineEdit(value)
            edit.setAccessibleName(provider)
            self.paths[provider] = edit
            row.addWidget(edit, 1)
            browse = bind_text(QPushButton(), "选择目录")
            browse.clicked.connect(lambda _checked=False, pid=provider: self._choose_directory(pid))
            row.addWidget(browse)
            self.browse_buttons.append(browse)
            form.addRow(provider.title(), row)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.scan_button = bind_text(QPushButton(), "扫描本机日志")
        self.scan_button.clicked.connect(self.scan)
        actions.addWidget(self.scan_button)
        self.period = QComboBox()
        for title, days in (("今日", 1), ("最近 7 天", 7), ("最近 30 天", 30), ("全部", 0)):
            add_item(self.period, title, days)
        self.period.setCurrentIndex(1)
        self.period.currentIndexChanged.connect(self.render)
        actions.addWidget(self.period)
        self.group = QComboBox()
        for title, key in (("模型", "model"), ("项目", "project"), ("日期", "day"), ("会话", "session")):
            add_item(self.group, title, key)
        self.group.currentIndexChanged.connect(self.render)
        actions.addWidget(self.group)
        self.project = QLineEdit()
        bind_text(self.project, "筛选项目", method="setPlaceholderText")
        bind_text(self.project, "筛选项目", method="setAccessibleName")
        self.project.textChanged.connect(self.render)
        actions.addWidget(self.project, 1)
        layout.addLayout(actions)
        self.status = bind_text(QLabel(), "点击扫描后读取日志；不会连接平台或启动 WSL")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 7)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._refresh_headers()
        controller = language_controller()
        if controller is not None:
            controller.changed.connect(self._refresh_headers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        footer = QHBoxLayout()
        self.export_button = bind_text(QPushButton(), "导出统计")
        self.export_button.clicked.connect(self.export)
        self.export_button.setEnabled(False)
        footer.addWidget(self.export_button)
        footer.addStretch()
        close = bind_text(QPushButton(), "关闭")
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        layout.addLayout(footer)

    def _refresh_headers(self, *_args):
        # QTableWidgetItem 不支持现有弱引用文字绑定；表头随语言信号原位刷新。
        self.table.setHorizontalHeaderLabels([tr(name) for name in (
            "平台", "分组", "Token", "输入", "输出", "缓存读取", "缓存写入",
        )])

    def _choose_directory(self, provider):
        path = QFileDialog.getExistingDirectory(self, tr("选择目录"), self.paths[provider].text())
        if path:
            self.paths[provider].setText(path)

    def scan(self):
        if self.busy:
            return
        roots = {provider: Path(edit.text()).expanduser() for provider, edit in self.paths.items() if edit.text().strip()}
        self.busy = True
        for control in (*self.paths.values(), *self.browse_buttons, self.scan_button, self.export_button):
            control.setEnabled(False)
        bind_text(self.status, "正在扫描本机日志")
        self._task = _ScanTask(self.scanner, roots)
        # 扫描在后台线程执行；Qt 自动断开已销毁对话框的信号，关闭窗口不会留下 UI 回调。
        self._task.signals.finished.connect(self._finished)
        QThreadPool.globalInstance().start(self._task)

    def _finished(self, rows, issues, failed):
        self.busy = False
        self.rows, self.issues = rows, issues
        for control in (*self.paths.values(), *self.browse_buttons, self.scan_button):
            control.setEnabled(True)
        self.render()
        if failed:
            bind_text(self.status, "扫描失败，请检查目录后重试")

    def filtered_rows(self):
        return filter_usage(self.rows, int(self.period.currentData() or 0), self.project.text().strip())

    def render(self, *_args):
        rows = self.filtered_rows()
        groups = defaultdict(lambda: [0, 0, 0, 0, 0])
        dimension = self.group.currentData() or "model"
        for row in rows:
            values = groups[row.provider, getattr(row, dimension)]
            for index, name in enumerate(("total", "input", "output", "cache_read", "cache_write")):
                values[index] += getattr(row, name)
        self.table.setRowCount(len(groups))
        for index, ((provider, key), counts) in enumerate(sorted(groups.items())):
            for column, value in enumerate((provider, key or "--", *counts)):
                self.table.setItem(index, column, QTableWidgetItem(str(value)))
        self.export_button.setEnabled(bool(rows) and not self.busy)
        if not self.busy:
            count, tokens = len(rows), sum(row.total for row in rows)
            bind_text(self.status, lambda: tr("{count} 条用量记录 · {tokens} Token · {issues} 项读取异常", count=count, tokens=tokens, issues=self.issues))

    def export(self):
        path, selected = QFileDialog.getSaveFileName(self, tr("导出统计"), "local-usage.json", "JSON (*.json);;CSV (*.csv)")
        if not path:
            return
        destination = Path(path)
        if destination.suffix.lower() not in {".json", ".csv"}:
            destination = destination.with_suffix(".csv" if selected.startswith("CSV") else ".json")
        try:
            export_usage(destination, self.filtered_rows(), issues=self.issues)
        except OSError:
            QMessageBox.warning(self, tr("导出统计"), tr("导出失败，请检查目标目录"))
