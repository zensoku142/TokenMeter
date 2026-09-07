"""On-demand multi-provider overview using existing collection snapshots."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from api.providers import PROVIDERS
from data.store import TokenData
from ui.formatting import format_money, format_reset_countdown, quota_used_percent
from ui.i18n import bind_text, tr
from ui.provider_branding import provider_icon
from ui.qt_theme import current_theme, theme_controller


def provider_status_message(data: TokenData | None) -> str:
    """Use error codes, never raw responses, for a safe recovery explanation."""
    if data is None:
        return "尚未采集，点击刷新总览"
    codes = set(data.refresh_error_codes) | {error.code for error in data.errors}
    if "AUTH_EXPIRED" in codes:
        return "登录已失效，请重新连接；下方仅为上次记录"
    if "NOT_CONFIGURED" in codes:
        return "请检查账户连接配置"
    if "RATE_LIMITED" in codes:
        return "接口限流，自动采集将在退避结束后重试"
    if data.is_stale or data.quota_source.startswith("cache"):
        return "当前显示上次记录，请留意成功更新时间"
    if data.errors or data.refresh_error_codes or data.status in {"partial", "error"}:
        return "部分数据未能更新，请留意下方数据来源"
    if data.last_success_at is None:
        return "等待首次更新"
    return ""


class ProviderOverview(QWidget):
    back_requested = Signal()
    refresh_requested = Signal()
    provider_selected = Signal(str)
    connection_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("providerOverview")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        toolbar = QHBoxLayout()
        self.back_button = bind_text(QPushButton(), "返回面板")
        self.back_button.clicked.connect(self.back_requested)
        toolbar.addWidget(self.back_button)
        toolbar.addWidget(bind_text(QLabel(), "平台总览"), 1)
        self.refresh_button = bind_text(QPushButton(), "刷新总览")
        self.refresh_button.clicked.connect(self.refresh_requested)
        toolbar.addWidget(self.refresh_button)
        self.manage_button = bind_text(QPushButton(), "账户连接")
        self.manage_button.clicked.connect(lambda: self.connection_requested.emit(""))
        toolbar.addWidget(self.manage_button)
        layout.addLayout(toolbar)
        self.hint = bind_text(QLabel(), "仅展示本机已配置平台；各平台额度和币种分别计算")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.cards_layout = QVBoxLayout(self.content)
        self.cards_layout.setContentsMargins(0, 0, 6, 0)
        self.cards_layout.setSpacing(8)
        self.empty = bind_text(QLabel(), "尚未配置平台，请打开账户连接")
        self.empty.setWordWrap(True)
        self.cards_layout.addWidget(self.empty)
        self.cards_layout.addStretch()
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)
        self.cards: dict[str, QFrame] = {}
        theme_controller().changed.connect(self.refresh_theme)
        self.refresh_theme()

    def set_refreshing(self, refreshing: bool) -> None:
        self.refresh_button.setEnabled(not refreshing)
        bind_text(self.refresh_button, "正在刷新" if refreshing else "刷新总览")

    def set_data(self, snapshots: dict[str, TokenData | None]) -> None:
        # 保留已有卡片和滚动位置；刷新不得让用户正在阅读的行跳到顶部。
        for provider_id in set(self.cards) - snapshots.keys():
            card = self.cards.pop(provider_id)
            self.cards_layout.removeWidget(card)
            card.deleteLater()
        self.empty.setVisible(not snapshots)
        for index, (provider_id, data) in enumerate(snapshots.items()):
            if provider_id not in self.cards:
                card = QFrame()
                card.setObjectName("overviewCard")
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(14, 10, 14, 10)
                heading = QHBoxLayout()
                brand = QLabel()
                brand.setFixedSize(24, 24)
                heading.addWidget(brand)
                name = self._label()
                bind_text(name, PROVIDERS[provider_id].name)
                heading.addWidget(name, 1)
                details = bind_text(QPushButton(), "详情")
                details.clicked.connect(lambda _checked=False, pid=provider_id: self.provider_selected.emit(pid))
                heading.addWidget(details)
                connection = bind_text(QPushButton(), "账户连接")
                connection.clicked.connect(lambda _checked=False, pid=provider_id: self.connection_requested.emit(pid))
                heading.addWidget(connection)
                card_layout.addLayout(heading)
                for _ in range(3):
                    card_layout.addWidget(self._label())
                self.cards[provider_id] = card
                self.cards_layout.insertWidget(index + 1, card)
            card = self.cards[provider_id]
            card_layout = card.layout()
            # 复用只读快照供语言切换重算摘要，不复制其中的完整历史记录。
            summary, status, timestamp = (card_layout.itemAt(i).widget() for i in range(1, 4))
            bind_text(summary, lambda pid=provider_id, value=data: self._summary(pid, value))
            bind_text(status, provider_status_message(data))
            updated = data.last_success_at.strftime("%Y-%m-%d %H:%M:%S") if data and data.last_success_at else ""
            source = ("缓存数据" if data.quota_source.startswith("cache") else
                      "本机快照" if data.quota_source == "local_snapshot" else "接口数据") if data else ""
            bind_text(timestamp, lambda value=updated, origin=source: (
                tr("最后成功更新：{timestamp}", timestamp=value) + " · " + tr(origin) if value else tr("等待首次更新")
            ))
        self.refresh_theme()

    @staticmethod
    def _label() -> QLabel:
        label = QLabel()
        # 平台名称和窗口标题来自接口，禁止 Qt 自动解释为 HTML。
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        return label

    @staticmethod
    def _summary(provider_id: str, data: TokenData | None) -> str:
        if data is None or data.last_success_at is None:
            return "--"
        provider = PROVIDERS[provider_id]
        if provider.supports_subscription_quota:
            # 最紧张的窗口先展示，不能只取接口前两项而漏掉受限模型。
            windows = sorted(data.quota_windows, key=lambda w: quota_used_percent(w.used_percent) or 0, reverse=True)
            rows = []
            for window in windows[:2]:
                used = quota_used_percent(window.used_percent)
                remaining = tr("剩余 {remaining}%", remaining=f"{max(0, 100 - used):g}") if used is not None else "--"
                reset = tr(format_reset_countdown(window.resets_at)) if window.resets_at else tr("平台未提供重置时间")
                rows.append(f"{tr(window.title)} · {remaining} · {reset}")
            if len(windows) > 2:
                rows.append(tr("还有 {count} 个额度窗口，请查看详情", count=len(windows) - 2))
            return "\n".join(rows) or tr("暂无可用额度，请查看详情")
        return "\n".join((
            f"{tr(provider.balance_label)} · {format_money(data.balance_cny, data.currency)}",
            f"{tr('今日使用金额')} · {format_money(data.today_cost_cny, data.currency)}",
        ))

    def refresh_theme(self, *_args) -> None:
        tokens = current_theme()
        self.setStyleSheet(f"""
            QWidget#providerOverview {{ background: {tokens.surface}; color: {tokens.text}; }}
            QFrame#overviewCard {{ background: {tokens.elevated}; border: 1px solid {tokens.border}; border-radius: 8px; }}
            QFrame#overviewCard QLabel {{ background: transparent; color: {tokens.text}; border: none; }}
        """)
        for provider_id, card in self.cards.items():
            card.layout().itemAt(0).layout().itemAt(0).widget().setPixmap(provider_icon(provider_id, 24).pixmap(24, 24))
