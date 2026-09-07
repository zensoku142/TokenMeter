"""Searchable, branded provider selection shared by the panel and settings."""

from __future__ import annotations

from math import cos, pi, sin

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from api.providers import PROVIDERS, configured_provider_ids
from config import runtime as config_manager
from config.defaults import DEFAULT_CONFIG, SECRET_KEYS
from ui.i18n import bind_text, language_controller, tr
from ui.provider_branding import (
    provider_icon,
    provider_search_terms,
    provider_short_name,
)
from ui.qt_theme import current_theme, theme_controller

_DEFAULT_PINS = ("codex", "claude", "cursor")


def pinned_provider_ids() -> list[str]:
    values = config_manager.load_panel_layout_state().get("pinned_providers", _DEFAULT_PINS)
    if not isinstance(values, (list, tuple)):
        values = _DEFAULT_PINS
    disabled = config_manager.get("DISABLED_PROVIDER_IDS", [])
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and value in PROVIDERS and value not in disabled))


class _ProviderCardDelegate(QStyledItemDelegate):
    hovered_pin = None
    pressed_pin = None

    @staticmethod
    def pin_rect(rect):
        # 绘制和命中检测共用 40px 热区，避免只有星形细线附近才能明确操作。
        return QRectF(rect).adjusted(rect.width() - 44, 6, -4, -rect.height() + 46)

    def paint(self, painter, option, index) -> None:
        provider_id, connected, pinned = index.data(Qt.ItemDataRole.UserRole)
        tokens = current_theme()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = QRectF(option.rect).adjusted(4, 4, -4, -4)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        border = QColor(tokens.accent if selected else tokens.border)
        if not selected:
            border.setAlpha(60 if hovered else 35)
        painter.setPen(QPen(border, 1.3 if selected else 1))
        painter.setBrush(QColor(tokens.accent_soft if selected else tokens.elevated if hovered else tokens.surface))
        painter.drawRoundedRect(rect, 10, 10)
        icon_rect = rect.adjusted(12, 11, 0, 0).toRect()
        icon_rect.setSize(QSize(26, 26))
        provider_icon(provider_id, 26).paint(painter, icon_rect)
        # 收藏采用矢量星形；品牌标记始终来自独立的品牌 SVG 资产。
        star_rect = self.pin_rect(option.rect)
        hovered_pin = self.hovered_pin == provider_id
        pressed_pin = hovered_pin and self.pressed_pin == provider_id
        if pinned or hovered_pin:
            painter.setPen(QPen(QColor(tokens.accent), 1) if hovered_pin else Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tokens.accent if pressed_pin else tokens.accent_soft))
            painter.drawRoundedRect(star_rect.adjusted(3, 3, -3, -3), 7, 7)
        center = star_rect.center()
        points = QPolygonF([
            QPointF(center.x() + (7 if n % 2 == 0 else 3.1) * cos(-pi / 2 + n * pi / 5),
                    center.y() + (7 if n % 2 == 0 else 3.1) * sin(-pi / 2 + n * pi / 5))
            for n in range(10)
        ])
        star_color = QColor(tokens.window if pressed_pin else tokens.accent if pinned or hovered_pin else tokens.subtext)
        painter.setPen(QPen(star_color, 1.4))
        painter.setBrush(star_color if pinned else Qt.BrushStyle.NoBrush)
        painter.drawPolygon(points)
        painter.setFont(option.font)
        painter.setPen(QColor(tokens.value))
        name = painter.fontMetrics().elidedText(
            provider_short_name(provider_id), Qt.TextElideMode.ElideRight, int(rect.width()) - 24
        )
        painter.drawText(rect.adjusted(12, 44, -12, -24), Qt.AlignmentFlag.AlignLeft, name)
        label = index.data(Qt.ItemDataRole.StatusTipRole) or tr("已配置" if connected else "未配置")
        painter.setPen(QColor(tokens.success if connected else tokens.subtext))
        painter.drawText(rect.adjusted(12, 67, -8, 0), Qt.AlignmentFlag.AlignLeft, label)
        painter.restore()


class _ProviderPopup(QFrame):
    closed = Signal()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.closed.emit()


class ProviderPicker(QComboBox):
    """Keep the existing combo data/signals while replacing its long native menu."""

    pins_changed = Signal()
    configuration_changed = Signal(str, bool)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._popup_open = False
        self._configured: set[str] = set()
        self._disabled: set[str] = set()
        self.remove_buttons: dict[str, QToolButton] = {}
        self._pins: list[str] = []
        self._filter = "all"
        self.popup_anchor: QWidget = self
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(28)
        self.popup = _ProviderPopup(self, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.popup.setObjectName("providerPickerPopup")
        self.popup.closed.connect(self._popup_closed)
        layout = QVBoxLayout(self.popup)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)
        title_row = QHBoxLayout()
        title = bind_text(QLabel(), "切换 AI 平台")
        title.setObjectName("providerPickerTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        self.count_label = QLabel()
        self.count_label.setObjectName("providerPickerCount")
        title_row.addWidget(self.count_label)
        layout.addLayout(title_row)
        self.search = QLineEdit()
        self.search.setObjectName("providerPickerSearch")
        self.search.setClearButtonEnabled(True)
        bind_text(self.search, "搜索平台名称，如 Claude、Kimi、智谱", method="setPlaceholderText")
        bind_text(self.search, "搜索 AI 平台", method="setAccessibleName")
        self.search.textChanged.connect(self._populate)
        layout.addWidget(self.search)
        filters = QHBoxLayout()
        filters.setSpacing(6)
        self.filter_buttons: dict[str, QToolButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, label in (("all", "全部平台"), ("configured", "已配置"), ("pinned", "常用平台")):
            button = bind_text(QToolButton(), label)
            button.setCheckable(True)
            button.setObjectName("providerPickerFilter")
            button.setFixedHeight(30)
            button.clicked.connect(lambda _checked=False, selected=key: self._set_filter(selected))
            group.addButton(button)
            filters.addWidget(button)
            self.filter_buttons[key] = button
        filters.addStretch()
        layout.addLayout(filters)
        self.grid = QListWidget()
        self.grid.setObjectName("providerPickerGrid")
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setFlow(QListView.Flow.LeftToRight)
        self.grid.setWrapping(True)
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.grid.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.grid.setMouseTracking(True)
        self.grid.setItemDelegate(_ProviderCardDelegate(self.grid))
        bind_text(self.grid, "AI 平台列表", method="setAccessibleName")
        self.grid.itemClicked.connect(self._activate_item)
        self.grid.itemActivated.connect(self._activate_item)
        self._grid_viewport = self.grid.viewport()
        self.search.installEventFilter(self)
        self.grid.installEventFilter(self)
        self._grid_viewport.installEventFilter(self)
        self.grid.verticalScrollBar().valueChanged.connect(self._position_remove_buttons)
        layout.addWidget(self.grid, 1)
        self.empty_label = bind_text(QLabel(), "没有匹配的平台，试试其他名称或查看全部平台")
        self.empty_label.setWordWrap(True)
        self.empty_label.setObjectName("providerPickerEmpty")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_label)
        self.hint = bind_text(QLabel(), "Enter 切换 · Ctrl+D 收藏 · Esc 关闭")
        self.hint.setObjectName("providerPickerHint")
        layout.addWidget(self.hint)
        self.currentIndexChanged.connect(self._selection_changed)
        theme_controller().changed.connect(self.refresh_theme)
        controller = language_controller()
        if controller is not None:
            controller.changed.connect(self.refresh_language)
        self.refresh_theme()

    def _selection_changed(self, *_args) -> None:
        self.setToolTip(str(self.currentText()))
        self.update()

    def _set_filter(self, selected: str) -> None:
        self._filter = selected
        self.filter_buttons[selected].setChecked(True)
        self._populate()

    def _populate(self, *_args) -> None:
        self.grid.itemDelegate().hovered_pin = None
        self.grid.itemDelegate().pressed_pin = None
        self._grid_viewport.unsetCursor()
        query = self.search.text().strip().casefold()
        if query and self._filter != "all":
            # 搜索面向所有可接入平台，不能因默认的已配置筛选而隐藏用户正在找的品牌。
            self._filter = "all"
            self.filter_buttons["all"].setChecked(True)
        for button in self.remove_buttons.values():
            button.hide()
            button.deleteLater()
        self.remove_buttons.clear()
        self.grid.clear()
        indices = list(range(self.count()))
        # 已连接和收藏项优先，顺序稳定；搜索只读内存，不能每次按键探测凭据或网络。
        indices.sort(key=lambda index: (
            str(self.itemData(index)) not in self._configured,
            str(self.itemData(index)) not in self._pins, index,
        ))
        for index in indices:
            provider_id = str(self.itemData(index))
            if provider_id not in PROVIDERS:
                continue
            connected = provider_id in self._configured
            pinned = provider_id in self._pins
            if self._filter == "configured" and not connected or self._filter == "pinned" and not pinned:
                continue
            terms = f"{self.itemText(index)} {provider_id} {provider_search_terms(provider_id)}".casefold()
            if query and not all(word in terms for word in query.split()):
                continue
            item = QListWidgetItem(provider_short_name(provider_id))
            item.setData(Qt.ItemDataRole.UserRole, (provider_id, connected, pinned))
            item.setSizeHint(self.grid.gridSize())
            status = tr("已移除" if provider_id in self._disabled else "已配置" if connected else "未配置")
            item.setData(Qt.ItemDataRole.StatusTipRole, status)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, f"{self.itemText(index)} · {status}")
            item.setToolTip(f"{self.itemText(index)}\n{tr(PROVIDERS[provider_id].support_description)}")
            if provider_id in self._disabled:
                item.setToolTip(item.toolTip() + "\n" + tr("选择此平台可重新启用监控；已删除的凭据需重新配置。"))
            self.grid.addItem(item)
            if connected:
                button = bind_text(QToolButton(self._grid_viewport), "删除")
                button.setObjectName("providerRemoveButton")
                button.setFixedSize(52, 24)
                button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.installEventFilter(self)
                bind_text(button, lambda current=provider_id: tr("删除 {provider} 配置", provider=PROVIDERS[current].name), method="setAccessibleName")
                bind_text(button, "删除配置", method="setToolTip")
                button.clicked.connect(lambda _checked=False, current=provider_id: self._remove_provider(current))
                self.remove_buttons[provider_id] = button
            if provider_id == self.currentData():
                self.grid.setCurrentItem(item)
        if self.grid.currentRow() < 0 and self.grid.count():
            self.grid.setCurrentRow(0)
        self.empty_label.setVisible(self.grid.count() == 0)
        self.grid.setVisible(self.grid.count() > 0)
        bind_text(self.count_label, f"{self.grid.count()} 个平台")
        self.grid.doItemsLayout()
        self._position_remove_buttons()

    def _position_remove_buttons(self, *_args) -> None:
        # 按卡片的实际视口位置摆放独立按钮；滚动或筛选后不能覆盖到另一平台的卡片。
        for row in range(self.grid.count()):
            item = self.grid.item(row)
            button = self.remove_buttons.get(item.data(Qt.ItemDataRole.UserRole)[0])
            if button is not None:
                rect = self.grid.visualItemRect(item)
                button.move(rect.right() - button.width() - 10, rect.bottom() - button.height() - 6)
                button.show()
                button.raise_()

    def _remove_selected(self) -> None:
        item = self.grid.currentItem()
        if item is None or not item.data(Qt.ItemDataRole.UserRole)[1]:
            return
        self._remove_provider(item.data(Qt.ItemDataRole.UserRole)[0])

    def _remove_provider(self, provider_id: str) -> None:
        if provider_id not in self._configured:
            return
        self.hidePopup()
        answer = QMessageBox.question(
            self.window(), tr("删除平台配置"),
            tr("删除 {provider} 的配置并停止监控？本机 CLI 登录和历史记录会保留。", provider=PROVIDERS[provider_id].name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        captured = config_manager.all_config()
        prefix = provider_id.upper() + "_"
        credential_keys = {
            prefix + name.upper() for name in PROVIDERS[provider_id].credential_fields
        } | {key for key in SECRET_KEYS if key.startswith(prefix)}
        # 清除凭据及登录路径，也覆盖旧版凭据字段；计价时段等平台偏好仍可供重新配置时使用。
        values = {key: DEFAULT_CONFIG.get(key, "") for key in credential_keys}
        values["DISABLED_PROVIDER_IDS"] = list(dict.fromkeys([*captured.get("DISABLED_PROVIDER_IDS", []), provider_id]))
        values["BACKGROUND_PROVIDER_IDS"] = [value for value in captured.get("BACKGROUND_PROVIDER_IDS", []) if value != provider_id]
        if captured.get("ACTIVE_PROVIDER") == provider_id:
            remaining = configured_provider_ids({**captured, **values})
            fallback = next((value for value in PROVIDERS if value not in values["DISABLED_PROVIDER_IDS"]), provider_id)
            values["ACTIVE_PROVIDER"] = next(iter(remaining), fallback)
        try:
            config_manager.save_config(values)
        except Exception:
            QMessageBox.warning(self.window(), tr("删除平台配置"), tr("配置删除失败，请检查数据目录后重试。"))
            return
        self.configuration_changed.emit(provider_id, True)
        self.pins_changed.emit()

    def _activate_item(self, item: QListWidgetItem) -> None:
        if not self._popup_open:
            return
        self.select_provider(item.data(Qt.ItemDataRole.UserRole)[0])

    def select_provider(self, provider_id: str) -> None:
        index = self.findData(provider_id)
        if index < 0:
            return
        # 先关闭弹层再通知设置页重建表单，避免删除仍持有焦点的弹层子控件。
        self.hidePopup()
        disabled = config_manager.all_config().get("DISABLED_PROVIDER_IDS", [])
        if provider_id in disabled:
            try:
                # 重新启用与选中属于同一次操作；一次保存避免先刷新旧平台、再切到目标平台。
                config_manager.save_config({
                    "DISABLED_PROVIDER_IDS": [value for value in disabled if value != provider_id],
                    "ACTIVE_PROVIDER": provider_id,
                })
            except Exception:
                QMessageBox.warning(self.window(), tr("管理平台"), tr("平台启用失败，请检查数据目录后重试。"))
                return
            self.configuration_changed.emit(provider_id, False)
            self.pins_changed.emit()
        self.setCurrentIndex(index)
        self.activated.emit(index)
        self.textActivated.emit(self.currentText())

    def _toggle_pin(self, item: QListWidgetItem) -> None:
        provider_id = item.data(Qt.ItemDataRole.UserRole)[0]
        if provider_id in config_manager.get("DISABLED_PROVIDER_IDS", []):
            # 删除后的快捷入口保持关闭；收藏不应隐式恢复监控，也不能误报为落盘失败。
            bind_text(self.hint, "请先在全部平台中重新启用此平台。")
            return
        pins = pinned_provider_ids()
        if provider_id in pins:
            pins.remove(provider_id)
        else:
            # 标题栏只容纳三个快捷入口，新收藏应立即出现在可见位置。
            pins.insert(0, provider_id)
        state = config_manager.load_panel_layout_state()
        # 收藏属于界面偏好，沿用独立布局状态，避免提交账户草稿或重写所有密钥。
        config_manager.save_panel_layout_state({**state, "pinned_providers": pins})
        self._pins = pinned_provider_ids()
        self._populate()
        if self._pins != pins:
            bind_text(self.hint, "常用平台保存失败，请检查数据目录")
            return
        bind_text(self.hint, lambda: tr(
            "已收藏 {provider}" if provider_id in pins else "已取消收藏 {provider}",
            provider=provider_short_name(provider_id),
        ))
        self.pins_changed.emit()

    def eventFilter(self, watched, event) -> bool:
        # Qt 在构造/释放子控件时也会同步发事件，不能回调尚未建立或已销毁的列表。
        if not hasattr(self, "_grid_viewport"):
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.FocusIn and watched in self.remove_buttons.values():
            # 独立按钮不是列表索引控件；键盘聚焦时主动滚到所属卡片，避免焦点落在视口外。
            for row in range(self.grid.count()):
                item = self.grid.item(row)
                if self.remove_buttons.get(item.data(Qt.ItemDataRole.UserRole)[0]) is watched:
                    self.grid.setCurrentItem(item)
                    self.grid.scrollToItem(item)
                    break
        if watched is self._grid_viewport and event.type() == QEvent.Type.Resize:
            self.grid.doItemsLayout()
            self._position_remove_buttons()
        if watched is self._grid_viewport and event.type() == QEvent.Type.Leave:
            self.grid.itemDelegate().hovered_pin = None
            self._grid_viewport.unsetCursor()
            self._grid_viewport.update()
        if watched is self._grid_viewport and event.type() in (
            QEvent.Type.MouseMove, QEvent.Type.ToolTip,
            QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
        ):
            position = event.pos() if event.type() == QEvent.Type.ToolTip else event.position().toPoint()
            item = self.grid.itemAt(position)
            delegate = self.grid.itemDelegate()
            over_pin = item is not None and delegate.pin_rect(self.grid.visualItemRect(item)).contains(position)
            provider_id = item.data(Qt.ItemDataRole.UserRole)[0] if over_pin else None
            delegate.hovered_pin = provider_id
            self._grid_viewport.setCursor(Qt.CursorShape.PointingHandCursor if over_pin else Qt.CursorShape.ArrowCursor)
            self._grid_viewport.update()
            if event.type() == QEvent.Type.ToolTip and over_pin:
                label = "请先在全部平台中重新启用此平台。" if provider_id in self._disabled else "取消收藏" if provider_id in self._pins else "收藏"
                QToolTip.showText(event.globalPos(), tr(label), self._grid_viewport)
                return True
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                delegate.pressed_pin = provider_id
                if over_pin:
                    return True
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                pressed_pin = delegate.pressed_pin
                delegate.pressed_pin = None
                # 必须在同一收藏热区按下并松开；拖出按钮时取消，不能误收藏或切换平台。
                if pressed_pin is not None:
                    if pressed_pin == provider_id:
                        self._toggle_pin(item)
                    return True
                if over_pin:
                    return True
        if event.type() == QEvent.Type.KeyPress:
            if (watched is self.grid or watched in self.remove_buttons.values()) and event.key() == Qt.Key.Key_Delete:
                self._remove_selected()
                return True
            if event.key() == Qt.Key.Key_Escape:
                self.hidePopup()
                return True
            if watched is self.search and event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self.grid.count():
                    self.grid.setCurrentRow(0)
                    if event.key() == Qt.Key.Key_Down:
                        self.grid.setFocus()
                    else:
                        self._activate_item(self.grid.item(0))
                return True
            if watched in (self.grid, self.search) and event.key() == Qt.Key.Key_D and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                if self.grid.currentItem() is not None:
                    self._toggle_pin(self.grid.currentItem())
                return True
        return super().eventFilter(watched, event)

    def showPopup(self) -> None:
        if self._popup_open:
            self.hidePopup()
            return
        captured_config = config_manager.all_config()
        self._configured = set(configured_provider_ids(captured_config))
        self._disabled = set(captured_config.get("DISABLED_PROVIDER_IDS", []))
        self._pins = pinned_provider_ids()
        bind_text(self.hint, "Enter 切换 · Ctrl+D 收藏 · Esc 关闭")
        self.search.clear()
        anchor = self.popup_anchor
        below = anchor.mapToGlobal(QPoint(0, anchor.height() + 6))
        screen = QGuiApplication.screenAt(below) or anchor.screen()
        available = screen.availableGeometry()
        width = min(560, available.width() - 16)
        height = min(458, available.height() - 16)
        self.popup.setFixedSize(width, height)
        columns = 3 if width >= 480 else 2
        self.grid.setGridSize(QSize((width - 48) // columns, 98))
        self._set_filter("configured" if self._configured else "all")
        x = max(available.left() + 8, min(below.x(), available.right() - width - 7))
        y = below.y()
        if y + height > available.bottom() - 8:
            y = max(available.top() + 8, anchor.mapToGlobal(QPoint()).y() - height - 6)
        self.popup.move(x, y)
        self._popup_open = True
        self.popup.show()
        self._position_remove_buttons()
        self.search.setFocus(Qt.FocusReason.PopupFocusReason)
        self.update()

    def hidePopup(self) -> None:
        self.popup.hide()
        self._popup_open = False
        self.update()

    def _popup_closed(self) -> None:
        self._popup_open = False
        self.update()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Down, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.showPopup()
            event.accept()
        elif event.text() and not event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
            self.showPopup()
            self.search.setText(event.text())
            event.accept()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        # 浏览设置页时滚轮应滚动页面，不能静默切换当前账号平台。
        event.ignore()

    def paintEvent(self, _event) -> None:
        tokens = current_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = self._popup_open or self.hasFocus()
        painter.setPen(QPen(QColor(tokens.accent if self.hasFocus() else tokens.border), .8))
        painter.setBrush(QColor(tokens.accent_soft if selected else tokens.surface))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 8, 8)
        provider_id = str(self.currentData() or "")
        provider_icon(provider_id, 20).paint(painter, 9, (self.height() - 20) // 2, 20, 20)
        painter.setPen(QColor(tokens.value))
        text = provider_short_name(provider_id) if provider_id in PROVIDERS else self.currentText()
        painter.drawText(self.rect().adjusted(36, 0, -24, 0), Qt.AlignmentFlag.AlignVCenter,
                         self.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, self.width() - 60))
        center = QPointF(self.width() - 13, self.height() / 2)
        painter.setPen(QPen(QColor(tokens.subtext), 1.5))
        painter.drawPolyline(QPolygonF([center + QPointF(-3, -1), center + QPointF(0, 2), center + QPointF(3, -1)]))

    def refresh_theme(self, *_args) -> None:
        tokens = current_theme()
        self.popup.setStyleSheet(f"""
QFrame#providerPickerPopup {{ background: {tokens.window}; border: 1px solid {tokens.border}; border-radius: 14px; }}
QLabel {{ background: transparent; border: none; color: {tokens.subtext}; }}
QLabel#providerPickerTitle {{ color: {tokens.value}; font-size: 16px; font-weight: 600; }}
QLineEdit#providerPickerSearch {{ background: {tokens.surface}; color: {tokens.text}; border: 1px solid {tokens.border}; border-radius: 8px; padding: 8px 10px; }}
QLineEdit#providerPickerSearch:focus {{ border-color: {tokens.accent}; }}
QToolButton#providerPickerFilter {{ background: transparent; color: {tokens.subtext}; border: 0; border-radius: 7px; padding: 0 12px; min-height: 0; }}
QToolButton#providerPickerFilter:checked {{ background: {tokens.accent_soft}; color: {tokens.accent_text}; }}
QToolButton#providerPickerFilter:hover {{ color: {tokens.accent}; }}
QToolButton#providerRemoveButton {{ color: {tokens.subtext}; background: transparent; border: 0; padding: 0; font-size: 11px; min-width: 0; min-height: 0; }}
QToolButton#providerRemoveButton:hover {{ color: {tokens.danger}; background: {tokens.surface}; border-radius: 5px; }}
QToolButton#providerRemoveButton:disabled {{ color: {tokens.subtext}; }}
QToolButton#providerRemoveButton:focus {{ border: 1px solid {tokens.accent}; border-radius: 6px; }}
QListWidget#providerPickerGrid {{ background: transparent; border: none; outline: none; padding: 0; }}
QListWidget#providerPickerGrid::item {{ border: none; }}
""")
        self.grid.viewport().update()
        self.update()

    def refresh_language(self, *_args) -> None:
        if self._popup_open:
            self._populate()
        self.update()


class ProviderShortcuts(QWidget):
    selected = Signal(str)

    def __init__(self, parent=None, *, current_provider: str = ""):
        super().__init__(parent)
        self.setObjectName("providerShortcuts")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self._provider_ids: list[str] = []
        self._current = current_provider
        self._pins: list[str] = []
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: dict[str, QToolButton] = {}
        theme_controller().changed.connect(self._render)
        self.refresh()

    def set_current(self, provider_id: str) -> None:
        selected = self.buttons.get(provider_id)
        if provider_id == self._current and (selected is None or selected.isChecked()):
            return
        self._current = provider_id
        self._render()

    def refresh(self, *_args) -> None:
        self._pins = pinned_provider_ids()
        self._render()

    def _render(self, *_args) -> None:
        # 用量刷新只更新选中态；收藏变化时才重新读文件，主题变化只重新绘制图标。
        provider_ids = self._pins[:5]
        # 移除常驻下拉后，当前平台即使未收藏也必须有可见且高亮的入口。
        if self._current in PROVIDERS and self._current not in provider_ids and self._current not in config_manager.get("DISABLED_PROVIDER_IDS", []):
            provider_ids = [self._current, *provider_ids[:4]]
        if self._provider_ids != provider_ids:
            while self._layout.count():
                item = self._layout.takeAt(0)
                if item is not None and (widget := item.widget()) is not None:
                    if isinstance(widget, QToolButton):
                        self._group.removeButton(widget)
                    widget.hide()
                    widget.deleteLater()
            self.buttons.clear()
            self._provider_ids = provider_ids
            for provider_id in provider_ids:
                button = QToolButton()
                button.setCheckable(True)
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
                self._group.addButton(button)
                button.setFixedSize(28, 28)
                button.setIconSize(QSize(20, 20))
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.clicked.connect(lambda _checked=False, value=provider_id: self.selected.emit(value))
                bind_text(button, PROVIDERS[provider_id].name, method="setToolTip")
                bind_text(button, PROVIDERS[provider_id].name, method="setAccessibleName")
                self._layout.addWidget(button)
                self.buttons[provider_id] = button
        tokens = current_theme()
        for provider_id, button in self.buttons.items():
            button.setIcon(provider_icon(provider_id, 20))
            button.setChecked(provider_id == self._current)
            # 全局工具按钮有内边距；这里是紧凑纯图标按钮，必须保留实际的 20px 图标空间。
            button.setStyleSheet(f"QToolButton {{ border: 0; border-radius: 7px; background: transparent; padding: 0; min-width: 0; min-height: 0; }} QToolButton:hover, QToolButton:checked {{ background: {tokens.accent_soft}; }} QToolButton:focus {{ border: 1px solid {tokens.accent}; }}")


class ProviderManagerButton(QToolButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        bind_text(self, "管理平台", method="setToolTip")
        bind_text(self, "管理平台", method="setAccessibleName")

    def paintEvent(self, event) -> None:
        tokens = current_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(tokens.accent), 1) if self.hasFocus() else Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens.accent_soft) if self.underMouse() or self.hasFocus() else Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 7, 7)
        painter.setPen(QPen(QColor(tokens.subtext), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        center = QPointF(self.rect().center())
        painter.drawLine(center + QPointF(-5, 0), center + QPointF(5, 0))
        painter.drawLine(center + QPointF(0, -5), center + QPointF(0, 5))
