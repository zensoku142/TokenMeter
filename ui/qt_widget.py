"""Qt floating window coordinating the ball, panel, refresh, and settings."""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QColor, QCursor, QGuiApplication, QPalette, QRegion
from PySide6.QtWidgets import QApplication, QHBoxLayout, QMenu, QSystemTrayIcon, QWidget

from api.deepseek_pricing import BEIJING_TIMEZONE, PricingState, pricing_state
from api.providers import PROVIDERS, configured_provider_ids
from api.providers.base import FetchError
from api.providers.mimo import MiMoProvider
from config import runtime as config_manager
from core import pet_extension
from core.identity import APP_DISPLAY_NAME
from data.store import PerProviderData, TokenData
from ui.formatting import format_codex_reset_time, format_money, format_reset_countdown
from ui.geometry import (
    WorkArea,
    clamp_window,
    compact_geometry,
    expanded_panel_geometry,
)
from ui.i18n import bind_text, tr
from ui.qt_ball import FloatingUsageBall
from ui.qt_theme import current_theme, theme_controller
from ui.qt_update import AppUpdateController
from ui.vpet_host import VPetHost, usage_message

if TYPE_CHECKING:
    from ui.qt_panel import MainPanel
    from ui.qt_settings import SettingsWindow


DEF_PANEL_W = 820
DEF_PANEL_H = 550
DEF_BALL_SIZE = 88
MIN_BALL_SIZE = 72
MAX_BALL_SIZE = 124
BALL_SIZE_STEP = 4
BALL_SIZE_SAVE_DELAY_MS = 300
BACKGROUND_PROVIDER_INTERVAL_MS = 60_000


class FetchSignals(QObject):
    finished = Signal(int, str, object)


class FetchTask(QRunnable):
    def __init__(
        self,
        request_id: int,
        config: dict[str, object],
        lightweight: bool = False,
    ):
        super().__init__()
        self.request_id = request_id
        self._config = dict(config)
        self.provider_id = str(self._config.get("ACTIVE_PROVIDER", "")).strip().lower()
        self._lightweight = lightweight
        self.signals = FetchSignals()

    @Slot()
    def run(self) -> None:
        result = _fetch_tokens_safely(self._config, self._lightweight)
        self.signals.finished.emit(self.request_id, self.provider_id, result)


class MiMoRenewalSignals(QObject):
    finished = Signal(str, str)


class MiMoRenewalTask(QRunnable):
    """Renew MiMo cookies through the retained browser profile off the UI thread."""

    _NO_VISIBLE_RETRY = {
        "CHROME_NOT_FOUND",
        "USER_DATA_DIR_FAILED",
        "NO_FREE_CDP_PORT",
        "CHROME_LAUNCH_FAILED",
    }

    def __init__(self) -> None:
        super().__init__()
        self.signals = MiMoRenewalSignals()
        self._stop_event = threading.Event()

    def cancel(self) -> None:
        self._stop_event.set()

    @Slot()
    def run(self) -> None:
        try:
            cookie = MiMoProvider.recover_verified_cookie_via_chrome(
                self._stop_event,
                headless=True,
            )
        except RuntimeError as exc:
            code = str(exc)
            current_provider = str(
                config_manager.get("ACTIVE_PROVIDER", "")
            ).strip().lower()
            if (
                self._stop_event.is_set()
                or code in self._NO_VISIBLE_RETRY
                or current_provider != "mimo"
            ):
                self.signals.finished.emit("", code)
                return
            try:
                cookie = MiMoProvider.recover_verified_cookie_via_chrome(
                    self._stop_event,
                    headless=False,
                )
            except RuntimeError as visible_exc:
                self.signals.finished.emit("", str(visible_exc))
                return
            except Exception:
                self.signals.finished.emit("", "ACQUIRE_UNEXPECTED")
                return
        except Exception:
            self.signals.finished.emit("", "ACQUIRE_UNEXPECTED")
            return
        direct_usable = MiMoProvider.is_direct_cookie_usable(cookie)
        self.signals.finished.emit(
            cookie,
            "" if direct_usable else "BROWSER_CONTEXT_ONLY",
        )


def _fetch_tokens_safely(
    config: dict[str, object], lightweight: bool = False
) -> TokenData:
    """Fetch token data from a captured provider and keep the worker thread
    from dying if a provider or config error is raised."""

    try:
        return TokenData.fetch(lightweight=lightweight, config=config, include_minute_history=False)
    except Exception:
        config_manager.logger().exception("Background refresh failed")
        provider_id = str(config.get("ACTIVE_PROVIDER", "")).strip().lower()
        provider_cls = PROVIDERS.get(provider_id)
        per_provider = []
        if provider_cls is not None:
            per_provider.append(
                PerProviderData(
                    provider_id,
                    provider_cls.name,
                    currency=provider_cls.default_currency,
                    status="error",
                )
            )
        data = TokenData(
            currency=provider_cls.default_currency if provider_cls is not None else "CNY",
            per_provider=per_provider,
            status="error",
        )
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
        # 启动时先展示同一账号的落盘快照；网络刷新继续在后台按原频率进行。
        self._data = (
            TokenData.persisted_snapshot(config_manager.all_config()) or TokenData()
        )
        self._refresh_lock = threading.Lock()
        self._refreshing = False
        self._request_id = 0
        self._in_flight_requests: dict[str, int] = {}
        self._pending_refreshes: dict[
            str, tuple[dict[str, object], bool, str]
        ] = {}
        self._provider_results: dict[str, TokenData] = {}
        self._provider_last_started: dict[str, float] = {}
        self._provider_task_started: dict[str, float] = {}
        self._closed = False
        self._vpet = VPetHost(self)
        self._vpet_updating = False
        self._vpet.ready.connect(self._on_vpet_ready)
        self._vpet.failed.connect(self._on_vpet_failed)
        self._vpet.action_requested.connect(self._on_vpet_action)
        QApplication.instance().aboutToQuit.connect(self._vpet.stop)
        self._auth_expired_providers: set[str] = set()
        self._auth_notified_providers: set[str] = set()
        self._auth_expired_provider_id: str | None = None
        self._mimo_renewal_task: MiMoRenewalTask | None = None
        self._mimo_renewal_attempted = False
        self._transitioning = False
        self._expand_horizontal = "right"
        self._expand_vertical = "down"
        self._drag_origin = QPoint()
        self._window_origin = QPoint()
        self._drag_started = False
        self._drag_source = ""
        self._panel_resize_origin: tuple[QPoint, int, int, bool] | None = None
        self._settings_window: SettingsWindow | None = None
        self._update_controller = AppUpdateController(self)
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
        self._ball_size_save_timer = QTimer(self)
        self._ball_size_save_timer.setSingleShot(True)
        self._ball_size_save_timer.setInterval(BALL_SIZE_SAVE_DELAY_MS)
        self._ball_size_save_timer.timeout.connect(self._save_ball_size)
        self._panel_width_save_timer = QTimer(self)
        self._panel_width_save_timer.setSingleShot(True)
        self._panel_width_save_timer.setInterval(300)
        self._panel_width_save_timer.timeout.connect(self._save_panel_width)
        self._pending_ball_position: QPoint | None = None
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
        # 图表面板会加载 pyqtgraph/NumPy；悬浮球常驻时延迟创建可显著降低基线内存。
        self.panel: MainPanel | None = None
        self._layout.addWidget(self.ball, 0, Qt.AlignmentFlag.AlignTop)
        self._panel_resize_handles = (QWidget(self), QWidget(self))
        for handle in self._panel_resize_handles:
            handle.setCursor(Qt.CursorShape.SizeHorCursor)
            handle.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
            handle.installEventFilter(self)
            handle.hide()
        self._connect_ui()
        controller = theme_controller()
        controller.changed.connect(self._on_theme_state_changed)
        self._sync_theme_controls(controller.mode, controller.resolved)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._periodic_refresh)
        self._background_refresh_timer = QTimer(self)
        self._background_refresh_timer.timeout.connect(self._periodic_background_refresh)
        self._background_refresh_timer.start(BACKGROUND_PROVIDER_INTERVAL_MS)
        self._pricing_state: PricingState | None = None
        self._pricing_timer = QTimer(self)
        self._pricing_timer.setSingleShot(True)
        self._pricing_timer.timeout.connect(self._on_pricing_boundary)
        # Schedule exact boundaries so the panel and notifications stay current
        # without adding an idle polling timer.
        self._sync_pricing_state(notify_transition=False)
        self._show_compact_at_saved_position()
        self._update_controller.schedule_startup_check()
        self._reschedule_refresh()
        self.refresh()

        self._sync_vpet()

    def _sync_vpet(self) -> None:
        if self._closed or self._vpet_updating:
            return
        # 启动也检查安装状态，防止旧的启用配置绕过设置页限制并自动调用本地开发宿主。
        if config_manager.get("VPET_ENABLED", False) and pet_extension.installed_manifest() is not None:
            self._vpet.start(config_manager.CONFIG_DIR / "vpet")
        else:
            was_active = self._vpet.active
            self._vpet.stop()
            if was_active and not self._expanded:
                self.show()

    def _pause_vpet_update(self) -> None:
        # 自动保存也会同步桌宠；更新期间必须禁止重启，避免 Windows 文件占用和替换竞态。
        self._vpet_updating = True
        self._vpet.stop()
        if not self._expanded and not self._closed:
            self.show()

    def _resume_vpet_update(self) -> None:
        self._vpet_updating = False
        self._sync_vpet()

    def _on_vpet_ready(self) -> None:
        if not self._expanded:
            self.hide()
        self._apply_update()

    def _on_vpet_failed(self, message: str) -> None:
        if self._closed:
            return
        config_manager.logger().warning("VPet: %s", message)
        self._set_theme_feedback(message, "danger")
        if not self._expanded:
            self.show()
        if self.tray is not None:
            self.tray.showMessage(APP_DISPLAY_NAME, message, QSystemTrayIcon.MessageIcon.Warning, 6000)

    def _on_vpet_action(self, action: str) -> None:
        if self._closed:
            return
        if action == "open_panel":
            self._vpet.set_visible(True)
            self.expand_panel()
            self.show()
            self.raise_()
            self.activateWindow()
        elif action == "open_settings":
            self.open_settings()
        elif action == "disable_pet":
            try:
                config_manager.save_config({"VPET_ENABLED": False})
            except Exception:
                config_manager.logger().exception("VPet preference could not be saved")
                return
            self._sync_vpet()
            if self._settings_window is not None:
                self._settings_window.vpet_check.setChecked(False)
        elif action == "quit":
            self.close()

    def _connect_ui(self) -> None:
        self.ball.pressed.connect(lambda point: self._start_drag(point, "ball"))
        self.ball.dragged.connect(self._move_drag)
        self.ball.released.connect(self._end_drag)
        self.ball.resize_requested.connect(self._resize_ball_by_wheel)

    def _ensure_panel(self) -> MainPanel:
        if self.panel is not None:
            return self.panel
        from ui.qt_panel import MainPanel

        panel = MainPanel()
        panel.hide()
        panel.header.pressed.connect(lambda point: self._start_drag(point, "header"))
        panel.header.dragged.connect(self._move_drag)
        panel.header.released.connect(self._end_drag)
        panel.settings_requested.connect(self.open_settings)
        panel.refresh_requested.connect(self.refresh)
        panel.provider_selected.connect(self._switch_provider)
        panel.close_requested.connect(self.collapse_panel)
        if hasattr(panel, "theme_requested"):
            panel.theme_requested.connect(self._request_theme_change)
        panel.activity_height_changed.connect(self._resize_expanded_panel)
        self.panel = panel
        controller = theme_controller()
        panel.set_theme_mode(controller.mode, controller.resolved)
        if self._pricing_state is None:
            panel.set_pricing_state(False)
        else:
            panel.set_pricing_state(
                True,
                self._pricing_state.is_peak,
                self._pricing_state.label,
                self._pricing_state.tooltip,
            )
        return panel

    @Slot(str)
    def _request_theme_change(self, mode: str) -> None:
        controller = theme_controller()
        previous_mode = controller.mode
        saved = False
        try:
            # Theme is a global immediate preference, independent of the
            # settings dialog's deferred credential/configuration save path.
            config_manager.save_ui_theme(mode)
            saved = True
            controller.set_mode(mode)
        except Exception as exc:
            if saved:
                try:
                    config_manager.save_ui_theme(previous_mode)
                except Exception:
                    config_manager.logger().exception("Theme preference rollback failed")
            if controller.mode != previous_mode:
                try:
                    controller.set_mode(previous_mode)
                except Exception:
                    config_manager.logger().exception("Theme controller rollback failed")
            config_manager.logger().exception("Theme change failed: %s", exc)
            self._sync_theme_controls(controller.mode, controller.resolved)
            self._set_theme_feedback("主题切换失败，已恢复原设置。", "danger")
            return
        self._sync_theme_controls(controller.mode, controller.resolved)
        if self._settings_window is not None and self._settings_window.isVisible():
            self._settings_window.set_theme_feedback("主题已切换。", "success")

    @Slot(str, str, int)
    def _preview_appearance_change(
        self, theme_name: str, accent_color: str, panel_opacity: int
    ) -> None:
        try:
            theme_controller().set_appearance(
                theme_name, accent_color, panel_opacity
            )
        except (TypeError, ValueError) as exc:
            config_manager.logger().warning("Theme appearance preview rejected: %s", exc)
            self._set_theme_feedback("主题外观预览失败，请检查输入。", "danger")

    @Slot(str, str, int)
    def _request_appearance_change(
        self, theme_name: str, accent_color: str, panel_opacity: int
    ) -> None:
        controller = theme_controller()
        normalized_theme = str(theme_name).strip().lower()
        color_key = f"UI_{normalized_theme.upper()}_ACCENT_COLOR"
        opacity_key = f"UI_{normalized_theme.upper()}_PANEL_OPACITY"
        previous_appearance = controller.appearance(normalized_theme)
        previous_color = str(
            config_manager.get(color_key, previous_appearance[0])
        )
        previous_opacity = int(
            config_manager.get(opacity_key, previous_appearance[1])
        )
        saved = False
        try:
            config_manager.save_ui_appearance(
                normalized_theme, accent_color, panel_opacity
            )
            saved = True
            controller.set_appearance(
                normalized_theme, accent_color, panel_opacity
            )
        except Exception as exc:
            if saved:
                try:
                    config_manager.save_ui_appearance(
                        normalized_theme, previous_color, previous_opacity
                    )
                except Exception:
                    config_manager.logger().exception(
                        "Theme appearance rollback persistence failed"
                    )
            try:
                controller.set_appearance(
                    normalized_theme, previous_color, previous_opacity
                )
            except Exception:
                config_manager.logger().exception("Theme appearance rollback failed")
            config_manager.logger().exception("Theme appearance change failed: %s", exc)
            self._sync_theme_controls(controller.mode, controller.resolved)
            self._set_theme_feedback("主题外观保存失败，已恢复原设置。", "danger")
            return
        self._sync_theme_controls(controller.mode, controller.resolved)
        self._set_theme_feedback("主题外观已保存。", "success")

    def _on_theme_state_changed(self, mode: str, resolved: str) -> None:
        self._sync_theme_controls(mode, resolved)
        self._sync_vpet_usage()

    def _sync_theme_controls(self, mode: str, resolved: str) -> None:
        sync_panel = getattr(self.panel, "set_theme_mode", None)
        if callable(sync_panel):
            sync_panel(mode, resolved)
        if self._settings_window is not None:
            # 关闭竞态或嵌入调用方可能暂时挂接普通 QDialog；主题广播不能因此中断。
            sync_settings = getattr(self._settings_window, "set_theme_mode", None)
            if callable(sync_settings):
                sync_settings(mode, resolved)

    def _set_theme_feedback(self, message: str, tone: str) -> None:
        panel_feedback = getattr(self.panel, "set_theme_feedback", None)
        if callable(panel_feedback):
            panel_feedback(message, tone)
        if self._settings_window is not None and self._settings_window.isVisible():
            settings_feedback = getattr(self._settings_window, "set_theme_feedback", None)
            if callable(settings_feedback):
                settings_feedback(message, tone)

    def _compact_size(self) -> int:
        if not hasattr(self, "_ball_size"):
            saved = config_manager.load_widget_size()
            configured = (
                config_manager.get("WIDGET_COMPACT_SIZE", DEF_BALL_SIZE)
                if saved is None
                else saved
            )
            self._ball_size = max(
                MIN_BALL_SIZE, min(MAX_BALL_SIZE, int(configured))
            )
        return self._ball_size

    def _expanded_size(self) -> tuple[int, int]:
        if not hasattr(self, "_panel_width"):
            saved = config_manager.load_panel_width()
            size = config_manager.get("WIDGET_EXPANDED_SIZE", (DEF_PANEL_W, DEF_PANEL_H))
            self._panel_width = int(size[0]) if saved is None else saved
        width = max(640, min(DEF_PANEL_W, self._panel_width))
        return width, self._ensure_panel().height()

    def _set_panel_width(self, width: int, right_edge: int | None = None) -> None:
        if not self._expanded:
            return
        work = self._work_area()
        width = min(max(640, min(DEF_PANEL_W, width)), max(1, work.width - 16))
        panel = self._ensure_panel()
        panel.setMinimumWidth(min(640, width))
        self.setFixedWidth(width)
        if right_edge is not None:
            self.move(right_edge - width, self.y())
        self._clamp_to_work_area()
        if self._panel_width != width:
            self._panel_width = width
            # 连续拖动只在停顿后落盘，退出前再冲刷最后一次修改。
            self._panel_width_save_timer.start()

    def _save_panel_width(self) -> None:
        config_manager.save_panel_width(self._panel_width)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not hasattr(self, "_panel_resize_handles"):
            return
        for index, handle in enumerate(self._panel_resize_handles):
            # 只覆盖边缘留白，避免截获标题栏按钮、图表和内嵌设置的操作。
            handle.setGeometry(
                0 if index == 0 else self.width() - 6, 8, 6,
                max(1, self.height() - 16),
            )
            handle.setVisible(self._expanded)
            handle.raise_()

    def eventFilter(self, watched, event) -> bool:
        if watched in self._panel_resize_handles and self._expanded:
            kind = event.type()
            if (
                kind == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
            ):
                self._panel_resize_origin = (
                    event.globalPosition().toPoint(), self.x(), self.width(),
                    watched is self._panel_resize_handles[0],
                )
                return True
            if kind == QEvent.Type.MouseMove and self._panel_resize_origin is not None:
                origin, x, width, left = self._panel_resize_origin
                delta = event.globalPosition().toPoint().x() - origin.x()
                self._set_panel_width(
                    width - delta if left else width + delta,
                    x + width if left else None,
                )
                return True
            if (
                kind == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
            ):
                self._panel_resize_origin = None
                return True
        return super().eventFilter(watched, event)

    def _resize_expanded_panel(self, height: int) -> None:
        if not self._expanded:
            return
        # 视图切换发生在展开态；外层窗口必须同步尺寸，否则缩短的面板会在底部留下透明占位。
        self.setFixedHeight(height)
        self._clamp_to_work_area()

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
        if self.panel is not None:
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
        panel = self._ensure_panel()
        while self._layout.count():
            self._layout.takeAt(0)
        # 展开态完全由面板替代悬浮球，避免重复入口并缩小窗口占用。
        self.ball.hide()
        self._layout.addWidget(panel, 1)

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
            panel = self._ensure_panel()
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
            panel.show()
            panel.setMinimumWidth(min(640, width))
            self.setFixedSize(width, height)
            self.move(x, y)
            self.show()
            self._apply_native_window_shape(compact=False)
            self.raise_()
            self.activateWindow()
            panel.setFocus(Qt.FocusReason.OtherFocusReason)
            loading = self._refreshing and self._data.last_success_at is None
            panel.update_data(self._data, loading, self._refreshing)
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
            self._panel_resize_origin = None
            if self.panel is not None:
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
            # 收回悬浮球时恢复置顶标志，让悬浮球始终浮在其它窗口之上。
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            self.clearMask()
            self.show()
            self._apply_native_window_shape(compact=True)
            self.raise_()
        finally:
            self._transitioning = False
        self._reschedule_refresh()
        if self._vpet.active:
            self.hide()

    def event(self, event) -> bool:
        if (
            event.type() == QEvent.Type.WindowDeactivate
            and self._expanded
            and not self._transitioning
            and bool(config_manager.get("PANEL_AUTO_COLLAPSE_ON_DEACTIVATE", True))
        ):
            # Defer until Qt has finished activating a possible picker or popup.
            QTimer.singleShot(0, self._collapse_after_deactivation)
        return super().event(event)

    def _collapse_after_deactivation(self) -> None:
        # 退出会在下一轮事件循环触发失焦回调；已关闭的面板不能再收起并重新显示悬浮球。
        if self._closed:
            return
        # 设置页沿用面板的失焦收起规则，但操作文件/颜色对话框或下拉菜单时不能误收起。
        if (
            bool(config_manager.get("PANEL_AUTO_COLLAPSE_ON_DEACTIVATE", True))
            and self._expanded
            and not self._transitioning
            and not self._drag_started
            and self._panel_resize_origin is None
            and QApplication.activeModalWidget() is None
            and QApplication.activePopupWidget() is None
            and not self.isActiveWindow()
        ):
            if self._has_settings_child():
                # 先保存并退出设置，再收回悬浮球，确保下次展开显示数据页。
                self._settings_window.reject()
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
        if source == "ball":
            self.ball.begin_container_motion(QPointF(self.pos()))

    def _move_drag(self, point: QPoint) -> None:
        delta = point - self._drag_origin
        if not self._drag_started and delta.manhattanLength() < 5:
            return
        self._drag_started = True
        self.move(self._window_origin + delta)
        if self._drag_source == "ball":
            self.ball.sample_container_motion(QPointF(self.pos()))

    def _end_drag(self, _point: QPoint) -> None:
        drag_source = self._drag_source
        if self._drag_started:
            # 先判断边缘吸附；若没有贴边再做常规工作区约束。这样用户
            # 把球拖到桌面任意边缘接触时都会被吸附并自动隐藏，而不是
            # 被 clamp 拉回安全距离后再检测。
            if not self._try_edge_snap():
                self._clamp_to_work_area()
        elif self._drag_source == "ball":
            self.toggle()
        if drag_source == "ball":
            self.ball.end_container_motion(QPointF(self.pos()))
        self._drag_started = False
        self._drag_source = ""

    def _resize_ball_by_wheel(self, steps: int) -> None:
        size = max(
            MIN_BALL_SIZE,
            min(MAX_BALL_SIZE, self._compact_size() + steps * BALL_SIZE_STEP),
        )
        if size == self._compact_size():
            return
        self._edge_animation.stop()
        self._edge_hide_timer.stop()
        self._edge_leave_timer.stop()
        self._edge_unsnap()
        center_x = self.x() + self.width() // 2
        center_y = self.y() + self.height() // 2
        self._ball_size = size
        self.ball.setFixedSize(size, size)
        self.setFixedSize(size, size)
        work = self._work_area()
        x, y = clamp_window(
            center_x - size // 2,
            center_y - size // 2,
            size,
            size,
            work,
        )
        self.move(x, y)
        self._apply_native_window_shape(compact=True)
        self._pending_ball_position = QPoint(x, y)
        self._ball_size_save_timer.start()

    def _save_ball_size(self) -> None:
        if self._pending_ball_position is not None:
            config_manager.save_widget_position(
                self._pending_ball_position.x(), self._pending_ball_position.y()
            )
            self._pending_ball_position = None
        config_manager.save_widget_size(self._compact_size())

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

        # 拖拽时鼠标通常抓在球体中间；只看窗口左上角会要求用户把球拖得过深，
        # 导致“已经碰到边缘但仍不吸附”。这里按整个球体与边缘的最近距离判定，
        # 只要球已经接触/覆盖边缘，就按 0 距离立即吸附。
        def edge_distance(edge_x: int) -> int:
            ball_left = x
            ball_right = x + size
            if ball_left <= edge_x <= ball_right:
                return 0
            return min(abs(ball_left - edge_x), abs(ball_right - edge_x))

        left_d = edge_distance(work.left)
        right_d = edge_distance(work.right)
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
        self.setWindowOpacity(1.0)
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
        """Slide partially off-screen while keeping a recognizable ball segment."""
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
        visible_extent = self._edge_visible_extent()
        x, y = self.x(), self.y()
        if self._edge_direction == "left":
            x = work.left - size + visible_extent
        elif self._edge_direction == "right":
            x = work.right - visible_extent
        self._edge_hidden = True
        self.setWindowOpacity(1.0)
        self._animate_edge_to(QPoint(x, y), 240)
        # Start polling the global mouse position — enterEvent/leaveEvent
        # are unreliable on frameless layered windows under Windows.
        # 200ms / 5 Hz is plenty fast enough for a hover reveal, and cuts
        # idle CPU versus the earlier 80ms / 12.5 Hz loop.
        self._edge_hover_check.start(100)

    def _edge_visible_extent(self) -> int:
        return max(12, min(16, round(self._compact_size() * 0.16)))

    def _edge_reveal_extent(self) -> int:
        return max(40, self._edge_visible_extent() + 16)

    def _check_edge_hover(self) -> None:
        """Poll global mouse position and decide whether to show or hide.

        The reveal region follows the ball's visible strip instead of the full
        screen edge, preventing unrelated edge movement from waking it."""
        if not self._edge_snapped or self._expanded:
            self._edge_hover_check.stop()
            return
        cursor = QCursor.pos()
        work = self._work_area()
        reveal_zone = self._edge_reveal_extent()
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
        # 取消贴边时必须清掉悬停唤出状态；否则下次重新贴边会被误判为仍在悬停，
        # 自动隐藏会直接被跳过。
        self._edge_hovering = False
        self._edge_snapped = False
        self._edge_direction = ""
        self._edge_hidden = False
        self.setWindowOpacity(1.0)

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
        self.setWindowOpacity(1.0)
        self._animate_edge_to(self._edge_visible_position(), 220)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        toggle = bind_text(QAction(menu), "展开/收起")
        toggle.triggered.connect(self.toggle)
        refresh = bind_text(QAction(menu), "刷新")
        refresh.triggered.connect(self.refresh)
        settings = bind_text(QAction(menu), "设置")
        settings.triggered.connect(self.open_settings)
        quit_action = bind_text(QAction(menu), "退出")
        quit_action.triggered.connect(self.close)
        menu.addActions((toggle, refresh, settings))
        menu.addSeparator()
        menu.addAction(quit_action)
        menu.exec(event.globalPos())

    def open_settings(
        self,
        provider_id: str | None = None,
        start_cookie_acquisition: bool = False,
    ) -> None:
        panel = self._ensure_panel()
        if self._settings_window is None:
            from ui.qt_settings import SettingsWindow

            # 复用内嵌设置页，保留未保存草稿，避免重复连接信号。
            self._settings_window = SettingsWindow(
                panel,
                on_saved=self._on_config_saved,
                update_controller=self._update_controller,
                embedded=True,
            )
            self._settings_window.finished.connect(panel.show_overview)
            self._settings_window.pet_update_started.connect(self._pause_vpet_update)
            self._settings_window.pet_update_finished.connect(self._resume_vpet_update)
            panel.settings_back_button.clicked.connect(self._settings_window.reject)
            self._settings_window.save_state_changed.connect(panel.set_settings_save_status)
            self._settings_window.theme_requested.connect(self._request_theme_change)
            self._settings_window.appearance_preview_requested.connect(
                self._preview_appearance_change
            )
            self._settings_window.appearance_requested.connect(
                self._request_appearance_change
            )
            controller = theme_controller()
            self._settings_window.set_theme_mode(controller.mode, controller.resolved)
        panel.show_settings(self._settings_window)
        self.expand_panel()
        self.show()
        self.raise_()
        self.activateWindow()
        self._settings_window.setFocus(Qt.FocusReason.OtherFocusReason)
        if provider_id:
            self._settings_window.open_provider(provider_id, start_cookie_acquisition)

    def _on_config_saved(self) -> None:
        config_manager.load_config()
        self._sync_vpet()
        # 设置保存后允许所有失效 Provider 各验证一次；验证成功后恢复定时采集，
        # 仍然失效则只重新进入一次通知周期。
        self._auth_expired_providers.clear()
        self._auth_notified_providers.clear()
        self._sync_pricing_state(notify_transition=False)
        self._update_controller.reload_cached_release()
        self._update_controller.schedule_startup_check()
        self._reschedule_refresh()
        self.refresh()

    @Slot(str)
    def _switch_provider(self, provider_id: str) -> None:
        provider_id = provider_id.strip().lower()
        if not provider_id or provider_id == config_manager.get("ACTIVE_PROVIDER", ""):
            return
        try:
            config_snapshot = config_manager.save_config({"ACTIVE_PROVIDER": provider_id})
        except Exception:
            config_manager.logger().exception("Quick provider switch failed")
            loading = self._refreshing and self._data.last_success_at is None
            if self.panel is not None:
                self.panel.update_data(self._data, loading, self._refreshing)
            return
        self._sync_pricing_state(notify_transition=False)
        provider_cls = PROVIDERS[provider_id]
        account_key = TokenData.account_key_for_config(config_snapshot)
        cached = self._provider_results.get(provider_id)
        if cached is not None and cached.account_key != account_key:
            cached = None
        if cached is None:
            cached = TokenData.cached_snapshot(provider_id, account_key)
        self._prepare_scope_switch(
            cached
            or TokenData(
                currency=provider_cls.default_currency,
                per_provider=[
                    PerProviderData(
                        provider_id,
                        provider_cls.name,
                        currency=provider_cls.default_currency,
                        status="loading",
                    )
                ],
                status="loading",
            ),
            config_snapshot,
        )

    def _prepare_scope_switch(
        self, data: TokenData, config_snapshot: dict[str, object]
    ) -> None:
        self._data = data
        # 旧 Provider 继续后台完成；回调按最新 ACTIVE_PROVIDER 决定是否更新界面。
        self.refresh(force=True, config_snapshot=config_snapshot)

    def refresh(
        self,
        *,
        force: bool = False,
        config_snapshot: dict[str, object] | None = None,
        queue_if_busy: bool = True,
        reason: str = "manual",
    ) -> None:
        captured_config = dict(
            config_snapshot
            if config_snapshot is not None
            else config_manager.all_config()
        )
        provider_id = str(captured_config.get("ACTIVE_PROVIDER", "")).strip().lower()
        self._start_provider_refresh(
            provider_id,
            captured_config,
            lightweight=self._uses_lightweight_mimo_refresh(provider_id),
            queue_if_busy=queue_if_busy and not force,
            reason="switch" if force else reason,
        )

    def _start_provider_refresh(
        self,
        provider_id: str,
        config_snapshot: dict[str, object],
        *,
        lightweight: bool,
        queue_if_busy: bool,
        reason: str,
    ) -> bool:
        provider_id = provider_id.strip().lower()
        if not provider_id:
            return False
        if reason in {"periodic_current", "periodic_background"} and provider_id in (
            self._auth_expired_providers
        ):
            config_manager.logger().debug(
                "Provider collection skipped: provider=%s reason=auth_expired",
                provider_id,
            )
            return False
        captured_config = dict(config_snapshot)
        captured_config["ACTIVE_PROVIDER"] = provider_id
        is_current = provider_id == str(
            config_manager.get("ACTIVE_PROVIDER", "")
        ).strip().lower()
        account_key = TokenData.account_key_for_config(captured_config)
        cached = self._provider_results.get(provider_id)
        if cached is not None and cached.account_key != account_key:
            self._provider_results.pop(provider_id, None)
        if is_current and self._data.account_key != account_key:
            # 同平台换号也属于范围切换；旧请求未结束时不能继续显示原账号数据。
            self._data = TokenData(account_key=account_key, per_provider=[
                PerProviderData(provider_id, PROVIDERS[provider_id].name)
            ])
        with self._refresh_lock:
            if self._closed:
                return False
            if provider_id in self._in_flight_requests:
                if queue_if_busy:
                    # 同一 Provider 只保留一个逻辑待刷新，不重复创建 QRunnable。
                    self._pending_refreshes[provider_id] = (
                        captured_config,
                        lightweight,
                        reason,
                    )
                if is_current:
                    self._refreshing = True
                request_id = self._in_flight_requests[provider_id]
                started = False
            else:
                self._request_id += 1
                request_id = self._request_id
                self._in_flight_requests[provider_id] = request_id
                started_at = time.monotonic()
                self._provider_last_started[provider_id] = started_at
                self._provider_task_started[provider_id] = started_at
                if is_current:
                    self._refreshing = True
                started = True
        if is_current:
            self._apply_update()
        if not started:
            config_manager.logger().debug(
                "Provider collection skipped: provider=%s reason=in_flight request_id=%s",
                provider_id,
                request_id,
            )
            return False
        config_manager.logger().debug(
            "Provider collection started: provider=%s reason=%s",
            provider_id,
            reason,
        )
        task = FetchTask(request_id, captured_config, lightweight=lightweight)
        task.signals.finished.connect(self._finish_refresh)
        self._thread_pool.start(task)
        return True

    def _schedule_pending_refresh(
        self,
        provider_id: str,
        pending: tuple[dict[str, object], bool, str],
    ) -> None:
        captured_config, lightweight, reason = pending
        self._start_provider_refresh(
            provider_id,
            captured_config,
            lightweight=lightweight,
            queue_if_busy=False,
            reason=reason,
        )

    @Slot(int, str, object)
    def _finish_refresh(
        self, request_id: int, provider_id: str, result: TokenData
    ) -> None:
        pending: tuple[dict[str, object], bool, str] | None = None
        started_at: float | None = None
        is_current = False
        provider_id = provider_id.strip().lower()
        current_config = dict(config_manager.all_config())
        current_config["ACTIVE_PROVIDER"] = provider_id
        stale_account = bool(result.account_key) and (
            result.account_key != TokenData.account_key_for_config(current_config)
        )
        with self._refresh_lock:
            if self._closed:
                return
            if self._in_flight_requests.get(provider_id) != request_id:
                return
            self._in_flight_requests.pop(provider_id, None)
            started_at = self._provider_task_started.pop(provider_id, None)
            pending = self._pending_refreshes.pop(provider_id, None)
            if stale_account:
                # 旧请求可完成清理，但不能显示、通知或覆盖新账号的界面缓存。
                self._provider_results.pop(provider_id, None)
                pending = (current_config, self._uses_lightweight_mimo_refresh(provider_id), "account_change")
            # Refresh results are immutable after delivery; the current view and
            # provider switch cache can safely share them instead of retaining a
            # second copy of all daily and minute history rows.
            if not stale_account:
                self._provider_results[provider_id] = result
            is_current = provider_id == str(
                config_manager.get("ACTIVE_PROVIDER", "")
            ).strip().lower()
            if is_current:
                self._data = TokenData(per_provider=[
                    PerProviderData(provider_id, PROVIDERS[provider_id].name)
                ]) if stale_account else result
                self._refreshing = stale_account
        elapsed_ms = (
            max(0, int((time.monotonic() - started_at) * 1000))
            if started_at is not None
            else 0
        )
        config_manager.logger().debug(
            "Provider collection completed: provider=%s status=%s elapsed_ms=%s",
            provider_id,
            result.status,
            elapsed_ms,
        )
        if not stale_account:
            self._notify_auth_expired(result, provider_id, is_current=is_current)
        if is_current:
            self._apply_update()
        if pending is not None:
            QTimer.singleShot(
                0,
                lambda value=pending, current_id=provider_id: self._schedule_pending_refresh(
                    current_id, value
                ),
            )

    def _notify_auth_expired(
        self, result: TokenData, provider_id: str, *, is_current: bool
    ) -> None:
        auth_error = next(
            (error for error in result.errors if error.code == "AUTH_EXPIRED"), None
        )
        if auth_error is None:
            remote_failures = {
                "NETWORK_ERROR",
                "NETWORK_TIMEOUT",
                "SERVER_ERROR",
                "RATE_LIMITED",
                "UNKNOWN_ERROR",
            }
            codes = {error.code for error in result.errors}
            if result.status in {"ok", "partial"} and not codes & remote_failures:
                # 只有该 Provider 实际恢复成功后才解除熔断并重新开放通知。
                self._auth_expired_providers.discard(provider_id)
                self._auth_notified_providers.discard(provider_id)
                if self._auth_expired_provider_id == provider_id:
                    self._auth_expired_provider_id = None
                if provider_id == "mimo":
                    self._mimo_renewal_attempted = False
            return
        # 同一失效周期以 Provider 为单位，不因余额、用量等错误来源或文案变化
        # 重复通知；后续新增 Provider 只需沿用 AUTH_EXPIRED 错误码即可复用。
        self._auth_expired_providers.add(provider_id)
        if provider_id == "mimo" and is_current:
            if getattr(self, "_mimo_renewal_task", None) is not None:
                return
            if getattr(self, "_mimo_renewal_attempted", False):
                self._show_mimo_renewal_failure("AUTH_EXPIRED")
                return
            self._start_mimo_cookie_renewal()
            return

        if provider_id in self._auth_notified_providers:
            return
        tray = getattr(self, "tray", None)
        if tray is None:
            return
        self._auth_notified_providers.add(provider_id)
        self._auth_expired_provider_id = provider_id
        if provider_id == "mimo":
            message = (
                f"{auth_error.message}\n请切换到小米 MiMo 或打开设置重新登录；"
                "后台不会自动打开浏览器窗口。"
            )
        else:
            message = f"{auth_error.message}\n点击此通知可打开对应平台设置。"
        tray.showMessage(
            tr(f"{APP_DISPLAY_NAME}：登录凭据已失效"),
            tr(message),
            QSystemTrayIcon.MessageIcon.Warning,
            10_000,
        )

    def _start_mimo_cookie_renewal(self) -> None:
        if self._closed or getattr(self, "_mimo_renewal_task", None) is not None:
            return
        task = MiMoRenewalTask()
        self._mimo_renewal_task = task
        self._mimo_renewal_attempted = True
        task.signals.finished.connect(self._finish_mimo_cookie_renewal)
        self._thread_pool.start(task)

    @Slot(str, str)
    def _finish_mimo_cookie_renewal(self, cookie_text: str, error_code: str) -> None:
        self._mimo_renewal_task = None
        if self._closed:
            return
        if cookie_text and error_code != "BROWSER_CONTEXT_ONLY":
            values = MiMoProvider.acquired_cookie_values(cookie_text)
            try:
                config_manager.save_config(
                    {
                        "MIMO_COOKIE": values.get("COOKIE", ""),
                        "MIMO_API_PLATFORM_PH": values.get("API_PLATFORM_PH", ""),
                    }
                )
            except Exception:
                config_manager.logger().exception("MiMo cookie renewal could not be saved")
                error_code = "ACQUIRE_UNEXPECTED"
            else:
                settings_window = getattr(self, "_settings_window", None)
                if settings_window is not None:
                    settings_window.sync_persisted_cookie("mimo", cookie_text)
                if self._auth_expired_provider_id == "mimo":
                    self._auth_expired_provider_id = None
                self._refresh_mimo_after_renewal()
                return

        if cookie_text and error_code == "BROWSER_CONTEXT_ONLY":
            # The browser session itself was verified, but its authentication
            # cannot be safely replayed by requests. Keep existing stored
            # credentials unchanged; normal refresh will use browser fallback.
            if self._auth_expired_provider_id == "mimo":
                self._auth_expired_provider_id = None
            tray = getattr(self, "tray", None)
            if tray is not None:
                tray.showMessage(
                    tr(f"{APP_DISPLAY_NAME}：MiMo 浏览器会话已验证"),
                    tr("网页会话仍有效；TokenMeter 将在 Cookie 直连失败时使用专用浏览器查询。"),
                    QSystemTrayIcon.MessageIcon.Information,
                    10_000,
                )
            self._refresh_mimo_after_renewal()
            return

        self._show_mimo_renewal_failure(error_code)

    def _show_mimo_renewal_failure(self, error_code: str) -> None:
        self._auth_expired_providers.add("mimo")
        if "mimo" in self._auth_notified_providers:
            return
        self._auth_expired_provider_id = "mimo"
        message = MiMoProvider.describe_acquire_error(
            RuntimeError(error_code or "ACQUIRE_UNEXPECTED")
        )
        tray = getattr(self, "tray", None)
        if tray is not None:
            self._auth_notified_providers.add("mimo")
            tray.showMessage(
                tr(f"{APP_DISPLAY_NAME}：MiMo 自动续期失败"),
                tr(f"{message}\n点击此通知可手动重新获取 Cookie。"),
                QSystemTrayIcon.MessageIcon.Warning,
                10_000,
            )

    def _refresh_mimo_after_renewal(self) -> None:
        captured_config = config_manager.all_config()
        active_provider = str(
            captured_config.get("ACTIVE_PROVIDER", "")
        ).strip().lower()
        self._start_provider_refresh(
            "mimo",
            captured_config,
            lightweight=(
                active_provider != "mimo"
                or self._uses_lightweight_mimo_refresh("mimo")
            ),
            queue_if_busy=True,
            reason="auth_recovery",
        )

    def handle_auth_expired_notification_click(self) -> None:
        provider_id = getattr(self, "_auth_expired_provider_id", None)
        if not provider_id:
            return
        # A tray click applies only to the notification that supplied this provider.
        self._auth_expired_provider_id = None
        self.open_settings(provider_id=provider_id, start_cookie_acquisition=True)

    def _sync_vpet_usage(self) -> None:
        if self._vpet.active:
            message = usage_message(
                self._data, self._refreshing, str(config_manager.get("ACTIVE_PROVIDER", "")),
                self._pricing_state.is_peak if self._pricing_state is not None else None,
            )
            theme = current_theme()
            # 仅发送绘制需要的颜色，不序列化主题控制器或配置；复用球体的水面色和峰时色规则。
            message["theme"] = {
                "accent": theme.accent,
                "accent_hover": theme.accent_hover,
                "water_top": FloatingUsageBall._water_top_color(theme).name(),
                "water_deep": QColor(theme.accent).darker(138).name(),
                "water_back": theme.heat[3] if theme.name == "light" else theme.accent_hover,
                "peak": "#FFB000" if theme.name == "light" else theme.warning,
                "on_accent": theme.on_accent,
            }
            self._vpet.update_usage(message)

    def _apply_update(self) -> None:
        self._sync_vpet_usage()
        loading = self._refreshing and self._data.last_success_at is None
        provider_id = (
            self._data.per_provider[0].provider_id if self._data.per_provider else ""
        )
        motion_provider_id = provider_id or str(
            config_manager.get("ACTIVE_PROVIDER", "")
        ).strip().lower()
        self.ball.set_motion_provider(motion_provider_id)
        provider_cls = PROVIDERS.get(provider_id)
        quota_mode = bool(
            self._data.quota_windows
            or (provider_cls and provider_cls.supports_subscription_quota)
        )
        if self._data.quota_windows:
            primary = self._data.quota_windows[0]
            reset_text = (
                format_codex_reset_time(primary.resets_at, compact=True)
                if provider_id == "codex"
                else format_reset_countdown(primary.resets_at)
            )
            self.ball.set_quota_state(
                None if loading else 100 - primary.used_percent,
                "正在更新额度" if loading else reset_text,
                primary.title,
            )
        elif quota_mode:
            # 订阅额度暂不可用时也不能回退成金额视图，否则会显示虚假的金额。
            unavailable_title = "每月额度" if provider_id == "cursor" else "周额度"
            self.ball.set_quota_state(None, "额度暂不可用", unavailable_title)
        else:
            self.ball.clear_quota_state()
            self.ball.set_labels("今日使用", "余额")
            self.ball.set_values(
                "--" if loading else format_money(self._data.today_cost_cny, self._data.currency),
                "--" if loading else format_money(self._data.balance_cny, self._data.currency),
            )
        if self.panel is not None:
            self.panel.set_refreshing(self._refreshing)
            if self._expanded:
                self.panel.update_data(self._data, loading, self._refreshing)

    def _periodic_refresh(self) -> None:
        self.refresh(queue_if_busy=False, reason="periodic_current")
        self._reschedule_refresh()

    def _periodic_background_refresh(self) -> None:
        if self._closed:
            return
        captured_config = config_manager.all_config()
        active_provider = str(
            captured_config.get("ACTIVE_PROVIDER", "")
        ).strip().lower()
        background_provider_ids = captured_config.get("BACKGROUND_PROVIDER_IDS", [])
        if not background_provider_ids:
            # 未显式勾选时只刷新当前来源，避免旧版本行为继续请求所有已配置账户。
            return
        configured_ids = set(configured_provider_ids(captured_config))
        now = time.monotonic()
        for provider_id in background_provider_ids:
            if provider_id == active_provider or provider_id not in configured_ids:
                continue
            last_started = self._provider_last_started.get(provider_id)
            if (
                last_started is not None
                and (now - last_started) * 1000 < BACKGROUND_PROVIDER_INTERVAL_MS
            ):
                continue
            self._start_provider_refresh(
                provider_id,
                captured_config,
                lightweight=True,
                queue_if_busy=False,
                reason="periodic_background",
            )

    def _on_pricing_boundary(self) -> None:
        self._sync_pricing_state(notify_transition=True)

    def _sync_pricing_state(self, notify_transition: bool) -> None:
        enabled = bool(config_manager.get("DEEPSEEK_PEAK_PRICING_ENABLED", False)) and (
            str(config_manager.get("ACTIVE_PROVIDER", "")).strip().lower() == "deepseek"
        )
        if not enabled:
            self._pricing_timer.stop()
            self._pricing_state = None
            if self.panel is not None:
                self.panel.set_pricing_state(False)
            self.ball.set_peak_highlight(False)
            self._sync_vpet_usage()
            return

        previous = self._pricing_state
        current = pricing_state(config_manager.all_config())
        self._pricing_state = current
        if self.panel is not None:
            self.panel.set_pricing_state(
                True, current.is_peak, current.label, current.tooltip
            )
        self.ball.set_peak_highlight(current.is_peak)
        # 沿用现有边界定时器切换云朵描边，不等下一次账户刷新，也不为桌宠增加轮询。
        self._sync_vpet_usage()
        if (
            notify_transition
            and previous is not None
            and not previous.is_peak
            and current.is_peak
            and self.tray is not None
        ):
            self.tray.showMessage(
                tr(f"{APP_DISPLAY_NAME}：DeepSeek 已进入高峰计价"),
                tr("当前所有计费项按平时价格 2 倍计费，"
                f"本时段至 {current.next_boundary.strftime('%H:%M')}（北京时间）。"),
                QSystemTrayIcon.MessageIcon.Warning,
                10_000,
            )

        # Add a small guard so an early timer wake-up cannot repeatedly classify
        # the instant immediately before the half-open boundary.
        now = datetime.now(BEIJING_TIMEZONE)
        delay_ms = max(
            1, int((current.next_boundary - now).total_seconds() * 1000) + 50
        )
        self._pricing_timer.start(delay_ms)

    def _uses_lightweight_mimo_refresh(self, provider_id: str | None = None) -> bool:
        selected = provider_id or str(
            config_manager.get("ACTIVE_PROVIDER", "")
        ).strip().lower()
        return not self._expanded and selected == "mimo"

    def _reschedule_refresh(self) -> None:
        configured = int(config_manager.get("REFRESH_INTERVAL", 60_000))
        # 面板与悬浮球应使用同一用户设置的刷新节奏，不能因窗口状态产生意外延迟。
        self._refresh_timer.start(configured)

    def set_visible_from_tray(self) -> None:
        if self._vpet.active:
            visible = not self._vpet.visible
            self._vpet.set_visible(visible)
            if not visible:
                self.hide()
            return
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
        if self._settings_window is not None:
            self._settings_window.flush_pending_saves()
        if self._ball_size_save_timer.isActive():
            self._ball_size_save_timer.stop()
            self._save_ball_size()
        if self._panel_width_save_timer.isActive():
            self._panel_width_save_timer.stop()
            self._save_panel_width()
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
        self._vpet.stop()
        self._refresh_timer.stop()
        self._background_refresh_timer.stop()
        self._pricing_timer.stop()
        self._edge_animation.stop()
        self._edge_hide_timer.stop()
        self._edge_leave_timer.stop()
        self._edge_hover_check.stop()
        self._ball_size_save_timer.stop()
        if self._mimo_renewal_task is not None:
            self._mimo_renewal_task.cancel()
        self._thread_pool.clear()
        event.accept()
        QApplication.instance().quit()
