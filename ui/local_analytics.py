"""Local-only analysis with an explicit scan and no provider requests."""

from __future__ import annotations

import os
import time
from collections import defaultdict
from contextlib import suppress
from datetime import date, timedelta
from html import escape
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import QDate, QEvent, QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config import runtime as config_manager
from data.local_usage import (
    LocalUsage,
    LocalUsageScanner,
    daily_model_series,
    filter_usage,
    load_local_report,
    local_cache_scope,
    save_local_report,
)
from ui.activity import compact_tokens
from ui.i18n import add_item, bind_text, language_controller, tr
from ui.qt_settings import _SettingsComboBox
from ui.qt_theme import current_theme, fluent_icon, model_usage_color, theme_controller


class _ScanSignals(QObject):
    finished = Signal(object, int, bool)
    cached = Signal(object, int)


class _ScanTask(QRunnable):
    def __init__(self, scanner, roots, load_cache=False):
        super().__init__()
        self.scanner, self.roots = scanner, roots
        self.signals = _ScanSignals()
        self.load_cache = load_cache

    def run(self):
        try:
            scope = local_cache_scope(self.roots)
            if self.load_cache:
                try:
                    cached = load_local_report(scope)
                    if cached is not None:
                        self.signals.cached.emit(*cached)
                except Exception:
                    pass
            rows = self.scanner.scan(self.roots)
            self.signals.finished.emit(rows, self.scanner.issues, False)
            # 快照是可选加速；存储失败不影响当前内存中的有效统计。
            with suppress(Exception):
                if self.scanner.changed:
                    save_local_report(scope, rows, self.scanner.issues)
        except Exception:
            # 后台扫描失败必须恢复按钮；错误提示不包含路径、原始日志或对话。
            self.signals.finished.emit([], 0, True)


class _ExportSignals(QObject):
    finished = Signal(bool)


class _ExportTask(QRunnable):
    def __init__(self, path, rows, issues, dimension):
        super().__init__()
        self.path, self.rows, self.issues, self.dimension = path, rows, issues, dimension
        self.signals = _ExportSignals()

    def run(self):
        from data.excel_export import export_local_usage

        try:
            export_local_usage(self.path, self.rows, issues=self.issues, dimension=self.dimension)
        except Exception:
            self.signals.finished.emit(False)
        else:
            self.signals.finished.emit(True)


class LocalAnalyticsDialog(QDialog):
    def __init__(self, parent=None, *, embedded=False):
        super().__init__(parent)
        self.setObjectName("localAnalytics")
        self._embedded = embedded
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        bind_text(self, "本机统计", method="setWindowTitle")
        self.resize(760, 560)
        self.setMinimumSize(480, 360)
        self.scanner = LocalUsageScanner()
        self.rows = []
        self.issues = 0
        self.busy = False
        self._export_busy = False
        self.scanned = False
        self._last_scan = 0.0
        self._scan_scope = ""
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(60_000)
        self._auto_timer.timeout.connect(self.scan)
        self._groups = {}
        self._rank_groups = []
        self._table_dirty = False
        self._hover_key = None
        self._render_rows = None
        self._aggregate_signature = None
        self._aggregate_totals = (0, 0, 0, 0)
        self._syncing_navigator = False
        self._daily_bars = []
        self._daily_bar_width = 0.1
        self._project_timer = QTimer(self)
        self._project_timer.setSingleShot(True)
        self._project_timer.setInterval(150)
        self._project_timer.timeout.connect(self.render)
        self._hidden_models = set()
        self._chart_zoomed = False
        self._chart_scope = None
        self.legend_buttons = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 14, 22, 14)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        title = bind_text(QLabel(), "本机统计")
        title.setObjectName("sectionTitle")
        heading.addWidget(title, 1)
        self.directory_button = bind_text(QAction(self), "日志目录")
        self.directory_button.setCheckable(True)
        self.export_button = bind_text(QAction(self), "导出 Excel")
        self.export_button.triggered.connect(self.export)
        self.export_button.setEnabled(False)
        self.reset_button = bind_text(QPushButton(), "重置视图")
        self.reset_button.clicked.connect(self._reset_chart)
        heading.addWidget(self.reset_button)
        view_segment = QFrame()
        view_segment.setObjectName("analyticsViewSegment")
        view_layout = QHBoxLayout(view_segment)
        view_layout.setContentsMargins(2, 2, 2, 2)
        view_layout.setSpacing(0)
        self.chart_button = bind_text(QPushButton(), "图表")
        self.chart_button.setCheckable(True)
        self.chart_button.setChecked(True)
        self.table_button = bind_text(QPushButton(), "表格")
        self.table_button.setCheckable(True)
        for button in (self.chart_button, self.table_button):
            button.setObjectName("analyticsViewButton")
            view_layout.addWidget(button)
        heading.addWidget(view_segment)
        self.scan_button = QToolButton()
        self.scan_button.setObjectName("analyticsIconButton")
        self.scan_button.setIconSize(QSize(16, 16))
        bind_text(self.scan_button, "刷新统计", method="setToolTip")
        bind_text(self.scan_button, "刷新统计", method="setAccessibleName")
        self.scan_button.clicked.connect(self.scan)
        self.scan_button.setFixedSize(30, 30)
        heading.addWidget(self.scan_button)
        self.options_button = QToolButton()
        self.options_button.setObjectName("analyticsIconButton")
        self.options_button.setIconSize(QSize(16, 16))
        self.options_button.setFixedSize(30, 30)
        bind_text(self.options_button, "更多操作", method="setAccessibleName")
        self.options_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.options_button.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        self.options_menu = QMenu(self.options_button)
        self.options_menu.addAction(self.directory_button)
        self.options_menu.addAction(self.export_button)
        self.options_button.setMenu(self.options_menu)
        heading.addWidget(self.options_button)
        layout.addLayout(heading)
        notice = bind_text(QLabel(), "仅统计所选目录的本机日志，账户归属未知；不代表订阅额度或实际账单")
        notice.setWordWrap(True)
        notice.setObjectName("analyticsNote")
        # 数据边界仍可见，但不与主图争夺垂直空间；详细说明移入页脚提示。
        notice.hide()
        summary = QHBoxLayout()
        self.total_label, self.input_total_label, self.output_total_label = (QLabel("--") for _ in range(3))
        for label, title in zip((self.total_label, self.input_total_label, self.output_total_label), ("总 Token", "输入（含缓存）", "输出")):
            metric = QVBoxLayout()
            caption = bind_text(QLabel(), title)
            caption.setObjectName("analyticsNote")
            metric.addWidget(caption)
            label.setObjectName("analyticsTotal")
            metric.addWidget(label)
            summary.addLayout(metric, 1)
        layout.addLayout(summary)
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
        self.period = _SettingsComboBox()
        for title, days in (("今日", 1), ("最近 7 天", 7), ("最近 30 天", 30), ("全部", 0)):
            add_item(self.period, title, days)
        add_item(self.period, "自定义日期", -1)
        self.period.setCurrentIndex(1)
        self.period.currentIndexChanged.connect(self.render)
        actions.addWidget(self.period)
        self.group = _SettingsComboBox()
        for title, key in (("模型", "model"), ("项目", "project"), ("日期", "day"), ("会话", "session")):
            add_item(self.group, title, key)
        self.group.currentIndexChanged.connect(self.render)
        actions.addWidget(self.group)
        self.chart_mode = _SettingsComboBox()
        add_item(self.chart_mode, "Top 排行", "rank")
        add_item(self.chart_mode, "按日模型对比", "daily")
        self.chart_mode.currentIndexChanged.connect(self.render)
        actions.addWidget(self.chart_mode)
        self.project = QLineEdit()
        bind_text(self.project, "筛选项目", method="setPlaceholderText")
        bind_text(self.project, "筛选项目", method="setAccessibleName")
        self.project.textChanged.connect(self._on_project_edited)
        actions.addWidget(self.project, 1)
        layout.addLayout(actions)
        self.date_controls = QWidget()
        date_layout = QHBoxLayout(self.date_controls)
        date_layout.setContentsMargins(0, 0, 0, 0)
        self.start_date = QDateEdit(QDate.currentDate().addDays(-6))
        self.end_date = QDateEdit(QDate.currentDate())
        for edit in (self.start_date, self.end_date):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd")
            edit.setFixedWidth(140)
        date_layout.addWidget(bind_text(QLabel(), "日期范围"))
        date_layout.addWidget(self.start_date)
        date_layout.addWidget(bind_text(QLabel(), "至"))
        date_layout.addWidget(self.end_date)
        date_layout.addStretch()
        self.start_date.dateChanged.connect(lambda value: self.end_date.setMinimumDate(value))
        self.end_date.dateChanged.connect(lambda value: self.start_date.setMaximumDate(value))
        self.start_date.dateChanged.connect(self.render)
        self.end_date.dateChanged.connect(self.render)
        self.date_controls.hide()
        self.period.currentIndexChanged.connect(lambda: self.date_controls.setVisible(self.period.currentData() == -1))
        layout.addWidget(self.date_controls)
        self.status = bind_text(QLabel(), "点击扫描后读取日志；不会连接平台或启动 WSL")
        self.status.setWordWrap(True)
        self.legend_scroll = QWidget()
        self.legend_layout = QGridLayout(self.legend_scroll)
        self.legend_layout.setContentsMargins(0, 0, 0, 0)
        self.legend_layout.setHorizontalSpacing(6)
        self.legend_layout.setVerticalSpacing(2)
        self.legend_scroll.hide()
        layout.addWidget(self.legend_scroll)
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
        from ui.qt_panel import MinuteUsageTooltip, create_usage_navigator, create_usage_plot

        self.chart = create_usage_plot()
        self.chart.getPlotItem().getViewBox().invertY(True)
        self.chart.showGrid(x=True, y=False, alpha=0.15)
        # 与首页复用同一提示组件；鼠标事件限频，避免原生 QToolTip 不断创建窗口/计算布局。
        self.hover_tooltip = MinuteUsageTooltip(self.chart)
        self.chart.viewport().installEventFilter(self)
        self._hover_proxy = pg.SignalProxy(self.chart.scene().sigMouseMoved, rateLimit=30,
                                          slot=lambda event: self._chart_hover(event[0]))
        self.chart.getPlotItem().getViewBox().sigRangeChangedManually.connect(self._mark_chart_zoomed)
        self.result_stack = QStackedWidget()
        self.result_stack.addWidget(self.chart)
        self.result_stack.addWidget(self.table)
        self.chart_button.clicked.connect(lambda: self._set_view(0))
        self.table_button.clicked.connect(lambda: self._set_view(1))
        layout.addWidget(self.result_stack, 1)
        self.navigator, self.navigator_region = create_usage_navigator()
        self.navigator_region.sigRegionChanged.connect(self._on_daily_region_changed)
        self.chart.getViewBox().sigXRangeChanged.connect(self._on_daily_main_range_changed)
        self.navigator.hide()
        layout.addWidget(self.navigator)
        footer = QHBoxLayout()
        source = bind_text(QLabel(), "本机日志")
        bind_text(source, "仅统计所选目录的本机日志，账户归属未知；不代表订阅额度或实际账单", method="setToolTip")
        source.setObjectName("analyticsNote")
        footer.addWidget(source)
        footer.addWidget(self.status, 1)
        footer.addStretch()
        close = bind_text(QPushButton(), "关闭")
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        close.setVisible(not embedded)
        layout.addLayout(footer)
        theme_controller().changed.connect(self.refresh_theme)
        self.refresh_theme()

    def _set_view(self, index):
        self.result_stack.setCurrentIndex(index)
        self.chart_button.setChecked(index == 0)
        self.table_button.setChecked(index == 1)
        self.reset_button.setVisible(index == 0)
        self.hover_tooltip.hide()
        if index == 1:
            self.navigator.hide()
            self._render_table()
        else:
            self._render_chart()
        self.legend_scroll.setVisible(index == 0 and self.chart_mode.currentData() == "daily" and bool(self.rows))

    def _mark_chart_zoomed(self, *_args):
        self._chart_zoomed = True

    def _on_daily_region_changed(self):
        if self._syncing_navigator or self.chart_mode.currentData() != "daily":
            return
        self._chart_zoomed = True
        self._syncing_navigator = True
        try:
            self.chart.setXRange(*self.navigator_region.getRegion(), padding=0)
            self.navigator_region.setRegion(self.chart.viewRange()[0])
        finally:
            self._syncing_navigator = False
        self._update_daily_range_details()

    def _on_daily_main_range_changed(self, *_args):
        if self._syncing_navigator or self.chart_mode.currentData() != "daily":
            return
        self._syncing_navigator = True
        try:
            self.navigator_region.setRegion(self.chart.viewRange()[0])
        finally:
            self._syncing_navigator = False
        self._update_daily_range_details()

    def _update_daily_range_details(self):
        if not self._daily_bars:
            return
        from math import ceil, floor

        from ui.qt_panel import adaptive_usage_bar_width

        low, high = self.chart.viewRange()[0]
        slot = 0.8 / max(1, len(self._visible_daily_items))
        self._daily_bar_width = adaptive_usage_bar_width(low, high, self.chart.getViewBox().width(), slot)
        for bar in self._daily_bars:
            bar.setOpts(width=self._daily_bar_width)
        first, last = max(0, ceil(low)), min(len(self._daily_days) - 1, floor(high))
        step = max(1, ceil(max(0, last - first) / 7))
        positions = list(range(first, last + 1, step))
        if positions and positions[-1] != last:
            positions.append(last)
        self.chart.getAxis("bottom").setTicks([[(index, self._daily_days[index][5:].replace("-", "/")) for index in positions]])
        self.hover_tooltip.hide()
        self._hover_key = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "chart") and self.chart_mode.currentData() == "daily":
            self._update_daily_range_details()

    def _reset_chart(self):
        self._hidden_models.clear()
        self._chart_zoomed = False
        self._render_chart()

    def _set_model_visible(self, key, visible):
        if visible:
            self._hidden_models.discard(key)
        else:
            self._hidden_models.add(key)
        self._chart_zoomed = False
        self._render_chart()

    def _solo_model(self, key):
        self._hidden_models = set(self.legend_buttons) - {key}
        self._chart_zoomed = False
        self._render_chart()

    def _update_legend(self, series):
        for key in set(self.legend_buttons) - set(series):
            button = self.legend_buttons.pop(key)
            self.legend_layout.removeWidget(button)
            button.deleteLater()
        for index, key in enumerate(series):
            if key not in self.legend_buttons:
                button = QToolButton()
                button.setObjectName("analyticsLegend")
                button.setCheckable(True)
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                button.clicked.connect(lambda checked, current=key: self._set_model_visible(current, checked))
                button.customContextMenuRequested.connect(lambda _point, current=key: self._solo_model(current))
                self.legend_buttons[key] = button
            button = self.legend_buttons[key]
            self.legend_layout.addWidget(button, index // 3, index % 3, Qt.AlignmentFlag.AlignLeft)
            self.legend_layout.setColumnStretch(index % 3, 1)
            visible = key not in self._hidden_models
            title = key[1] or "--"
            button.setText(tr(title) if len(title) <= 24 else title[:21] + "…")
            button.setChecked(visible)
            bind_text(button, lambda value=key: " · ".join(filter(None, value)), method="setAccessibleName")
            button.setToolTip(f"<p>{escape(' · '.join(filter(None, key)))}<br>{escape(tr('单击显示或隐藏；右键只看此模型'))}</p>")
            color = model_usage_color(key[1], single=len(series) == 1) if visible else QColor(current_theme().muted)
            swatch = QPixmap(8, 8)
            swatch.fill(color)
            button.setIcon(QIcon(swatch))
            font = button.font()
            font.setStrikeOut(not visible)
            button.setFont(font)

    def showEvent(self, event):
        super().showEvent(event)
        self._auto_timer.start()
        # 先让 Qt 绘制已有数据，再增量扫描；短时间返回页面直接复用内存结果。
        QTimer.singleShot(0, self._scan_if_due)

    def hideEvent(self, event):
        self._auto_timer.stop()
        self._project_timer.stop()
        self.hover_tooltip.hide()
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if watched is self.chart.viewport() and event.type() == QEvent.Type.Leave:
            self.hover_tooltip.hide()
            self._hover_key = None
        return super().eventFilter(watched, event)

    def _scan_if_due(self):
        if self.isVisible() and (not self.scanned or time.monotonic() - self._last_scan >= 60):
            self.scan()

    def _on_project_edited(self):
        # 用户连续输入时合并筛选，避免每个按键都重聚合大日志；程序设值仍立即生效。
        if self.project.hasFocus():
            self._project_timer.start()
        else:
            self.render()

    def _refresh_headers(self, *_args):
        # QTableWidgetItem 不支持现有弱引用文字绑定；表头随语言信号原位刷新。
        self.table.setHorizontalHeaderLabels([tr(name) for name in (
            "平台", "分组", "Token", "输入", "输出", "缓存读取", "缓存写入",
        )])
        self._table_dirty = True
        if hasattr(self, "export_button") and hasattr(self, "status"):
            self.render()

    def refresh_theme(self, *_args):
        tokens = current_theme()
        border = QColor(tokens.border)
        divider = f"rgba({border.red()}, {border.green()}, {border.blue()}, 82)"
        self.setStyleSheet(f"""
            QDialog#localAnalytics {{ background: {'transparent' if self._embedded else tokens.window}; color: {tokens.text}; }}
            QDialog#localAnalytics QLabel#analyticsNote {{ color: {tokens.subtext}; }}
            QDialog#localAnalytics QLabel#analyticsTotal {{ color: {tokens.value}; font-size: 26px; font-weight: 600; }}
            QDialog#localAnalytics QPushButton {{ min-height: 28px; padding: 0 12px; border: 1px solid {divider}; border-radius: 9px; }}
            QDialog#localAnalytics QLineEdit {{ border: 1px solid {divider}; border-radius: 9px; }}
            QDialog#localAnalytics QDateEdit {{ min-height: 28px; border: 1px solid {divider}; border-radius: 9px; padding: 0 8px; }}
            QDialog#localAnalytics QComboBox {{ min-height: 28px; padding: 0 28px 0 10px; border: 1px solid {divider}; border-radius: 9px; background: {tokens.surface}; }}
            QDialog#localAnalytics QComboBox::drop-down {{ border: 0; width: 24px; background: transparent; }}
            QDialog#localAnalytics QComboBox::down-arrow {{ width: 0; height: 0; }}
            QDialog#localAnalytics QTableWidget {{ background: {tokens.surface}; alternate-background-color: {tokens.elevated}; color: {tokens.text}; border: 0; selection-background-color: {tokens.accent_soft}; selection-color: {tokens.text}; }}
            QDialog#localAnalytics QTableWidget::item {{ padding: 4px 8px; border: 0; }}
            QDialog#localAnalytics QHeaderView::section {{ background: {tokens.surface}; color: {tokens.subtext}; border: 0; border-bottom: 1px solid {tokens.border}; padding: 8px 6px; }}
            QDialog#localAnalytics QFrame#analyticsViewSegment {{ background: {tokens.surface}; border: 1px solid {divider}; border-radius: 10px; }}
            QDialog#localAnalytics QPushButton#analyticsViewButton {{ border: 0; min-height: 24px; border-radius: 8px; background: transparent; }}
            QDialog#localAnalytics QPushButton#analyticsViewButton:checked {{ background: {tokens.accent_soft}; color: {tokens.accent_text}; }}
            QDialog#localAnalytics QToolButton#analyticsLegend {{ border: 0; background: transparent; text-align: left; padding: 2px 4px; min-height: 22px; }}
            QDialog#localAnalytics QToolButton#analyticsLegend:hover {{ background: {tokens.accent_soft}; }}
            QDialog#localAnalytics QToolButton#analyticsIconButton {{ padding: 0; min-height: 0; border: 1px solid {divider}; border-radius: 8px; font-size: 20px; }}
        """)
        icon = fluent_icon("refresh", 16)
        self.scan_button.setIcon(icon if not icon.isNull() else self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.options_button.setIcon(fluent_icon("more", 16))
        from ui.qt_panel import refresh_usage_plot_theme

        refresh_usage_plot_theme(self.chart)
        refresh_usage_plot_theme(self.navigator)
        self.navigator_region.setBrush(pg.mkBrush(QColor(tokens.accent_soft)))
        for line in self.navigator_region.lines:
            line.setPen(pg.mkPen(tokens.accent, width=1.4))
        self._render_chart()

    def _choose_directory(self, provider):
        path = QFileDialog.getExistingDirectory(self, tr("选择目录"), self.paths[provider].text())
        if path:
            self.paths[provider].setText(path)

    def scan(self):
        if self.busy:
            return
        roots = {provider: Path(edit.text()).expanduser() for provider, edit in self.paths.items() if edit.text().strip()}
        scope = local_cache_scope(roots)
        changed_scope = scope != self._scan_scope
        if changed_scope:
            self.rows = []
            self.scanner = LocalUsageScanner()
            self.scanned = True
            self.render()
            self.total_label.setText("--")
            self.input_total_label.setText("--")
            self.output_total_label.setText("--")
        self._scan_scope = scope
        self.busy = True
        for control in (*self.paths.values(), *self.browse_buttons, self.scan_button, self.export_button):
            control.setEnabled(False)
        bind_text(self.status, "正在扫描本机日志")
        self._task = _ScanTask(self.scanner, roots, load_cache=changed_scope)
        # 扫描在后台线程执行；Qt 自动断开已销毁对话框的信号，关闭窗口不会留下 UI 回调。
        self._task.signals.finished.connect(self._finished)
        self._task.signals.cached.connect(self._show_cached)
        QThreadPool.globalInstance().start(self._task)

    def _show_cached(self, rows, issues):
        self.rows, self.issues = rows, issues
        self.scanned = True
        self.render()
        bind_text(self.status, "显示上次统计，正在后台更新")

    def _finished(self, rows, issues, failed):
        self.busy = False
        self.scanned = True
        self._last_scan = time.monotonic()
        if not failed:
            self.rows, self.issues = rows, issues
        for control in (*self.paths.values(), *self.browse_buttons, self.scan_button):
            control.setEnabled(True)
        self.render()
        if failed:
            bind_text(self.status, "扫描失败，请检查目录后重试")

    def filtered_rows(self):
        days = int(self.period.currentData() or 0)
        rows = filter_usage(self.rows, max(0, days), self.project.text().strip())
        if days == -1:
            start, end = self.start_date.date().toString("yyyy-MM-dd"), self.end_date.date().toString("yyyy-MM-dd")
            rows = [row for row in rows if start <= row.day <= end]
        return rows

    def render(self, *_args):
        if not self.scanned:
            return
        dimension = self.group.currentData() or "model"
        daily = self.chart_mode.currentData() == "daily"
        self.group.setEnabled(not daily)
        self.group.setVisible(not daily)
        signature = (self.period.currentData(), dimension, daily, self.project.text(), self.start_date.date().toString(), self.end_date.date().toString(), date.today())
        if self._render_rows is not self.rows or signature != self._aggregate_signature:
            rows = self.filtered_rows()
            groups = defaultdict(lambda: [0, 0, 0, 0, 0])
            tokens = input_tokens = output_tokens = 0
            for row in rows:
                key = f"{row.day} · {row.model}" if daily else getattr(row, dimension)
                values = groups[row.provider, key]
                values[0] += row.total
                values[1] += row.input
                values[2] += row.output
                values[3] += row.cache_read
                values[4] += row.cache_write
                tokens += row.total
                input_tokens += row.input
                output_tokens += row.output
            self._groups = dict(groups)
            self._rank_groups = sorted(groups.items(), key=lambda item: item[1][0], reverse=True)[:10]
            self._aggregate_totals = (len(rows), tokens, input_tokens, output_tokens)
            self._render_rows, self._aggregate_signature = self.rows, signature
            self._table_dirty = True
        if self.result_stack.currentWidget() is self.chart:
            self._render_chart()
        if self.result_stack.currentWidget() is self.table:
            self._render_table()
        count, tokens, input_tokens, output_tokens = self._aggregate_totals
        self.export_button.setEnabled(bool(count) and not self.busy and not self._export_busy)
        for label, amount in ((self.total_label, tokens), (self.input_total_label, input_tokens),
                              (self.output_total_label, output_tokens)):
            bind_text(label, lambda value=amount: tr(compact_tokens(value)))
            label.setToolTip(f"{amount:,} Token")
        if not self.busy:
            bind_text(self.status, lambda: tr("{count} 条记录 · {issues} 项读取异常", count=count, issues=self.issues))

    def _render_table(self):
        if not self._table_dirty:
            return
        # 图表页不创建隐藏表格的成千上万个单元格；切换表格时批量更新，期间暂停重绘。
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(self._groups))
            for index, ((provider, key), counts) in enumerate(sorted(self._groups.items())):
                for column, value in enumerate((provider, key or "--", *counts)):
                    item = QTableWidgetItem(tr(compact_tokens(value)) if column >= 2 else str(value))
                    if column >= 2:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                        item.setToolTip(f"{value:,} Token")
                    self.table.setItem(index, column, item)
        finally:
            self.table.setUpdatesEnabled(True)
        self._table_dirty = False

    def _render_chart(self):
        if not hasattr(self, "chart"):
            return
        self._hover_key = None
        self.hover_tooltip.hide()
        scope = (self.period.currentData(), self.group.currentData(), self.project.text(), self.chart_mode.currentData(),
                 self.start_date.date().toString(), self.end_date.date().toString())
        # 自动增量更新保留用户缩放；改变筛选范围时才重新适配坐标。
        previous_range = self.chart.viewRange() if self._chart_zoomed and self._chart_scope == scope else None
        if self._chart_scope != scope:
            self._chart_zoomed = False
        self._chart_scope = scope
        self._syncing_navigator = True
        self.chart.clear()
        if self.chart_mode.currentData() == "daily":
            self._render_daily_chart()
            if previous_range:
                self.chart.setRange(xRange=previous_range[0], yRange=previous_range[1], padding=0)
            self._syncing_navigator = False
            self._on_daily_main_range_changed()
            return
        self._syncing_navigator = False
        self.navigator.hide()
        self.chart.getViewBox().setLimits(xMin=None, xMax=None, minXRange=None, maxXRange=None)
        self.legend_scroll.hide()
        self.chart.getAxis("left").setWidth(min(185, self.width() // 3))
        self.chart.getPlotItem().getViewBox().invertY(True)
        self.chart.showGrid(x=True, y=False, alpha=0.15)
        # 同一筛选结果取 Token 最高的十组；完整分组与精确数值始终可在表格查看。
        groups = self._rank_groups
        tokens = current_theme()
        ticks = []
        for index, ((provider, key), counts) in enumerate(groups):
            label = f"{provider} · {key or '--'}"
            ticks.append((index, label if len(label) <= 28 else label[:25] + "…"))
            self.chart.addItem(pg.BarGraphItem(x0=0, y=[index], width=[counts[0]], height=0.55, brush=tokens.accent, pen=None))
            value = pg.TextItem(text=tr(compact_tokens(counts[0])), color=tokens.text, anchor=(0, 0.5))
            value.setPos(counts[0], index)
            self.chart.addItem(value)
        self.chart.getAxis("left").setTicks([ticks])
        maximum = max((counts[0] for _, counts in groups), default=1)
        self.chart.getAxis("bottom").setTicks([[(maximum * fraction, tr(compact_tokens(round(maximum * fraction)))) for fraction in (0, 0.25, 0.5, 0.75, 1)]])
        self.chart.setXRange(0, maximum * 1.25, padding=0)
        self.chart.setYRange(-0.6, max(1, len(groups)) - 0.4, padding=0)
        self.chart.setTitle(tr("按 Token 排序，显示前 10 项") if groups else tr("暂无本机统计"), color=tokens.subtext, size="10pt")
        if previous_range:
            self.chart.setRange(xRange=previous_range[0], yRange=previous_range[1], padding=0)

    def _render_daily_chart(self):
        from ui.qt_panel import USAGE_AXIS_WIDTH

        self.chart.getAxis("left").setWidth(USAGE_AXIS_WIDTH)
        # 使用已聚合的日/模型分组生成图表，显隐图例时不再扫描数万条原始记录。
        rows = []
        for (provider, key), counts in self._groups.items():
            day, model = key.split(" · ", 1)
            rows.append(LocalUsage(provider, "", "", day, model, counts[1], counts[2], counts[3], counts[4], counts[0]))
        period = int(self.period.currentData() or 0)
        end = date.today()
        if period == -1:
            start, end = self.start_date.date().toPython(), self.end_date.date().toPython()
        elif period:
            start = end - timedelta(days=period - 1)
        else:
            start = date.fromisoformat(min((row.day for row in rows), default=end.isoformat()))
        days, series = daily_model_series(rows, start, end)
        self._update_legend(series)
        visible = {key: values for key, values in series.items() if key not in self._hidden_models}
        active = [index for index in range(len(days)) if any(values[index] > 0 for values in visible.values())]
        days = [days[index] for index in active]
        self._daily_days = days
        self._daily_series = {key: [values[index] for index in active] for key, values in series.items()}
        self._visible_daily_series = {key: [values[index] for index in active] for key, values in visible.items()}
        visible_series = self._visible_daily_series
        self._visible_daily_items = list(visible_series.items())
        slot = 0.8 / max(1, len(visible_series))
        self._daily_bars = []
        maximum = 1
        for position, ((provider, model), values) in enumerate(visible_series.items()):
            color = model_usage_color(model, single=len(series) == 1)
            x = [index - 0.4 + (position + 0.5) * slot for index in range(len(days))]
            bar = pg.BarGraphItem(x=x, height=values, width=slot * 0.85, brush=color, pen=None)
            self.chart.addItem(bar)
            self._daily_bars.append(bar)
            maximum = max(maximum, max(values, default=0))
        self.legend_scroll.setVisible(bool(series) and self.result_stack.currentWidget() is self.chart)
        self.chart.getPlotItem().getViewBox().invertY(False)
        self.chart.showGrid(x=False, y=True, alpha=0.15)
        step = max(1, (len(days) + 6) // 7)
        self.chart.getAxis("bottom").setTicks([[(index, day[5:].replace("-", "/")) for index, day in enumerate(days) if index % step == 0 or index == len(days) - 1]])
        self.chart.getAxis("left").setTicks([[(maximum * fraction, tr(compact_tokens(round(maximum * fraction)))) for fraction in (0, 0.5, 1)]])
        from ui.qt_panel import MinuteUsageChart

        # 按今日分时的默认可见柱数展示，而非把整个日期范围压进一屏；底部保留完整概览。
        right = max(0.5, len(days) - 0.5)
        span = min(max(1, len(days)), max(1, MinuteUsageChart.DEFAULT_VISIBLE_BUCKETS / max(1, len(visible_series))))
        self.chart.getViewBox().setLimits(xMin=-0.5, xMax=right, minXRange=1, maxXRange=max(1, len(days)))
        self.chart.setXRange(max(-0.5, right - span), right, padding=0)
        self.chart.setYRange(0, maximum * 1.15, padding=0)
        self.chart.setTitle(tr("各模型每日 Token 使用量"), color=current_theme().subtext, size="10pt")
        self.navigator.clear()
        self.navigator.addItem(self.navigator_region)
        totals = [sum(values[index] for values in visible_series.values()) for index in range(len(days))]
        self.navigator.addItem(pg.BarGraphItem(x=list(range(len(days))), height=totals, width=0.8,
                                             brush=QColor(current_theme().accent), pen=None))
        self.navigator_region.setBounds((-0.5, right))
        self.navigator_region.setRegion(self.chart.viewRange()[0])
        self.navigator.setXRange(-0.5, right, padding=0)
        self.navigator.setYRange(0, max(totals, default=1) or 1, padding=0)
        self.navigator.getAxis("bottom").setTicks([[(index, day[5:].replace("-", "/")) for index, day in enumerate(days) if index % step == 0 or index == len(days) - 1]])
        self.navigator.setVisible(bool(series) and self.result_stack.currentWidget() is self.chart)

    def _chart_hover(self, position):
        view = self.chart.getPlotItem().getViewBox()
        if not view.sceneBoundingRect().contains(position):
            self.hover_tooltip.hide()
            self._hover_key = None
            return
        point = view.mapSceneToView(position)
        label, value, counts, day = "", None, None, date.today()
        hover_key = None
        if self.chart_mode.currentData() == "daily":
            series = getattr(self, "_visible_daily_items", [])
            index = round(point.x())
            slot = 0.8 / max(1, len(series))
            model_index = int((point.x() - index + 0.4) // slot)
            if 0 <= index < len(getattr(self, "_daily_days", [])) and 0 <= model_index < len(series):
                (provider, model), values = series[model_index]
                center = index - 0.4 + (model_index + 0.5) * slot
                if abs(point.x() - center) <= self._daily_bar_width / 2 and 0 <= point.y() <= values[index] and values[index] > 0:
                    day = date.fromisoformat(self._daily_days[index])
                    label = f"{provider} · {model}" if provider else tr(model)
                    value = values[index]
                    counts = self._groups.get((provider, f"{day.isoformat()} · {model}"))
                    hover_key = ("daily", index, model_index)
        else:
            groups = self._rank_groups
            index = round(point.y())
            if 0 <= index < len(groups):
                (provider, model), counts = groups[index]
                if abs(point.y() - index) <= 0.275 and 0 <= point.x() <= counts[0] and counts[0] > 0:
                    label, value = f"{provider} · {model}", counts[0]
                    hover_key = ("rank", index)
        if value is None:
            self.hover_tooltip.hide()
            self._hover_key = None
            return
        changed = hover_key != self._hover_key
        if changed:
            self._hover_key = hover_key
            values = (counts[3], max(0, counts[1] - counts[3]), counts[2]) if counts else (0, 0, 0)
            self.hover_tooltip.set_values(0, 0, values, None, "", [label])
            bind_text(self.hover_tooltip.time_label, day.isoformat() if self.chart_mode.currentData() == "daily" else self.period.currentText())
            bind_text(self.hover_tooltip.cost_name, "本时段消耗金额")
            bind_text(self.hover_tooltip.total_label, f"总计 {compact_tokens(value)}")
            self.hover_tooltip.total_label.setAccessibleDescription(f"{value:,} Token")
            if counts is None:
                for value_label in self.hover_tooltip.value_labels:
                    bind_text(value_label, "--")
            from ui.qt_panel import MinuteUsageChart

            self.hover_tooltip.refresh_colors(MinuteUsageChart._colors())
        from ui.qt_panel import show_usage_tooltip

        show_usage_tooltip(self.chart, self.hover_tooltip, self.chart.mapFromScene(position), refresh_layout=changed)

    def export(self):
        if self._export_busy:
            return
        path, _selected = QFileDialog.getSaveFileName(self, tr("导出 Excel"), "local-usage.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        destination = Path(path)
        if destination.suffix.lower() != ".xlsx":
            destination = Path(str(destination) + ".xlsx")
            if destination.exists() and QMessageBox.question(self, tr("导出 Excel"), tr("目标文件已存在，是否覆盖？")) != QMessageBox.StandardButton.Yes:
                return
        dimension = "daily_model" if self.chart_mode.currentData() == "daily" else str(self.group.currentData())
        # 捕获当前筛选的只读记录；导出期间允许继续浏览，不在 GUI 线程构造工作簿。
        self._export_task = _ExportTask(destination, self.filtered_rows(), self.issues, dimension)
        self._export_task.signals.finished.connect(self._export_finished)
        self._export_busy = True
        self.export_button.setEnabled(False)
        bind_text(self.status, "正在导出 Excel")
        QThreadPool.globalInstance().start(self._export_task)

    def _export_finished(self, success):
        self._export_busy = False
        self.render()
        if success:
            bind_text(self.status, "Excel 已导出")
        else:
            QMessageBox.warning(self, tr("导出 Excel"), tr("导出失败，请检查目标目录或关闭正在使用的文件"))
