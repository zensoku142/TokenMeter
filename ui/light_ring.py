"""Compact single/multi-provider floating rings using existing quota snapshots."""

from decimal import Decimal, InvalidOperation
from html import escape

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QFrame,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from api.codex_resets import reset_news
from api.providers import PROVIDERS
from config import runtime as config_manager
from config import state
from data.store import TokenData
from ui.formatting import (
    _currency_prefix,
    format_money,
    format_quota_metric,
    quota_used_percent,
)
from ui.i18n import bind_text, language_controller, tr
from ui.provider_branding import provider_icon
from ui.provider_overview import ProviderOverview, provider_status_message
from ui.qt_theme import current_theme, theme_controller


def ring_value(provider_id: str, data: TokenData | None) -> tuple[str, float | None]:
    if data is None or data.last_success_at is None:
        return "--", None
    if data.quota_windows:
        primary = data.quota_windows[0]
        if provider_id in {"codex", "claude"}:
            # 与原悬浮球保持同一口径，短期窗口决定当前能否继续使用。
            primary = next((w for w in data.quota_windows if w.window_minutes == 300), primary)
        used = quota_used_percent(primary.used_percent)
        remaining = max(0.0, 100 - used) if used is not None else None
        return (f"{remaining:g}%", remaining) if remaining is not None else ("--", None)
    if PROVIDERS[provider_id].supports_subscription_quota:
        unlimited = next((metric for metric in data.quota_metrics
                          if metric.value_kind == "unlimited" or metric.value in {"不限量", "不限额"}), None)
        return (format_quota_metric(unlimited), None) if unlimited else ("--", None)
    # 金额没有可用总额，不能伪造为百分比进度。
    return format_money(data.balance_cny, data.currency), None


def _compact_amount(value) -> str:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return "--"
    if not amount.is_finite():
        return "--"
    for scale, suffix in ((10**12, "T"), (10**9, "B"), (10**6, "M"), (1000, "k")):
        if abs(amount) >= scale:
            return f"{amount / scale:.2f}".rstrip("0").rstrip(".") + suffix
    return f"{amount:.2f}".rstrip("0").rstrip(".")


class _RingItem(QAbstractButton):
    def __init__(self, rail, provider_id, entry_id, label):
        super().__init__(rail)
        self.rail = rail
        self.provider_id = provider_id
        self.entry_id = entry_id
        self.label = label
        self.value = "--"
        self.display_value = "--"
        self.remaining = None
        self.stale = False
        self._keyboard_focus = False
        self.setFixedHeight(62)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(lambda: rail.provider_selected.emit(entry_id))
        self.refresh_theme()

    def refresh_theme(self):
        # 直接使用项目品牌资源；彩色图标保留原色，单色图标跟随主题文字颜色。
        self._brand_icon = provider_icon(self.provider_id, 22)
        self.update()

    def set_data(self, data):
        self.value, self.remaining = ring_value(self.provider_id, data)
        self.display_value = self.value
        if data is not None and data.last_success_at is not None and not data.quota_windows:
            if PROVIDERS[self.provider_id].supports_subscription_quota:
                metric = next((m for m in data.quota_metrics
                               if m.value_kind in {"unlimited", "credits"} or m.value in {"不限量", "不限额"}), None)
                if (metric is not None and metric.value_kind == "credits"
                        and self.value == format_quota_metric(metric)):
                    # 常驻栏只留紧凑数值，完整积分和单位仍保留在详情及无障碍名称中。
                    self.display_value = _compact_amount(metric.value)
            elif data.balance_cny is not None and abs(data.balance_cny) >= 1000:
                self.display_value = _currency_prefix(data.currency) + _compact_amount(data.balance_cny)
        self.stale = bool(provider_status_message(data))
        label = tr(PROVIDERS[self.provider_id].name) + (" · " + self.label if self.label else "") + " · " + self.value
        self.setAccessibleName(label)
        self.setAccessibleDescription(tr(provider_status_message(data)))
        self.update()

    def paintEvent(self, event):
        theme = current_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        ring = QRectF((self.width() - 38) / 2, 4, 38, 38)
        track = QColor(theme.border)
        track.setAlpha(95)
        painter.setPen(QPen(track, 3.2))
        painter.drawEllipse(ring)
        if self.remaining is not None:
            accent = QColor(theme.accent if config_manager.get("FLOATING_RING_FOLLOW_THEME", True)
                            else config_manager.get("FLOATING_RING_COLOR", theme.accent))
            if self.stale:
                accent.setAlpha(145)
            pen = QPen(accent, 3.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(ring, 90 * 16, -round(min(100, self.remaining) * 3.6 * 16))
        self._brand_icon.paint(painter, round(ring.center().x() - 11), 12, 22, 22)
        font = QFont("Segoe UI")
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor(theme.value))
        metrics = QFontMetrics(font)
        available = self.width() - 4
        if metrics.horizontalAdvance(self.display_value) > available:
            font.setPixelSize(max(12, int(14 * available / metrics.horizontalAdvance(self.display_value))))
            painter.setFont(font)
        text = QFontMetrics(font).elidedText(self.display_value, Qt.TextElideMode.ElideRight, available)
        painter.drawText(QRectF(0, 46, self.width(), 17), Qt.AlignmentFlag.AlignCenter, text)
        if self.stale:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.warning))
            painter.drawEllipse(QRectF(ring.right() - 3, 3, 5, 5))
        if self.hasFocus() and self._keyboard_focus and not self.rail._dragging:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(theme.accent), 1, Qt.PenStyle.DotLine))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 10, 10)

    def enterEvent(self, event):
        super().enterEvent(event)
        if not self.rail._dragging:
            self.rail.show_details(self.entry_id)

    def focusInEvent(self, event):
        self._keyboard_focus = event.reason() in {
            Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
            Qt.FocusReason.ShortcutFocusReason,
        }
        super().focusInEvent(event)

    def leaveEvent(self, event):
        self.rail.schedule_details_hide()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.rail.start_drag(event.globalPosition().toPoint(), self.entry_id)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            point = event.globalPosition().toPoint()
            origin = self.rail._drag_origin
            if origin is not None:
                delta = point - origin
                if not self.rail._gesture and delta.manhattanLength() >= QApplication.startDragDistance():
                    self.rail._gesture = "reorder" if len(self.rail.items) > 1 and abs(delta.y()) > abs(delta.x()) else "move"
                if self.rail._gesture == "reorder":
                    self.rail.reorder_at(self.entry_id, point)
                else:
                    self.rail.move_drag(point)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rail.finish_drag():
            # 拖动结束不能再触发点击，否则松手就会打开主面板。
            self.setDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class LightRingWidget(QWidget):
    provider_selected = Signal(str)
    settings_requested = Signal()
    refresh_requested = Signal()
    quit_requested = Signal()
    auto_refresh_requested = Signal()
    auto_refresh_stopped = Signal()

    def __init__(self, parent=None):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus
                 | Qt.WindowType.NoDropShadowWindowHint)
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        bind_text(self, "轻底细环", method="setWindowTitle")
        self.items = {}
        self._entry_providers = {}
        self._labels = {}
        self._snapshots = {}
        self._hovered = ""
        self._dragging = False
        self._gesture = ""
        self._drag_origin = None
        self._position_loaded = False
        self._reset_payload = None
        self._reset_enabled = False
        self._row_animations = {}
        self._drag_entry = ""
        self._drag_item_offset = 31
        self.setFixedSize(58, 94)
        root = self._root_layout = QVBoxLayout(self)
        root.setContentsMargins(1, 8, 1, 8)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.rows = QVBoxLayout(self.content)
        self.rows.setContentsMargins(3, 0, 3, 0)
        self.rows.setSpacing(8)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll)
        self.popup = QFrame(self, flags)
        self.popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.popup.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        popup_layout = QVBoxLayout(self.popup)
        popup_layout.setContentsMargins(0, 0, 0, 0)
        self.details = ProviderOverview(self.popup)
        self.details.hide()
        self._leave_timer = QTimer(self)
        self._leave_timer.setSingleShot(True)
        self._leave_timer.timeout.connect(self._hide_details_if_outside)
        self._timer = QTimer(self)
        self._timer.setInterval(60_000)
        self._timer.timeout.connect(self.auto_refresh_requested)
        self._snap_animation = QPropertyAnimation(self, b"pos", self)
        self._snap_animation.setDuration(180)
        self._snap_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._snap_animation.finished.connect(self._finish_snap)
        theme_controller().changed.connect(self.refresh_theme)
        controller = language_controller()
        if controller is not None:
            controller.changed.connect(self.refresh_theme)
        self.refresh_theme()

    def selected_providers(self):
        return [key for key in self.items if not key.startswith("profile:")]

    def selected_sources(self):
        return list(self.items)

    def set_sources(self, provider_ids, *, providers=None, labels=None):
        if self._dragging:
            # 定时刷新不能在排序过程中重建被鼠标抓住的条目。
            return
        entries = {key: (providers or {}).get(key, key) for key in provider_ids}
        ids = list(dict.fromkeys(key for key in provider_ids if entries[key] in PROVIDERS))
        saved = state.load_dict(config_manager.PANEL_LAYOUT_PATH).get("light_ring_order", [])
        if isinstance(saved, list):
            ids = list(dict.fromkeys([key for key in saved if isinstance(key, str) and key in ids] + ids))
        names = {key: (labels or {}).get(key, "") for key in ids}
        if ids == self.selected_sources() and entries == self._entry_providers and names == self._labels:
            return
        self._entry_providers = entries
        self._labels = names
        self.popup.hide()
        self._hovered = ""
        for animation in self._row_animations.values():
            animation.stop()
            animation.deleteLater()
        self._row_animations.clear()
        for item in self.items.values():
            self.rows.removeWidget(item)
            item.deleteLater()
        self.items = {}
        for pid in ids:
            item = _RingItem(self, entries[pid], pid, names[pid])
            self.items[pid] = item
            self.rows.addWidget(item)
        self._resize_to_content()
        if self.isVisible():
            self.auto_refresh_requested.emit()

    def set_data(self, snapshots):
        self._snapshots = {pid: snapshots.get(pid) for pid in self.items}
        for pid, item in self.items.items():
            item.set_data(self._snapshots[pid])
        self._resize_to_content()
        if self.popup.isVisible() and self._hovered in self.items:
            self.show_details(self._hovered)

    def _resize_to_content(self):
        if hasattr(self, "_snap_animation") and self._snap_animation.state() == QPropertyAnimation.State.Running:
            return
        # 单个平台的长金额或积分不能撑宽整个贴边栏。
        width = 58
        area = self.screen().availableGeometry()
        margin = 22 if self._edge_side() else 8
        self._root_layout.setContentsMargins(1, margin, 1, margin)
        right = self.x() + self.width()
        anchored_right = self._position_loaded and abs(right - (area.right() + 1)) <= 2
        self.content.setMinimumHeight(max(1, len(self.items) * 70 - 8))
        self.setFixedSize(width, min(self.content.minimumHeight() + margin * 2, max(96, area.height() - 32)))
        if anchored_right:
            self.move(area.right() + 1 - self.width(), self.y())
        if self._position_loaded:
            self._clamp_position()

    def _clamp_position(self, snap=False, animate=False):
        area = self.screen().availableGeometry()
        x = max(area.left(), min(self.x(), area.right() + 1 - self.width()))
        y = max(area.top(), min(self.y(), area.bottom() + 1 - self.height()))
        if snap:
            if x - area.left() <= 20:
                x = area.left()
            elif area.right() + 1 - x - self.width() <= 20:
                x = area.right() + 1 - self.width()
        target = QPoint(x, y)
        if animate and target != self.pos():
            self._snap_animation.stop()
            self._snap_animation.setStartValue(self.pos())
            self._snap_animation.setEndValue(target)
            self._snap_animation.start()
        else:
            self.move(target)

    def _edge_side(self):
        if not self._position_loaded:
            return "right"
        area = self.screen().availableGeometry()
        if abs(self.x() - area.left()) <= 1:
            return "left"
        if abs(self.x() + self.width() - area.right() - 1) <= 1:
            return "right"
        return ""

    def showEvent(self, event):
        super().showEvent(event)
        if not self._position_loaded:
            position = state.load_dict(config_manager.PANEL_LAYOUT_PATH).get("light_ring_position")
            if (isinstance(position, list) and len(position) == 2
                    and all(isinstance(value, int) for value in position)):
                self.move(*position)
            else:
                area = self.screen().availableGeometry()
                self.move(area.right() + 1 - self.width(), area.center().y() - self.height() // 2)
            self._position_loaded = True
        self._resize_to_content()
        self._clamp_position()
        self._timer.start()
        # 信号接好、窗口完成显示后再启动采集，悬停只读取缓存。
        QTimer.singleShot(0, self._request_refresh)

    def _request_refresh(self):
        if self.isVisible():
            self.auto_refresh_requested.emit()

    def hideEvent(self, event):
        self.popup.hide()
        self._timer.stop()
        self._snap_animation.stop()
        self._leave_timer.stop()
        self.auto_refresh_stopped.emit()
        super().hideEvent(event)

    def start_drag(self, point, entry_id=""):
        self._snap_animation.stop()
        self._drag_origin = point
        self._window_origin = self.pos()
        self._dragging = False
        self._gesture = ""
        self._drag_entry = entry_id
        item = self.items.get(entry_id)
        self._drag_item_offset = point.y() - item.mapToGlobal(QPoint()).y() if item is not None else 31

    def move_drag(self, point):
        if self._drag_origin is None:
            return
        delta = point - self._drag_origin
        if not self._dragging and delta.manhattanLength() < QApplication.startDragDistance():
            return
        self._dragging = True
        self._gesture = "move"
        self.popup.hide()
        self.move(self._window_origin + delta)

    def finish_drag(self):
        dragged = self._dragging
        gesture = self._gesture
        self._drag_origin = None
        self._dragging = False
        self._gesture = ""
        if dragged:
            if gesture == "reorder":
                item = self.items.get(self._drag_entry)
                if item is not None:
                    self._animate_row(self._drag_entry, item.pos(), QPoint(item.x(), list(self.items).index(self._drag_entry) * 70))
                try:
                    state.merge_dict(config_manager.PANEL_LAYOUT_PATH, {"light_ring_order": self.selected_sources()})
                except OSError:
                    config_manager.logger().warning("Light ring order could not be saved")
            else:
                self._clamp_position(snap=True, animate=True)
                if self._snap_animation.state() != QPropertyAnimation.State.Running:
                    self._finish_snap()
        return dragged

    def _finish_snap(self):
        self._resize_to_content()
        self.save_position()
        self.update()

    def reorder_at(self, entry_id, point):
        if entry_id not in self.items:
            return
        self._dragging = True
        self._gesture = "reorder"
        self._drag_entry = entry_id
        self.popup.hide()
        others = [key for key in self.items if key != entry_id]
        y = point.y() - self.content.mapToGlobal(QPoint()).y() - self._drag_item_offset
        index = max(0, min(len(others), round(y / 70)))
        order = others[:index] + [entry_id] + others[index:]
        if order != list(self.items):
            previous = {key: item.pos() for key, item in self.items.items()}
            for animation in self._row_animations.values():
                animation.stop()
                animation.deleteLater()
            self._row_animations.clear()
            self.rows.insertWidget(index, self.items[entry_id])
            self.items = {key: self.items[key] for key in order}
            self.rows.activate()
            # 邻项从当前位置平滑让位，被拖动的条目则直接跟随鼠标。
            for key in others:
                self._animate_row(key, previous[key], self.items[key].pos())
        dragged = self.items[entry_id]
        dragged.move(dragged.x(), max(0, min(y, max(0, self.content.height() - dragged.height()))))
        dragged.raise_()

    def _animate_row(self, key, start, target):
        previous = self._row_animations.pop(key, None)
        if previous is not None:
            previous.stop()
            previous.deleteLater()
        if start == target:
            return
        animation = QPropertyAnimation(self.items[key], b"pos", self)
        animation.setDuration(160)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.setStartValue(start)
        animation.setEndValue(target)
        self.items[key].move(start)
        self._row_animations[key] = animation
        animation.finished.connect(lambda: self._finish_row_animation(key, animation))
        animation.start()

    def _finish_row_animation(self, key, animation):
        if self._row_animations.get(key) is animation:
            self._row_animations.pop(key, None)
        animation.deleteLater()

    def set_reset_news(self, payload, *, enabled):
        self._reset_payload = payload
        self._reset_enabled = enabled
        if self.popup.isVisible() and self._hovered in self.items:
            self.show_details(self._hovered)

    def save_position(self):
        if not self._position_loaded:
            return
        try:
            state.merge_dict(config_manager.PANEL_LAYOUT_PATH, {"light_ring_position": [self.x(), self.y()]})
        except OSError:
            config_manager.logger().warning("Light ring position could not be saved")

    def show_details(self, provider_id):
        if self._dragging or provider_id not in self.items or not self.isVisible():
            return
        self._hovered = provider_id
        self._leave_timer.stop()
        layout = self.popup.layout()
        while layout.count():
            previous = layout.takeAt(0).widget()
            previous.hide()
            self.details.cards_layout.insertWidget(1, previous)
        self.details.set_data({provider_id: self._snapshots.get(provider_id)},
                              providers=self._entry_providers, labels=self._labels)
        card = self.details.cards[provider_id]
        for button in card.findChildren(QPushButton):
            button.hide()
        footer = card.findChild(QLabel, "publicResetNews")
        show_news = self._reset_enabled and self._entry_providers.get(provider_id, provider_id) == "codex"
        if show_news:
            if footer is None:
                footer = QLabel(card)
                footer.setObjectName("publicResetNews")
                footer.setWordWrap(True)
                footer.setOpenExternalLinks(True)
                card.layout().addWidget(footer)
            try:
                news = reset_news(self._reset_payload) if self._reset_payload else None
            except (TypeError, ValueError, AttributeError):
                news = None
            lines = news["lines"] if news else ["公开重置动态暂不可用"]
            text = "<br>".join(escape(tr(line)) for line in lines)
            color = current_theme().accent_hover
            links = f'<a style="color:{color}" href="https://tibo.cc/">TIBO</a> · <a style="color:{color}" href="https://codex-resets.com/">Codex Resets</a>'
            if news and news["source_url"]:
                links += f' · <a style="color:{color}" href="' + escape(news["source_url"], quote=True) + '">' + escape(tr("原公告")) + '</a>'
            footer.setText(text + "<br>" + links + "<br>" + escape(tr("社区动态，不代表个人额度到账")))
            footer.setStyleSheet(f"font-size: 11px; color: {current_theme().subtext};")
            footer.show()
        elif footer is not None:
            footer.hide()
        # 只借用总览卡片的展示和格式化，不把总览滚动区及其留白带进悬停窗口。
        self.details.cards_layout.removeWidget(card)
        layout.addWidget(card)
        self.popup.setStyleSheet(self.details.styleSheet())
        card.show()
        card.setFixedWidth(330)
        card.ensurePolished()
        card.layout().invalidate()
        # 按固定宽度下的换行高度收紧窗口，不能沿用总览滚动区的最小高度或留白。
        content_height = max(card.minimumSizeHint().height(),
                             card.heightForWidth(330) if card.hasHeightForWidth() else card.sizeHint().height())
        height = min(content_height, self.screen().availableGeometry().height() - 24)
        self.popup.setFixedSize(330, height)
        area = self.screen().availableGeometry()
        x = self.x() - self.popup.width() - 12
        if x < area.left():
            x = self.x() + self.width() + 12
        point = self.items[provider_id].mapToGlobal(QPoint(0, 0))
        y = max(area.top(), min(point.y(), area.bottom() + 1 - self.popup.height()))
        self.popup.move(max(area.left(), min(x, area.right() + 1 - self.popup.width())), y)
        self.popup.show()
        self.schedule_details_hide()

    def schedule_details_hide(self):
        self._leave_timer.start(600)

    def _hide_details_if_outside(self):
        cursor = QCursor.pos()
        if self.geometry().contains(cursor) or (self.popup.isVisible() and self.popup.geometry().contains(cursor)):
            self._leave_timer.start(600)
        else:
            self.popup.hide()

    def refresh_theme(self, *_args):
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: 0; } QScrollArea > QWidget > QWidget { background: transparent; }")
        self.details.refresh_theme()
        self.popup.setStyleSheet(self.details.styleSheet())
        for item in self.items.values():
            item.refresh_theme()
            item.set_data(self._snapshots.get(item.entry_id))
        self._resize_to_content()
        if self.popup.isVisible() and self._hovered in self.items:
            self.show_details(self._hovered)
        self.update()

    def paintEvent(self, event):
        theme = current_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        border = QColor(theme.border)
        border.setAlpha(130)
        # 使用柔和的主题表面，保持文字和品牌图标不受窗口整体透明度影响。
        painter.setBrush(QColor(theme.elevated))
        side = self._edge_side()
        if side:
            # 上下凹弧与屏幕边缘相接；离开边缘后不保留伸出的肩部。
            width, height, shoulder, radius = self.width(), self.height(), 14, 20
            path = QPainterPath()
            path.moveTo(width, 0)
            path.quadTo(width, shoulder, width - shoulder, shoulder)
            path.lineTo(radius, shoulder)
            path.quadTo(0, shoulder, 0, shoulder + radius)
            path.lineTo(0, height - shoulder - radius)
            path.quadTo(0, height - shoulder, radius, height - shoulder)
            path.lineTo(width - shoulder, height - shoulder)
            path.quadTo(width, height - shoulder, width, height)
            path.closeSubpath()
            if side == "left":
                painter.translate(width, 0)
                painter.scale(-1, 1)
        else:
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 20, 20)
        # 小范围软边增加明暗桌面上的辨识度，不依赖系统原生阴影或持续动画。
        shadow = QColor(theme.shadow)
        for width, alpha in ((5, 16), (3, 28)):
            shadow.setAlpha(alpha)
            painter.strokePath(path, QPen(shadow, width))
        painter.setPen(QPen(border, 1))
        painter.drawPath(path)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        for title, signal in (("刷新", self.refresh_requested), ("设置", self.settings_requested), ("退出", self.quit_requested)):
            action = bind_text(QAction(menu), title)
            action.triggered.connect(signal.emit)
            menu.addAction(action)
        menu.exec(event.globalPos())

    def closeEvent(self, event):
        self._snap_animation.stop()
        self._clamp_position(snap=True)
        self.save_position()
        self.popup.hide()
        self._timer.stop()
        self._snap_animation.stop()
        self._leave_timer.stop()
        super().closeEvent(event)
