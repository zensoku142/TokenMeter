"""Optional two-provider desktop view reusing overview cards and collection."""

from PySide6.QtCore import QPoint, QRectF, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizeGrip,
    QSizePolicy,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from api.providers import PROVIDERS
from config import runtime as config_manager
from config import state
from ui.i18n import add_item, bind_text
from ui.provider_overview import ProviderOverview
from ui.qt_panel import (
    HEADER_HEIGHT,
    DraggableHeader,
    create_panel_tool_button,
    set_panel_tool_icon,
)
from ui.qt_settings import _SettingsComboBox
from ui.qt_theme import app_icon, current_theme, panel_background, theme_controller


class _CompactFrame(QFrame):
    def paintEvent(self, event):
        # 壳体复用主面板的颜色/透明度算法，不依赖全局 QSS 是否负责绘制面板背景。
        tokens = current_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(tokens.border), 1))
        painter.setBrush(panel_background(tokens.window, tokens))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 18, 18)
        painter.end()
        super().paintEvent(event)


class CompactOverview(QDialog):
    refresh_requested = Signal()
    auto_refresh_requested = Signal()
    auto_refresh_stopped = Signal()
    sources_changed = Signal()
    provider_selected = Signal(str)
    settings_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("settingsPage")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("QDialog#settingsPage { background: transparent; }")
        bind_text(self, "双平台看板", method="setWindowTitle")
        self.resize(430, 480)
        self.setMinimumSize(360, 300)
        self._sources_initialized = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        frame = _CompactFrame()
        frame.setObjectName("panelFrame")
        frame.setStyleSheet("QFrame#panelFrame { background: transparent; border: 1px solid transparent; border-radius: 18px; }")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(1, 1, 1, 1)
        frame_layout.setSpacing(0)
        root.addWidget(frame)
        self.header = DraggableHeader()
        self.header.setObjectName("panelHeader")
        self.header.setFixedHeight(HEADER_HEIGHT)
        header = QHBoxLayout(self.header)
        header.setContentsMargins(14, 5, 12, 5)
        header.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(app_icon(28).pixmap(28, 28))
        header.addWidget(logo)
        title = bind_text(QLabel(), "双平台看板")
        title.setObjectName("panelTitle")
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        header.addWidget(title, 1)
        self.refresh_button = create_panel_tool_button("refresh", QStyle.StandardPixmap.SP_BrowserReload, "刷新")
        self.refresh_button.clicked.connect(self.refresh_requested)
        self.menu_button = create_panel_tool_button("more", QStyle.StandardPixmap.SP_FileDialogDetailedView, "更多操作")
        self.close_button = create_panel_tool_button("close", QStyle.StandardPixmap.SP_TitleBarCloseButton, "关闭", role="close")
        self.close_button.clicked.connect(self.close)
        for button in (self.refresh_button, self.menu_button, self.close_button):
            header.addWidget(button)
        frame_layout.addWidget(self.header)
        self._drag_offset = QPoint()
        self.header.pressed.connect(self._start_drag)
        self.header.dragged.connect(lambda point: self.move(point - self._drag_offset))
        self.header.released.connect(self._finish_drag)
        body = QWidget()
        frame_layout.addWidget(body, 1)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 8, 8, 8)
        choices = QHBoxLayout()
        self.selectors = [_SettingsComboBox(), _SettingsComboBox()]
        for selector in self.selectors:
            selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            selector.setMinimumContentsLength(6)
            selector.setMinimumWidth(0)
            selector.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            choices.addWidget(selector, 1)
            selector.activated.connect(self._selection_changed)
        self.menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_button.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        bind_text(self.menu_button, "更多操作", method="setAccessibleName")
        menu = QMenu(self.menu_button)
        refresh = bind_text(QAction(menu), "刷新")
        refresh.triggered.connect(self.refresh_requested)
        settings = bind_text(QAction(menu), "账户连接")
        settings.triggered.connect(lambda: self.settings_requested.emit(""))
        close = bind_text(QAction(menu), "关闭")
        close.triggered.connect(self.close)
        menu.addActions((refresh, settings, close))
        self.menu_button.setMenu(menu)
        layout.addLayout(choices)
        self.board = ProviderOverview(self, window_limit=1)
        bind_text(self.board.empty, "选择要监控的平台")
        # 小看板只保留选择器和两张卡片，复用总览的图形、状态及数据口径。
        toolbar = self.board.layout().itemAt(0).layout()
        for index in range(toolbar.count()):
            toolbar.itemAt(index).widget().hide()
        self.board.hint.hide()
        self.board.layout().setContentsMargins(0, 0, 0, 0)
        self.board.auto_refresh_requested.connect(self.auto_refresh_requested)
        self.board.auto_refresh_stopped.connect(self.auto_refresh_stopped)
        self.board.provider_selected.connect(self.provider_selected)
        self.board.connection_requested.connect(self.settings_requested)
        layout.addWidget(self.board, 1)
        note = bind_text(QLabel(), "只监控所选平台的默认连接")
        note.setWordWrap(True)
        footer = QHBoxLayout()
        footer.addWidget(note, 1)
        footer.addWidget(QSizeGrip(frame))
        layout.addLayout(footer)
        theme_controller().changed.connect(self._refresh_icon)
        # 透明度专用预览信号是可选能力；有该信号时只重绘壳体，不重建图表或控件。
        preview = getattr(theme_controller(), "opacity_preview_changed", None)
        if preview is not None:
            preview.connect(frame.update)
        self._refresh_icon()

    def _refresh_icon(self, *_args):
        set_panel_tool_icon(self.refresh_button, "refresh", QStyle.StandardPixmap.SP_BrowserReload)
        set_panel_tool_icon(self.menu_button, "more", QStyle.StandardPixmap.SP_FileDialogDetailedView)
        set_panel_tool_icon(self.close_button, "close", QStyle.StandardPixmap.SP_TitleBarCloseButton, "close")

    def _start_drag(self, point):
        self._drag_offset = point - self.pos()

    def _finish_drag(self, _point):
        area = self.screen().availableGeometry()
        self.move(max(area.left(), min(self.x(), area.right() - self.width() + 1)),
                  max(area.top(), min(self.y(), area.bottom() - self.height() + 1)))

    def selected_providers(self):
        return list(dict.fromkeys(str(selector.currentData()) for selector in self.selectors if selector.currentData()))

    def set_sources(self, providers):
        selected = self.selected_providers() if self._sources_initialized else None
        if selected is None:
            saved = state.load_dict(config_manager.PANEL_LAYOUT_PATH).get("compact_providers")
            selected = [value for value in saved if value in providers] if isinstance(saved, list) else None
        if selected is None:
            active = config_manager.get("ACTIVE_PROVIDER", "")
            selected = ([active] if active in providers else []) + [value for value in providers if value != active]
        # 显式清空选择表示停止这个看板的监控，不能在再次打开时自动选回两个平台。
        self._sources_initialized = True
        for index, selector in enumerate(self.selectors):
            blocker = QSignalBlocker(selector)
            selector.clear()
            add_item(selector, "选择平台", "")
            for provider_id in providers:
                add_item(selector, PROVIDERS[provider_id].name, provider_id)
            value = selected[index] if index < len(selected) else ""
            selector.setCurrentIndex(max(0, selector.findData(value)))
            del blocker

    def _selection_changed(self, *_args):
        if self.selectors[0].currentData() and self.selectors[0].currentData() == self.selectors[1].currentData():
            self.selectors[1].setCurrentIndex(0)
        try:
            state.merge_dict(config_manager.PANEL_LAYOUT_PATH, {"compact_providers": self.selected_providers()})
        except OSError:
            config_manager.logger().warning("Compact overview preferences could not be saved")
        self.sources_changed.emit()
