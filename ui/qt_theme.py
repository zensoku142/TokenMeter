"""Shared colors and Qt style sheet for the desktop UI."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPen, QPixmap


C_MASK = "#010101"
C_DEEP = "#010513"
C_PANEL = "#051228"
C_SURFACE = "#0B1831"
C_CARD = "#0F2448"
C_CARD_DEEP = C_SURFACE
C_BORDER = "#142A5D"
C_DIVIDER = "#132750"

# Qt style sheets composite these translucent surfaces over C_PANEL. The opaque
# card colors remain available to custom-painted assets that cannot inherit a fill.
C_GLASS_CARD = "rgba(38, 89, 158, 26)"
C_GLASS_CARD_HOVER = "rgba(46, 107, 191, 36)"
C_GLASS_BORDER = "rgba(102, 166, 255, 41)"
C_GLASS_BORDER_HOVER = "rgba(143, 183, 255, 72)"
C_CARD_HOVER = "#132A5D"

C_ACCENT = "#2767E5"
C_BRIGHT_BLUE = "#4E8BF2"
C_HIGHLIGHT_BLUE = "#6EA8FF"
C_PALE_BLUE = "#8FB7FF"
C_DEEP_BLUE = "#1442A2"
C_LOW_BLUE = "#132A5D"
C_ACCENT_2 = C_BRIGHT_BLUE

C_TEXT = "#E5E9F0"
C_VALUE = "#F1F5FF"
C_SUBTEXT = "#9DA5B7"
C_TIME = "#7D879C"
C_MUTED = "#586279"
C_DISABLED = "#3F4960"
C_ROW_BG = C_SURFACE

C_GREEN = "#25D07D"
C_YELLOW = "#F5A524"
C_RED = "#FF5C73"
C_HEAT = (C_LOW_BLUE, C_DEEP_BLUE, "#1F58C7", C_ACCENT, C_BRIGHT_BLUE)

PANEL_PADDING = 14
SECTION_SPACING = 8
CARD_PADDING = 12
CARD_RADIUS = 14
CONTROL_RADIUS = 9
HEADER_HEIGHT = 54
METRIC_CARD_HEIGHT = 100
ACTIVITY_CARD_HEIGHT = 176
LOWER_CARD_HEIGHT = 128
STATUS_BAR_HEIGHT = 38


# Windows can retain its light native popup background while inheriting the app's
# light foreground, so menus must define both sides of the contrast explicitly.
APP_STYLE = f"""
QWidget {{
    color: {C_TEXT};
    font-family: "Microsoft YaHei UI";
    font-size: 13px;
}}
QWidget#panelRoot, QDialog {{
    background: {C_PANEL};
}}
QFrame#panelFrame {{
    background: {C_PANEL};
    border: 1px solid {C_GLASS_BORDER};
    border-radius: 18px;
}}
QFrame#card {{
    background: {C_GLASS_CARD};
    border: 1px solid {C_GLASS_BORDER};
    border-radius: 14px;
}}
QFrame#card:hover {{
    background: {C_GLASS_CARD_HOVER};
    border-color: {C_GLASS_BORDER_HOVER};
}}
QLabel#panelTitle {{ color: {C_TEXT}; font-size: 20px; font-weight: 700; }}
QLabel#cardTitle {{ color: {C_SUBTEXT}; font-size: 13px; font-weight: 600; }}
QLabel#metricIcon {{
    background: rgba(78, 139, 242, 26);
    border: 1px solid rgba(110, 168, 255, 31);
    border-radius: 9px;
}}
QLabel#metricValue {{ color: {C_VALUE}; font-size: 28px; font-weight: 700; }}
QLabel#metricDetail, QLabel#muted {{ color: {C_SUBTEXT}; font-size: 12px; }}
QLabel#sectionTitle {{ color: {C_TEXT}; font-size: 15px; font-weight: 700; }}
QLabel#statusText {{ color: {C_TIME}; font-size: 12px; }}
QWidget#statusBar {{ border-top: 1px solid rgba(143, 183, 255, 26); }}
QPushButton, QToolButton {{
    min-height: 32px;
    padding: 0 12px;
    color: {C_SUBTEXT};
    background: {C_ROW_BG};
    border: 1px solid {C_BORDER};
    border-radius: {CONTROL_RADIUS}px;
}}
QPushButton:hover, QToolButton:hover {{
    color: {C_TEXT};
    background: rgba(78, 139, 242, 26);
    border-color: rgba(110, 168, 255, 54);
}}
QPushButton:pressed, QToolButton:pressed {{ background: rgba(39, 103, 229, 41); }}
QPushButton:focus, QToolButton:focus {{ border-color: {C_ACCENT_2}; }}
QToolButton#panelToolButton {{
    min-width: 32px;
    max-width: 32px;
    min-height: 32px;
    max-height: 32px;
    padding: 0;
    background: transparent;
    border: 1px solid transparent;
    border-radius: {CONTROL_RADIUS}px;
}}
QToolButton#panelToolButton:hover {{
    background: rgba(78, 139, 242, 26);
    border-color: rgba(110, 168, 255, 54);
}}
QToolButton#panelToolButton:pressed {{
    background: rgba(39, 103, 229, 41);
    border-color: rgba(143, 183, 255, 38);
}}
QToolButton#panelToolButton:focus {{ border-color: {C_ACCENT_2}; }}
QToolButton#panelToolButton[role="close"]:hover {{
    color: #FF7A8B;
    background: rgba(255, 92, 115, 20);
    border-color: rgba(255, 122, 139, 52);
}}
QPushButton#primaryButton {{
    color: white;
    background: {C_ACCENT};
    border-color: {C_ACCENT};
    font-weight: 600;
}}
QPushButton#primaryButton:hover {{ background: {C_BRIGHT_BLUE}; }}
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {{
    color: {C_TEXT};
    background: {C_ROW_BG};
    border: 1px solid {C_BORDER};
    border-radius: {CONTROL_RADIUS}px;
    padding: 8px 10px;
    selection-background-color: {C_ACCENT};
}}
QLineEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QComboBox:hover {{
    border-color: {C_DEEP_BLUE};
}}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {C_ACCENT_2};
}}
QComboBox QAbstractItemView {{
    color: {C_TEXT};
    background: {C_SURFACE};
    border: 1px solid {C_BORDER};
    selection-background-color: {C_ACCENT};
    selection-color: {C_VALUE};
    outline: 0;
    padding: 4px;
}}
QComboBox QAbstractItemView::item {{
    padding: 6px 10px;
    min-height: 24px;
}}
QComboBox QAbstractItemView::item:hover {{
    background: {C_GLASS_CARD_HOVER};
}}
QComboBox::drop-down {{
    border: 0;
    width: 28px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {C_SUBTEXT};
    margin-right: 8px;
}}
QScrollArea {{ background: transparent; border: 0; }}
QScrollBar:horizontal {{ background: {C_ROW_BG}; height: 8px; border-radius: 4px; }}
QScrollBar::handle:horizontal {{ background: {C_DEEP_BLUE}; min-width: 36px; border-radius: 4px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QMenu {{
    color: {C_TEXT};
    background: {C_SURFACE};
    border: 1px solid {C_BORDER};
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 24px 7px 12px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    color: {C_VALUE};
    background: {C_DEEP_BLUE};
}}
QMenu::item:disabled {{ color: {C_DISABLED}; }}
QMenu::separator {{
    height: 1px;
    background: {C_DIVIDER};
    margin: 5px 8px;
}}
QToolTip {{
    color: {C_TEXT};
    background: {C_SURFACE};
    border: 1px solid rgba(110, 168, 255, 56);
    border-radius: {CONTROL_RADIUS}px;
    padding: 8px;
}}
"""


_FLUENT_GLYPHS = {
    "settings": "\ue713",
    "refresh": "\ue72c",
    "close": "\ue711",
}


@lru_cache(maxsize=1)
def _fluent_icon_family() -> str | None:
    available = {family.casefold(): family for family in QFontDatabase.families()}
    for candidate in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
        if candidate.casefold() in available:
            return available[candidate.casefold()]
    return None


def fluent_icon(
    name: str,
    size: int = 18,
    active_color: str = C_ACCENT_2,
) -> QIcon:
    """Return a Windows Fluent line icon with consistent normal/hover states."""
    family = _fluent_icon_family()
    glyph = _FLUENT_GLYPHS.get(name)
    if family is None or glyph is None:
        return QIcon()

    icon = QIcon()
    for mode, color in (
        (QIcon.Mode.Normal, C_SUBTEXT),
        (QIcon.Mode.Active, active_color),
        (QIcon.Mode.Selected, active_color),
        (QIcon.Mode.Disabled, C_TIME),
    ):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        font = QFont(family)
        font.setPixelSize(size - 1)
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
        painter.end()
        icon.addPixmap(pixmap, mode)
    return icon


def metric_icon(name: str, size: int = 18) -> QPixmap:
    """Draw metric-card icons as a small, dependency-free line set."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(C_HIGHLIGHT_BLUE))
    pen.setWidthF(1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if name == "usage":
        painter.drawRoundedRect(QRectF(2.5, 4.0, size - 5.0, size - 7.0), 2, 2)
        painter.drawLine(QPointF(3.5, 7.0), QPointF(size - 3.5, 7.0))
        painter.drawLine(QPointF(5.0, size - 5.0), QPointF(8.0, size - 5.0))
    elif name == "balance":
        painter.drawRoundedRect(QRectF(2.5, 4.0, size - 5.0, size - 7.0), 2, 2)
        painter.drawRoundedRect(QRectF(size - 8.0, 7.0, 5.5, 4.5), 1.5, 1.5)
        painter.drawPoint(QPointF(size - 5.0, 9.25))
    else:
        painter.drawRoundedRect(QRectF(2.5, 3.5, size - 5.0, size - 6.0), 2, 2)
        painter.drawLine(QPointF(3.0, 7.0), QPointF(size - 3.0, 7.0))
        painter.drawLine(QPointF(6.0, 2.5), QPointF(6.0, 5.0))
        painter.drawLine(QPointF(size - 6.0, 2.5), QPointF(size - 6.0, 5.0))
        painter.drawLine(QPointF(6.0, 10.0), QPointF(8.0, 10.0))
        painter.drawLine(QPointF(10.0, 10.0), QPointF(12.0, 10.0))
    painter.end()
    return pixmap


def app_icon(size: int = 64) -> QIcon:
    """Port the existing spider-web tray mark to a high-DPI Qt pixmap."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    center = QPointF(size / 2, size / 2)
    radius = size / 2 - 4
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(C_CARD))
    painter.drawEllipse(center, radius, radius)
    painter.setPen(QPen(QColor(77, 124, 255, 180), max(1.0, size / 64)))
    for end in (
        QPointF(size / 2, 5), QPointF(size - 8, size / 4),
        QPointF(size - 8, size * 3 / 4), QPointF(size / 2, size - 5),
        QPointF(8, size * 3 / 4), QPointF(8, size / 4),
    ):
        painter.drawLine(center, end)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(C_ACCENT))
    dot = max(4.0, size / 10)
    painter.drawEllipse(QRectF(center.x() - dot, center.y() - dot, dot * 2, dot * 2))
    painter.end()
    return QIcon(pixmap)
