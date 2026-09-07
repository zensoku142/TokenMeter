"""Offline provider marks and compact labels used by the provider picker."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ui.qt_theme import current_theme, metric_icon

_ASSET_DIR = Path(__file__).resolve().parents[1] / "assets" / "providers"
_PROVIDER_BRANDS = {
    "deepseek": ("deepseek-color.svg", "DeepSeek", "深度求索 深度搜索"),
    "mimo": ("xiaomimimo.svg", "MiMo", "Xiaomi MiMo 小米 米模"),
    "codex": ("openai.svg", "Codex", "OpenAI ChatGPT GPT coding 编程"),
    "cursor": ("cursor.svg", "Cursor", "Anysphere IDE 编辑器"),
    "nayuto": ("generic.svg", "NayutoAI", "Nayuto AI 中转 聚合"),
    "openrouter": ("openrouter.svg", "OpenRouter", "Open Router 聚合 路由"),
    "moonshot": ("kimi.svg", "Kimi API", "Moonshot 月之暗面 月亮 暗面 开放平台"),
    "copilot": ("githubcopilot.svg", "Copilot", "GitHub Copilot Github 编程"),
    "claude": ("claude-color.svg", "Claude", "Anthropic Claude Code 克劳德 编程"),
    "zai": ("zai.svg", "GLM / Z.ai", "Zhipu 智谱 智谱清言 GLM Coding Plan"),
    "gemini": ("gemini-color.svg", "Gemini CLI", "Google Code Assist 谷歌 双子座"),
    "kimi": ("kimi.svg", "Kimi Coding", "Moonshot 月之暗面 编程 会员 订阅"),
    "minimax": ("minimax-color.svg", "MiniMax", "Mini Max 稀宇 海螺 Token Plan Coding"),
    "elevenlabs": ("elevenlabs.svg", "ElevenLabs", "Eleven Labs 语音 配音 TTS"),
    "alibaba": ("bailian-color.svg", "Alibaba", "Alibaba Cloud Bailian 阿里云 百炼 通义 千问"),
    "openai": ("openai.svg", "OpenAI API", "ChatGPT Open AI 开放平台"),
}


def provider_short_name(provider_id: str) -> str:
    """Return a compact product label, retaining an unknown provider's ID."""
    key = provider_id.strip().casefold()
    brand = _PROVIDER_BRANDS.get(key)
    return brand[1] if brand else provider_id.strip()


def provider_search_terms(provider_id: str) -> str:
    """Include the compact name, stable ID, and common Chinese/English aliases."""
    key = provider_id.strip().casefold()
    brand = _PROVIDER_BRANDS.get(key)
    return f"{key} {brand[1]} {brand[2]}" if brand else provider_id.strip()


def provider_icon(provider_id: str, size: int = 24) -> QIcon:
    """Return a local brand icon; call again after changing the application theme."""
    brand = _PROVIDER_BRANDS.get(provider_id.strip().casefold())
    filename = brand[0] if brand else "generic.svg"
    # 与窗口图标共用源码/PyInstaller 的相对资源布局；主题颜色进入缓存键，避免切换后仍显示旧色。
    return QIcon(_render_icon(str(_ASSET_DIR), filename, max(1, size), current_theme().text))


@lru_cache(maxsize=192)
def _render_icon(directory: str, filename: str, size: int, foreground: str) -> QIcon:
    try:
        data = (Path(directory) / filename).read_bytes()
    except OSError:
        # 部分检出或资源损坏时仍给出通用用量图标，避免供应商选择器无法打开。
        return QIcon(metric_icon("usage", size))

    # 只替换上游显式声明的单色 currentColor；彩色标记与渐变保留原始品牌色。
    renderer = QSvgRenderer(QByteArray(data.replace(b"currentColor", foreground.encode("ascii"))))
    if not renderer.isValid():
        return QIcon(metric_icon("usage", size))

    view_box = renderer.viewBoxF()
    icon = QIcon()
    # 缓存不同像素密度，避免在 Windows 150%/200% 缩放时放大低分辨率位图。
    for ratio in (1, 2, 3):
        pixels = size * ratio
        pixmap = QPixmap(pixels, pixels)
        pixmap.fill(Qt.GlobalColor.transparent)
        target = view_box.size().scaled(pixels, pixels, Qt.AspectRatioMode.KeepAspectRatio)
        bounds = QRectF((pixels - target.width()) / 2, (pixels - target.height()) / 2,
                        target.width(), target.height())
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter, bounds)
        painter.end()
        pixmap.setDevicePixelRatio(ratio)
        icon.addPixmap(pixmap)
    return icon
