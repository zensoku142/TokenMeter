import ast
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from ui import provider_branding
from ui.provider_branding import (
    provider_icon,
    provider_search_terms,
    provider_short_name,
)
from ui.qt_theme import DARK_THEME, LIGHT_THEME

APP = QApplication.instance() or QApplication([])
ROOT = Path(__file__).resolve().parents[1]


def _opaque_colors(image):
    return {
        image.pixelColor(x, y).name()
        for x in range(image.width())
        for y in range(image.height())
        if image.pixelColor(x, y).alpha() == 255
    }


@pytest.mark.parametrize("theme", [LIGHT_THEME, DARK_THEME], ids=["light", "dark"])
def test_every_registered_brand_renders_visible_pixels_in_both_themes(monkeypatch, theme):
    monkeypatch.setattr(provider_branding, "current_theme", lambda: theme)
    for provider_id in provider_branding._PROVIDER_BRANDS:
        icon = provider_icon(provider_id, 32)
        image = icon.pixmap(32, 32).toImage()
        assert not icon.isNull(), provider_id
        assert _opaque_colors(image), provider_id


def test_monochrome_marks_follow_theme_and_color_marks_keep_brand_color(monkeypatch):
    for theme in (LIGHT_THEME, DARK_THEME):
        monkeypatch.setattr(provider_branding, "current_theme", lambda: theme)
        monochrome = provider_icon("cursor", 32).pixmap(32, 32).toImage()
        deepseek = provider_icon("deepseek", 32).pixmap(32, 32).toImage()
        assert _opaque_colors(monochrome) == {QColor(theme.text).name()}
        assert _opaque_colors(deepseek) == {"#4d6bfe"}


def test_provider_icons_include_high_density_rasters():
    icon = provider_icon("claude", 24)
    for ratio in (1.0, 1.5, 2.0, 3.0):
        pixmap = icon.pixmap(QSize(24, 24), ratio)
        assert pixmap.size() == QSize(round(24 * ratio), round(24 * ratio))
        assert pixmap.devicePixelRatio() == ratio


def test_unknown_provider_uses_the_same_explicit_generic_mark_as_nayuto():
    known = provider_icon("nayuto", 32).pixmap(32, 32).toImage()
    unknown = provider_icon("unknown-service", 32).pixmap(32, 32).toImage()
    assert known == unknown
    assert _opaque_colors(unknown)
    assert provider_short_name("unknown-service") == "unknown-service"
    assert provider_search_terms("unknown-service") == "unknown-service"


@pytest.mark.parametrize("corrupt", [False, True], ids=["missing", "corrupt"])
def test_missing_or_corrupt_packaged_mark_keeps_picker_usable(monkeypatch, tmp_path, corrupt):
    monkeypatch.setattr(provider_branding, "_ASSET_DIR", tmp_path)
    if corrupt:
        (tmp_path / "cursor.svg").write_text("not an SVG", encoding="utf-8")
    icon = provider_icon("cursor", 32)
    assert not icon.isNull()
    assert _opaque_colors(icon.pixmap(32, 32).toImage())


def test_product_names_and_search_aliases_distinguish_kimi_api_from_coding():
    assert provider_short_name("moonshot") == "Kimi API"
    assert provider_short_name("kimi") == "Kimi Coding"
    assert provider_short_name(" COPILOT ") == "Copilot"
    assert provider_short_name("zai") == "GLM / Z.ai"
    assert "月之暗面" in provider_search_terms("moonshot")
    assert "智谱" in provider_search_terms("zai")
    assert "Google" in provider_search_terms("gemini")
    assert "语音" in provider_search_terms("elevenlabs")


def test_distribution_contains_brand_assets_and_upstream_license():
    spec = ast.parse((ROOT / "packaging/pyinstaller/TokenMeter.spec").read_text(encoding="utf-8"))
    analysis = next(
        node for node in ast.walk(spec)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "Analysis"
    )
    datas = next(keyword.value for keyword in analysis.keywords if keyword.arg == "datas")
    assert ("../../assets/providers", "assets/providers") in ast.literal_eval(datas)
    asset_root = ROOT / "assets/providers"
    license_text = (asset_root / "LICENSE.lobe-icons").read_text(encoding="utf-8")
    notes = (asset_root / "README.md").read_text(encoding="utf-8")
    assert "Copyright (c) 2023 LobeHub" in license_text
    assert "MIT License" in license_text
    for filename, _label, _aliases in provider_branding._PROVIDER_BRANDS.values():
        assert (asset_root / filename).is_file()
        assert filename in notes
