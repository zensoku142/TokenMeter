"""Request output speed, grouped by time and model without inventing idle samples."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from html import escape
from typing import cast

import pyqtgraph as pg
from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from ui.i18n import bind_text, current_language, tr
from ui.qt_panel import (
    MinuteUsageTooltip,
    create_usage_navigator,
    create_usage_plot,
    refresh_usage_plot_theme,
    show_usage_tooltip,
)
from ui.qt_theme import current_theme


class _TimeAxis(pg.AxisItem):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.minutes = []

    def tickStrings(self, values, scale, spacing):
        labels = []
        for value in values:
            index = round(value)
            minute = self.minutes[index] if abs(value - index) < 0.0001 and 0 <= index < len(self.minutes) else None
            labels.append(f"{minute // 60:02d}:{minute % 60:02d}" if minute is not None else "")
        return labels


class TokenSpeedChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        self._points = []
        self._interval = 5
        self._signature = None
        self._items = []
        self._candles = []
        self._wicks = []
        self._candle_style = None
        self._selected_model = None
        self._model_colors = {}
        self._hover_primary = {}
        self._hover_key = None
        self._pointer_inside = False
        self._hover_marker = None
        self._syncing_region = False
        self._axis_high = 0.5
        self._has_initial_range = False
        self._range_scope = None
        self._latest = None
        self._latest_cached = False
        self.legend_buttons = {}
        self._model_group = QButtonGroup(self)
        self._model_group.setExclusive(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        toolbar = QHBoxLayout()
        self.legend_scroll = QScrollArea()
        self.legend_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.legend_scroll.setWidgetResizable(True)
        self.legend_scroll.setFixedHeight(26)
        self.legend_scroll.setMinimumWidth(60)
        self.legend_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.legend_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.legend_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.legend_content = QWidget()
        self.legend_layout = QHBoxLayout(self.legend_content)
        self.legend_layout.setContentsMargins(0, 0, 0, 0)
        self.legend_layout.setSpacing(6)
        self.legend_layout.addStretch(1)
        self.legend_layout.addStretch(1)
        self.legend_scroll.setWidget(self.legend_content)
        self._legend_viewport = self.legend_scroll.viewport()
        self._legend_viewport.installEventFilter(self)
        toolbar.addWidget(self.legend_scroll, 1)
        direction = bind_text(QLabel(), "实心升 · 空心降")
        direction.setObjectName("muted")
        toolbar.addWidget(direction)
        self.ma_label = QLabel("MA5")
        bind_text(self.ma_label, "最近 5 个有效时段末次速度的平均值", method="setToolTip")
        toolbar.addWidget(self.ma_label)
        toolbar.addStretch()
        self.summary = QLabel()
        self.summary.setObjectName("activitySummary")
        toolbar.addWidget(self.summary)
        layout.addLayout(toolbar)
        self.state_label = QLabel()
        self.state_label.setObjectName("minuteUsageState")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label.setWordWrap(True)
        layout.addWidget(self.state_label, 1)
        self.plot = create_usage_plot(y_axis=pg.AxisItem(orientation="left"))
        self.plot.setMinimumHeight(90)
        self.plot.setAxisItems({"bottom": _TimeAxis(orientation="bottom")})
        self.plot.getAxis("left").setLabel("tok/s")
        self.plot.getViewBox().setLimits(xMin=-0.5, yMin=0, minXRange=1)
        self.plot.getViewBox().sigResized.connect(self._update_candle_width)
        self._plot_viewport = self.plot.viewport()
        self._plot_viewport.installEventFilter(self)
        layout.addWidget(self.plot, 1)
        self.navigator, self.region = create_usage_navigator()
        self.navigator.setAxisItems({"bottom": _TimeAxis(orientation="bottom")})
        self.navigator.getAxis("bottom").setHeight(15)
        self.navigator.getAxis("bottom").setTickFont(QFont("Microsoft YaHei UI", 7))
        self.navigator.getViewBox().setLimits(xMin=-0.5, yMin=0)
        self.region.sigRegionChanged.connect(self._on_region_changed)
        self.plot.getViewBox().sigXRangeChanged.connect(self._on_main_range_changed)
        layout.addWidget(self.navigator)
        self.tooltip = MinuteUsageTooltip(self.plot, speed_mode=True)
        # 合并高频鼠标事件；禁用散点原生 hover 后，不再重复命中测试、重绘符号和设置系统提示。
        scene = cast(pg.GraphicsScene, self.plot.scene())
        self._mouse_proxy = pg.SignalProxy(scene.sigMouseMoved, rateLimit=60, slot=self._on_mouse_moved)
        self.refresh_theme()

    def set_rows(self, rows, usage_date, *, status="recorded", loading=False, interval_minutes=5):
        signature = (usage_date, status, loading, interval_minutes, tuple(
            (row.get("timestamp"), row.get("model"), row.get("output_tokens"), row.get("duration_ms"), row.get("first_token_ms"), row.get("status"))
            for row in rows
        ))
        if signature == self._signature:
            return
        self._signature = signature
        self._interval = max(1, min(60, interval_minutes))
        scope = (usage_date, self._interval)
        if scope != self._range_scope:
            self._has_initial_range = False
            self._range_scope = scope
        self._rows = []
        latest = None
        for row in rows:
            output, duration = row.get("output_tokens"), row.get("duration_ms")
            try:
                stamp = datetime.fromisoformat(str(row.get("timestamp", "")))
            except ValueError:
                continue
            if stamp.date().isoformat() != usage_date:
                continue
            if type(output) is not int or output < 0:
                continue
            valid = (row.get("status") in (None, "completed") and type(duration) is int
                     and output >= 200 and 1000 <= duration <= 3_600_000)
            model = str(row.get("model") or "unknown")
            # 低输出、未完成或过短窗口的速度容易失真；缺计时不等于速度为零。
            if not valid:
                continue
            # 后续不可计速的请求不能覆盖最近一次有效速度；按记录时间排序而非文件顺序。
            if latest is None or stamp.timestamp() > latest[0].timestamp():
                latest = (stamp, model, output * 1000 / duration)
            first_token = row.get("first_token_ms")
            if type(first_token) is not int or not 0 <= first_token <= duration:
                first_token = None
            self._rows.append({"minute": stamp.hour * 60 + stamp.minute,
                               "model": model, "timestamp": stamp.timestamp(),
                               "output": output, "duration": duration, "first_token_ms": first_token})
        self._latest = latest
        self._latest_cached = status in {"failed", "storage_error"}
        details = f"{latest[0]:%Y-%m-%d %H:%M:%S} · {latest[1]}" if latest else ""
        bind_text(self.summary, lambda: f"<p>{escape(tr('最近显示所选日期最后一次可计速请求；未完成、输出不足 200 Token 或耗时不足 1 秒的请求不计入。'))}<br>{escape(details)}</p>", method="setToolTip")
        self._refresh_model_colors()
        models = list(self._model_colors)
        # 刷新时保留选择；首次加载或原模型已无有效请求时，使用输出量最多的模型，避免空图。
        if self._selected_model not in models:
            self._selected_model = models[0] if models else None
            self._has_initial_range = False
        self._update_legend(models)
        if loading and not self._rows:
            bind_text(self.state_label, "正在加载速度数据…")
        elif status in {"failed", "storage_error"} and not self._rows:
            bind_text(self.state_label, "速度数据暂不可用，请刷新后重试")
        else:
            bind_text(self.state_label, "暂无可计速请求：输出至少 200 Token，耗时至少 1 秒")
        self.render()

    def render(self):
        self._hide_hover()
        self._hover_primary = {}
        previous_region = cast(tuple[float, float], self.region.getRegion())
        previous_high = self._axis_high
        rows = [row for row in self._rows if row["model"] == self._selected_model]
        groups = {}
        for row in rows:
            key = (row["minute"] // self._interval * self._interval, row["model"])
            values = groups.setdefault(key, {"output": 0, "duration": 0, "count": 0, "samples": []})
            values["output"] += row["output"]
            values["duration"] += row["duration"]
            values["count"] += 1
            values["samples"].append(row)
        self._points = []
        for (minute, model), values in sorted(groups.items()):
            samples = sorted(values.pop("samples"), key=lambda row: row["timestamp"])
            speeds = [row["output"] * 1000 / row["duration"] for row in samples]
            self._points.append({"minute": minute, "model": model, **values,
                                 "speed": speeds[-1], "first": speeds[0], "high": max(speeds), "low": min(speeds),
                                 "first_token_ms": samples[-1]["first_token_ms"]})
        # 与分时稀疏视图一致：横轴只保留有用量的时段，标签仍显示真实时间。
        minutes = sorted({point["minute"] for point in self._points})
        positions = {minute: index for index, minute in enumerate(minutes)}
        self.plot.getAxis("bottom").minutes = minutes
        self.plot.getAxis("bottom").picture = None
        for point in self._points:
            point["x"] = positions[point["minute"]]
        for item in self._items:
            self.plot.removeItem(item)
        self._items = []
        self._candles = []
        self._wicks = []
        self._candle_style = None
        self._ma_line = None
        self._hover_marker = None
        self.navigator.clear()
        self.navigator.addItem(self.region)
        self.navigator.getAxis("bottom").minutes = minutes
        self.navigator.getAxis("bottom").picture = None
        self.plot.setVisible(bool(self._points))
        self.navigator.setVisible(bool(self._points))
        self.state_label.setVisible(not self._points)
        if not self._points:
            self.ma_label.hide()
            self._set_summary(None, None)
            return
        # 汇总分子和分母，不能平均每条请求的 tok/s；并行请求也不会被混算成吞吐量。
        average = sum(row["output"] for row in rows) * 1000 / sum(row["duration"] for row in rows)
        peak = max(point["high"] for point in self._points)
        self._set_summary(average, peak)
        # K 线、均线、导航和悬停均使用当前模型，避免跨模型衔接造成速度跳变。
        primary = self._points
        self._hover_primary = {point["x"]: point for point in primary}
        candles = defaultdict(list)
        for index, point in enumerate(primary):
            # 用前一有效时段的末次速度作为起点；首个时段使用自己的首个请求速度。
            opening = primary[index - 1]["speed"] if index else point["first"]
            point["open"] = opening
            point["high"] = max(point["high"], opening)
            point["low"] = min(point["low"], opening)
            candles[(point["model"], point["speed"] >= opening)].append(point)
        for (model, rising), points in candles.items():
            color = self._model_colors[model]
            wick_x, wick_y = [], []
            for point in points:
                wick_x.extend((point["x"], point["x"], float("nan")))
                wick_y.extend((point["low"], point["high"], float("nan")))
            wick = pg.PlotDataItem(wick_x, wick_y, pen=pg.mkPen(color, width=1.2), connect="finite", antialias=True)
            fill = QColor(color)
            fill.setAlphaF(color.alphaF() * 0.7)
            bars = pg.BarGraphItem(x=[point["x"] for point in points],
                                   y0=[min(point["open"], point["speed"]) for point in points],
                                   height=[abs(point["speed"] - point["open"]) for point in points],
                                   width=0.55, pen=pg.mkPen(color, width=1.1),
                                   brush=pg.mkBrush(fill) if rising else pg.mkBrush(None))
            self.plot.addItem(wick)
            self.plot.addItem(bars)
            self._items.extend((wick, bars))
            self._wicks.append(wick)
            self._candles.append(bars)
        self.ma_label.setVisible(len(primary) >= 5)
        if len(primary) >= 5:
            # MA5 仅从第五个有效时段开始，不把缺失时段补零，也不修改请求速度。
            self._ma_line = pg.PlotDataItem(
                [primary[index]["x"] for index in range(4, len(primary))],
                [sum(point["speed"] for point in primary[index - 4:index + 1]) / 5
                 for index in range(4, len(primary))],
                pen=pg.mkPen(self._ma_color(), width=0.8), antialias=True,
            )
            self._ma_line.setZValue(5)
            self.plot.addItem(self._ma_line)
            self._items.append(self._ma_line)
        nav_color = QColor(current_theme().accent)
        nav_color.setAlpha(190)
        self.navigator.addItem(pg.BarGraphItem(x=[point["x"] for point in primary],
                               height=[point["speed"] for point in primary], width=0.8,
                               pen=None, brush=pg.mkBrush(nav_color)))
        # 密集时段通过悬停逐点查看，避免常驻圆点遮住 K 线。
        visible_points = primary if len(minutes) <= 24 else []
        color = self._model_colors[self._selected_model]
        dots = pg.ScatterPlotItem(
            spots=[{"pos": (point["x"], point["speed"]), "data": point} for point in visible_points],
            # Qt/PySide 部分版本的缓存散点绘制参数不兼容；少量点直接绘制更可靠。
            size=6, pen=pg.mkPen(color), brush=pg.mkBrush(color), antialias=True, tip=None, useCache=False,
        )
        dots.setAcceptHoverEvents(False)
        self.plot.addItem(dots)
        self._items.append(dots)
        self._hover_marker = pg.ScatterPlotItem(size=9, antialias=True, tip=None, useCache=False)
        self._hover_marker.setAcceptHoverEvents(False)
        self._hover_marker.setZValue(10)
        self.plot.addItem(self._hover_marker)
        self._hover_marker.hide()
        self._items.append(self._hover_marker)
        high = max(0.5, len(minutes) - 0.5)
        self._axis_high = high
        self.plot.getViewBox().setLimits(xMax=high, maxXRange=high + 0.5)
        self.navigator.getViewBox().setLimits(xMax=high)
        self.navigator.getViewBox().setXRange(-0.5, high, padding=0)
        self.navigator.getViewBox().setYRange(0, max(1, peak * 1.15), padding=0)
        if self._has_initial_range:
            low, end = previous_region
            if end >= previous_high - 0.01:
                low, end = low + high - previous_high, high
            width = min(high + 0.5, max(1, end - low))
            end = max(-0.5 + width, min(high, end))
            low = end - width
        else:
            end, low = high, max(-0.5, high - 24)
            self._has_initial_range = True
        self._syncing_region = True
        try:
            self.region.setBounds((-0.5, high))
            self.region.setRegion((low, end))
            self.plot.getViewBox().setXRange(low, end, padding=0)
        finally:
            self._syncing_region = False
        self.plot.getViewBox().setYRange(0, max(1, peak * 1.15), padding=0)
        self._update_candle_width()

    def _on_region_changed(self):
        if self._syncing_region:
            return
        self._syncing_region = True
        try:
            low, high = self.region.getRegion()
            self.plot.getViewBox().setXRange(low, high, padding=0)
            self._update_candle_width()
        finally:
            self._syncing_region = False

    def _set_summary(self, average, peak):
        latest = self._latest[2] if self._latest else None
        latest_text = "--" if latest is None else f"≈{latest:.1f}"
        average_text = "--" if average is None else f"≈{average:.1f}"
        peak_text = "--" if peak is None else f"≈{peak:.1f}"
        source = ("缓存 {latest} · 均速 {average} · 峰值 {peak} tok/s" if self._latest_cached
                  else "最近 {latest} · 均速 {average} · 峰值 {peak} tok/s")
        bind_text(self.summary, lambda: tr(source, latest=latest_text, average=average_text, peak=peak_text))

    def _on_main_range_changed(self, _view, values):
        if self._syncing_region:
            return
        self._syncing_region = True
        try:
            self.region.setRegion(values)
            self._update_candle_width()
        finally:
            self._syncing_region = False

    def _update_candle_width(self):
        if not self._candles:
            return
        low, high = self.plot.getViewBox().viewRange()[0]
        view_width = max(1, self.plot.getViewBox().width())
        pitch = view_width / max(high - low, 0.001)
        # 以每个时段的屏幕间距控制实体和线宽，密集时保留空隙；缩放不改变任何数据。
        width = min(9.0, pitch * 0.56) / pitch
        pen_width = 0.45 if pitch < 4 else 0.7 if pitch < 8 else 1.0
        style = (width, pen_width)
        if self._candle_style is not None and abs(style[0] - self._candle_style[0]) < 0.0001 and style[1] == self._candle_style[1]:
            return
        self._candle_style = style
        for candle in self._candles:
            pen = pg.mkPen(candle.opts["pen"])
            pen.setWidthF(pen_width)
            candle.setOpts(width=width, pen=pen)
        for wick in self._wicks:
            pen = pg.mkPen(wick.opts["pen"])
            pen.setWidthF(pen_width)
            wick.setPen(pen)

    def _refresh_model_colors(self):
        totals = defaultdict(int)
        for row in self._rows:
            totals[row["model"]] += row["output"]
        accent = QColor(current_theme().accent)
        # 每次只展示一个模型；保持排序不变，图例和图表都使用当前主题色。
        self._model_colors = {
            model: QColor(accent) for model in sorted(totals, key=lambda name: (-totals[name], name))
        }

    def _ma_color(self):
        # 均线沿用主题色并降低不透明度，避免压过 K 线。
        color = QColor(current_theme().accent)
        color.setAlpha(110)
        return color

    def _update_legend(self, models):
        for model in set(self.legend_buttons) - set(models):
            button = self.legend_buttons.pop(model)
            self._model_group.removeButton(button)
            self.legend_layout.removeWidget(button)
            button.deleteLater()
        for index, model in enumerate(models):
            if model not in self.legend_buttons:
                button = QToolButton()
                button.setObjectName("speedLegendButton")
                button.setCheckable(True)
                button.setAutoRaise(True)
                button.setIconSize(QSize(8, 8))
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                button.clicked.connect(lambda _checked, name=model: self._select_model(name))
                self._model_group.addButton(button)
                button.installEventFilter(self)
                self.legend_buttons[model] = button
            button = self.legend_buttons[model]
            # 长模型名不撑宽单行图例；提示保留全名，并转义接口提供的文本。
            button.setText(model if len(model) <= 24 else model[:21] + "…")
            bind_text(button, lambda name=model: f"<p>{escape(name)}<br>{escape(tr('点击切换到此模型'))}</p>", method="setToolTip")
            button.setAccessibleName(model)
            visible = model == self._selected_model
            button.setChecked(visible)
            button.setProperty("speedSelected", visible)
            color = self._model_colors[model] if visible else QColor(current_theme().muted)
            swatch = QPixmap(8, 8)
            swatch.fill(color)
            button.setIcon(QIcon(swatch))
            # 动态属性变化后 Qt 不一定重新匹配样式；重新应用以免图例保留旧的选中态。
            button.style().unpolish(button)
            button.style().polish(button)
            font = button.font()
            font.setBold(visible)
            button.setFont(font)
            button.update()
            self.legend_layout.insertWidget(index + 1, button)

    def _select_model(self, model):
        if model == self._selected_model:
            return
        self._selected_model = model
        # 横轴压缩了空闲时段，不同模型的索引不对应同一时间；切换后重新定位最近时段。
        self._has_initial_range = False
        self._update_legend(list(self._model_colors))
        self.render()

    def eventFilter(self, watched, event):
        # 图例移入面板后可能先于图表销毁，退出期间不能再调用其 C++ 方法。
        if not isValid(self) or not isValid(self.legend_scroll):
            return False
        if watched is self._legend_viewport and event.type() == QEvent.Type.Wheel:
            # 沿用本机统计的单行图例交互，模型较多时滚轮横向浏览，不挤占图表高度。
            delta = event.angleDelta().x() or event.angleDelta().y()
            bar = self.legend_scroll.horizontalScrollBar()
            bar.setValue(bar.value() - delta)
            event.accept()
            return True
        if event.type() == QEvent.Type.FocusIn and watched in self.legend_buttons.values():
            self.legend_scroll.ensureWidgetVisible(watched, 12, 0)
        if hasattr(self, "tooltip") and watched is getattr(self, "_plot_viewport", None):
            if event.type() == QEvent.Type.Enter:
                self._pointer_inside = True
            elif event.type() in {QEvent.Type.Leave, QEvent.Type.Hide}:
                # 离开后还可能收到限频队列的最后一次事件，不能让提示再次弹出。
                self._pointer_inside = False
                self._hide_hover()
        return super().eventFilter(watched, event)

    def _on_mouse_moved(self, event):
        if not isValid(self) or not isValid(self.plot) or not self._pointer_inside or not self.isVisible():
            return
        scene = event[0]
        view = self.plot.getViewBox()
        if not view.sceneBoundingRect().contains(scene):
            self._hide_hover()
            return
        mouse = view.mapSceneToView(scene)
        pixel_x, pixel_y = view.viewPixelSize()
        if not pixel_x or not pixel_y:
            return
        pixel_x, pixel_y = abs(pixel_x), abs(pixel_y)
        closest = None
        point = self._hover_primary.get(round(mouse.x()))
        if point is not None:
            dx = abs(mouse.x() - point["x"]) / pixel_x
            low, high = point["low"], point["high"]
            dy = max(low - mouse.y(), 0, mouse.y() - high) / pixel_y
            if dx <= max(10, self._candles[0].opts["width"] / (2 * pixel_x) + 2) and dy <= 10:
                closest = point
        if closest is None:
            self._hide_hover()
        else:
            self._show_hover(closest, self.plot.mapFromScene(scene))

    def _show_hover(self, point, local):
        key = (point["minute"], point["model"], current_language())
        changed = key != self._hover_key
        if changed:
            self._hover_key = key
            if self._hover_marker is not None:
                color = self._model_colors[point["model"]]
                self._hover_marker.setData([{"pos": (point["x"], point["speed"])}],
                                           pen=pg.mkPen(color), brush=pg.mkBrush(color))
                self._hover_marker.show()
            self.tooltip.set_speed_values(point["minute"], min(1439, point["minute"] + self._interval - 1),
                                          point["model"], point["speed"], point["first_token_ms"])
        # 同一节点移动只更新坐标，避免反复格式化文字、激活布局和调整提示尺寸。
        show_usage_tooltip(self.plot, self.tooltip, local, refresh_layout=changed)

    def _hide_hover(self):
        self._hover_key = None
        if isValid(self.tooltip):
            self.tooltip.hide()
        if self._hover_marker is not None:
            self._hover_marker.hide()

    def refresh_theme(self):
        refresh_usage_plot_theme(self.plot)
        refresh_usage_plot_theme(self.navigator)
        self.ma_label.setStyleSheet(f"color: {self._ma_color().name()}; font-size: 10px;")
        self.region.setBrush(pg.mkBrush(QColor(current_theme().accent_soft)))
        for line in self.region.lines:
            line.setPen(pg.mkPen(current_theme().accent, width=1.4))
        self.plot.getAxis("left").setLabel("tok/s", color=current_theme().subtext)
        self._refresh_model_colors()
        self._update_legend(list(self._model_colors))
        self.render()
