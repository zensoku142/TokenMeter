"""Local-only analysis with an explicit scan and no provider requests."""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
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
    QWidget,
)

from config import runtime as config_manager
from data.local_usage import LocalUsageScanner, export_usage, filter_usage
from ui.activity import compact_tokens
from ui.i18n import add_item, bind_text, language_controller, tr
from ui.qt_settings import _SettingsComboBox
from ui.qt_theme import current_theme, theme_controller


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
    def __init__(self, parent=None, *, embedded=False):
        super().__init__(parent)
        self.setObjectName("localAnalytics")
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        bind_text(self, "本机统计", method="setWindowTitle")
        self.resize(760, 560)
        self.setMinimumSize(480, 360)
        self.scanner = LocalUsageScanner()
        self.rows = []
        self.issues = 0
        self.busy = False
        self.scanned = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 14, 22, 14)
        layout.setSpacing(10)
        heading = QHBoxLayout()
        title = bind_text(QLabel(), "本机统计")
        title.setObjectName("sectionTitle")
        heading.addWidget(title, 1)
        self.directory_button = bind_text(QPushButton(), "日志目录")
        self.directory_button.setCheckable(True)
        heading.addWidget(self.directory_button)
        self.export_button = bind_text(QPushButton(), "导出统计")
        self.export_button.clicked.connect(self.export)
        self.export_button.setEnabled(False)
        heading.addWidget(self.export_button)
        layout.addLayout(heading)
        notice = bind_text(QLabel(), "仅统计所选目录的本机日志，账户归属未知；不代表订阅额度或实际账单")
        notice.setWordWrap(True)
        notice.setObjectName("analyticsNote")
        layout.addWidget(notice)
        self.directory_panel = QWidget()
        form = QFormLayout(self.directory_panel)
        form.setContentsMargins(0, 0, 0, 0)
        self.directory_panel.hide()
        self.directory_button.toggled.connect(self.directory_panel.setVisible)
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
        layout.addWidget(self.directory_panel)
        actions = QHBoxLayout()
        self.scan_button = bind_text(QPushButton(), "扫描本机日志")
        self.scan_button.setObjectName("primaryButton")
        self.scan_button.clicked.connect(self.scan)
        actions.addWidget(self.scan_button)
        self.period = _SettingsComboBox()
        for title, days in (("今日", 1), ("最近 7 天", 7), ("最近 30 天", 30), ("全部", 0)):
            add_item(self.period, title, days)
        self.period.setCurrentIndex(1)
        self.period.currentIndexChanged.connect(self.render)
        actions.addWidget(self.period)
        self.group = _SettingsComboBox()
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
        self.total_label = QLabel("--")
        self.total_label.setObjectName("analyticsTotal")
        layout.addWidget(self.total_label)
        self.status = bind_text(QLabel(), "点击扫描后读取日志；不会连接平台或启动 WSL")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 7)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._refresh_headers()
        controller = language_controller()
        if controller is not None:
            controller.changed.connect(self._refresh_headers)
        self.table.horizontalHeader().setMinimumSectionSize(48)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        footer = QHBoxLayout()
        footer.addWidget(bind_text(QLabel(), "输入包含缓存；悬停查看精确 Token 数"))
        footer.addStretch()
        close = bind_text(QPushButton(), "关闭")
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        close.setVisible(not embedded)
        layout.addLayout(footer)
        theme_controller().changed.connect(self.refresh_theme)
        self.refresh_theme()

    def _refresh_headers(self, *_args):
        # QTableWidgetItem 不支持现有弱引用文字绑定；表头随语言信号原位刷新。
        self.table.setHorizontalHeaderLabels([tr(name) for name in (
            "平台", "分组", "Token", "输入", "输出", "缓存读取", "缓存写入",
        )])
        if hasattr(self, "export_button") and hasattr(self, "status"):
            self.render()

    def refresh_theme(self, *_args):
        tokens = current_theme()
        border = QColor(tokens.border)
        divider = f"rgba({border.red()}, {border.green()}, {border.blue()}, 82)"
        self.setStyleSheet(f"""
            QDialog#localAnalytics {{ background: {tokens.window}; color: {tokens.text}; }}
            QDialog#localAnalytics QLabel#analyticsNote {{ color: {tokens.subtext}; }}
            QDialog#localAnalytics QLabel#analyticsTotal {{ color: {tokens.value}; font-size: 26px; font-weight: 600; }}
            QDialog#localAnalytics QPushButton {{ min-height: 28px; padding: 0 12px; border: 1px solid {divider}; border-radius: 9px; }}
            QDialog#localAnalytics QLineEdit {{ border: 1px solid {divider}; border-radius: 9px; }}
            QDialog#localAnalytics QComboBox {{ min-height: 28px; padding: 0 28px 0 10px; border: 1px solid {divider}; border-radius: 9px; background: {tokens.surface}; }}
            QDialog#localAnalytics QComboBox::drop-down {{ border: 0; width: 24px; background: transparent; }}
            QDialog#localAnalytics QComboBox::down-arrow {{ width: 0; height: 0; }}
            QDialog#localAnalytics QTableWidget {{ background: {tokens.surface}; alternate-background-color: {tokens.elevated}; color: {tokens.text}; border: 0; selection-background-color: {tokens.accent_soft}; selection-color: {tokens.text}; }}
            QDialog#localAnalytics QTableWidget::item {{ padding: 4px 8px; border: 0; }}
            QDialog#localAnalytics QHeaderView::section {{ background: {tokens.surface}; color: {tokens.subtext}; border: 0; border-bottom: 1px solid {tokens.border}; padding: 8px 6px; }}
        """)

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
        self.scanned = True
        self.rows, self.issues = rows, issues
        for control in (*self.paths.values(), *self.browse_buttons, self.scan_button):
            control.setEnabled(True)
        self.render()
        if failed:
            bind_text(self.status, "扫描失败，请检查目录后重试")

    def filtered_rows(self):
        return filter_usage(self.rows, int(self.period.currentData() or 0), self.project.text().strip())

    def render(self, *_args):
        if not self.scanned:
            return
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
                item = QTableWidgetItem(tr(compact_tokens(value)) if column >= 2 else str(value))
                if column >= 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    item.setToolTip(f"{value:,} Token")
                self.table.setItem(index, column, item)
        self.export_button.setEnabled(bool(rows) and not self.busy)
        if not self.busy:
            count, tokens = len(rows), sum(row.total for row in rows)
            bind_text(self.total_label, lambda: f"{tr(compact_tokens(tokens))} Token")
            self.total_label.setToolTip(f"{tokens:,} Token")
            bind_text(self.status, lambda: tr("{count} 条用量记录 · {tokens} Token · {issues} 项读取异常", count=count, tokens=tr(compact_tokens(tokens)), issues=self.issues))

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
