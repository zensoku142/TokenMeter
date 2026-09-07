"""Complete subscription quota details without adding requests or copying credentials."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from api.providers import PROVIDERS
from data.store import TokenData
from ui.formatting import format_quota_metric, format_reset_countdown, quota_used_percent
from ui.i18n import bind_text, tr
from ui.provider_branding import provider_icon
from ui.provider_overview import provider_status_message
from ui.qt_theme import ThemeTokens, current_theme, theme_controller

_SOURCE_NAMES = {
    "interface": "接口数据", "local_snapshot": "本机快照", "cache": "缓存数据",
    "mixed": "接口 + 今日本机估算", "cache_mixed": "缓存 + 今日本机估算",
    "local": "今日本机估算",
}


class QuotaDetailsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("quotaDetailsDialog")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        bind_text(self, "全部额度", method="setWindowTitle")
        self.setMinimumSize(380, 300)
        self.resize(560, 590)
        self.provider_id = ""
        self.account_key = ""
        self._data: TokenData | None = None
        self._rows_signature: tuple[object, ...] | None = None
        self._dynamic_details: list[tuple[QLabel, Callable[[], str]]] = []
        self._theme_tokens: ThemeTokens | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        header = QHBoxLayout()
        self.brand = QLabel()
        self.brand.setFixedSize(30, 30)
        self.title = QLabel()
        self.title.setObjectName("quotaDetailsTitle")
        self.title.setWordWrap(True)
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        header.addWidget(self.brand)
        header.addWidget(self.title, 1)
        layout.addLayout(header)
        self.updated = QLabel()
        self.updated.setWordWrap(True)
        self.updated.setObjectName("quotaDetailsUpdated")
        layout.addWidget(self.updated)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setObjectName("quotaDetailsNotice")
        layout.addWidget(self.notice)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.scroll_area, 1)
        footer = QHBoxLayout()
        self.dashboard_button = bind_text(QPushButton(), "打开官方用量页面")
        self.dashboard_button.clicked.connect(self._open_dashboard)
        footer.addWidget(self.dashboard_button)
        footer.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        # 英文等较长按钮文案仍需保留主题内边距，防止运行中换语言后裁切 Close。
        buttons.button(QDialogButtonBox.StandardButton.Close).setMinimumWidth(92)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)
        self.rows: list[QFrame] = []
        self.progress_bars: list[QProgressBar] = []
        theme_controller().changed.connect(self.refresh_theme)
        self.refresh_theme()

    def _open_dashboard(self) -> None:
        provider = PROVIDERS.get(self.provider_id)
        if provider and provider.dashboard_url:
            from PySide6.QtCore import QUrl

            QDesktopServices.openUrl(QUrl(provider.dashboard_url))

    def _new_card(
        self, parent_layout: QVBoxLayout, title: str,
        value: str | Callable[[], str], detail: str | Callable[[], str],
    ) -> QVBoxLayout:
        card = QFrame()
        card.setObjectName("quotaDetailCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(5)
        heading = QHBoxLayout()
        name = bind_text(QLabel(), title)
        # 标题/明细来自平台；按纯文本展示，不能把上游字符串解释成富文本或图片。
        name.setTextFormat(Qt.TextFormat.PlainText)
        name.setWordWrap(True)
        name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        name.setMinimumWidth(0)
        # 连续模型 ID/额度文本不能撑宽滚动区；忽略文本固有宽度后交由换行计算高度。
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        label = bind_text(QLabel(), value)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setObjectName("quotaDetailValue")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        heading.addWidget(name, 1)
        heading.addWidget(label, 1)
        layout.addLayout(heading)
        if detail:
            text = bind_text(QLabel(), detail)
            text.setTextFormat(Qt.TextFormat.PlainText)
            text.setObjectName("quotaDetailDescription")
            text.setWordWrap(True)
            text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(text)
            if callable(detail):
                self._dynamic_details.append((text, detail))
        parent_layout.addWidget(card)
        self.rows.append(card)
        return layout

    def set_data(self, data: TokenData) -> None:
        provider_id = data.per_provider[0].provider_id if data.per_provider else ""
        same_account = (self.provider_id, self.account_key) == (provider_id, data.account_key)
        scroll_position = self.scroll_area.verticalScrollBar().value() if same_account else 0
        self._data = data
        self.provider_id = provider_id
        self.account_key = data.account_key
        provider = PROVIDERS.get(self.provider_id)
        name = data.per_provider[0].provider_name if data.per_provider else ""
        def title() -> str:
            return f"{tr(name)} · {tr('全部额度')}" if name else tr("全部额度")

        bind_text(self.title, title)
        bind_text(self, title, method="setWindowTitle")
        self.dashboard_button.setVisible(bool(provider and provider.dashboard_url))
        updated = data.last_success_at.strftime("%Y-%m-%d %H:%M:%S") if data.last_success_at else ""
        source = _SOURCE_NAMES.get(data.quota_source, "暂无数据")
        bind_text(self.updated, lambda: (
            tr("最后成功更新：{timestamp}", timestamp=updated) + " · " + tr(source)
            if updated else tr("等待首次更新") + " · " + tr(source)
        ))
        has_quota = bool(data.quota_windows or data.quota_metrics or data.quota_statistics)
        message = (
            "当前显示缓存额度，最新状态请刷新或查看官方页面"
            if data.quota_source == "cache" and has_quota
            else "部分数据未能更新，请留意下方数据来源" if (data.errors or data.is_stale) and has_quota
            else "暂无可用额度，请检查账户配置或查看官方页面" if not has_quota
            else ""
        )
        # 与总览共用恢复文案；错误码优先，避免失效凭据只提示用户不断刷新。
        if data.errors or data.refresh_error_codes:
            message = provider_status_message(data)
        bind_text(self.notice, message)
        self.notice.setVisible(bool(message))
        # TokenData/列表会原地更新；复制冻结窗口和指标的元组，才能准确识别行内容与账号变化。
        signature = (
            provider_id, data.account_key,
            # Python 的 True/False 等于 1/0，但布尔百分比无效，不能复用之前的合法进度条。
            tuple((window, isinstance(window.used_percent, bool)) for window in data.quota_windows),
            tuple(data.quota_metrics),
            data.account_plan, data.account_label, tuple(data.quota_statistics), data.statistics_source,
            data.quota_forecast_enabled, tuple(data.quota_forecasts.items()),
            bool(data.is_stale or data.errors or data.refresh_error_codes or data.quota_source not in {"interface", "local_snapshot"}) if data.quota_forecast_enabled else False,
        )
        if signature == self._rows_signature:
            # 未变额度保留滚动位置和文字选择，但倒计时仍随本轮刷新重新计算。
            for label, detail_source in self._dynamic_details:
                bind_text(label, detail_source)
            self.refresh_theme()
            return
        # 每次替换的是完整展示快照；删除旧行可避免刷新后混入上一账号或上一周期的窗口。
        old = self.scroll_area.takeWidget()
        if old is not None:
            old.deleteLater()
        self.rows = []
        self.progress_bars = []
        self._dynamic_details = []
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 4, 0)
        content_layout.setSpacing(8)
        for window in data.quota_windows:
            used = quota_used_percent(window.used_percent)
            value = f"已用 {used:g}%" if used is not None else "--"
            # 保留原始窗口作动态绑定；切换语言时重算每段文字，避免拼接后的半翻译状态。
            def detail(current=window) -> str:
                forecast = ""
                if data.quota_forecast_enabled and not data.is_stale and not data.errors and not data.refresh_error_codes and data.quota_source in {"interface", "local_snapshot"}:
                    seconds = data.quota_forecasts.get(current.id)
                    forecast = (tr("近期无明显消耗") if seconds == 0 else
                                tr("按近期速度估计还可持续使用约 {minutes} 分钟，实际消耗会变化", minutes=max(1, round(seconds / 60))) if seconds is not None else
                                tr("有效样本不足，暂无法估计"))
                return " · ".join(filter(None, (
                    tr(current.detail),
                    tr(format_reset_countdown(current.resets_at)) if current.resets_at
                    else tr("平台未提供重置时间"),
                    forecast,
                )))
            row = self._new_card(content_layout, window.title, value, detail)
            if used is not None:
                progress = QProgressBar()
                progress.setRange(0, 1000)
                progress.setValue(round(max(0, min(100, used)) * 10))
                progress.setTextVisible(False)
                progress.setFixedHeight(6)
                progress.setProperty("quotaLevel", "low" if used >= 90 else "normal")
                bind_text(progress, window.title, method="setAccessibleName")
                bind_text(progress, value, method="setAccessibleDescription")
                row.addWidget(progress)
                self.progress_bars.append(progress)
        for metric in data.quota_metrics:
            self._new_card(
                content_layout, metric.title,
                lambda current=metric: format_quota_metric(current), metric.detail,
            )
        if data.account_plan:
            self._new_card(content_layout, "订阅套餐", data.account_plan, data.account_label)
        for metric in data.quota_statistics:
            # 统计接口可单独回退缓存，不能沿用顶部额度来源而误标为最新数据。
            def statistic_detail(current=metric, source=data.statistics_source) -> str:
                return " · ".join(filter(None, (
                    tr(current.detail), tr(_SOURCE_NAMES.get(source, "暂无数据")),
                )))

            self._new_card(
                content_layout, metric.title,
                lambda current=metric: format_quota_metric(current), statistic_detail,
            )
        content_layout.addStretch()
        self.scroll_area.setWidget(content)
        # 同账号自动刷新不能把正在阅读的长列表跳回顶部；切换账号时从新列表开头展示。
        self.scroll_area.verticalScrollBar().setValue(scroll_position)
        self._rows_signature = signature
        self.refresh_theme()

    def refresh_theme(self, *_args) -> None:
        tokens = current_theme()
        if self.provider_id:
            icon = provider_icon(self.provider_id, 30)
            self.brand.setPixmap(icon.pixmap(QSize(30, 30)))
            self.setWindowIcon(icon)
        else:
            self.brand.clear()
            self.setWindowIcon(QIcon())
        # 重装同一 QSS 会同步重算所有额度行；仅主题变化时重设，新增行会自动继承已有样式。
        if tokens == self._theme_tokens:
            return
        self.setStyleSheet(f"""
QDialog#quotaDetailsDialog {{ background: {tokens.window}; }}
QLabel {{ color: {tokens.text}; }}
QLabel#quotaDetailsTitle {{ font-size: 17px; font-weight: 600; }}
QLabel#quotaDetailsUpdated, QLabel#quotaDetailDescription {{ color: {tokens.subtext}; }}
QLabel#quotaDetailsNotice {{ color: {tokens.warning}; background: {tokens.accent_soft}; border-radius: 6px; padding: 8px; }}
QFrame#quotaDetailCard {{ background: {tokens.surface}; border: 1px solid {tokens.border}; border-radius: 9px; }}
QLabel#quotaDetailValue {{ font-weight: 600; color: {tokens.value}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
QProgressBar {{ border: 0; border-radius: 3px; background: {tokens.accent_soft}; }}
QProgressBar::chunk {{ border-radius: 3px; background: {tokens.accent}; }}
QProgressBar[quotaLevel="low"]::chunk {{ background: {tokens.warning}; }}
""")
        self._theme_tokens = tokens
