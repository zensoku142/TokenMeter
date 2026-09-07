"""Application theme tokens, controller, and shared painted assets."""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Literal, TypeAlias

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QIcon,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
)


ThemeMode: TypeAlias = Literal["system", "light", "dark"]


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    name: Literal["light", "dark"]
    window: str
    surface: str
    elevated: str
    border: str
    border_hover: str
    text: str
    value: str
    subtext: str
    muted: str
    disabled: str
    accent: str
    accent_hover: str
    accent_soft: str
    accent_text: str
    on_accent: str
    selection: str
    success: str
    warning: str
    danger: str
    shadow: str
    heat: tuple[str, ...]
    panel_opacity: int


LIGHT_THEME = ThemeTokens(
    name="light",
    window="#F7F7F8",
    surface="#FFFFFF",
    elevated="#FFFFFF",
    border="#90949B",
    border_hover="#707782",
    text="#25272B",
    value="#111318",
    subtext="#5E6571",
    muted="#686F79",
    disabled="#A8ADB5",
    accent="#2F72E8",
    accent_hover="#1E61D2",
    accent_soft="#E9F1FF",
    accent_text="#2F72E8",
    on_accent="#FFFFFF",
    selection="#2076FA",
    success="#087A4A",
    warning="#946000",
    danger="#C43B4D",
    shadow="#24111827",
    heat=("#F0F3F8", "#DCE8FB", "#B7D0F8", "#84AEF3", "#4E88ED", "#2F72E8"),
    panel_opacity=100,
)

DARK_THEME = ThemeTokens(
    name="dark",
    window="#151515",
    surface="#1D1D1D",
    elevated="#242424",
    border="#6A6A6A",
    border_hover="#858585",
    text="#E6E6E6",
    value="#FAFAFA",
    subtext="#A8A8AD",
    muted="#92929A",
    disabled="#55555B",
    accent="#3478F6",
    accent_hover="#5A93FA",
    accent_soft="#20304A",
    accent_text="#3478F6",
    on_accent="#FFFFFF",
    selection="#2076FA",
    success="#18C77A",
    warning="#F2AB3B",
    danger="#FF6477",
    shadow="#6E000000",
    heat=("#242424", "#28364E", "#294C7A", "#2B62A7", "#2F76D7", "#3B82F6"),
    panel_opacity=100,
)


# These names remain import-compatible while new UI code reads ThemeTokens at
# paint time. They deliberately resolve to the dark palette used before startup.
C_MASK = "#010101"
C_DEEP = DARK_THEME.window
C_PANEL = DARK_THEME.window
C_SURFACE = DARK_THEME.surface
C_CARD = DARK_THEME.elevated
C_CARD_DEEP = DARK_THEME.surface
C_BORDER = DARK_THEME.border
C_DIVIDER = DARK_THEME.border
C_GLASS_CARD = DARK_THEME.surface
C_GLASS_CARD_HOVER = DARK_THEME.elevated
C_GLASS_BORDER = DARK_THEME.border
C_GLASS_BORDER_HOVER = DARK_THEME.border_hover
C_CARD_HOVER = DARK_THEME.elevated
C_ACCENT = DARK_THEME.accent
C_BRIGHT_BLUE = DARK_THEME.accent_hover
C_HIGHLIGHT_BLUE = DARK_THEME.accent_hover
C_PALE_BLUE = DARK_THEME.accent_hover
C_DEEP_BLUE = DARK_THEME.accent
C_LOW_BLUE = DARK_THEME.accent_soft
C_ACCENT_2 = DARK_THEME.accent_hover
C_TEXT = DARK_THEME.text
C_VALUE = DARK_THEME.value
C_SUBTEXT = DARK_THEME.subtext
C_TIME = DARK_THEME.muted
C_MUTED = DARK_THEME.muted
C_DISABLED = DARK_THEME.disabled
C_ROW_BG = DARK_THEME.surface
C_GREEN = DARK_THEME.success
C_YELLOW = DARK_THEME.warning
C_RED = DARK_THEME.danger
C_HEAT = DARK_THEME.heat

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


def _mix_color(first: str, second: str, second_weight: float) -> str:
    """Blend two opaque colors while retaining the selected accent's hue."""
    left = QColor(first)
    right = QColor(second)
    weight = max(0.0, min(1.0, float(second_weight)))
    mixed = QColor(
        round(left.red() * (1 - weight) + right.red() * weight),
        round(left.green() * (1 - weight) + right.green() * weight),
        round(left.blue() * (1 - weight) + right.blue() * weight),
    )
    return mixed.name(QColor.NameFormat.HexRgb).upper()


def _relative_luminance(color: str) -> float:
    value = QColor(color)

    def linear(channel: float) -> float:
        return (
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
        )

    red, green, blue = (
        linear(channel)
        for channel in (value.redF(), value.greenF(), value.blueF())
    )
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(first: str, second: str) -> float:
    lighter, darker = sorted(
        (_relative_luminance(first), _relative_luminance(second)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def _readable_accent(accent: str, surface: str, theme_name: str) -> str:
    if _contrast(accent, surface) >= 4.5:
        return accent
    target = "#000000" if theme_name == "light" else "#FFFFFF"
    for step in range(1, 11):
        candidate = _mix_color(accent, target, step / 10)
        if _contrast(candidate, surface) >= 4.5:
            return candidate
    return target


def derive_theme_tokens(
    base: ThemeTokens,
    accent_color: str,
    panel_opacity: int = 100,
) -> ThemeTokens:
    """Derive every non-semantic accent role from one user-selected color."""
    accent = QColor(str(accent_color).strip())
    if not accent.isValid() or accent.alpha() != 255:
        raise ValueError("Accent color must be an opaque #RRGGBB color")
    normalized = accent.name(QColor.NameFormat.HexRgb).upper()
    opacity = int(panel_opacity)
    if not 70 <= opacity <= 100:
        raise ValueError("Panel opacity must be between 70 and 100")
    if normalized == base.accent.upper():
        # 精确复用既有派生色，保证用户未自定义时与当前版本像素级一致。
        return replace(base, panel_opacity=opacity)

    hover_target = "#000000" if base.name == "light" else "#FFFFFF"
    heat_base = base.heat[0]
    heat = (
        heat_base,
        *(
            _mix_color(heat_base, normalized, weight)
            for weight in (0.18, 0.36, 0.56, 0.76)
        ),
        normalized,
    )
    black_contrast = _contrast("#000000", normalized)
    white_contrast = _contrast("#FFFFFF", normalized)
    return replace(
        base,
        accent=normalized,
        accent_hover=_mix_color(normalized, hover_target, 0.16),
        accent_soft=_mix_color(base.surface, normalized, 0.16),
        accent_text=_readable_accent(normalized, base.surface, base.name),
        on_accent="#000000" if black_contrast > white_contrast else "#FFFFFF",
        selection=normalized,
        heat=heat,
        panel_opacity=opacity,
    )


def panel_background(color: str, theme: ThemeTokens | None = None) -> QColor:
    """Return a translucent panel background without fading foreground content."""
    tokens = theme or current_theme()
    result = QColor(color)
    result.setAlpha(round(255 * tokens.panel_opacity / 100))
    return result


def _panel_css(color: str, theme: ThemeTokens) -> str:
    value = panel_background(color, theme)
    return f"rgba({value.red()}, {value.green()}, {value.blue()}, {value.alpha()})"


def _theme_tokens(theme: ThemeTokens | str | None) -> ThemeTokens:
    if isinstance(theme, ThemeTokens):
        return theme
    if theme == "light":
        return LIGHT_THEME
    if theme in (None, "dark"):
        return DARK_THEME if theme == "dark" else current_theme()
    raise ValueError("Theme must be light or dark")


def build_app_style(theme: ThemeTokens | str | None = None) -> str:
    """Build the complete application QSS for one resolved theme."""
    tokens = _theme_tokens(theme)
    panel_window = _panel_css(tokens.window, tokens)
    panel_surface = _panel_css(tokens.surface, tokens)
    panel_elevated = _panel_css(tokens.elevated, tokens)
    divider = QColor(tokens.border)
    divider_color = f"rgba({divider.red()}, {divider.green()}, {divider.blue()}, 82)"
    return f"""
QWidget {{
    color: {tokens.text};
    font-family: "Microsoft YaHei UI";
    font-size: 12px;
}}
QWidget#panelRoot {{
    background: transparent;
}}
QDialog {{
    background: {tokens.window};
}}
QFrame#panelFrame {{
    background: {panel_window};
    border: 1px solid {tokens.border};
    border-radius: 18px;
}}
QWidget#panelHeader, QWidget#topSection {{
    background: transparent;
    border: 0;
}}
QFrame#card {{
    background: {panel_surface};
    border: 1px solid {tokens.border};
    border-radius: 14px;
}}
QFrame#card:hover {{
    background: {panel_elevated};
    border-color: {tokens.border_hover};
}}
QFrame#settingsCard {{
    background: {tokens.surface};
    border: 1px solid {tokens.border};
    border-radius: 12px;
}}
QFrame#activityTooltip {{
    background: {tokens.elevated};
    border: 1px solid {tokens.border_hover};
    border-radius: 9px;
}}
QTabWidget::pane {{
    border: 1px solid {tokens.border};
    border-radius: 12px;
    top: -1px;
}}
QTabBar::tab {{
    min-height: 30px;
    padding: 0 14px;
    color: {tokens.subtext};
    background: transparent;
    border: 1px solid transparent;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: {tokens.text};
    border-bottom-color: {tokens.accent};
}}
QTabBar::tab:hover {{ color: {tokens.text}; }}
QTabWidget#settingsTabs::pane {{ border: 0; top: 0; }}
QTabWidget#settingsTabs QTabBar {{ background: transparent; font-size: 13px; }}
QFrame#settingsSection {{ background: transparent; border: 0; }}
QDialog#settingsPage QFrame#settingsCard {{ border-color: {divider_color}; }}
QDialog#settingsPage QLineEdit, QDialog#settingsPage QPlainTextEdit,
QDialog#settingsPage QComboBox, QDialog#settingsPage QSpinBox, QDialog#settingsPage QTimeEdit {{
    border: 1px solid {divider_color};
    border-radius: 9px;
}}
QDialog#settingsPage QLineEdit:focus, QDialog#settingsPage QPlainTextEdit:focus,
QDialog#settingsPage QComboBox:focus, QDialog#settingsPage QSpinBox:focus,
QDialog#settingsPage QTimeEdit:focus {{ border-color: {tokens.accent}; }}
QDialog#settingsPage QComboBox {{ padding-right: 28px; }}
QDialog#settingsPage QComboBox::drop-down {{ border: 0; width: 24px; }}
/* Qt 会自动扣除箭头按钮宽度，这里只留文字间距，避免重复预留导致百分比被裁切。 */
QDialog#settingsPage QSpinBox {{ padding-right: 4px; }}
QDialog#settingsPage QSpinBox::up-button,
QDialog#settingsPage QSpinBox::down-button {{
    subcontrol-origin: border;
    width: 26px;
    height: 14px;
    margin-right: 4px;
    border: 0;
    border-radius: 4px;
    background: transparent;
}}
QDialog#settingsPage QSpinBox::up-button {{
    subcontrol-position: top right;
    margin-top: 3px;
}}
QDialog#settingsPage QSpinBox::down-button {{
    subcontrol-position: bottom right;
    margin-bottom: 3px;
}}
QDialog#settingsPage QSpinBox::up-button:hover,
QDialog#settingsPage QSpinBox::down-button:hover {{ background: {tokens.accent_soft}; }}
QDialog#settingsPage QSpinBox::up-button:pressed,
QDialog#settingsPage QSpinBox::down-button:pressed {{ background: {tokens.selection}; }}
QDialog#settingsPage QSpinBox::up-button:disabled,
QDialog#settingsPage QSpinBox::down-button:disabled,
QDialog#settingsPage QSpinBox::up-button:off,
QDialog#settingsPage QSpinBox::down-button:off {{ background: transparent; }}
/* 箭头由设置数字框按主题绘制，避免原生立体按钮和字体差异。 */
QDialog#settingsPage QSpinBox::up-arrow,
QDialog#settingsPage QSpinBox::down-arrow {{ width: 0; height: 0; }}
QFrame#settingsSwitchRow {{
    background: transparent;
    border: 0;
    border-bottom: 1px solid {divider_color};
}}
QLabel#settingsRowTitle {{ font-size: 14px; font-weight: 600; }}
QLabel#settingsSaveStatus {{ font-size: 11px; }}
QLabel#panelTitle {{ color: {tokens.value}; font-size: 17px; font-weight: 700; }}
QComboBox#headerProviderCombo, QToolButton#settingsBackButton {{
    background: transparent;
    border: 0;
    padding: 0;
    font-size: 12px;
}}
QToolButton#settingsBackButton:focus {{ background: {tokens.accent_soft}; }}
QFrame#headerProviderPopup {{
    background: transparent;
    border: 0;
}}
QAbstractItemView#headerProviderView {{
    color: {tokens.text};
    background: {tokens.elevated};
    border: 0;
    border-radius: 10px;
    padding: 5px;
    outline: 0;
    selection-background-color: transparent;
    selection-color: {tokens.text};
}}
QLabel#panelSubtitle {{ color: {tokens.text}; font-size: 16px; font-weight: 400; }}
QLabel#pricingBadge {{
    color: {tokens.accent_text};
    background: {tokens.accent_soft};
    border: 1px solid {tokens.accent};
    border-radius: 9px;
    padding: 2px 7px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel#pricingBadge[pricingState="peak"] {{
    color: {tokens.warning};
    background: {tokens.surface};
    border-color: {tokens.warning};
}}
QLabel#cardTitle, QLabel#metricLabel, QLabel#statLabel {{
    color: {tokens.subtext};
    font-size: 12px;
    font-weight: 500;
}}
QToolButton#quotaDetailsButton {{
    color: {tokens.accent_text};
    background: transparent;
    border: 0;
    border-radius: 6px;
    padding: 0 6px;
    min-height: 0;
    font-size: 12px;
    font-weight: 500;
}}
QToolButton#quotaDetailsButton:hover {{ background: {tokens.accent_soft}; }}
QToolButton#quotaDetailsButton:focus {{ border: 1px solid {tokens.accent}; }}
QLabel#metricIcon {{
    background: {tokens.accent_soft};
    border: 1px solid {tokens.border};
    border-radius: 9px;
}}
QLabel#heroValue {{ color: {tokens.value}; font-size: 36px; font-weight: 700; }}
QLabel#metricValue {{ color: {tokens.value}; font-size: 20px; font-weight: 700; }}
QLabel#statValue {{ color: {tokens.value}; font-size: 17px; font-weight: 600; }}
QLabel#metricDetail, QLabel#muted, QLabel[tone="muted"] {{
    color: {tokens.subtext};
    font-size: 12px;
}}
QLabel#activitySummary {{
    color: {tokens.subtext};
    font-size: 10px;
}}
QLabel[tone="success"] {{ color: {tokens.success}; }}
QLabel[tone="warning"] {{ color: {tokens.warning}; }}
QLabel[tone="danger"] {{ color: {tokens.danger}; }}
QLabel#sectionTitle {{ color: {tokens.value}; font-size: 14px; font-weight: 700; }}
QLabel#statusText {{ color: {tokens.muted}; font-size: 11px; }}
QLabel#minuteUsageState {{ color: {tokens.subtext}; font-size: 12px; }}
QLabel#minuteDateLabel {{ color: {tokens.subtext}; font-size: 11px; }}
QFrame#divider {{ background: {divider_color}; border: 0; }}
QWidget#statusBar {{ border-top: 1px solid {divider_color}; }}
QPushButton, QToolButton {{
    min-height: 32px;
    padding: 0 12px;
    color: {tokens.subtext};
    background: {tokens.surface};
    border: 1px solid {tokens.border};
    border-radius: {CONTROL_RADIUS}px;
}}
QPushButton:hover, QToolButton:hover {{
    color: {tokens.text};
    background: {tokens.elevated};
    border-color: {tokens.border_hover};
}}
QPushButton:pressed, QToolButton:pressed {{ background: {tokens.accent_soft}; }}
QPushButton:focus, QToolButton:focus {{ border-color: {tokens.accent}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {tokens.disabled}; }}
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
    background: {tokens.elevated};
    border-color: {tokens.border_hover};
}}
QToolButton#panelToolButton:pressed {{
    background: {tokens.accent_soft};
    border-color: {tokens.border_hover};
}}
QToolButton#panelToolButton:focus {{ border-color: {tokens.accent}; }}
QToolButton#panelToolButton[role="close"]:hover {{
    color: {tokens.danger};
    background: {tokens.elevated};
    border-color: {tokens.danger};
}}
QFrame#themeSegment {{
    background: {tokens.surface};
    border: 1px solid {tokens.border};
    border-radius: {CONTROL_RADIUS}px;
}}
QToolButton#themeButton {{
    min-width: 24px;
    max-width: 24px;
    min-height: 24px;
    max-height: 24px;
    padding: 0;
    background: transparent;
    border: 0;
}}
QToolButton#themeButton[selected="true"] {{
    color: {tokens.value};
    background: {tokens.accent_soft};
}}
QFrame#activityModeSegment,
QWidget#minuteDateEdit {{
    background: {tokens.surface};
    border: 1px solid {tokens.border};
    border-radius: 6px;
}}
QToolButton#activityModeButton {{
    min-width: 72px;
    max-width: 72px;
    min-height: 22px;
    max-height: 22px;
    padding: 0;
    color: {tokens.subtext};
    background: transparent;
    border: 0;
    border-radius: 5px;
    font-size: 11px;
}}
QToolButton#activityModeButton:checked {{
    color: {tokens.on_accent};
    background: {tokens.selection};
}}
QToolButton#minuteDatePreviousButton,
QToolButton#minuteDateTextButton,
QToolButton#minuteDateNextButton {{
    min-height: 24px;
    max-height: 24px;
    padding: 0;
    color: {tokens.text};
    background: transparent;
    border: 0;
    border-radius: 0;
    font-size: 11px;
}}
QToolButton#minuteDatePreviousButton,
QToolButton#minuteDateTextButton {{
    border-right: 1px solid {tokens.border};
}}
QToolButton#minuteDatePreviousButton {{
    border-top-left-radius: 5px;
    border-bottom-left-radius: 5px;
}}
QToolButton#minuteDateNextButton {{
    border-top-right-radius: 5px;
    border-bottom-right-radius: 5px;
}}
QToolButton#minuteDatePreviousButton:hover,
QToolButton#minuteDateTextButton:hover,
QToolButton#minuteDateNextButton:hover {{
    color: {tokens.value};
    background: {tokens.accent_soft};
}}
QToolButton#minuteDatePreviousButton:pressed,
QToolButton#minuteDateTextButton:pressed,
QToolButton#minuteDateNextButton:pressed {{
    color: {tokens.on_accent};
    background: {tokens.accent};
}}
QToolButton#minuteDatePreviousButton:disabled,
QToolButton#minuteDateTextButton:disabled,
QToolButton#minuteDateNextButton:disabled {{
    color: {tokens.disabled};
    background: transparent;
}}
QFrame#minuteCalendarPopup {{
    color: {tokens.text};
    background: {tokens.elevated};
    border: 1px solid {tokens.border};
    border-radius: 8px;
}}
QLabel#minuteCalendarMonth {{
    color: {tokens.value};
    background: transparent;
    border: 0;
    font-size: 12px;
}}
QToolButton#minuteCalendarNavButton {{
    min-width: 28px;
    max-width: 28px;
    min-height: 26px;
    max-height: 26px;
    padding: 0;
    color: {tokens.accent_text};
    background: transparent;
    border: 0;
    border-radius: 5px;
    font-size: 16px;
}}
QToolButton#minuteCalendarNavButton:hover {{
    background: {tokens.accent_soft};
}}
QToolButton#minuteCalendarNavButton:disabled {{
    color: {tokens.disabled};
    background: transparent;
}}
QCalendarWidget#minuteCalendar {{
    color: {tokens.text};
    background: transparent;
    border: 0;
}}
QCalendarWidget#minuteCalendar QAbstractItemView {{
    color: {tokens.text};
    background: transparent;
    alternate-background-color: transparent;
    selection-background-color: transparent;
    selection-color: {tokens.text};
    border: 0;
    outline: 0;
    font-size: 11px;
}}
QToolButton#minuteLegendButton {{
    min-height: 22px;
    padding: 0;
    color: {tokens.subtext};
    background: transparent;
    border: 0;
    font-size: 10px;
}}
QToolButton#minuteLegendButton:!checked {{
    color: {tokens.disabled};
}}
QFrame#minuteTooltip {{
    color: {tokens.text};
    background: {tokens.elevated};
    border: 1px solid {tokens.border_hover};
    border-radius: 6px;
}}
QLabel#minuteTooltipTitle,
QLabel#minuteTooltipValue {{
    color: {tokens.text};
    background: transparent;
    border: 0;
    font-size: 11px;
}}
QLabel#minuteTooltipTitle {{ font-weight: 600; }}
QLabel#minuteTooltipMuted {{
    color: {tokens.subtext};
    background: transparent;
    border: 0;
    font-size: 11px;
}}
QLabel#minuteTooltipCost {{
    color: {tokens.accent_text};
    background: transparent;
    border: 0;
    font-size: 11px;
    font-weight: 600;
}}
QPushButton#primaryButton {{
    color: {tokens.on_accent};
    background: {tokens.accent};
    border-color: {tokens.accent};
    font-weight: 600;
}}
QPushButton#primaryButton:hover {{ background: {tokens.accent_hover}; }}
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {{
    color: {tokens.text};
    background: {tokens.surface};
    border: 1px solid {tokens.border};
    border-radius: {CONTROL_RADIUS}px;
    padding: 8px 10px;
    selection-background-color: {tokens.accent};
}}
QLineEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QComboBox:hover {{
    border-color: {tokens.border_hover};
}}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {tokens.accent};
}}
QComboBox QAbstractItemView {{
    color: {tokens.text};
    background: {tokens.elevated};
    border: 1px solid {tokens.border};
    selection-background-color: {tokens.accent};
    selection-color: {tokens.on_accent};
    outline: 0;
    padding: 4px;
}}
QComboBox QAbstractItemView::item {{
    padding: 6px 10px;
    min-height: 24px;
}}
QComboBox QAbstractItemView::item:hover {{
    background: {tokens.accent_soft};
}}
QScrollArea {{ background: transparent; border: 0; }}
QScrollBar:horizontal {{ background: {tokens.surface}; height: 8px; border-radius: 4px; }}
QScrollBar:vertical {{ background: {tokens.surface}; width: 8px; border-radius: 4px; }}
QScrollBar::handle:horizontal {{ background: {tokens.border_hover}; min-width: 36px; border-radius: 4px; }}
QScrollBar::handle:vertical {{ background: {tokens.border_hover}; min-height: 36px; border-radius: 4px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QMenu {{
    color: {tokens.text};
    background: {tokens.surface};
    border: 1px solid {tokens.border};
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 24px 7px 12px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    color: {tokens.value};
    background: {tokens.accent_soft};
}}
QMenu::item:disabled {{ color: {tokens.disabled}; }}
QMenu::separator {{
    height: 1px;
    background: {tokens.border};
    margin: 5px 8px;
}}
QToolTip {{
    color: {tokens.text};
    background: {tokens.elevated};
    border: 1px solid {tokens.border_hover};
    border-radius: {CONTROL_RADIUS}px;
    padding: 8px;
}}
"""


def build_qt_palette(theme: ThemeTokens | str | None = None) -> QPalette:
    """Return a native-widget palette aligned with the application QSS."""
    tokens = _theme_tokens(theme)
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: tokens.window,
        QPalette.ColorRole.WindowText: tokens.text,
        QPalette.ColorRole.Base: tokens.surface,
        QPalette.ColorRole.AlternateBase: tokens.elevated,
        QPalette.ColorRole.ToolTipBase: tokens.elevated,
        QPalette.ColorRole.ToolTipText: tokens.text,
        QPalette.ColorRole.Text: tokens.text,
        QPalette.ColorRole.Button: tokens.surface,
        QPalette.ColorRole.ButtonText: tokens.text,
        QPalette.ColorRole.BrightText: tokens.danger,
        QPalette.ColorRole.Highlight: tokens.accent,
        QPalette.ColorRole.HighlightedText: tokens.on_accent,
        QPalette.ColorRole.Link: tokens.accent,
        QPalette.ColorRole.LinkVisited: tokens.accent_hover,
        QPalette.ColorRole.PlaceholderText: tokens.muted,
        QPalette.ColorRole.Light: tokens.elevated,
        QPalette.ColorRole.Midlight: tokens.surface,
        QPalette.ColorRole.Mid: tokens.border,
        QPalette.ColorRole.Dark: tokens.border_hover,
        QPalette.ColorRole.Shadow: tokens.shadow,
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.PlaceholderText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(tokens.disabled))
    return palette


class ThemeController(QObject):
    """Resolve theme preference and apply it to a running QApplication."""

    changed = Signal(str, str)

    def __init__(
        self,
        app: QGuiApplication,
        mode: ThemeMode | str = "dark",
        *,
        light_accent: str = LIGHT_THEME.accent,
        dark_accent: str = DARK_THEME.accent,
        light_panel_opacity: int = 100,
        dark_panel_opacity: int = 100,
        sync_accent: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._app = app
        self._style_hints = app.styleHints()
        self._mode: ThemeMode = "dark"
        self._resolved: Literal["light", "dark"] = "dark"
        self._appearances = {
            "light": (str(light_accent), int(light_panel_opacity)),
            "dark": (str(light_accent if sync_accent else dark_accent), int(dark_panel_opacity)),
        }
        self._sync_accent = bool(sync_accent)
        self._tokens = DARK_THEME
        self._applied = False
        self._setting_mode = False
        self._style_hints.colorSchemeChanged.connect(self._system_scheme_changed)
        self.set_mode(mode)

    @property
    def mode(self) -> ThemeMode:
        return self._mode

    @property
    def resolved(self) -> Literal["light", "dark"]:
        return self._resolved

    @property
    def tokens(self) -> ThemeTokens:
        return self._tokens

    def appearance(self, theme_name: str) -> tuple[str, int]:
        normalized = str(theme_name).strip().lower()
        if normalized not in {"light", "dark"}:
            raise ValueError("Resolved theme must be light or dark")
        return self._appearances[normalized]

    @property
    def sync_accent(self) -> bool:
        return self._sync_accent

    def set_accent_sync(self, enabled: bool) -> None:
        if self._sync_accent == enabled:
            return
        self._sync_accent = enabled
        if enabled:
            # 开启同步时以当前显示的主色为准，只同步颜色，不覆盖另一模式的透明度。
            other = "light" if self._resolved == "dark" else "dark"
            self._appearances[other] = (
                self._appearances[self._resolved][0], self._appearances[other][1]
            )
        self.changed.emit(self._mode, self._resolved)

    def set_appearance(
        self,
        theme_name: str,
        accent_color: str,
        panel_opacity: int,
    ) -> None:
        """Update one resolved theme and refresh visible widgets when active."""
        normalized = str(theme_name).strip().lower()
        if normalized not in {"light", "dark"}:
            raise ValueError("Resolved theme must be light or dark")
        base = LIGHT_THEME if normalized == "light" else DARK_THEME
        tokens = derive_theme_tokens(base, accent_color, panel_opacity)
        appearance = (tokens.accent, tokens.panel_opacity)
        other = "light" if normalized == "dark" else "dark"
        other_appearance = (
            (tokens.accent, self._appearances[other][1])
            if self._sync_accent else self._appearances[other]
        )
        if (
            self._appearances[normalized] == appearance
            and self._appearances[other] == other_appearance
        ):
            return
        self._appearances[normalized] = appearance
        self._appearances[other] = other_appearance
        if self._resolved != normalized and not self._sync_accent:
            return
        self._apply(self._resolved)
        self.changed.emit(self._mode, self._resolved)

    def set_mode(self, mode: ThemeMode | str) -> None:
        normalized = str(mode).strip().lower()
        if normalized not in {"system", "light", "dark"}:
            raise ValueError("Theme mode must be system, light, or dark")

        previous = (self._mode, self._resolved)
        self._mode = normalized  # type: ignore[assignment]
        self._setting_mode = True
        try:
            if normalized == "system":
                # Removing Qt's override is necessary for later Windows theme changes
                # to reach colorSchemeChanged instead of being masked by a forced mode.
                self._style_hints.unsetColorScheme()
                resolved = self._resolved_from_scheme(self._style_hints.colorScheme())
            else:
                scheme = (
                    Qt.ColorScheme.Light if normalized == "light" else Qt.ColorScheme.Dark
                )
                self._style_hints.setColorScheme(scheme)
                resolved = normalized
        finally:
            self._setting_mode = False
        self._apply(resolved)
        if self._applied and previous != (self._mode, self._resolved):
            self.changed.emit(self._mode, self._resolved)

    def _resolved_from_scheme(
        self, scheme: Qt.ColorScheme
    ) -> Literal["light", "dark"]:
        if scheme == Qt.ColorScheme.Light:
            return "light"
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
        # Some platforms briefly report Unknown while the native palette changes.
        # Dark is the safe startup default; afterwards retain the visible theme.
        return self._resolved if self._applied else "dark"

    def _system_scheme_changed(self, scheme: Qt.ColorScheme) -> None:
        if self._mode != "system" or self._setting_mode:
            return
        resolved = self._resolved_from_scheme(scheme)
        if resolved == self._resolved and self._applied:
            return
        self._apply(resolved)
        self.changed.emit(self._mode, self._resolved)

    def _apply(self, resolved: str) -> None:
        self._resolved = "light" if resolved == "light" else "dark"
        base = LIGHT_THEME if self._resolved == "light" else DARK_THEME
        accent, panel_opacity = self._appearances[self._resolved]
        tokens = derive_theme_tokens(base, accent, panel_opacity)
        self._tokens = tokens
        self._app.setPalette(build_qt_palette(tokens))
        # QApplication owns setStyleSheet; QGuiApplication is used in the type
        # annotation so the controller can still be tested with compatible apps.
        self._app.setStyleSheet(build_app_style(tokens))  # type: ignore[attr-defined]
        self._applied = True


_THEME_CONTROLLER: ThemeController | None = None


def configure_theme(
    app: QGuiApplication,
    mode: ThemeMode | str,
    *,
    light_accent: str = LIGHT_THEME.accent,
    dark_accent: str = DARK_THEME.accent,
    light_panel_opacity: int = 100,
    dark_panel_opacity: int = 100,
    sync_accent: bool = False,
) -> ThemeController:
    """Configure and apply the application-wide theme before widgets are built."""
    global _THEME_CONTROLLER
    if _THEME_CONTROLLER is None or _THEME_CONTROLLER._app is not app:
        _THEME_CONTROLLER = ThemeController(
            app,
            mode,
            light_accent=light_accent,
            dark_accent=dark_accent,
            light_panel_opacity=light_panel_opacity,
            dark_panel_opacity=dark_panel_opacity,
            sync_accent=sync_accent,
        )
    else:
        # 整组配置载入期间先关闭联动，避免先写入的一种颜色覆盖另一种。
        _THEME_CONTROLLER.set_accent_sync(False)
        _THEME_CONTROLLER.set_appearance("light", light_accent, light_panel_opacity)
        _THEME_CONTROLLER.set_appearance(
            "dark", light_accent if sync_accent else dark_accent, dark_panel_opacity
        )
        _THEME_CONTROLLER.set_mode(mode)
        _THEME_CONTROLLER.set_accent_sync(sync_accent)
    return _THEME_CONTROLLER


def theme_controller() -> ThemeController:
    global _THEME_CONTROLLER
    if _THEME_CONTROLLER is None:
        app = QGuiApplication.instance()
        if app is None:
            raise RuntimeError("Theme has not been configured")
        # Standalone widget tests do not use the App bootstrap; lazily applying
        # dark keeps those constructors compatible without creating an app here.
        _THEME_CONTROLLER = ThemeController(app, "dark")
    return _THEME_CONTROLLER


def current_theme() -> ThemeTokens:
    if _THEME_CONTROLLER is None:
        return DARK_THEME
    return _THEME_CONTROLLER.tokens


# Existing imports keep a stable dark style until configure_theme() takes over.
APP_STYLE = build_app_style(DARK_THEME)


_FLUENT_GLYPHS = {
    "settings": "\ue713",
    "refresh": "\ue72c",
    "close": "\ue711",
    "sun": "\ue706",
    "moon": "\ue708",
    "check": "\ue73e",
    "chevron-down": "\ue70d",
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
    active_color: str | None = None,
) -> QIcon:
    """Return a Windows Fluent line icon with consistent normal/hover states."""
    if name == "views":
        # 几何视图图标不依赖系统图标字体，与标题栏相同的颜色和笔画在所有机器上稳定显示。
        icon = QIcon()
        tokens = current_theme()
        for mode, color in ((QIcon.Mode.Normal, tokens.subtext), (QIcon.Mode.Active, tokens.accent_hover)):
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(color), 1.3))
            cell = (size - 7) / 2
            for x in (2, size / 2 + 1.5):
                for y in (2, size / 2 + 1.5):
                    painter.drawRoundedRect(QRectF(x, y, cell, cell), 1, 1)
            painter.end()
            icon.addPixmap(pixmap, mode)
        return icon
    family = _fluent_icon_family()
    glyph = _FLUENT_GLYPHS.get(name)
    if family is None or glyph is None:
        return QIcon()

    tokens = current_theme()
    active = active_color or tokens.accent_hover
    icon = QIcon()
    for mode, color in (
        (QIcon.Mode.Normal, tokens.subtext),
        (QIcon.Mode.Active, active),
        (QIcon.Mode.Selected, active),
        (QIcon.Mode.Disabled, tokens.muted),
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
    pen = QPen(QColor(current_theme().accent_hover))
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
    """Load the shared TokenMeter icon used by windows, tray, and branding."""
    icon_path = Path(__file__).resolve().parents[1] / "assets" / "TokenMeter.ico"
    icon = QIcon(str(icon_path))
    if not icon.isNull():
        return icon

    # Keep startup resilient in partial source checkouts or malformed packages;
    # release builds include the ICO explicitly through packaging/pyinstaller/TokenMeter.spec.
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
