"""Fluent-style monitoring panel built from PySide6 widgets."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pyqtgraph as pg
from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QGradient, QLinearGradient, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QStyle,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from data.store import TokenData
from ui.activity import compact_tokens
from ui.qt_heatmap import TokenActivityHeatmap
from ui.qt_theme import (
    ACTIVITY_CARD_HEIGHT,
    CARD_PADDING,
    C_ACCENT,
    C_ACCENT_2,
    C_BORDER,
    C_GREEN,
    C_PALE_BLUE,
    C_RED,
    C_SUBTEXT,
    C_TEXT,
    C_TIME,
    C_YELLOW,
    HEADER_HEIGHT,
    LOWER_CARD_HEIGHT,
    METRIC_CARD_HEIGHT,
    PANEL_PADDING,
    SECTION_SPACING,
    STATUS_BAR_HEIGHT,
    app_icon,
    fluent_icon,
    metric_icon,
)


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


def _smooth_curve(values: list[float], samples_per_segment: int = 12) -> tuple[list[float], list[float]]:
    if len(values) < 2:
        return [float(index) for index in range(len(values))], list(values)

    deltas = [right - left for left, right in zip(values, values[1:])]
    slopes = [deltas[0]]
    for previous, following in zip(deltas, deltas[1:]):
        # Zero the tangent at turning points and use a harmonic mean elsewhere,
        # so smoothing cannot invent spikes between the real daily amounts.
        if previous * following <= 0:
            slopes.append(0.0)
        else:
            slopes.append(2 * previous * following / (previous + following))
    slopes.append(deltas[-1])

    smooth_x: list[float] = []
    smooth_y: list[float] = []
    for index, (left, right) in enumerate(zip(values, values[1:])):
        for sample in range(samples_per_segment):
            t = sample / samples_per_segment
            t2 = t * t
            t3 = t2 * t
            smooth_x.append(index + t)
            smooth_y.append(
                (2 * t3 - 3 * t2 + 1) * left
                + (t3 - 2 * t2 + t) * slopes[index]
                + (-2 * t3 + 3 * t2) * right
                + (t3 - t2) * slopes[index + 1]
            )
    smooth_x.append(float(len(values) - 1))
    smooth_y.append(values[-1])
    return smooth_x, smooth_y


class DraggableHeader(QFrame):
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


class StatusDot(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._color = QColor(C_ACCENT)
        self.setFixedSize(12, 12)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(2, 2, 8, 8)
        painter.end()


class MetricCard(QFrame):
    def __init__(self, title: str, icon_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(CARD_PADDING, 7, CARD_PADDING, 7)
        layout.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        icon = QLabel()
        icon.setObjectName("metricIcon")
        icon.setFixedSize(28, 28)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setPixmap(metric_icon(icon_name))
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        self.title_label = title_label
        title_row.addWidget(icon)
        title_row.addWidget(title_label)
        title_row.addStretch(1)
        self.value = QLabel("--")
        self.value.setObjectName("metricValue")
        self.detail = QLabel()
        self.detail.setObjectName("metricDetail")
        self.footer = QLabel()
        self.footer.setObjectName("muted")
        layout.addLayout(title_row)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)
        layout.addStretch(1)
        layout.addWidget(self.footer)
        self.footer.hide()

    def set_values(self, value: str, detail: str = "", footer: str = "") -> None:
        self.value.setText(value)
        self.detail.setText(detail)
        self.footer.setText(footer)
        self.footer.setVisible(bool(footer))

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)


class TrendCard(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(CARD_PADDING, 8, CARD_PADDING, 6)
        layout.setSpacing(2)
        self.title = QLabel("近 7 天使用金额")
        self.title.setObjectName("sectionTitle")
        layout.addWidget(self.title)
        self.plot = pg.PlotWidget(
            background=None,
            axisItems={"left": MoneyAxis(orientation="left")},
        )
        self.plot.setStyleSheet("background: transparent; border: 0;")
        self.plot.viewport().setStyleSheet("background: transparent;")
        # The compact lower card gives the plot the remaining height while
        # preserving its title, three grid intervals, and complete date axis.
        self.plot.setMinimumHeight(78)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        self.plot.setMenuEnabled(False)
        self.plot.showGrid(x=False, y=True, alpha=0.10)
        axis_font = QFont("Microsoft YaHei UI", 8)
        self.plot.getAxis("left").setTickFont(axis_font)
        self.plot.getAxis("bottom").setTickFont(axis_font)
        self.plot.getAxis("bottom").setStyle(hideOverlappingLabels=False)
        self.plot.getAxis("left").setStyle(hideOverlappingLabels=False)
        self.plot.getAxis("left").setTextPen(pg.mkPen(C_TIME))
        self.plot.getAxis("bottom").setTextPen(pg.mkPen(C_TIME))
        self.plot.getAxis("left").setPen(pg.mkPen(C_BORDER))
        self.plot.getAxis("bottom").setPen(pg.mkPen(C_BORDER))
        self.plot.getAxis("left").setWidth(52)
        self.plot.getAxis("left").enableAutoSIPrefix(False)
        self.plot.getAxis("bottom").setHeight(22)
        # Leave half a day at both edges so the first and last points are not
        # clamped to the plot border while all dates keep their integer x positions.
        self.plot.getViewBox().setLimits(xMin=-0.5, xMax=6.5, yMin=0)
        self._dates: list[date] = []
        self._values: list[float] = []
        self._series: pg.PlotDataItem | None = None
        self._hover_marker: pg.ScatterPlotItem | None = None
        self._mouse_proxy = pg.SignalProxy(
            self.plot.scene().sigMouseMoved,
            rateLimit=60,
            slot=self._on_mouse_moved,
        )
        layout.addWidget(self.plot, 1)

    def set_rows(self, rows: list[dict], today: date | None = None) -> None:
        current = today or date.today()
        by_date = {str(row.get("date")): row for row in rows}
        self._dates = [current - timedelta(days=offset) for offset in range(6, -1, -1)]
        self._values = [
            float(by_date.get(day.isoformat(), {}).get("cost_cny", 0) or 0)
            for day in self._dates
        ]
        self.plot.clear()

        area_gradient = QLinearGradient(0, 0, 0, 1)
        area_gradient.setCoordinateMode(QGradient.CoordinateMode.ObjectMode)
        area_gradient.setColorAt(0.0, QColor(39, 103, 229, 82))
        area_gradient.setColorAt(0.55, QColor(39, 103, 229, 36))
        area_gradient.setColorAt(1.0, QColor(39, 103, 229, 0))
        curve_x, curve_y = _smooth_curve(self._values)
        self._series = self.plot.plot(
            curve_x,
            curve_y,
            pen=pg.mkPen(C_ACCENT, width=2),
            antialias=True,
            fillLevel=0,
            brush=QBrush(area_gradient),
        )
        self.plot.plot(
            list(range(7)),
            self._values,
            pen=None,
            symbol="o",
            symbolSize=6,
            symbolBrush=pg.mkBrush(C_PALE_BLUE),
            symbolPen=pg.mkPen(C_ACCENT, width=1),
            antialias=True,
        )
        self._hover_marker = pg.ScatterPlotItem(
            size=9,
            brush=pg.mkBrush(C_ACCENT_2),
            pen=pg.mkPen(C_PALE_BLUE, width=1.5),
        )
        self._hover_marker.hide()
        self.plot.addItem(self._hover_marker)
        self.plot.getAxis("bottom").setTicks(
            [[(index, day.strftime("%m/%d")) for index, day in enumerate(self._dates)]]
        )
        # Match the ViewBox limits so the intended edge spacing is not clamped away.
        self.plot.setXRange(-0.5, 6.5, padding=0)
        maximum = max(self._values, default=0)
        tick_max = max(0.01, maximum * 1.18)
        self.plot.setYRange(0, tick_max * 1.18, padding=0)
        self.plot.getAxis("left").setTicks(
            [[
                (tick_max * index / 3, format_money_axis(tick_max * index / 3))
                for index in range(1, 4)
            ]]
        )

    def _on_mouse_moved(self, event) -> None:
        scene_pos = event[0]
        if not self.plot.sceneBoundingRect().contains(scene_pos):
            self._hide_hover()
            return
        point = self.plot.getViewBox().mapSceneToView(scene_pos)
        index = int(round(point.x()))
        if not 0 <= index < len(self._values) or abs(point.x() - index) > 0.45:
            self._hide_hover()
            return
        if self._hover_marker is not None:
            self._hover_marker.setData([index], [self._values[index]])
            self._hover_marker.show()
        local = self.plot.mapFromScene(scene_pos)
        QToolTip.showText(
            self.plot.mapToGlobal(local),
            self.tooltip_text(index),
            self.plot,
        )

    def _hide_hover(self) -> None:
        if self._hover_marker is not None:
            self._hover_marker.hide()
        QToolTip.hideText()

    def tooltip_text(self, index: int) -> str:
        return (
            f"{self._dates[index].isoformat()}\n"
            f"使用金额：{format_money(self._values[index])}"
        )


class StatisticsCard(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(CARD_PADDING, 8, CARD_PADDING, 6)
        layout.setSpacing(0)
        title = QLabel("使用统计")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self._values: list[QLabel] = []
        labels = (
            "本月使用金额",
            "历史使用总金额",
            "本月 Token",
            "近 7 天使用金额",
            "近 7 天 Token",
        )
        for label in labels:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            name = QLabel(label)
            name.setObjectName("muted")
            if label == "历史使用总金额":
                # 平台不提供账户全生命周期总额，明确提示本地缓存口径，避免误解。
                name.setToolTip("按本机已缓存账单累计，未同步的早期账单不计入")
            value = QLabel("--")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            value.setStyleSheet(f"color: {C_TEXT}; font-weight: 600;")
            value.setMinimumWidth(70)
            row_layout.addWidget(name)
            row_layout.addStretch(1)
            row_layout.addWidget(value)
            layout.addWidget(row)
            self._values.append(value)
        layout.addStretch(1)

    def set_data(self, data: TokenData) -> None:
        recent_rows = {
            str(row.get("date")): row for row in data.daily_usage
        }
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

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("panelFrame")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumWidth(640)
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        self.header = DraggableHeader()
        self.header.setFixedHeight(HEADER_HEIGHT)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(18, 9, 12, 8)
        header_layout.setSpacing(9)
        logo = QLabel()
        logo.setPixmap(app_icon(32).pixmap(32, 32))
        self._title_label = QLabel("API 使用监控")
        self._title_label.setObjectName("panelTitle")
        header_layout.addWidget(logo)
        header_layout.addWidget(self._title_label)
        header_layout.addStretch(1)
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
        for button in (
            self.settings_button,
            self.refresh_button,
            self.close_button,
        ):
            header_layout.addWidget(button)
        root.addWidget(self.header)

        body = QWidget()
        body.setObjectName("panelRoot")
        content = QVBoxLayout(body)
        content.setContentsMargins(PANEL_PADDING, 8, PANEL_PADDING, 8)
        content.setSpacing(SECTION_SPACING)

        metrics = QHBoxLayout()
        metrics.setSpacing(SECTION_SPACING)
        self.today_card = MetricCard("今日使用金额", "usage")
        self.balance_card = MetricCard("账户余额", "balance")
        self.month_card = MetricCard("本月累计", "month")
        for card in (self.today_card, self.balance_card, self.month_card):
            card.setFixedHeight(METRIC_CARD_HEIGHT)
            metrics.addWidget(card, 1)
        content.addLayout(metrics)

        self.activity_card = QFrame()
        self.activity_card.setObjectName("card")
        activity_layout = QVBoxLayout(self.activity_card)
        activity_layout.setContentsMargins(CARD_PADDING, 10, CARD_PADDING, 6)
        activity_layout.setSpacing(4)
        self.activity_card.setFixedHeight(ACTIVITY_CARD_HEIGHT)
        activity_header = QHBoxLayout()
        activity_title = QLabel("Token 活动")
        activity_title.setObjectName("sectionTitle")
        self.activity_summary = QLabel("暂无 Token 活动")
        self.activity_summary.setObjectName("muted")
        activity_header.addWidget(activity_title)
        activity_header.addStretch(1)
        activity_header.addWidget(self.activity_summary)
        activity_layout.addLayout(activity_header)
        self.activity_scroll = QScrollArea()
        self.activity_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.activity_scroll.setWidgetResizable(True)
        self.activity_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.activity_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.activity_scroll.viewport().setStyleSheet("background: transparent;")
        self.activity = TokenActivityHeatmap()
        self.activity_scroll.setWidget(self.activity)
        self.activity_scroll.setFixedHeight(self.activity.height())
        activity_layout.addWidget(self.activity_scroll)
        content.addWidget(self.activity_card)

        lower_container = QWidget()
        lower_container.setFixedHeight(LOWER_CARD_HEIGHT)
        lower = QHBoxLayout(lower_container)
        lower.setContentsMargins(0, 0, 0, 0)
        lower.setSpacing(SECTION_SPACING)
        self.trend = TrendCard()
        self.statistics = StatisticsCard()
        self.trend.setFixedHeight(LOWER_CARD_HEIGHT)
        self.statistics.setFixedHeight(LOWER_CARD_HEIGHT)
        lower.addWidget(self.trend, 2)
        lower.addWidget(self.statistics, 1)
        content.addWidget(lower_container)

        footer_widget = QWidget()
        footer_widget.setObjectName("statusBar")
        footer_widget.setFixedHeight(STATUS_BAR_HEIGHT)
        footer = QHBoxLayout(footer_widget)
        footer.setContentsMargins(4, 5, 4, 0)
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

    def _tool_button(
        self,
        name: str,
        standard: QStyle.StandardPixmap,
        tooltip: str,
        role: str = "",
    ) -> QToolButton:
        button = QToolButton()
        icon = fluent_icon(name, active_color=C_RED if role == "close" else C_ACCENT_2)
        button.setIcon(icon if not icon.isNull() else self.style().standardIcon(standard))
        button.setIconSize(QSize(18, 18))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setObjectName("panelToolButton")
        if role:
            button.setProperty("role", role)
        button.setFixedSize(32, 32)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        return button

    def set_refreshing(self, refreshing: bool) -> None:
        self.refresh_button.setEnabled(not refreshing)
        self.refresh_button.setToolTip("刷新中" if refreshing else "刷新")

    def update_data(self, data: TokenData, loading: bool = False) -> None:
        money = lambda value: "--" if loading else format_money(value)
        tokens = lambda value: "--" if loading or value is None else compact_tokens(int(value))
        # Show the active provider name in the title bar.
        provider_id = ""
        if data.per_provider:
            provider_id = data.per_provider[0].provider_id
            provider_name = data.per_provider[0].provider_name
            self._title_label.setText(f"API 使用监控 · {provider_name}")
        self.today_card.set_title("今日使用金额")
        self.balance_card.set_title("账户余额")
        self.month_card.set_title("本月累计")
        self.today_card.set_values(
            money(data.today_cost_cny),
            tokens(data.today_tokens),
            "",
        )
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
        self.activity_summary.setText(summary)
        self.trend.set_rows(data.daily_usage)
        self.statistics.set_data(data)
        status, color = self.status_summary(data, loading)
        self.status_text.setText(status)
        self.status_dot.set_color(color)
        self.updated_text.setText(self.relative_update_time(data))

    @staticmethod
    def status_summary(data: TokenData, loading: bool = False) -> tuple[str, str]:
        if loading:
            return "正在更新", C_ACCENT
        codes = {error.code for error in data.errors}
        if "NOT_CONFIGURED" in codes:
            return "尚未配置 Token/Cookie，请前往设置", C_YELLOW
        if "AUTH_EXPIRED" in codes:
            return "认证信息已失效，请重新配置", C_RED
        if codes & {"NETWORK_TIMEOUT", "NETWORK_ERROR"}:
            return "网络连接失败", C_RED
        if "SERVER_ERROR" in codes:
            return "API 服务异常", C_RED
        if data.status == "not_configured":
            return "尚未配置凭据，请前往设置", C_YELLOW
        if data.status == "ok" and data.today_tokens is None:
            return "连接正常，平台未提供按日明细", C_GREEN
        if data.status == "ok" and not any(day.get("tokens", 0) for day in data.daily_usage):
            return "连接正常，暂无 Token 活动", C_GREEN
        return {
            "ok": ("连接正常", C_GREEN),
            "partial": ("部分数据异常，显示可用数据", C_YELLOW),
            "error": ("连接异常", C_RED),
        }.get(data.status, ("等待连接", C_ACCENT))

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
