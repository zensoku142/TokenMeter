"""Qt floating window coordinating the ball, panel, refresh, and settings."""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRunnable,
    QThreadPool,
    QTimer,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QColor, QCursor, QGuiApplication, QPalette, QRegion
from PySide6.QtWidgets import QApplication, QHBoxLayout, QMenu, QWidget

import config_manager
from data.store import TokenData
from api.providers.base import FetchError
from ui.geometry import (
    WorkArea,
    clamp_window,
    compact_geometry,
    expanded_panel_geometry,
)
from ui.qt_ball import FloatingUsageBall
from ui.qt_panel import MainPanel, format_money
from ui.qt_settings import SettingsWindow


DEF_PANEL_W = 820
DEF_PANEL_H = 550
DEF_BALL_SIZE = 96


class FetchSignals(QObject):
    finished = Signal(int, object)


class FetchTask(QRunnable):
    def __init__(self, request_id: int):
        super().__init__()
        self.request_id = request_id
        self.signals = FetchSignals()

    @Slot()
    def run(self) -> None:
        result = _fetch_tokens_safely()
        self.signals.finished.emit(self.request_id, result)


def _fetch_tokens_safely() -> TokenData:
    """Fetch token data from the active provider and keep the worker thread
    from dying if a provider or config error is raised."""

    try:
        return TokenData.fetch()
    except Exception:
        config_manager.logger().exception("Background refresh failed")
        data = TokenData(status="error")
        data.last_attempt_at = __import__("datetime").datetime.now()
        data.errors.append(
            FetchError("UNKNOWN_ERROR", "后台刷新", "刷新数据时发生未知错误")
        )
        return data


class FloatingWidget(QWidget):
    def __init__(self, tray_icon=None):
        super().__init__()
        self.tray = tray_icon
        self._expanded = False
        self._data = TokenData()
        self._refresh_lock = threading.Lock()
        self._refreshing = False
        self._pending_refresh = False
        self._request_id = 0
        self._closed = False
        self._transitioning = False
        self._expand_horizontal = "right"
        self._expand_vertical = "down"
        self._drag_origin = QPoint()
        self._window_origin = QPoint()
        self._drag_started = False
        self._drag_source = ""
        self._settings_window: SettingsWindow | None = None
        self._thread_pool = QThreadPool.globalInstance()
        # Edge auto-hide state.
        self._edge_snapped = False
        self._edge_direction = ""  # "left" | "right" | "top" | "bottom"
        self._edge_hide_timer = QTimer(self)
        self._edge_hide_timer.setSingleShot(True)
        self._edge_hide_timer.timeout.connect(self._do_edge_hide)
        self._edge_leave_timer = QTimer(self)
        self._edge_leave_timer.setSingleShot(True)
        self._edge_leave_timer.timeout.connect(self._do_edge_leave)
        self._edge_hovering = False
        self._edge_hidden = False
        self._edge_hover_check = QTimer(self)
        self._edge_hover_check.timeout.connect(self._check_edge_hover)
        # 吸附、隐藏和唤出共用一个位置动画，避免多个动画同时争抢窗口坐标。
        self._edge_animation = QPropertyAnimation(self, b"pos", self)
        self._edge_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setObjectName("floatingRoot")
        # Windows may composite a native rectangular surface around a layered
        # frameless window, so keep both the Qt background and palette transparent.
        self.setAutoFillBackground(False)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0, 0))
        self.setPalette(palette)
        self.setStyleSheet(
            "QWidget#floatingRoot { background: transparent; border: 0; }"
        )

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self.ball = FloatingUsageBall(self._compact_size())
        self.panel = MainPanel()
        self.panel.hide()
        self._layout.addWidget(self.ball, 0, Qt.AlignmentFlag.AlignTop)
        self._connect_ui()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._periodic_refresh)
        # Note: we no longer run a periodic "clock timer" that repaints
        # the ball every 30 seconds. The UI is updated only when new
        # data arrives from the provider, which saves significant idle
        # CPU (painting a translucent anti-aliased circle is not free).
        self._show_compact_at_saved_position()
        self.refresh()

    def _connect_ui(self) -> None:
        self.ball.pressed.connect(lambda point: self._start_drag(point, "ball"))
        self.ball.dragged.connect(self._move_drag)
        self.ball.released.connect(self._end_drag)
        self.panel.header.pressed.connect(lambda point: self._start_drag(point, "header"))
        self.panel.header.dragged.connect(self._move_drag)
        self.panel.header.released.connect(self._end_drag)
        self.panel.settings_requested.connect(self.open_settings)
        self.panel.refresh_requested.connect(self.refresh)
        self.panel.close_requested.connect(self.collapse_panel)

    @staticmethod
    def _compact_size() -> int:
        configured = int(config_manager.get("WIDGET_COMPACT_SIZE", DEF_BALL_SIZE))
        return DEF_BALL_SIZE if configured < 96 else min(124, configured)

    @staticmethod
    def _expanded_size() -> tuple[int, int]:
        size = config_manager.get("WIDGET_EXPANDED_SIZE", (DEF_PANEL_W, DEF_PANEL_H))
        width = max(640, min(DEF_PANEL_W, int(size[0])))
        return width, DEF_PANEL_H

    def _show_compact_at_saved_position(self) -> None:
        size = self._compact_size()
        screen = QGuiApplication.primaryScreen().availableGeometry()
        saved = config_manager.load_widget_position()
        if saved is None:
            x = screen.center().x() - size // 2
            y = screen.top() + 90
        else:
            work = WorkArea(screen.x(), screen.y(), screen.x() + screen.width(), screen.y() + screen.height())
            x, y = clamp_window(saved[0], saved[1], size, size, work)
        self.panel.hide()
        self.ball.show()
        self.setFixedSize(size, size)
        self.clearMask()
        self.move(x, y)
        self.show()
        self._apply_native_window_shape(compact=True)

    def _apply_native_window_shape(self, compact: bool) -> None:
        # NOTE: 为了兼容 Windows 高 DPI 和多屏幕环境，不再使用 Win32 的
        # SetWindowRgn。我们通过 Qt 自身的 WA_TranslucentBackground +
        # setMask 来控制可见区域。直接调用 Win32 容易在
        # devicePixelRatio 非 1 时把整个窗口切到屏幕外。
        if compact:
            size = self._compact_size()
            region = QRegion(0, 0, size, size, QRegion.RegionType.Ellipse)
            self.setMask(region)
        else:
            self.clearMask()

    def _arrange_expanded(self) -> None:
        while self._layout.count():
            self._layout.takeAt(0)
        # 展开态完全由面板替代悬浮球，避免重复入口并缩小窗口占用。
        self.ball.hide()
        self._layout.addWidget(self.panel, 1)

    def toggle(self) -> None:
        if self._transitioning:
            return
        if self._expanded:
            self.collapse_panel()
        else:
            self.expand_panel()

    def expand_panel(self) -> None:
        if self._expanded or self._transitioning:
            return
        self._edge_unsnap()
        self._transitioning = True
        size = self._compact_size()
        try:
            work = self._work_area()
            geometry = expanded_panel_geometry(
                (self.x(), self.y(), size, size), self._expanded_size(), work
            )
            x, y, width, height, horizontal, vertical = geometry
            self._expanded = True
            self._expand_horizontal = horizontal
            self._expand_vertical = vertical
            self.clearMask()
            self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, False)
            self._arrange_expanded()
            self.panel.show()
            self.setFixedSize(width, height)
            self.move(x, y)
            self.show()
            self._apply_native_window_shape(compact=False)
            self.raise_()
            self.activateWindow()
            self.panel.setFocus(Qt.FocusReason.OtherFocusReason)
            self.panel.update_data(self._data, self._refreshing)
            self.refresh()
        finally:
            self._transitioning = False
        self._reschedule_refresh()

    def collapse_panel(self) -> None:
        if not self._expanded or self._transitioning:
            return
        self._transitioning = True
        try:
            size = self._compact_size()
            work = self._work_area()
            x, y = compact_geometry(
                (self.x(), self.y(), self.width(), self.height()),
                size,
                self._expand_horizontal,
                self._expand_vertical,
                work,
            )
            self._expanded = False
            self.panel.hide()
            self.setFixedSize(size, size)
            while self._layout.count():
                self._layout.takeAt(0)
            self._layout.addWidget(self.ball, 0, Qt.AlignmentFlag.AlignTop)
            self.ball.show()
            self.move(x, y)
            config_manager.save_widget_position(x, y)
            # Compact mode remains clickable but cannot take keyboard focus away
            # from the application the user is currently working in.
            self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
            self.clearMask()
            self.show()
            self._apply_native_window_shape(compact=True)
            self.raise_()
        finally:
            self._transitioning = False
        self._reschedule_refresh()

    def event(self, event) -> bool:
        if (
            event.type() == QEvent.Type.WindowDeactivate
            and self._expanded
            and not self._transitioning
        ):
            # Defer until Qt has finished activating a possible child dialog.
            # This distinguishes a real outside click from opening Settings.
            QTimer.singleShot(0, self._collapse_after_deactivation)
        return super().event(event)

    def _collapse_after_deactivation(self) -> None:
        if (
            self._expanded
            and not self._transitioning
            and not self._drag_started
            and not self._has_settings_child()
            and not self.isActiveWindow()
        ):
            self.collapse_panel()

    def _has_settings_child(self) -> bool:
        return bool(self._settings_window and self._settings_window.isVisible())

    def keyPressEvent(self, event) -> None:
        key = event.key()
        # Shift+Esc：任何状态下都退出程序，防止贴边隐藏后关不掉
        if key == Qt.Key.Key_Escape and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.close()
            event.accept()
            return
        # Esc：如果在贴边隐藏状态 → 强制恢复显示；否则关闭展开面板
        if key == Qt.Key.Key_Escape:
            if self._edge_snapped:
                self._edge_unsnap()
                self._clamp_to_work_area()
            elif self._expanded:
                if self._has_settings_child():
                    self._settings_window.reject()
                else:
                    self.collapse_panel()
            event.accept()
            return
        super().keyPressEvent(event)

    def _start_drag(self, point: QPoint, source: str) -> None:
        # If the user starts a drag while the ball is snapped (i.e. only
        # an 8px strip is visible), immediately restore the full ball so
        # they can grab and move it intuitively.
        self._edge_animation.stop()
        self._edge_hide_timer.stop()
        self._edge_leave_timer.stop()
        if self._edge_snapped:
            self._edge_unsnap()
            size = self._compact_size()
            work = self._work_area()
            x, y = self.x(), self.y()
            if x < work.left:
                x = work.left
            elif x + size > work.right:
                x = work.right - size
            if y < work.top:
                y = work.top
            elif y + size > work.bottom:
                y = work.bottom - size
            self.move(x, y)
        self._drag_origin = point
        self._window_origin = self.pos()
        self._drag_started = False
        self._drag_source = source

    def _move_drag(self, point: QPoint) -> None:
        delta = point - self._drag_origin
        if not self._drag_started and delta.manhattanLength() < 5:
            return
        self._drag_started = True
        self.move(self._window_origin + delta)

    def _end_drag(self, _point: QPoint) -> None:
        if self._drag_started:
            # 先判断边缘吸附；若没有贴边再做常规工作区约束。这样用户
            # 把球拖到桌面任意边缘接触时都会被吸附并自动隐藏，而不是
            # 被 clamp 拉回安全距离后再检测。
            if not self._try_edge_snap():
                self._clamp_to_work_area()
        elif self._drag_source == "ball":
            self.toggle()
        self._drag_started = False
        self._drag_source = ""

    def _work_area(self):
        # Use Qt's availableGeometry() directly; it returns logical pixels
        # matching self.x()/self.y().  Do NOT fall through to the Win32
        # GetMonitorInfoW helper — that function returns physical pixels
        # and breaks edge-snap on any system with DPI scaling != 100%.
        frame = self.frameGeometry()
        if self._edge_snapped and self._edge_direction == "left":
            probe = QPoint(frame.right(), frame.center().y())
        elif self._edge_snapped and self._edge_direction == "right":
            probe = QPoint(frame.left(), frame.center().y())
        else:
            probe = frame.center()
        # 隐藏态用仍留在屏幕内的触发条取屏幕，避免负坐标副屏回退到主屏。
        screen = QGuiApplication.screenAt(probe) or QGuiApplication.primaryScreen()
        available = screen.availableGeometry()
        # WorkArea 使用右/下边界开区间，与窗口宽高计算保持一致。
        return WorkArea(
            available.x(),
            available.y(),
            available.x() + available.width(),
            available.y() + available.height(),
        )

    def _clamp_to_work_area(self) -> None:
        work = self._work_area()
        if self._expanded:
            x, y = clamp_window(self.x(), self.y(), self.width(), self.height(), work)
        else:
            size = self._compact_size()
            # 自由拖拽仍需限制在工作区内，避免悬浮球被拖出屏幕后无法找回。
            x, y = clamp_window(self.x(), self.y(), size, size, work)
            config_manager.save_widget_position(x, y)
        self.move(x, y)

    # -------------------------------------------------------------- edge hide
    def _edge_hide_enabled(self) -> bool:
        return bool(config_manager.get("EDGE_HIDE_ENABLED", True))

    def _try_edge_snap(self) -> bool:
        """Check whether the ball is close to any screen edge and snap it.

        Only the left and right edges auto-hide.  Top/bottom remain available
        as normal drag positions so the taskbar and title areas are not covered.
        """
        if self._expanded:
            self._edge_unsnap()
            return False
        if not self._edge_hide_enabled():
            self._edge_unsnap()
            return False
        work = self._work_area()
        size = self._compact_size()
        x, y = self.x(), self.y()
        threshold = 36

        left_d = x - work.left
        right_d = work.right - (x + size)
        candidates = [
            ("left", left_d),
            ("right", right_d),
        ]
        direction, closest = min(candidates, key=lambda item: abs(item[1]))
        if abs(closest) > threshold:
            self._edge_unsnap()
            return False

        if direction == "left":
            x = work.left
        elif direction == "right":
            x = work.right - size
        y = max(work.top, min(y, work.bottom - size))
        self._animate_edge_to(QPoint(x, y), 180)
        config_manager.save_widget_position(x, y)
        self._edge_direction = direction
        self._edge_snapped = True
        self._edge_hidden = False
        # 先完成吸附，再短暂停留，避免松手后悬浮球立刻消失。
        self._edge_hide_timer.start(850)
        self._reschedule_refresh()
        return True

    def _animate_edge_to(self, target: QPoint, duration: int) -> None:
        self._edge_animation.stop()
        self._edge_animation.setDuration(duration)
        self._edge_animation.setStartValue(self.pos())
        self._edge_animation.setEndValue(target)
        self._edge_animation.start()

    def _do_edge_hide(self) -> None:
        """Slide the window mostly off-screen, leaving a thin trigger strip.

        The strip is slightly wider than the older 4px version so users
        can actually see it; on high-DPI displays a 4px strip disappears.
        """
        if (
            not self._edge_snapped
            or self._expanded
            or self._drag_started
            or self._transitioning
            or self._edge_hovering
        ):
            return
        work = self._work_area()
        size = self._compact_size()
        strip = 10
        x, y = self.x(), self.y()
        if self._edge_direction == "left":
            x = work.left - size + strip
        elif self._edge_direction == "right":
            x = work.right - strip
        self._edge_hidden = True
        self._animate_edge_to(QPoint(x, y), 240)
        # Start polling the global mouse position — enterEvent/leaveEvent
        # are unreliable on frameless layered windows under Windows.
        # 200ms / 5 Hz is plenty fast enough for a hover reveal, and cuts
        # idle CPU versus the earlier 80ms / 12.5 Hz loop.
        self._edge_hover_check.start(100)

    def _check_edge_hover(self) -> None:
        """Poll global mouse position and decide whether to show or hide.

        The reveal region follows the ball's visible strip instead of the full
        screen edge, preventing unrelated edge movement from waking it."""
        if not self._edge_snapped or self._expanded:
            self._edge_hover_check.stop()
            return
        cursor = QCursor.pos()
        work = self._work_area()
        reveal_zone = 28
        vertical_hit = self.y() - 24 <= cursor.y() <= self.y() + self._compact_size() + 24
        hit = False
        if self._edge_direction == "left":
            hit = work.left <= cursor.x() <= work.left + reveal_zone and vertical_hit
        elif self._edge_direction == "right":
            hit = work.right - reveal_zone <= cursor.x() <= work.right and vertical_hit
        if hit:
            if not self._edge_hovering:
                self._edge_hovering = True
                self._edge_leave_timer.stop()
                self._edge_restore()
        else:
            if self._edge_hovering:
                self._edge_hovering = False
                self._edge_leave_timer.start(600)

    def _do_edge_leave(self) -> None:
        """Mouse has left the trigger area long enough — hide again."""
        if self._edge_snapped and not self._edge_hovering:
            self._do_edge_hide()

    def _edge_unsnap(self) -> None:
        """Cancel any pending edge-hide and clear snap state."""
        if self._edge_snapped:
            self._edge_animation.stop()
            self.move(self._edge_visible_position())
        self._edge_hide_timer.stop()
        self._edge_leave_timer.stop()
        self._edge_hover_check.stop()
        self._edge_snapped = False
        self._edge_direction = ""
        self._edge_hidden = False

    def _edge_visible_position(self) -> QPoint:
        work = self._work_area()
        size = self._compact_size()
        if self._edge_direction == "left":
            return QPoint(work.left, self.y())
        if self._edge_direction == "right":
            return QPoint(work.right - size, self.y())
        return self.pos()

    def _edge_restore(self) -> None:
        """Bring the window fully back on-screen after hovering the strip."""
        if not self._edge_snapped:
            return
        self._edge_hidden = False
        self._animate_edge_to(self._edge_visible_position(), 220)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        toggle = QAction("展开/收起", menu)
        toggle.triggered.connect(self.toggle)
        refresh = QAction("刷新", menu)
        refresh.triggered.connect(self.refresh)
        settings = QAction("设置", menu)
        settings.triggered.connect(self.open_settings)
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.close)
        menu.addActions((toggle, refresh, settings))
        menu.addSeparator()
        menu.addAction(quit_action)
        menu.exec(event.globalPos())

    def open_settings(self) -> None:
        if self._settings_window and self._settings_window.isVisible():
            self._settings_window.raise_()
            self._settings_window.activateWindow()
            return
        # Reuse the same dialog so repeated opens do not duplicate signal
        # connections or leave hidden child windows behind.
        if self._settings_window is None:
            self._settings_window = SettingsWindow(self, on_saved=self._on_config_saved)
        self._settings_window.show()
        self._settings_window.raise_()
        self._settings_window.activateWindow()

    def _on_config_saved(self) -> None:
        config_manager.load_config()
        self._reschedule_refresh()
        self.refresh()

    def refresh(self) -> None:
        with self._refresh_lock:
            if self._closed:
                return
            if self._refreshing:
                self._pending_refresh = True
                return
            self._refreshing = True
            self._request_id += 1
            request_id = self._request_id
        self._apply_update()
        task = FetchTask(request_id)
        task.signals.finished.connect(self._finish_refresh)
        self._thread_pool.start(task)

    @Slot(int, object)
    def _finish_refresh(self, request_id: int, result: TokenData) -> None:
        with self._refresh_lock:
            if self._closed:
                return
            if request_id == self._request_id:
                self._data = result
            self._refreshing = False
            pending = self._pending_refresh
            self._pending_refresh = False
        self._apply_update()
        if pending:
            QTimer.singleShot(0, self.refresh)

    def _apply_update(self) -> None:
        loading = self._refreshing and self._data.last_success_at is None
        self.ball.set_values(
            "--" if loading else format_money(self._data.today_cost_cny),
            "--" if loading else format_money(self._data.balance_cny),
        )
        self.panel.set_refreshing(self._refreshing)
        if self._expanded:
            self.panel.update_data(self._data, loading)

    def _periodic_refresh(self) -> None:
        self.refresh()
        self._reschedule_refresh()

    def _reschedule_refresh(self) -> None:
        configured = int(config_manager.get("REFRESH_INTERVAL", 60_000))
        # Panel visible = refresh at the configured interval (min 60s).
        # Compact + snapped = almost never — the user can't see the data, so
        # fetching it wastes CPU and network. 10 minutes is fine.
        # Compact + normal (ball visible) = every 5 minutes.
        if self._expanded:
            interval = configured
        elif self._edge_snapped:
            interval = max(configured * 10, 600_000)
        else:
            interval = max(configured * 5, 300_000)
        self._refresh_timer.start(interval)

    def set_visible_from_tray(self) -> None:
        # 托盘点击时：如果处于贴边隐藏状态，先完整恢复显示
        if self._edge_snapped and not self._expanded:
            self._edge_unsnap()
            self._clamp_to_work_area()
        self.setVisible(not self.isVisible())
        if self.isVisible():
            self.raise_()
            if self._expanded:
                self.activateWindow()

    def closeEvent(self, event) -> None:
        size = self._compact_size()
        if self._expanded:
            x, y = compact_geometry(
                (self.x(), self.y(), self.width(), self.height()),
                size,
                self._expand_horizontal,
                self._expand_vertical,
                self._work_area(),
            )
        elif self._edge_snapped:
            # 贴边隐藏时不要保存隐藏坐标；改为保存边缘的"边缘完整显示位置"
            work = self._work_area()
            x, y = self.x(), self.y()
            if x < work.left:
                x = work.left
            elif x + size > work.right:
                x = work.right - size
            if y < work.top:
                y = work.top
            elif y + size > work.bottom:
                y = work.bottom - size
        else:
            x, y = self.x(), self.y()
        config_manager.save_widget_position(x, y)
        self._closed = True
        self._refresh_timer.stop()
        self._edge_animation.stop()
        self._edge_hide_timer.stop()
        self._edge_leave_timer.stop()
        self._edge_hover_check.stop()
        self._thread_pool.clear()
        event.accept()
        QApplication.instance().quit()
