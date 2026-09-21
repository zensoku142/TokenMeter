"""Display-only translations and small, state-preserving Qt text bindings."""

from __future__ import annotations

import re
import weakref
from collections.abc import Callable
from string import Formatter

from PySide6.QtCore import (
    QEvent,
    QLibraryInfo,
    QLocale,
    QObject,
    QSignalBlocker,
    QTranslator,
    Signal,
)
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from ui.translations import MESSAGES

LANGUAGES = (
    ("system", "跟随系统"),
    ("zh-cn", "简体中文"),
    ("zh-tw", "繁體中文"),
    ("en", "English"),
    ("ja", "日本語"),
    ("ko", "한국어"),
)
_LANGUAGE_INDEX = {"en": 0, "zh-tw": 1, "ja": 2, "ko": 3}
_BOUND: weakref.WeakSet = weakref.WeakSet()
_controller: LanguageController | None = None
_formatter = Formatter()
_patterns: list[tuple[re.Pattern, str]] = []


def resolve_language(preference: str, ui_languages: list[str] | None = None) -> str:
    preference = str(preference).strip().lower()
    if preference in _LANGUAGE_INDEX or preference == "zh-cn":
        return preference
    for candidate in QLocale.system().uiLanguages() if ui_languages is None else ui_languages:
        parts = candidate.replace("_", "-").lower().split("-")
        if parts[0] == "zh":
            # 脚本优先于地区；香港、澳门和台湾默认繁体，其余中文使用简体。
            traditional = "hant" in parts or (
                "hans" not in parts and bool({"tw", "hk", "mo"}.intersection(parts))
            )
            return "zh-tw" if traditional else "zh-cn"
        if parts[0] in {"en", "ja", "ko"}:
            return parts[0]
    return "en"


def current_language() -> str:
    # 独立控件及旧调用方未经过应用启动时保持原来的中文显示。
    return _controller.resolved if _controller is not None else "zh-cn"


def ui_locale() -> QLocale:
    return QLocale({"zh-cn": "zh_CN", "zh-tw": "zh_TW"}.get(current_language(), current_language()))


def _catalog_text(source: str, language: str) -> str:
    row = MESSAGES.get(source)
    return row[_LANGUAGE_INDEX[language]] if row else source


def startup_running_message(app_name: str, preference: str) -> str:
    language = resolve_language(preference)
    source = "{app} 已在运行。"
    return (source if language == "zh-cn" else _catalog_text(source, language)).format(app=app_name)


def _build_patterns() -> None:
    if _patterns:
        return
    for source in MESSAGES:
        if "{" not in source:
            continue
        parts = []
        for literal, field, _spec, _conversion in _formatter.parse(source):
            parts.append(re.escape(literal))
            if field is not None:
                # 日期必须是数字，不能把前面的“剩余 75% ·”误吞进月份再重排整句。
                value_pattern = (
                    r"\d+"
                    if field in {"month", "day", "year", "hours", "minutes", "seconds", "n"}
                    else r".+?"
                )
                parts.append(f"(?P<{field}>{value_pattern})")
        _patterns.append((re.compile("".join(parts), re.DOTALL), source))
    # 先匹配带有更多固定文字的完整句子，避免短模板吞掉更具体的提示。
    _patterns.sort(key=lambda item: len(re.sub(r"\{[^}]+\}", "", item[1])), reverse=True)


def tr(source: str, **values) -> str:
    """Translate application-owned source text; leave unknown external details intact."""
    language = current_language()
    source = str(source)
    if language == "zh-cn":
        return source.format(**values) if values else source
    if values:
        return _catalog_text(source, language).format(
            **{key: tr(value) if isinstance(value, str) else value for key, value in values.items()}
        )
    if source in MESSAGES:
        return _catalog_text(source, language)
    # Provider 和旧快照仍返回原中文文案；只匹配已登记的完整模板，不修改业务数据。
    if re.search(r"[\u4e00-\u9fff]", source):
        _build_patterns()
        for pattern, template in _patterns:
            match = pattern.fullmatch(source)
            if match:
                translated_values = {key: tr(value) for key, value in match.groupdict().items()}
                if language in {"en", "ko"} and "schedule" in translated_values:
                    translated_values["schedule"] = translated_values["schedule"].replace(
                        "、", ", "
                    )
                return _catalog_text(template, language).format(
                    **translated_values
                )
        if "、" in source:
            return ", ".join(tr(part) for part in source.split("、"))
        amount = re.fullmatch(r"(-?\d+(?:\.\d+)?)(万|亿)", source)
        if amount:
            number, unit = amount.groups()
            if language == "en":
                value = float(number) * (10_000 if unit == "万" else 100_000_000)
                for scale, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
                    if abs(value) >= scale:
                        return f"{value / scale:.2f}".rstrip("0").rstrip(".") + suffix
                return f"{value:g}"
            units = {"zh-tw": ("萬", "億"), "ja": ("万", "億"), "ko": ("만", "억")}
            return number + units[language][unit == "亿"]
        if "\n" in source:
            return "\n".join(tr(line) for line in source.split("\n"))
        if "；" in source:
            return "; ".join(tr(part) for part in source.split("；"))
        # 状态来源摘要和图表明细由固定标签拼接；只翻译这些标签，外部明细保持原文。
        label, separator, detail = source.partition("：")
        labels = label.split("/")
        if separator and all(item in MESSAGES for item in labels):
            return "/".join(tr(item) for item in labels) + ": " + tr(detail)
        label, separator, detail = source.partition("　")
        if separator and label.removeprefix("■ ") in MESSAGES:
            prefix = "■ " if label.startswith("■ ") else ""
            return prefix + tr(label.removeprefix("■ ")) + "  " + tr(detail)
    return source


def _display_text(source: str | Callable[[], str]) -> str:
    return source() if callable(source) else tr(source)


def bind_text(
    target, source: str | Callable[[], str], *, method: str = "setText", index: int | None = None
):
    """Remember source text so changing language never recreates a widget or its draft."""
    bindings = getattr(target, "_language_bindings", None)
    if bindings is None:
        bindings = target._language_bindings = {}
        if isinstance(target, QWidget):
            target.setLocale(ui_locale())
    bindings[method, index] = source if callable(source) else str(source)
    _BOUND.add(target)
    setter = getattr(target, method)
    if index is None:
        setter(_display_text(source))
    else:
        setter(index, _display_text(source))
    return target


def add_item(combo, source: str, data=None) -> None:
    combo.addItem("", data)
    bind_text(combo, source, method="setItemText", index=combo.count() - 1)


def add_tab(tabs, widget, source: str) -> None:
    index = tabs.addTab(widget, "")
    bind_text(tabs, source, method="setTabText", index=index)


class LanguageController(QObject):
    changed = Signal(str, str)

    def __init__(self, app, preference: str):
        super().__init__(app)
        self.app = app
        self.preference = "system"
        self.resolved = "zh-cn"
        self._translator: QTranslator | None = None
        self._changing = False
        app.installEventFilter(self)
        self.set_language(preference)

    def set_language(self, preference: str) -> None:
        if preference not in dict(LANGUAGES):
            raise ValueError("Unsupported UI language")
        resolved = resolve_language(preference)
        # 切换前先加载资源；后续换语言和刷新文字只在 GUI 线程中完成。
        translator = QTranslator(self)
        name = {"zh-cn": "zh_CN", "zh-tw": "zh_TW"}.get(resolved, resolved)
        translator.load(
            f"qtbase_{name}", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        )
        self._changing = True
        try:
            self.preference, self.resolved = preference, resolved
            if self._translator is not None:
                self.app.removeTranslator(self._translator)
                self._translator.deleteLater()
            self._translator = translator
            self.app.installTranslator(translator)
            for target in list(_BOUND):
                if not isValid(target):
                    _BOUND.discard(target)
                    continue
                # 下拉框文字和单位变化不能触发自动保存或改变当前选项。
                blocker = QSignalBlocker(target)
                if isinstance(target, QWidget):
                    target.setLocale(ui_locale())
                for (method, index), source in target._language_bindings.items():
                    setter = getattr(target, method)
                    if index is None:
                        setter(_display_text(source))
                    else:
                        setter(index, _display_text(source))
                del blocker
            # 自绘球、热力图及坐标轴没有文本属性，重新绘制但不重建数据或视图范围。
            for widget in QApplication.allWidgets():
                if widget.__class__.__name__ == "MainPanel":
                    widget.refresh_language_layout()
                if widget.__class__.__name__ in {"TokenActivityHeatmap", "FloatingUsageBall"}:
                    widget.update()
                if widget.__class__.__name__ == "PlotWidget":
                    for axis in widget.getPlotItem().axes.values():
                        refresh = getattr(axis["item"], "refresh_language", None)
                        if refresh is not None:
                            refresh()
                        axis["item"].picture = None
                        axis["item"].update()
            self.changed.emit(preference, resolved)
        finally:
            self._changing = False

    def eventFilter(self, watched, event) -> bool:
        if (
            event.type() == QEvent.Type.LocaleChange
            and self.preference == "system"
            and not self._changing
        ):
            if resolve_language("system") != self.resolved:
                self.set_language("system")
        return False


def configure_language(app, preference: str = "system") -> LanguageController:
    global _controller
    if preference not in dict(LANGUAGES):
        preference = "system"
    if _controller is None or not isValid(_controller) or _controller.app is not app:
        # 先注册实例再应用，确保 tr() 在首次刷新时读取的是新语言。
        _controller = LanguageController(app, "zh-cn")
    _controller.set_language(preference)
    return _controller


def language_controller() -> LanguageController | None:
    return _controller
