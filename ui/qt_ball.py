"""Custom-painted floating usage ball."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

from ui.qt_theme import C_ACCENT, C_ACCENT_2, C_BORDER, C_CARD, C_PANEL, C_SUBTEXT, C_TEXT


DESIGN_SIZE = 120


class FloatingUsageBall(QWidget):
    pressed = Signal(QPoint)
    dragged = Signal(QPoint)
    released = Signal(QPoint)

    def __init__(self, size: int = 96, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._today = "--"
        self._balance = "--"
        self._hovered = False
        self._active = False

    def set_values(self, today: str, balance: str) -> None:
        if self._today == today and self._balance == balance:
            return
        self._today = today
        self._balance = balance
        self.update()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._active = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.update()
            self.pressed.emit(event.globalPosition().toPoint())
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.dragged.emit(event.globalPosition().toPoint())
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._active = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.update()
            self.released.emit(event.globalPosition().toPoint())
            event.accept()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        side = min(self.width(), self.height())
        painter.scale(side / DESIGN_SIZE, side / DESIGN_SIZE)
        side = DESIGN_SIZE
        center = QPointF(side / 2, side / 2)

        glow_alpha = 70 if self._hovered else 36
        if self._active:
            glow_alpha = 24
        painter.setPen(QPen(QColor(47, 111, 228, glow_alpha), 4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, side / 2 - 2, side / 2 - 2)

        outer = QRadialGradient(center, side / 2)
        outer.setColorAt(0.0, QColor(C_CARD))
        outer.setColorAt(0.72, QColor(C_PANEL))
        outer.setColorAt(1.0, QColor("#0b2c66"))
        painter.setBrush(outer)
        painter.setPen(QPen(QColor(C_ACCENT_2 if self._hovered else C_ACCENT), 2))
        painter.drawEllipse(center, side / 2 - 3, side / 2 - 3)

        highlight = QLinearGradient(0, 8, 0, side * 0.55)
        highlight.setColorAt(0.0, QColor(90, 145, 255, 42))
        highlight.setColorAt(1.0, QColor(90, 145, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(highlight)
        painter.drawEllipse(QRectF(16, 12, side - 32, side * 0.42))

        painter.setPen(QColor(C_SUBTEXT))
        painter.setFont(QFont("Microsoft YaHei UI", 9))
        painter.drawText(QRectF(10, 18, side - 20, 18), Qt.AlignmentFlag.AlignCenter, "今日使用")

        painter.setPen(QColor(C_TEXT))
        value_size = 16 if len(self._today) <= 8 else 12
        painter.setFont(QFont("Microsoft YaHei UI", value_size, QFont.Weight.Bold))
        painter.drawText(QRectF(8, 34, side - 16, 25), Qt.AlignmentFlag.AlignCenter, self._today)

        painter.setPen(QPen(QColor(C_BORDER), 1))
        painter.drawLine(QPointF(side * 0.25, 64), QPointF(side * 0.75, 64))
        painter.setPen(QColor(C_SUBTEXT))
        painter.setFont(QFont("Microsoft YaHei UI", 8))
        painter.drawText(QRectF(10, 65, side - 20, 15), Qt.AlignmentFlag.AlignCenter, "余额")
        painter.setPen(QColor(C_ACCENT_2))
        balance_size = 11 if len(self._balance) <= 8 else 9
        painter.setFont(QFont("Microsoft YaHei UI", balance_size, QFont.Weight.DemiBold))
        painter.drawText(QRectF(14, 80, side - 28, 19), Qt.AlignmentFlag.AlignCenter, self._balance)
        painter.end()
