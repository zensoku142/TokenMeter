"""Codex-inspired monitoring panel built from PySide6 widgets."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pyqtgraph as pg
from PySide6.QtCore import QDate, QLocale, QPoint, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
    QTextCharFormat,
)
from PySide6.QtWidgets import (
    QCalendarWidget,
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QStackedWidget,
    QStyle,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

import config_manager
from data.store import TokenData
from ui.activity import compact_tokens
from ui.qt_heatmap import TokenActivityHeatmap
from ui.qt_theme import app_icon, current_theme, fluent_icon, theme_controller


PANEL_MIN_WIDTH = 640
PANEL_MAX_WIDTH = 820
PANEL_HEIGHT = 550
HEADER_HEIGHT = 42
TOP_SECTION_HEIGHT = 160
ANNUAL_ACTIVITY_SECTION_HEIGHT = 176
ACTIVITY_SECTION_HEIGHT = 230
ANNUAL_PANEL_HEIGHT = PANEL_HEIGHT - (ACTIVITY_SECTION_HEIGHT - ANNUAL_ACTIVITY_SECTION_HEIGHT)
STATISTICS_SECTION_HEIGHT = 76
STATUS_SECTION_HEIGHT = 40
SECTION_SPACING = 0
SECTION_HORIZONTAL_MARGIN = 22


def format_money(value: float | Decimal | None) -> str:
    if value is None:
        return "--"
    amount = float(value)
    decimals = 4 if 0 < abs(amount) < 0.01 else 2
    return f"¥{amount:.{decimals}f}"


def format_token_axis(value: float) -> str:
    return compact_tokens(int(round(value)))


def format_money_axis(value: float) -> str:
    absolute = abs(value)
    if absolute >= 100:
        return f"¥{value:,.0f}"
    decimals = 4 if 0 < absolute < 0.01 else 2
    return f"¥{value:.{decimals}f}"


class MoneyAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):
        return [format_money_axis(value * scale) for value in values]


class TokenAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):
        return [format_token_axis(value * scale) for value in values]


class DraggableHeader(QFrame):
    """Header drag surface used to move the entire frameless window."""

    pressed = Signal(QPoint)
    dragged = Signal(QPoint)
    released = Signal(QPoint)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.pressed.emit(event.globalPosition().toPoint())
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.dragged.emit(event.globalPosition().toPoint())
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.released.emit(event.globalPosition().toPoint())
            event.accept()


class MinuteCalendarWidget(QCalendarWidget):
    """Compact calendar grid whose cell states follow the application theme."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("minuteCalendar")
        self.setNavigationBarVisible(False)
        self.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.ShortDayNames)
        self.setGridVisible(False)
        self.setLocale(QLocale(QLocale.Language.Chinese, QLocale.Country.China))
        self.setAccessibleName("分时日期日历")
        self.setFixedSize(264, 190)
        self.refresh_theme()

    def refresh_theme(self) -> None:
        tokens = current_theme()
        header_format = QTextCharFormat()
        header_format.setForeground(QColor(tokens.subtext))
        for day in Qt.DayOfWeek:
            self.setWeekdayTextFormat(day, header_format)
        self.updateCells()

    def paintCell(self, painter: QPainter, rect, value: QDate) -> None:
        tokens = current_theme()
        in_month = value.year() == self.yearShown() and value.month() == self.monthShown()
        in_range = self.minimumDate() <= value <= self.maximumDate()
        selectable = in_month and in_range
        selected = selectable and value == self.selectedDate()
        today = selectable and value == QDate.currentDate() and not selected
        cell = rect.adjusted(4, 2, -4, -2)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tokens.accent))
            painter.drawRoundedRect(cell, 6, 6)
            text_color = QColor("#FFFFFF")
        else:
            if today:
                painter.setPen(QPen(QColor(tokens.accent), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(cell, 6, 6)
            text_color = QColor(tokens.text if selectable else tokens.disabled)
        painter.setPen(text_color)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(value.day()))
        painter.restore()


class MinuteCalendarPopup(QFrame):
    """Popup calendar with compact month navigation and range-aware selection."""

    dateSelected = Signal(QDate)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("minuteCalendarPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(288)
        self._selected_date = QDate.currentDate()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 9, 11, 11)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        self.previous_month_button = self._month_button("‹", "上个月")
        self.month_label = QLabel()
        self.month_label.setObjectName("minuteCalendarMonth")
        self.month_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next_month_button = self._month_button("›", "下个月")
        header.addWidget(self.previous_month_button)
        header.addWidget(self.month_label, 1)
        header.addWidget(self.next_month_button)
        layout.addLayout(header)

        self.calendar = MinuteCalendarWidget(self)
        layout.addWidget(self.calendar, 0, Qt.AlignmentFlag.AlignHCenter)
        self._escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._escape_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._escape_shortcut.activated.connect(self.close)
        self.previous_month_button.clicked.connect(lambda: self._change_month(-1))
        self.next_month_button.clicked.connect(lambda: self._change_month(1))
        self.calendar.clicked.connect(self._select_date)
        self.calendar.activated.connect(self._select_date)
        self._update_month_header()

    def _month_button(self, text: str, tooltip: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("minuteCalendarNavButton")
        button.setText(text)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setFixedSize(28, 26)
        return button

    def setDateRange(self, minimum: QDate, maximum: QDate) -> None:
        self.calendar.setDateRange(minimum, maximum)
        self._update_month_header()

    def setDate(self, value: QDate) -> None:
        if not value.isValid():
            return
        self._selected_date = value
        self.calendar.setSelectedDate(value)
        self.calendar.setCurrentPage(value.year(), value.month())
        self._update_month_header()

    def refresh_theme(self) -> None:
        self.calendar.refresh_theme()
        self.update()

    def _month_intersects_range(self, first: QDate) -> bool:
        last = QDate(first.year(), first.month(), first.daysInMonth())
        return last >= self.calendar.minimumDate() and first <= self.calendar.maximumDate()

    def _change_month(self, offset: int) -> None:
        shown = QDate(self.calendar.yearShown(), self.calendar.monthShown(), 1)
        target = shown.addMonths(offset)
        if not self._month_intersects_range(target):
            return
        self.calendar.setCurrentPage(target.year(), target.month())
        self._update_month_header()

    def _update_month_header(self) -> None:
        shown = QDate(self.calendar.yearShown(), self.calendar.monthShown(), 1)
        self.month_label.setText(f"{shown.year()}年{shown.month()}月")
        self.previous_month_button.setEnabled(self._month_intersects_range(shown.addMonths(-1)))
        self.next_month_button.setEnabled(self._month_intersects_range(shown.addMonths(1)))

    def _select_date(self, value: QDate) -> None:
        in_shown_month = (
            value.year() == self.calendar.yearShown()
            and value.month() == self.calendar.monthShown()
        )
        if (
            not in_shown_month
            or value < self.calendar.minimumDate()
            or value > self.calendar.maximumDate()
        ):
            self.setDate(self._selected_date)
            return
        self._selected_date = value
        self.dateSelected.emit(value)
        self.close()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)


class MinuteDateEdit(QWidget):
    """Fixed-size previous/date/next selector used by the minute chart."""

    dateChanged = Signal(QDate)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("minuteDateEdit")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedSize(118, 26)
        self._minimum_date = QDate(1752, 9, 14)
        self._maximum_date = QDate(9999, 12, 31)
        self._date = QDate.currentDate()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        self.previous_button = self._button("‹", "前一天", "minuteDatePreviousButton", 20)
        self.date_button = self._button("", "选择分时日期", "minuteDateTextButton", 76)
        self.next_button = self._button("›", "后一天", "minuteDateNextButton", 20)
        layout.addWidget(self.previous_button)
        layout.addWidget(self.date_button)
        layout.addWidget(self.next_button)

        self.popup = MinuteCalendarPopup(self)
        self.previous_button.clicked.connect(lambda: self._change_day(-1))
        self.next_button.clicked.connect(lambda: self._change_day(1))
        self.date_button.clicked.connect(self.showCalendarPopup)
        self.popup.dateSelected.connect(self.setDate)
        self._sync_display()

    def _button(self, text: str, tooltip: str, name: str, width: int) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName(name)
        button.setText(text)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setFixedSize(width, 24)
        return button

    def date(self) -> QDate:
        return self._date

    def minimumDate(self) -> QDate:
        return self._minimum_date

    def maximumDate(self) -> QDate:
        return self._maximum_date

    def setDateRange(self, minimum: QDate, maximum: QDate) -> None:
        if not minimum.isValid() or not maximum.isValid() or minimum > maximum:
            return
        self._minimum_date = minimum
        self._maximum_date = maximum
        self.popup.setDateRange(minimum, maximum)
        self.setDate(self._date)
        self._update_button_states()

    def setDate(self, value: QDate) -> None:
        if not value.isValid():
            return
        bounded = value
        if bounded < self._minimum_date:
            bounded = self._minimum_date
        elif bounded > self._maximum_date:
            bounded = self._maximum_date
        changed = bounded != self._date
        self._date = bounded
        self._sync_display()
        if changed:
            self.dateChanged.emit(bounded)

    def _sync_display(self) -> None:
        self.date_button.setText(self._date.toString("yyyy-MM-dd"))
        self.popup.setDate(self._date)
        self._update_button_states()

    def _update_button_states(self) -> None:
        enabled = self.isEnabled()
        self.previous_button.setEnabled(enabled and self._date > self._minimum_date)
        self.date_button.setEnabled(enabled)
        self.next_button.setEnabled(enabled and self._date < self._maximum_date)

    def _change_day(self, offset: int) -> None:
        self.setDate(self._date.addDays(offset))

    def showCalendarPopup(self) -> None:
        if not self.isEnabled():
            return
        if self.popup.isVisible():
            self.popup.close()
            return
        self.popup.setDate(self._date)
        self.popup.adjustSize()
        below = self.mapToGlobal(QPoint(0, self.height() + 2))
        above = self.mapToGlobal(QPoint(0, -self.popup.height() - 2))
        screen = QGuiApplication.screenAt(below) or self.screen()
        available = screen.availableGeometry()
        x = max(available.left(), min(below.x(), available.right() - self.popup.width() + 1))
        y = below.y()
        if y + self.popup.height() - 1 > available.bottom() and above.y() >= available.top():
            y = above.y()
        self.popup.move(x, max(available.top(), y))
        self.popup.show()
        self.popup.raise_()
        self.popup.calendar.setFocus(Qt.FocusReason.PopupFocusReason)

    def setEnabled(self, enabled: bool) -> None:
        super().setEnabled(enabled)
        if not enabled:
            self.popup.close()
        self._update_button_states()

    def refresh_theme(self) -> None:
        self.popup.refresh_theme()
        self.update()

    def closeEvent(self, event) -> None:
        self.popup.close()
        super().closeEvent(event)


class StatusDot(QWidget):
    """Small semantic status mark that follows live theme changes."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._role = "accent"
        self._explicit_color: QColor | None = None
        self.setFixedSize(12, 12)

    def set_role(self, role: str) -> None:
        self._role = role
        self._explicit_color = None
        self.update()

    def set_color(self, color: str) -> None:
        """Keep the old color API available for callers outside MainPanel."""
        self._explicit_color = QColor(color)
        self.update()

    def refresh_theme(self) -> None:
        self.update()

    def paintEvent(self, _event) -> None:
        tokens = current_theme()
        color = self._explicit_color or QColor(getattr(tokens, self._role, tokens.accent))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(2, 2, 8, 8)
        painter.end()


class MetricCard(QFrame):
    """One logical metric in the flat top summary area."""

    def __init__(self, title: str, icon_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.icon_name = icon_name
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("metricLabel")
        self.value = QLabel("--")
        self.value.setObjectName("metricValue")
        self.detail = QLabel()
        self.detail.setObjectName("metricDetail")
        self.footer = QLabel()
        self.footer.setObjectName("muted")
        # The third visual direction intentionally keeps the summary sparse.
        # Detail values remain populated for compatibility and accessibility.
        self.detail.hide()
        self.footer.hide()

        layout.addWidget(self.title_label)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)
        layout.addWidget(self.footer)
        layout.addStretch(1)

    def set_variant(self, variant: str) -> None:
        self.value.setObjectName("heroValue" if variant == "hero" else "metricValue")
        self.setProperty("variant", variant)

    def set_values(self, value: str, detail: str = "", footer: str = "") -> None:
        self.value.setText(value)
        self.detail.setText(detail)
        self.footer.setText(footer)
        self.detail.setToolTip(detail)
        self.footer.setToolTip(footer)

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)


class TrendCard(QFrame):
    """Seven-day cost chart rendered as seven flat bars."""

    BAR_WIDTH = 0.36

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("trendSection")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 5, 0, 2)
        layout.setSpacing(2)

        self.title = QLabel("近 7 天使用金额")
        self.title.setObjectName("sectionTitle")
        layout.addWidget(self.title)

        self.plot = pg.PlotWidget(
            axisItems={"left": MoneyAxis(orientation="left")},
        )
        self.plot.setStyleSheet("border: 0;")
        self.plot.setMinimumHeight(100)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        self.plot.setMenuEnabled(False)
        self.plot.showGrid(x=False, y=True, alpha=0.14)

        axis_font = QFont("Microsoft YaHei UI", 8)
        left_axis = self.plot.getAxis("left")
        bottom_axis = self.plot.getAxis("bottom")
        left_axis.setTickFont(axis_font)
        bottom_axis.setTickFont(axis_font)
        bottom_axis.setStyle(hideOverlappingLabels=False)
        left_axis.setStyle(hideOverlappingLabels=False)
        left_axis.setWidth(44)
        left_axis.enableAutoSIPrefix(False)
        bottom_axis.setHeight(22)
        self.plot.getViewBox().setLimits(xMin=-0.5, xMax=6.5, yMin=0)

        self._dates: list[date] = []
        self._values: list[float] = []
        self._series: pg.BarGraphItem | None = None
        self._hover_index: int | None = None
        self._mouse_proxy = pg.SignalProxy(
            self.plot.scene().sigMouseMoved,
            rateLimit=60,
            slot=self._on_mouse_moved,
        )
        layout.addWidget(self.plot, 1)
        self._connect_theme_changes()
        self.set_rows([])

    def _connect_theme_changes(self) -> None:
        try:
            theme_controller().changed.connect(self._on_theme_changed)
        except RuntimeError:
            # Standalone component tests may construct the chart before an app-level
            # controller exists; production configures the theme before any window.
            pass

    def set_rows(self, rows: list[dict], today: date | None = None) -> None:
        current = today or date.today()
        by_date = {str(row.get("date")): row for row in rows}
        self._dates = [current - timedelta(days=offset) for offset in range(6, -1, -1)]
        self._values = [
            float(by_date.get(day.isoformat(), {}).get("cost_cny", 0) or 0)
            for day in self._dates
        ]
        self.plot.clear()

        tokens = current_theme()
        self._series = pg.BarGraphItem(
            x=list(range(7)),
            height=self._values,
            width=self.BAR_WIDTH,
            pen=pg.mkPen(tokens.accent),
            brush=pg.mkBrush(tokens.accent),
        )
        self.plot.addItem(self._series)

        self.plot.getAxis("bottom").setTicks(
            [[(index, day.strftime("%m/%d")) for index, day in enumerate(self._dates)]]
        )
        # Preserve half a day at each edge so all seven bars stay fully visible.
        self.plot.setXRange(-0.5, 6.5, padding=0)
        maximum = max(self._values, default=0.0)
        tick_max = max(0.01, maximum)
        range_max = tick_max * 1.08 if maximum > 0 else tick_max
        self.plot.setYRange(0, range_max, padding=0)
        self.plot.getAxis("left").setTicks(
            [[
                (tick_max * index / 3, format_money_axis(tick_max * index / 3))
                for index in range(4)
            ]]
        )
        self._hover_index = None
        self.refresh_theme()

    def refresh_theme(self) -> None:
        tokens = current_theme()
        # The selected layout is one continuous surface; the chart must not
        # introduce a nested rectangular card behind the bars.
        self.plot.setBackground(tokens.window)
        left_axis = self.plot.getAxis("left")
        bottom_axis = self.plot.getAxis("bottom")
        left_axis.setTextPen(pg.mkPen(tokens.subtext))
        bottom_axis.setTextPen(pg.mkPen(tokens.subtext))
        axis_color = QColor(tokens.border)
        axis_color.setAlpha(96)
        left_axis.setPen(pg.mkPen(axis_color))
        bottom_axis.setPen(pg.mkPen(axis_color))
        if self._series is not None:
            if self._hover_index is None:
                self._series.setOpts(
                    pens=None,
                    brushes=None,
                    pen=pg.mkPen(tokens.accent),
                    brush=pg.mkBrush(tokens.accent),
                )
            else:
                self._series.setOpts(
                    pens=[
                        pg.mkPen(tokens.accent_hover if index == self._hover_index else tokens.accent)
                        for index in range(len(self._values))
                    ],
                    brushes=[
                        pg.mkBrush(tokens.accent_hover if index == self._hover_index else tokens.accent)
                        for index in range(len(self._values))
                    ],
                )

    def _on_theme_changed(self, _mode: str, _resolved: str) -> None:
        self.refresh_theme()

    def _on_mouse_moved(self, event) -> None:
        scene_pos = event[0]
        if not self.plot.sceneBoundingRect().contains(scene_pos):
            self._hide_hover()
            return
        point = self.plot.getViewBox().mapSceneToView(scene_pos)
        index = int(round(point.x()))
        if not 0 <= index < len(self._values) or abs(point.x() - index) > self.BAR_WIDTH / 2:
            self._hide_hover()
            return

        self._hover_index = index
        self.refresh_theme()
        local = self.plot.mapFromScene(scene_pos)
        QToolTip.showText(
            self.plot.mapToGlobal(local),
            self.tooltip_text(index),
            self.plot,
        )

    def _hide_hover(self) -> None:
        had_hover = self._hover_index is not None
        self._hover_index = None
        if had_hover:
            self.refresh_theme()
        QToolTip.hideText()

    def tooltip_text(self, index: int) -> str:
        return (
            f"{self._dates[index].isoformat()}\n"
            f"使用金额：{format_money(self._values[index])}"
        )


class MinuteUsageTooltip(QFrame):
    """图内悬停明细，避免系统提示框遮挡或离开图表。"""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("minuteTooltip")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 7, 9, 7)
        layout.setSpacing(5)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.time_label = QLabel("00:00")
        self.time_label.setObjectName("minuteTooltipTitle")
        self.total_label = QLabel("总计 0")
        self.total_label.setObjectName("minuteTooltipValue")
        header.addWidget(self.time_label)
        header.addStretch(1)
        header.addWidget(self.total_label)
        layout.addLayout(header)

        rows = QGridLayout()
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setHorizontalSpacing(7)
        rows.setVerticalSpacing(3)
        self.swatches: list[QLabel] = []
        self.value_labels: list[QLabel] = []
        for row, label in enumerate(("输入（命中缓存）", "输入（未命中缓存）", "输出")):
            swatch = QLabel()
            swatch.setFixedSize(8, 8)
            name_label = QLabel(label)
            name_label.setObjectName("minuteTooltipMuted")
            value_label = QLabel("0")
            value_label.setObjectName("minuteTooltipValue")
            value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            rows.addWidget(swatch, row, 0)
            rows.addWidget(name_label, row, 1)
            rows.addWidget(value_label, row, 2)
            self.swatches.append(swatch)
            self.value_labels.append(value_label)
        layout.addLayout(rows)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        rate_name = QLabel("缓存命中率")
        rate_name.setObjectName("minuteTooltipMuted")
        self.rate_label = QLabel("--")
        self.rate_label.setObjectName("minuteTooltipValue")
        footer.addWidget(rate_name)
        footer.addStretch(1)
        footer.addWidget(self.rate_label)
        layout.addLayout(footer)
        self.hide()

    def set_values(self, minute: int, values: tuple[int, int, int]) -> None:
        hit, miss, output = values
        total = hit + miss + output
        rate = "--" if hit + miss == 0 else f"{hit / (hit + miss) * 100:.1f}%"
        self.time_label.setText(f"{minute // 60:02d}:{minute % 60:02d}")
        self.total_label.setText(f"总计 {compact_tokens(total)}")
        for label, value in zip(self.value_labels, values):
            label.setText(compact_tokens(value))
        self.rate_label.setText(rate)

    def refresh_colors(self, colors: tuple[QColor, QColor, QColor]) -> None:
        for swatch, color in zip(self.swatches, colors):
            swatch.setStyleSheet(f"background: {color.name()}; border-radius: 2px;")


class MinuteUsageChart(QWidget):
    """当天 Token 差额的估算分时图；原始分钟数据始终保持不变。"""

    SPARSE_POINT_LIMIT = 24
    DEFAULT_VISIBLE_MINUTES = 24
    BAR_MIN_WIDTH_PX = 3.0
    BAR_MAX_WIDTH_PX = 36.0

    SERIES = (
        ("PROMPT_CACHE_HIT_TOKEN", "输入（命中缓存）"),
        ("PROMPT_CACHE_MISS_TOKEN", "输入（未命中缓存）"),
        ("RESPONSE_TOKEN", "输出"),
    )

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("minuteUsageChart")
        self._values = {key: [0] * 1440 for key, _label in self.SERIES}
        self._visible = {key: True for key, _label in self.SERIES}
        self._signature: tuple | None = None
        self._updating_region = False
        self._bars: dict[str, pg.BarGraphItem] = {}
        self._bar_width = 0.8
        self._nav_bars: pg.BarGraphItem | None = None
        self._hover_line: pg.InfiniteLine | None = None
        self._hover_bar: pg.BarGraphItem | None = None
        self._nav_handles: pg.ScatterPlotItem | None = None
        self._has_initial_range = False
        self._sparse_mode = False
        self._active_minutes: list[int] = []
        self._x_bounds = (-0.5, 1439.5)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.state_label = QLabel("等待首次刷新建立估算基线")
        self.state_label.setObjectName("minuteUsageState")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.state_label, 1)

        self.chart_container = QWidget()
        chart_layout = QVBoxLayout(self.chart_container)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        chart_layout.setSpacing(1)
        self.plot = pg.PlotWidget(axisItems={"left": TokenAxis(orientation="left")})
        self.plot.setStyleSheet("border: 0;")
        self.plot.setMouseEnabled(x=True, y=False)
        self.plot.hideButtons()
        self.plot.setMenuEnabled(False)
        self.plot.showGrid(x=False, y=True, alpha=0.14)
        self.plot.setMinimumHeight(118)
        self.plot.getViewBox().setLimits(
            xMin=-0.5, xMax=1439.5, yMin=0, minXRange=1, maxXRange=1440
        )
        self.plot.getAxis("left").setWidth(42)
        self.plot.getAxis("left").setTickFont(QFont("Microsoft YaHei UI", 8))
        self.plot.getAxis("bottom").setHeight(18)
        self.plot.getAxis("bottom").setTickFont(QFont("Microsoft YaHei UI", 8))
        self.plot.getAxis("bottom").setTicks(
            [[(minute, f"{minute // 60:02d}:00") for minute in range(0, 1441, 60)]]
        )
        chart_layout.addWidget(self.plot, 1)

        self.navigator = pg.PlotWidget()
        self.navigator.setStyleSheet("border: 0;")
        self.navigator.setFixedHeight(34)
        self.navigator.setMouseEnabled(x=False, y=False)
        self.navigator.hideButtons()
        self.navigator.setMenuEnabled(False)
        self.navigator.getAxis("left").hide()
        self.navigator.getAxis("bottom").setHeight(15)
        self.navigator.getAxis("bottom").setTickFont(QFont("Microsoft YaHei UI", 7))
        self.navigator.getAxis("bottom").setTicks(
            [[(minute, f"{minute // 60:02d}:00") for minute in range(0, 1441, 240)]]
        )
        self.navigator.getViewBox().setLimits(xMin=-0.5, xMax=1439.5, yMin=0)
        self.region = pg.LinearRegionItem(values=(720, 960), movable=True)
        self.region.sigRegionChanged.connect(self._on_region_changed)
        self.navigator.addItem(self.region)
        chart_layout.addWidget(self.navigator)
        layout.addWidget(self.chart_container, 1)
        self.chart_container.hide()
        self.hover_tooltip = MinuteUsageTooltip(self.plot)
        self._mouse_proxy = pg.SignalProxy(
            self.plot.scene().sigMouseMoved, rateLimit=60, slot=self._on_mouse_moved
        )
        self.plot.getViewBox().sigXRangeChanged.connect(self._on_main_range_changed)
        try:
            theme_controller().changed.connect(self._on_theme_changed)
        except RuntimeError:
            pass

    @staticmethod
    def _colors() -> tuple[QColor, QColor, QColor]:
        tokens = current_theme()
        hit = QColor(tokens.accent).lighter(145)
        miss = QColor(tokens.accent)
        output = QColor(tokens.accent).darker(130)
        return hit, miss, output

    def legend_color(self, token_type: str) -> QColor:
        return dict(zip((key for key, _label in self.SERIES), self._colors()))[token_type]

    def set_rows(self, rows: list[dict], status: str, loading: bool = False) -> None:
        values = {key: [0] * 1440 for key, _label in self.SERIES}
        for row in rows:
            try:
                minute = int(row.get("minute", -1))
            except (TypeError, ValueError):
                continue
            token_type = str(row.get("token_type", ""))
            if not 0 <= minute < 1440 or token_type not in values:
                continue
            values[token_type][minute] += max(0, int(row.get("token_amount", 0) or 0))
        signature = (status, tuple(tuple(values[key]) for key, _label in self.SERIES))
        self._values = values
        if loading and not rows:
            self._show_state("正在刷新分时估算数据…")
            return
        if status == "baseline":
            self._show_state("已建立估算基线，下一次刷新后显示分时数据")
            return
        if status == "cross_day":
            self._show_state("已跨日重建估算基线，下一次刷新后显示分时数据")
            return
        if status == "unavailable":
            self._show_state("当前平台未启用估算分时数据")
            return
        if status in {"failed", "storage_error"} and not rows:
            self._show_state("分时数据暂不可用，请刷新后重试")
            return
        if not any(sum(values[key]) for key, _label in self.SERIES):
            self._show_state("今日暂无 Token 消耗")
            return
        self.state_label.hide()
        self.chart_container.show()
        if signature != self._signature:
            self._signature = signature
            self._render_series()

    def _show_state(self, message: str) -> None:
        self._hide_hover()
        self.state_label.setText(message)
        self.state_label.show()
        self.chart_container.hide()

    def _render_series(self) -> None:
        x = list(range(1440))
        hit = self._values["PROMPT_CACHE_HIT_TOKEN"]
        miss = self._values["PROMPT_CACHE_MISS_TOKEN"]
        output = self._values["RESPONSE_TOKEN"]
        total = [output[index] + miss[index] + hit[index] for index in x]
        active_minutes = [minute for minute, amount in enumerate(total) if amount > 0]
        self._active_minutes = active_minutes
        self._sparse_mode = len(active_minutes) <= self.SPARSE_POINT_LIMIT
        self.plot.clear()
        self.navigator.clear()
        self._bars = {}
        for z_value, token_type in enumerate(
            ("RESPONSE_TOKEN", "PROMPT_CACHE_MISS_TOKEN", "PROMPT_CACHE_HIT_TOKEN"),
            start=2,
        ):
            bars = pg.BarGraphItem(x=x, y0=[0] * 1440, height=[0] * 1440, width=0.8)
            bars.setZValue(z_value)
            self.plot.addItem(bars)
            self._bars[token_type] = bars
        nav_x = active_minutes
        self._nav_bars = pg.BarGraphItem(
            x=nav_x,
            height=[total[minute] for minute in nav_x],
            width=1.0,
            pen=None,
        )
        self._nav_bars.setZValue(2)
        self.navigator.addItem(self._nav_bars)
        self.region.setZValue(1)
        self.navigator.addItem(self.region)
        self._nav_handles = pg.ScatterPlotItem(size=9, symbol="s")
        self._nav_handles.setZValue(3)
        self.navigator.addItem(self._nav_handles)
        self._hover_line = pg.InfiniteLine(angle=90, movable=False)
        self._hover_line.hide()
        self.plot.addItem(self._hover_line)
        self._hover_bar = pg.BarGraphItem(x=[0], height=[0], width=0.8)
        self._hover_bar.setZValue(8)
        self._hover_bar.hide()
        self.plot.addItem(self._hover_bar)
        maximum = max(total, default=0)
        self.plot.setYRange(0, max(1, maximum * 1.08), padding=0)
        self.navigator.setYRange(0, max(1, maximum * 1.08), padding=0)
        self._apply_x_range(active_minutes)
        self._has_initial_range = True
        self._update_bar_width(*self.plot.getViewBox().viewRange()[0])
        self._update_nav_handles()
        self.refresh_theme()
        self._apply_visibility()

    def _apply_x_range(self, active_minutes: list[int]) -> None:
        """主图按活跃分钟动态取景，导航条始终保留全天真实时间轴。"""
        self._updating_region = True
        try:
            self._x_bounds = (-0.5, 1439.5)
            self.plot.getViewBox().setLimits(
                xMin=-0.5, xMax=1439.5, minXRange=1, maxXRange=1440
            )
            self.navigator.getViewBox().setLimits(xMin=-0.5, xMax=1439.5, minXRange=1)
            self.region.setBounds(self._x_bounds)
            self.navigator.setXRange(*self._x_bounds, padding=0)
            self.navigator.getAxis("bottom").setTicks(
                [[(minute, f"{minute // 60:02d}:00") for minute in range(0, 1441, 240)]]
            )
            first = active_minutes[0]
            last = active_minutes[-1]
            span = last - first + 1
            if self._sparse_mode and span <= self.SPARSE_POINT_LIMIT:
                low, high = first - 0.5, last + 0.5
            else:
                high = min(1439.5, last + 0.5)
                low = max(-0.5, high - self.DEFAULT_VISIBLE_MINUTES)
                high = min(1439.5, low + self.DEFAULT_VISIBLE_MINUTES)
            self.region.setRegion((low, high))
            self.plot.setXRange(low, high, padding=0)
            self._update_main_ticks(low, high)
        finally:
            self._updating_region = False

    def _update_main_ticks(self, low: float, high: float) -> None:
        span = max(1.0, high - low)
        step = next(
            (candidate for candidate in (1, 2, 5, 10, 15, 30, 60, 120, 240) if span / candidate <= 8),
            240,
        )
        first = max(0, int((low + step - 1) // step) * step)
        last = min(1439, int(high))
        ticks = [
            (minute, f"{minute // 60:02d}:{minute % 60:02d}")
            for minute in range(first, last + 1, step)
        ]
        self.plot.getAxis("bottom").setTicks([ticks])

    def refresh_theme(self) -> None:
        tokens = current_theme()
        for widget in (self.plot, self.navigator):
            widget.setBackground(tokens.window)
            for axis_name in ("left", "bottom"):
                axis = widget.getAxis(axis_name)
                axis.setTextPen(pg.mkPen(tokens.subtext))
                border = QColor(tokens.border)
                border.setAlpha(96)
                axis.setPen(pg.mkPen(border))
        colors = self._colors()
        for ((token_type, _label), color) in zip(self.SERIES, colors):
            bars = self._bars.get(token_type)
            if bars is not None:
                brush = QColor(color)
                brush.setAlpha(232 if tokens.name == "dark" else 240)
                border = QColor(color).darker(112)
                border.setAlpha(230)
                bars.setOpts(brush=pg.mkBrush(brush), pen=pg.mkPen(border, width=0.8))
        if self._nav_bars is not None:
            nav_brush = QColor(tokens.accent)
            nav_brush.setAlpha(190)
            self._nav_bars.setOpts(brush=pg.mkBrush(nav_brush), pen=None)
        if self._nav_handles is not None:
            self._nav_handles.setPen(pg.mkPen(tokens.accent_hover, width=1.0))
            self._nav_handles.setBrush(pg.mkBrush(tokens.accent))
        if self._hover_line is not None:
            hover_pen = QColor(tokens.subtext)
            hover_pen.setAlpha(150)
            self._hover_line.setPen(pg.mkPen(hover_pen, width=1.0))
        if self._hover_bar is not None:
            hover_fill = QColor(tokens.accent_soft)
            hover_fill.setAlpha(70)
            self._hover_bar.setOpts(
                brush=pg.mkBrush(hover_fill), pen=pg.mkPen(tokens.value, width=1.2)
            )
        self.region.setBrush(pg.mkBrush(QColor(tokens.accent_soft)))
        for line in self.region.lines:
            line.setPen(pg.mkPen(tokens.accent, width=1.4))
        self.hover_tooltip.refresh_colors(colors)

    def set_series_visible(self, token_type: str, visible: bool) -> None:
        if token_type not in self._visible:
            return
        self._visible[token_type] = visible
        self._apply_visibility()

    def _apply_visibility(self) -> None:
        x = list(range(1440))
        baseline = [0] * 1440
        for token_type in (
            "RESPONSE_TOKEN",
            "PROMPT_CACHE_MISS_TOKEN",
            "PROMPT_CACHE_HIT_TOKEN",
        ):
            bars = self._bars.get(token_type)
            if bars is None:
                continue
            visible = self._visible[token_type]
            values = self._values[token_type]
            bars.setOpts(x=x, y0=baseline, height=values, width=self._bar_width)
            bars.setVisible(visible)
            if visible:
                baseline = [baseline[index] + values[index] for index in x]

    def _on_region_changed(self) -> None:
        if self._updating_region:
            return
        low, high = self.region.getRegion()
        self._updating_region = True
        try:
            self.plot.setXRange(low, high, padding=0)
            self._update_main_ticks(low, high)
            self._update_bar_width(low, high)
            self._update_nav_handles()
        finally:
            self._updating_region = False

    def _update_nav_handles(self) -> None:
        if self._nav_handles is None:
            return
        low, high = self.region.getRegion()
        y_range = self.navigator.getViewBox().viewRange()[1]
        center_y = (y_range[0] + y_range[1]) / 2
        self._nav_handles.setData([low, high], [center_y, center_y])

    def _on_main_range_changed(self, _view_box, ranges) -> None:
        if self._updating_region:
            return
        x_range = ranges[0] if isinstance(ranges[0], (tuple, list)) else ranges
        low, high = x_range
        self._updating_region = True
        try:
            bound_low, bound_high = self._x_bounds
            low = max(bound_low, low)
            high = min(bound_high, high)
            self.region.setRegion((low, high))
            self._update_main_ticks(low, high)
            self._update_bar_width(low, high)
        finally:
            self._updating_region = False

    def _update_bar_width(self, low: float, high: float) -> None:
        view_width = max(1.0, self.plot.getViewBox().width())
        units_per_pixel = max(1.0, high - low) / view_width
        target_pixels = min(
            self.BAR_MAX_WIDTH_PX,
            max(self.BAR_MIN_WIDTH_PX, 0.7 / units_per_pixel),
        )
        self._bar_width = min(0.84, target_pixels * units_per_pixel)
        for bars in self._bars.values():
            bars.setOpts(width=self._bar_width)
        if self._hover_bar is not None:
            self._hover_bar.setOpts(width=self._bar_width)

    def _on_theme_changed(self, _mode: str, _resolved: str) -> None:
        self.refresh_theme()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._bars:
            self._update_bar_width(*self.plot.getViewBox().viewRange()[0])

    def _on_mouse_moved(self, event) -> None:
        scene_pos = event[0]
        view_box = self.plot.getViewBox()
        if not view_box.sceneBoundingRect().contains(scene_pos):
            self._hide_hover()
            return
        point = view_box.mapSceneToView(scene_pos)
        minute = int(round(point.x()))
        total = (
            sum(self._values[key][minute] for key, _label in self.SERIES)
            if 0 <= minute < 1440
            else 0
        )
        if (
            not 0 <= minute < 1440
            or abs(point.x() - minute) > self._bar_width / 2
            or not 0 <= point.y() <= total
        ):
            self._hide_hover()
            return
        local = self.plot.mapFromScene(scene_pos)
        self._show_hover(minute, local)

    def _minute_at_x(self, value: float) -> int:
        return max(0, min(1439, int(round(value))))

    def _show_hover(self, minute: int, local: QPoint) -> None:
        values = tuple(self._values[key][minute] for key, _label in self.SERIES)
        total = sum(values)
        if self._hover_line is not None:
            self._hover_line.setPos(minute)
            self._hover_line.show()
        if self._hover_bar is not None:
            self._hover_bar.setOpts(x=[minute], height=[total], width=self._bar_width)
            self._hover_bar.show()
        self.hover_tooltip.set_values(minute, values)
        self.hover_tooltip.adjustSize()
        x = local.x() + 10
        if x + self.hover_tooltip.width() > self.plot.width() - 6:
            x = local.x() - self.hover_tooltip.width() - 10
        y = max(6, min(local.y() + 8, self.plot.height() - self.hover_tooltip.height() - 6))
        self.hover_tooltip.move(max(6, x), y)
        self.hover_tooltip.raise_()
        self.hover_tooltip.show()

    def _hide_hover(self) -> None:
        self.hover_tooltip.hide()
        if self._hover_line is not None:
            self._hover_line.hide()
        if self._hover_bar is not None:
            self._hover_bar.hide()

    def tooltip_text(self, minute: int) -> str:
        hit = self._values["PROMPT_CACHE_HIT_TOKEN"][minute]
        miss = self._values["PROMPT_CACHE_MISS_TOKEN"][minute]
        output = self._values["RESPONSE_TOKEN"][minute]
        total = hit + miss + output
        rate = "--" if hit + miss == 0 else f"{hit / (hit + miss) * 100:.1f}%"
        return (
            f"{minute // 60:02d}:{minute % 60:02d}　总计 {total:,}\n"
            f"■ 输入（命中缓存）　{hit:,}\n"
            f"■ 输入（未命中缓存）　{miss:,}\n"
            f"■ 输出　{output:,}\n"
            f"缓存命中率　{rate}"
        )

    def summary_text(self) -> str:
        hit = sum(self._values["PROMPT_CACHE_HIT_TOKEN"])
        miss = sum(self._values["PROMPT_CACHE_MISS_TOKEN"])
        output = sum(self._values["RESPONSE_TOKEN"])
        total = hit + miss + output
        if not total:
            return "今日 0 · 缓存命中 -- · 峰值 --"
        peak = max(
            range(1440),
            key=lambda minute: sum(self._values[key][minute] for key, _label in self.SERIES),
        )
        rate = "--" if hit + miss == 0 else f"{hit / (hit + miss) * 100:.1f}%"
        return f"今日 {compact_tokens(total)} · 缓存命中 {rate} · 峰值 {peak // 60:02d}:{peak % 60:02d}"


class StatisticsCard(QFrame):
    """Five equal columns matching the selected third-direction mockup."""

    LABELS = (
        "本月使用金额",
        "历史使用总金额",
        "本月 Token",
        "近 7 天使用金额",
        "近 7 天 Token",
    )

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("statisticsSection")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SECTION_HORIZONTAL_MARGIN, 0, SECTION_HORIZONTAL_MARGIN, 2)
        layout.setSpacing(3)

        line = QFrame()
        line.setObjectName("divider")
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        layout.addWidget(line)

        title = QLabel("使用统计")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(0)
        self._values: list[QLabel] = []
        self._names: list[QLabel] = []
        for index, label in enumerate(self.LABELS):
            column = QWidget()
            column_layout = QVBoxLayout(column)
            column_layout.setContentsMargins(0, 0, 0, 0)
            column_layout.setSpacing(1)
            name = QLabel(label)
            name.setObjectName("statLabel")
            name.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            if label == "历史使用总金额":
                # The provider has no lifetime total; this value is the local cache scope.
                name.setToolTip("按本机已缓存账单累计，未同步的早期账单不计入")
            value = QLabel("--")
            value.setObjectName("statValue")
            value.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            column_layout.addWidget(name)
            column_layout.addWidget(value)
            columns.addWidget(column, 1)
            self._names.append(name)
            self._values.append(value)
            if index < len(self.LABELS) - 1:
                separator = QFrame()
                separator.setObjectName("divider")
                separator.setFrameShape(QFrame.Shape.VLine)
                separator.setFixedSize(1, 46)
                columns.addWidget(separator, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(columns, 1)

    def set_data(self, data: TokenData) -> None:
        recent_rows = {str(row.get("date")): row for row in data.daily_usage}
        recent_dates = [date.today() - timedelta(days=offset) for offset in range(6, -1, -1)]
        recent_cost = sum(
            float(recent_rows.get(day.isoformat(), {}).get("cost_cny", 0) or 0)
            for day in recent_dates
        )
        recent_tokens = sum(
            int(recent_rows.get(day.isoformat(), {}).get("tokens", 0) or 0)
            for day in recent_dates
        )
        has_daily_data = data.today_tokens is not None
        values = (
            format_money(data.monthly_cost_cny),
            format_money(data.total_cost_cny),
            compact_tokens(data.monthly_usage_tokens) if data.monthly_usage_tokens is not None else "--",
            format_money(recent_cost) if has_daily_data else "--",
            compact_tokens(recent_tokens) if has_daily_data else "--",
        )
        for label, value in zip(self._values, values):
            label.setText(value)


class MainPanel(QFrame):
    settings_requested = Signal()
    refresh_requested = Signal()
    close_requested = Signal()
    theme_requested = Signal(str)
    activity_height_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("panelFrame")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(PANEL_MIN_WIDTH, ANNUAL_PANEL_HEIGHT)
        self.setMaximumSize(PANEL_MAX_WIDTH, PANEL_HEIGHT)
        self._theme_mode = "dark"
        self._resolved_theme = current_theme().name
        self._theme_feedback_message = ""
        self._button_specs: list[tuple[QToolButton, str, QStyle.StandardPixmap, str]] = []
        self._minute_provider_id = ""
        self._minute_current_date = ""
        self._minute_current_rows: list[dict] = []
        self._minute_current_status = "unavailable"
        self._minute_usage_history: dict[str, list[dict]] = {}
        self._minute_usage_days: list[str] = []
        self._minute_selected_date = ""
        self._minute_follows_latest = True

        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        self.header = DraggableHeader()
        self.header.setObjectName("panelHeader")
        self.header.setFixedHeight(HEADER_HEIGHT)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(14, 7, 12, 7)
        header_layout.setSpacing(8)

        logo = QLabel()
        logo.setPixmap(app_icon(28).pixmap(28, 28))
        logo.setFixedSize(28, 28)
        self._title_label = QLabel("TokenSpider")
        self._title_label.setObjectName("panelTitle")
        provider_id = str(config_manager.get("ACTIVE_PROVIDER", "deepseek"))
        provider_name = {"deepseek": "DeepSeek", "mimo": "小米 MiMo"}.get(
            provider_id, provider_id
        )
        self._provider_label = QLabel(f" · {provider_name}" if provider_name else "")
        self._provider_label.setObjectName("panelSubtitle")
        header_layout.addWidget(logo)
        header_layout.addWidget(self._title_label)
        header_layout.addWidget(self._provider_label)
        header_layout.addStretch(1)

        self.theme_segment = QFrame()
        self.theme_segment.setObjectName("themeSegment")
        self.theme_segment.setFixedHeight(30)
        theme_layout = QHBoxLayout(self.theme_segment)
        theme_layout.setContentsMargins(2, 2, 2, 2)
        theme_layout.setSpacing(0)
        self._theme_group = QButtonGroup(self)
        self._theme_group.setExclusive(True)
        self.light_theme_button = self._theme_button("sun", "light", "切换到浅色主题")
        self.dark_theme_button = self._theme_button("moon", "dark", "切换到深色主题")
        for button in (self.light_theme_button, self.dark_theme_button):
            self._theme_group.addButton(button)
            theme_layout.addWidget(button)
        header_layout.addWidget(self.theme_segment)

        header_divider = QFrame()
        header_divider.setObjectName("divider")
        header_divider.setFrameShape(QFrame.Shape.VLine)
        header_divider.setFixedSize(1, 22)
        header_layout.addWidget(header_divider)

        self.settings_button = self._tool_button(
            "settings", QStyle.StandardPixmap.SP_FileDialogDetailedView, "设置"
        )
        self.refresh_button = self._tool_button(
            "refresh", QStyle.StandardPixmap.SP_BrowserReload, "刷新"
        )
        self.close_button = self._tool_button(
            "close", QStyle.StandardPixmap.SP_TitleBarCloseButton, "收起", role="close"
        )
        self.settings_button.clicked.connect(self.settings_requested)
        self.refresh_button.clicked.connect(self.refresh_requested)
        self.close_button.clicked.connect(self.close_requested)
        for button in (self.settings_button, self.refresh_button, self.close_button):
            header_layout.addWidget(button)
        root.addWidget(self.header)

        body = QWidget()
        body.setObjectName("panelRoot")
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(SECTION_SPACING)

        self.top_section = QFrame()
        self.top_section.setObjectName("topSection")
        self.top_section.setFixedHeight(TOP_SECTION_HEIGHT)
        top_layout = QHBoxLayout(self.top_section)
        top_layout.setContentsMargins(SECTION_HORIZONTAL_MARGIN, 5, SECTION_HORIZONTAL_MARGIN, 5)
        top_layout.setSpacing(16)

        self.metrics_container = QWidget()
        self.metrics_container.setObjectName("metricsContainer")
        self.metrics_container.setMinimumWidth(205)
        metrics_layout = QVBoxLayout(self.metrics_container)
        metrics_layout.setContentsMargins(0, 0, 0, 0)
        metrics_layout.setSpacing(3)

        self.today_card = MetricCard("今日使用金额", "usage")
        self.today_card.set_variant("hero")
        self.balance_card = MetricCard("账户余额", "balance")
        self.balance_card.set_variant("compact")
        self.month_card = MetricCard("本月累计", "month")
        self.month_card.set_variant("compact")
        metrics_layout.addWidget(self.today_card, 3)

        compact_metrics = QHBoxLayout()
        compact_metrics.setContentsMargins(0, 0, 0, 0)
        compact_metrics.setSpacing(14)
        compact_metrics.addWidget(self.balance_card, 1)
        metric_divider = QFrame()
        metric_divider.setObjectName("divider")
        metric_divider.setFrameShape(QFrame.Shape.VLine)
        metric_divider.setFixedWidth(1)
        compact_metrics.addWidget(metric_divider)
        compact_metrics.addWidget(self.month_card, 1)
        metrics_layout.addLayout(compact_metrics, 2)
        top_layout.addWidget(self.metrics_container, 5)

        main_divider = QFrame()
        main_divider.setObjectName("divider")
        main_divider.setFrameShape(QFrame.Shape.VLine)
        main_divider.setFixedWidth(1)
        top_layout.addWidget(main_divider)

        self.trend = TrendCard()
        self.trend.setMinimumWidth(300)
        top_layout.addWidget(self.trend, 11)
        content.addWidget(self.top_section)

        self.activity_card = QFrame()
        self.activity_card.setObjectName("activitySection")
        self.activity_card.setFixedHeight(ACTIVITY_SECTION_HEIGHT)
        activity_layout = QVBoxLayout(self.activity_card)
        activity_layout.setContentsMargins(SECTION_HORIZONTAL_MARGIN, 0, SECTION_HORIZONTAL_MARGIN, 3)
        activity_layout.setSpacing(4)

        activity_divider = QFrame()
        activity_divider.setObjectName("divider")
        activity_divider.setFrameShape(QFrame.Shape.HLine)
        activity_divider.setFixedHeight(1)
        activity_layout.addWidget(activity_divider)

        activity_header = QHBoxLayout()
        activity_header.setContentsMargins(0, 0, 0, 0)
        activity_header.setSpacing(5)
        activity_title = QLabel("Token 活动")
        activity_title.setObjectName("sectionTitle")
        # 标题只占文本所需宽度，避免分时控件显示后被布局拉伸并把切换按钮推向右侧。
        activity_title.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred
        )
        self.activity_mode_group = QButtonGroup(self)
        self.activity_mode_group.setExclusive(True)
        self.annual_activity_button = self._activity_mode_button("年度活动", True)
        self.minute_activity_button = self._activity_mode_button("今日分时", False)
        self.activity_mode_group.addButton(self.annual_activity_button)
        self.activity_mode_group.addButton(self.minute_activity_button)
        self.annual_activity_button.clicked.connect(lambda: self._set_activity_view("annual"))
        self.minute_activity_button.clicked.connect(lambda: self._set_activity_view("minute"))
        self.activity_mode_segment = QFrame()
        self.activity_mode_segment.setObjectName("activityModeSegment")
        # 分时头部控件较多，固定紧凑尺寸可避免切换后被布局压缩而产生位移。
        self.activity_mode_segment.setFixedSize(148, 26)
        mode_layout = QHBoxLayout(self.activity_mode_segment)
        mode_layout.setContentsMargins(1, 1, 1, 1)
        mode_layout.setSpacing(0)
        mode_layout.addWidget(self.annual_activity_button)
        mode_layout.addWidget(self.minute_activity_button)

        self.minute_date_edit = MinuteDateEdit()
        self.minute_date_edit.setEnabled(False)
        self.minute_date_edit.dateChanged.connect(self._on_minute_date_changed)
        # 保留旧属性名，避免现有测试或外部调用方因复合控件替换而失效。
        self.minute_previous_button = self.minute_date_edit.previous_button
        self.minute_date_label = self.minute_date_edit.date_button
        self.minute_next_button = self.minute_date_edit.next_button
        self.minute_date_segment = self.minute_date_edit
        self.minute_controls: list[QWidget] = [self.minute_date_edit]
        self.minute_estimate_label = QLabel("估算")
        self.minute_estimate_label.setObjectName("muted")
        estimate_tooltip = "按刷新间隔均摊：两次成功刷新之间的累计 Token 差额，非平台原始分钟明细"
        self.minute_estimate_label.setToolTip(estimate_tooltip)
        self.activity_summary = QLabel("暂无 Token 活动")
        self.activity_summary.setObjectName("activitySummary")
        self.activity_summary.setMinimumWidth(200)
        self.activity_summary.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.activity_summary.setToolTip(estimate_tooltip)
        self._annual_activity_summary = "暂无 Token 活动"
        self._activity_view = "annual"
        activity_header.addWidget(activity_title)
        activity_header.addWidget(self.activity_mode_segment)
        for control in self.minute_controls:
            activity_header.addWidget(control)
            control.hide()
        activity_header.addWidget(self.minute_estimate_label)
        self.minute_estimate_label.hide()
        self.activity_header_spacer = QSpacerItem(
            0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        activity_header.addSpacerItem(self.activity_header_spacer)
        self.minute_legend_buttons: dict[str, QToolButton] = {}
        legend_text = {
            "PROMPT_CACHE_HIT_TOKEN": "命中缓存",
            "PROMPT_CACHE_MISS_TOKEN": "未命中",
            "RESPONSE_TOKEN": "输出",
        }
        legend_width = {
            "PROMPT_CACHE_HIT_TOKEN": 64,
            "PROMPT_CACHE_MISS_TOKEN": 54,
            "RESPONSE_TOKEN": 44,
        }
        for token_type, label in MinuteUsageChart.SERIES:
            button = QToolButton()
            button.setObjectName("minuteLegendButton")
            button.setText(legend_text[token_type])
            button.setCheckable(True)
            button.setChecked(True)
            button.setIconSize(QSize(7, 7))
            button.setFixedWidth(legend_width[token_type])
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setToolTip(f"显示/隐藏{label}（不改变原始估算数据）")
            button.clicked.connect(
                lambda checked, value=token_type: self.minute_chart.set_series_visible(value, checked)
            )
            self.minute_legend_buttons[token_type] = button
            activity_header.addWidget(button)
            button.hide()
        activity_header.addWidget(self.activity_summary)
        activity_layout.addLayout(activity_header)

        self.activity_scroll = QScrollArea()
        self.activity_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.activity_scroll.setWidgetResizable(True)
        self.activity_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.activity_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Do not style the viewport locally: Qt cascades such rules into the
        # heatmap tooltip. The application palette supplies the themed surface.
        self.activity = TokenActivityHeatmap()
        self._fit_activity_heatmap()
        self.activity_scroll.setWidget(self.activity)
        # 年度页需要铺满与分时图共用的堆栈，避免固定高度后在底部露出一块背景。
        self.activity_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.minute_chart = MinuteUsageChart()
        self.activity_stack = QStackedWidget()
        self.activity_stack.setObjectName("activityStack")
        self.activity_stack.addWidget(self.activity_scroll)
        self.activity_stack.addWidget(self.minute_chart)
        activity_layout.addWidget(self.activity_stack)
        content.addWidget(self.activity_card)
        self.middle_section = self.activity_card

        self.statistics = StatisticsCard()
        self.statistics.setFixedHeight(STATISTICS_SECTION_HEIGHT)
        content.addWidget(self.statistics)
        self.bottom_section = self.statistics

        footer_widget = QWidget()
        footer_widget.setObjectName("statusBar")
        footer_widget.setFixedHeight(STATUS_SECTION_HEIGHT)
        footer = QHBoxLayout(footer_widget)
        footer.setContentsMargins(SECTION_HORIZONTAL_MARGIN, 0, SECTION_HORIZONTAL_MARGIN, 0)
        footer.setSpacing(8)
        self.status_dot = StatusDot()
        self.status_text = QLabel("等待连接")
        self.status_text.setObjectName("statusText")
        self.updated_text = QLabel()
        self.updated_text.setObjectName("statusText")
        footer.addWidget(self.status_dot)
        footer.addWidget(self.status_text)
        footer.addStretch(1)
        footer.addWidget(self.updated_text)
        content.addWidget(footer_widget)
        root.addWidget(body, 1)

        configured_mode = str(config_manager.get("UI_THEME", "dark"))
        self.set_theme_mode(configured_mode, current_theme().name)
        try:
            theme_controller().changed.connect(self._on_theme_changed)
        except RuntimeError:
            # Preserve standalone construction compatibility for callers that do
            # not own application startup; the desktop app configures this first.
            pass
        self._refresh_icons()
        self._set_activity_view("annual")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "activity"):
            self._fit_activity_heatmap()
        if hasattr(self, "activity_summary"):
            self._update_activity_header_visibility()

    def _fit_activity_heatmap(self) -> None:
        # At the supported 640 px minimum, the full 53-week calendar must stay
        # visible without a scrollbar stealing vertical room from the last row.
        compact = self.width() < 775
        self.activity.CELL = 9 if compact else 11
        self.activity.MIN_HORIZONTAL_GAP = 1 if compact else 2
        required_width = (
            self.activity.LEFT
            + self.activity.period.week_count
            * (self.activity.CELL + self.activity.MIN_HORIZONTAL_GAP)
            + 12
        )
        self.activity.setMinimumWidth(required_width)
        self.activity.update()

    def _activity_mode_button(self, text: str, checked: bool) -> QToolButton:
        button = QToolButton()
        button.setObjectName("activityModeButton")
        button.setText(text)
        button.setCheckable(True)
        button.setChecked(checked)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setFixedSize(72, 22)
        return button

    def _on_minute_date_changed(self, value: QDate) -> None:
        selected_date = value.toString("yyyy-MM-dd")
        if selected_date not in self._minute_usage_days:
            return
        self._minute_selected_date = selected_date
        self._minute_follows_latest = selected_date == self._minute_current_date
        self._render_minute_date(loading=False)

    def _update_minute_data(self, data: TokenData, loading: bool) -> None:
        previous_current_date = self._minute_current_date
        was_following_latest = (
            self._minute_follows_latest
            or not self._minute_selected_date
            or self._minute_selected_date == previous_current_date
        )
        provider_id = (
            data.per_provider[0].provider_id
            if data.per_provider
            else str(config_manager.get("ACTIVE_PROVIDER", ""))
        )
        provider_changed = bool(
            self._minute_provider_id
            and provider_id
            and provider_id != self._minute_provider_id
        )
        if provider_id:
            self._minute_provider_id = provider_id

        self._minute_current_date = data.minute_usage_date
        self._minute_current_rows = data.minute_usage
        self._minute_current_status = data.minute_usage_status
        self._minute_usage_history = dict(data.minute_usage_history)
        self._minute_usage_days = []

        current_date = QDate.fromString(data.minute_usage_date, "yyyy-MM-dd")
        if current_date.isValid():
            try:
                retention_days = max(
                    1, int(config_manager.get("MINUTE_USAGE_RETENTION_DAYS", 3))
                )
            except (TypeError, ValueError):
                retention_days = 3
            minimum_date = current_date.addDays(-(retention_days - 1))
            self._minute_usage_days = [
                minimum_date.addDays(offset).toString("yyyy-MM-dd")
                for offset in range(retention_days)
            ]

            if (
                provider_changed
                or not self._minute_selected_date
                or self._minute_selected_date not in self._minute_usage_days
                or (previous_current_date != data.minute_usage_date and was_following_latest)
            ):
                self._minute_selected_date = data.minute_usage_date
            self._minute_follows_latest = (
                self._minute_selected_date == self._minute_current_date
            )

            blocker = QSignalBlocker(self.minute_date_edit)
            self.minute_date_edit.setDateRange(minimum_date, current_date)
            self.minute_date_edit.setDate(
                QDate.fromString(self._minute_selected_date, "yyyy-MM-dd")
            )
            del blocker
        else:
            self._minute_selected_date = ""
            self._minute_follows_latest = True

        self.minute_date_edit.setEnabled(
            current_date.isValid() and data.minute_usage_status != "unavailable"
        )
        self._render_minute_date(loading)

    def _render_minute_date(self, loading: bool) -> None:
        selected_date = self._minute_selected_date
        if selected_date == self._minute_current_date:
            rows = self._minute_current_rows
            status = self._minute_current_status
            status_hint = {
                "failed": "；刷新失败，当前显示最近结果",
                "storage_error": "；分时缓存读取失败",
                "adjusted": "；平台已校正当前数据",
            }.get(status, "")
            tooltip = f"选择分时日期{status_hint}"
        else:
            rows = self._minute_usage_history.get(selected_date, [])
            status = "recorded" if rows else "empty"
            tooltip = f"选择分时日期；当前查看 {selected_date}"
        self.minute_date_edit.date_button.setToolTip(tooltip)
        self.minute_date_edit.date_button.setAccessibleName(tooltip)
        self.minute_chart.set_rows(
            rows,
            status,
            loading=loading and selected_date == self._minute_current_date,
        )
        self.activity_summary.setText(
            self.minute_chart.summary_text()
            if self._activity_view == "minute"
            else self._annual_activity_summary
        )

    def _set_activity_view(self, view: str) -> None:
        minute_view = view == "minute"
        self._activity_view = view
        if not minute_view:
            self.minute_date_edit.popup.close()
        activity_height = (
            ACTIVITY_SECTION_HEIGHT if minute_view else ANNUAL_ACTIVITY_SECTION_HEIGHT
        )
        panel_height = PANEL_HEIGHT if minute_view else ANNUAL_PANEL_HEIGHT
        # 两种视图的内容高度不同；同步收紧面板可消除年度页底部占位，同时保留分时图空间。
        self.activity_card.setFixedHeight(activity_height)
        self.setFixedHeight(panel_height)
        self.activity_stack.setCurrentIndex(1 if minute_view else 0)
        self.annual_activity_button.setChecked(not minute_view)
        self.minute_activity_button.setChecked(minute_view)
        for control in self.minute_controls:
            control.setVisible(minute_view)
        for button in self.minute_legend_buttons.values():
            button.setVisible(minute_view)
        self.activity_summary.setText(
            self.minute_chart.summary_text()
            if minute_view
            else self._annual_activity_summary
        )
        self.activity_card.layout().invalidate()
        self._refresh_minute_control_colors()
        self._update_activity_header_visibility()
        self.activity_height_changed.emit(panel_height)

    def _update_activity_header_visibility(self) -> None:
        minute_view = self._activity_view == "minute"
        self.activity_summary.setVisible(not minute_view or self.width() >= 775)
        self.minute_estimate_label.setVisible(minute_view and self.width() < 775)

    def _refresh_minute_control_colors(self) -> None:
        if not hasattr(self, "minute_chart"):
            return
        for token_type, button in self.minute_legend_buttons.items():
            color = self.minute_chart.legend_color(token_type)
            swatch = QPixmap(8, 8)
            swatch.fill(color)
            button.setIcon(QIcon(swatch))

    def _theme_button(self, icon_name: str, mode: str, tooltip: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName("themeButton")
        button.setProperty("themeValue", mode)
        button.setCheckable(True)
        button.setAutoRaise(True)
        button.setFixedSize(24, 24)
        button.setIconSize(QSize(14, 14))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.clicked.connect(lambda _checked=False, value=mode: self._request_theme(value))
        button._theme_icon_name = icon_name
        return button

    def _tool_button(
        self,
        name: str,
        standard: QStyle.StandardPixmap,
        tooltip: str,
        role: str = "",
    ) -> QToolButton:
        button = QToolButton()
        button.setIconSize(QSize(18, 18))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setObjectName("panelToolButton")
        if role:
            button.setProperty("role", role)
        button.setFixedSize(32, 32)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._button_specs.append((button, name, standard, role))
        return button

    def _request_theme(self, mode: str) -> None:
        self.set_theme_mode(mode, mode)
        self.theme_requested.emit(mode)

    def set_theme_mode(self, mode: str, resolved: str | None = None) -> None:
        """Synchronize the header selector with the configured and resolved mode."""
        normalized_mode = mode if mode in {"system", "light", "dark"} else "dark"
        normalized_resolved = resolved if resolved in {"light", "dark"} else current_theme().name
        if normalized_resolved not in {"light", "dark"}:
            normalized_resolved = "dark"
        self._theme_mode = normalized_mode
        self._resolved_theme = normalized_resolved
        self._theme_feedback_message = ""

        is_light = normalized_resolved == "light"
        self.light_theme_button.setChecked(is_light)
        self.dark_theme_button.setChecked(not is_light)
        for button, selected in (
            (self.light_theme_button, is_light),
            (self.dark_theme_button, not is_light),
        ):
            button.setProperty("selected", selected)
            button.style().unpolish(button)
            button.style().polish(button)

        if normalized_mode == "system":
            theme_name = "浅色" if is_light else "深色"
            segment_tip = f"跟随系统（当前为{theme_name}主题）"
            self.light_theme_button.setToolTip(f"{segment_tip}；点击固定为浅色主题")
            self.dark_theme_button.setToolTip(f"{segment_tip}；点击固定为深色主题")
        else:
            segment_tip = "浅色主题" if is_light else "深色主题"
            self.light_theme_button.setToolTip("浅色主题（当前）" if is_light else "切换到浅色主题")
            self.dark_theme_button.setToolTip("深色主题（当前）" if not is_light else "切换到深色主题")
        self.theme_segment.setToolTip(segment_tip)
        self.theme_segment.setAccessibleDescription(segment_tip)

    def set_theme_feedback(self, message: str, tone: str = "danger") -> None:
        """Expose persistence feedback without replacing provider connection status."""
        self._theme_feedback_message = message.strip()
        self.theme_segment.setProperty("feedbackTone", tone)
        if self._theme_feedback_message:
            self.theme_segment.setToolTip(self._theme_feedback_message)
            self.light_theme_button.setToolTip(self._theme_feedback_message)
            self.dark_theme_button.setToolTip(self._theme_feedback_message)

    def _on_theme_changed(self, mode: str, resolved: str) -> None:
        self.set_theme_mode(mode, resolved)
        self.status_dot.refresh_theme()
        self.minute_chart.refresh_theme()
        self.minute_date_edit.refresh_theme()
        self._refresh_minute_control_colors()
        self._refresh_icons()
        self.update()

    def _refresh_icons(self) -> None:
        tokens = current_theme()
        for button, name, standard, role in self._button_specs:
            active_color = tokens.danger if role == "close" else tokens.accent_hover
            icon = fluent_icon(name, active_color=active_color)
            button.setIcon(icon if not icon.isNull() else self.style().standardIcon(standard))
        for button in (self.light_theme_button, self.dark_theme_button):
            icon = fluent_icon(button._theme_icon_name, size=14, active_color=tokens.text)
            button.setIcon(icon)

    def set_refreshing(self, refreshing: bool) -> None:
        self.refresh_button.setEnabled(not refreshing)
        self.refresh_button.setToolTip("刷新中" if refreshing else "刷新")

    def update_data(self, data: TokenData, loading: bool = False) -> None:
        money = lambda value: "--" if loading else format_money(value)
        tokens = lambda value: "--" if loading or value is None else compact_tokens(int(value))
        if data.per_provider:
            provider_name = data.per_provider[0].provider_name
            self._provider_label.setText(f" · {provider_name}")

        self.today_card.set_title("今日使用金额")
        self.balance_card.set_title("账户余额")
        self.month_card.set_title("本月累计")
        self.today_card.set_values(money(data.today_cost_cny), tokens(data.today_tokens), "")
        self.balance_card.set_values(
            money(data.balance_cny),
            f"约 {tokens(data.balance_tokens)}" if data.balance_tokens else "账户可用余额",
            "",
        )
        self.month_card.set_values(
            money(data.monthly_cost_cny),
            tokens(data.monthly_usage_tokens),
            "",
        )

        self.activity.set_activity(data.daily_usage)
        source_days = [day for day in self.activity.days if day.has_source_data]
        total = sum(day.token_count for day in source_days)
        if not source_days:
            summary = "暂无 Token 活动"
        else:
            first = min(day.date for day in source_days)
            if first > self.activity.period.start:
                summary = f"数据始于 {first.isoformat()} · 共 {compact_tokens(total)}"
            else:
                summary = f"过去 12 个月共使用 {compact_tokens(total)}"
        self._annual_activity_summary = summary

        self._update_minute_data(data, loading)
        for button in self.minute_legend_buttons.values():
            button.setEnabled(data.minute_usage_status != "unavailable")
        self._refresh_minute_control_colors()

        self.trend.set_rows(data.daily_usage)
        self.statistics.set_data(data)
        status, _color = self.status_summary(data, loading)
        self.status_text.setText(status)
        self.status_dot.set_role(self.status_role(data, loading))
        self.updated_text.setText(self.relative_update_time(data))

    @staticmethod
    def status_role(data: TokenData, loading: bool = False) -> str:
        if loading:
            return "accent"
        codes = {error.code for error in data.errors}
        if "NOT_CONFIGURED" in codes or data.status == "not_configured":
            return "warning"
        if codes & {"AUTH_EXPIRED", "NETWORK_TIMEOUT", "NETWORK_ERROR", "SERVER_ERROR"}:
            return "danger"
        if data.status == "partial":
            return "warning"
        if data.status == "error":
            return "danger"
        if data.status == "ok":
            return "success"
        return "accent"

    @staticmethod
    def status_summary(data: TokenData, loading: bool = False) -> tuple[str, str]:
        theme = current_theme()
        if loading:
            return "正在更新", theme.accent
        codes = {error.code for error in data.errors}
        if "NOT_CONFIGURED" in codes:
            return "尚未配置 Token/Cookie，请前往设置", theme.warning
        if "AUTH_EXPIRED" in codes:
            return "认证信息已失效，请重新配置", theme.danger
        if codes & {"NETWORK_TIMEOUT", "NETWORK_ERROR"}:
            return "网络连接失败", theme.danger
        if "SERVER_ERROR" in codes:
            return "API 服务异常", theme.danger
        if data.status == "not_configured":
            return "尚未配置凭据，请前往设置", theme.warning
        if data.status == "ok" and data.today_tokens is None:
            return "连接正常，平台未提供按日明细", theme.success
        if data.status == "ok" and not any(day.get("tokens", 0) for day in data.daily_usage):
            return "连接正常，暂无 Token 活动", theme.success
        return {
            "ok": ("连接正常", theme.success),
            "partial": ("部分数据异常，显示可用数据", theme.warning),
            "error": ("连接异常", theme.danger),
        }.get(data.status, ("等待连接", theme.accent))

    @staticmethod
    def relative_update_time(data: TokenData) -> str:
        if not data.last_success_at:
            return "等待首次更新"
        seconds = max(0, int((datetime.now() - data.last_success_at).total_seconds()))
        if seconds < 60:
            return "数据更新于刚刚"
        minutes = seconds // 60
        if minutes < 60:
            return f"数据更新于 {minutes} 分钟前"
        return f"数据更新于 {minutes // 60} 小时前"
