"""OpenAI API 余额的局部界面契约；不连接真实账户或启动刷新线程。"""

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPainter
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from api.providers.base import FetchError, QuotaWindow
from config import runtime as config_manager
from config import state
from config.defaults import DEFAULT_CONFIG
from data.store import PerProviderData, TokenData
from ui.formatting import format_money, format_openai_balance
from ui.i18n import bind_text, configure_language
from ui.light_ring import LightRingWidget, ring_value
from ui.provider_overview import ProviderOverview, openai_balance_details, openai_status_message
from ui.qt_panel import ANNUAL_PANEL_HEIGHT, PANEL_MAX_WIDTH, PANEL_MIN_WIDTH, MainPanel
from ui.qt_theme import configure_theme

APP = QApplication.instance() or QApplication([])
SUCCESS_AT = datetime(2026, 11, 9, 10, 20, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolate_views(monkeypatch, tmp_path):
    values = {**DEFAULT_CONFIG, "ACTIVE_PROVIDER": "codex"}
    monkeypatch.setattr(config_manager, "_config", values)
    monkeypatch.setattr(config_manager, "load_config", lambda: values.copy())
    monkeypatch.setattr(config_manager, "PANEL_LAYOUT_PATH", tmp_path / "layout.json")
    monkeypatch.setattr(config_manager, "CONFIG_DIR", tmp_path)
    configure_language(APP, "zh-cn")
    configure_theme(APP, "light")


def snapshot(provider="openai", **values):
    return TokenData(**{
        "per_provider": [PerProviderData(provider, "OpenAI API" if provider == "openai" else provider)],
        "currency": "USD" if provider == "openai" else "CNY",
        "balance_cny": Decimal("24.8486408"),
        "last_success_at": SUCCESS_AT,
        "status": "ok",
        "account_key": "identity-a",
        "account_label": "组织 …1234" if provider == "openai" else "",
        **values,
    })


def codex_panel():
    panel = MainPanel()
    panel.update_data(snapshot("codex", quota_windows=[QuotaWindow("week", "周额度", 10)]))
    panel.resize(PANEL_MAX_WIDTH, ANNUAL_PANEL_HEIGHT)
    panel.show()
    APP.processEvents()
    return panel


@pytest.mark.parametrize("value,expected", [
    (None, "--"), (True, "--"), (Decimal("NaN"), "--"), (float("inf"), "--"),
    (Decimal("0"), "$0.00"), (Decimal("-0"), "$0.00"),
    (Decimal("0.001"), "<$0.01"), (Decimal("0.005"), "<$0.01"),
    (Decimal("-0.001"), ">-$0.01"),
    (Decimal("24.8486408"), "$24.85"), (Decimal("-2.5"), "$-2.50"),
])
def test_openai_formatter_is_local_and_always_has_two_decimals(value, expected):
    assert format_openai_balance(value) == expected
    assert format_money(Decimal("0.001"), "USD") == "$0.0010"
    assert format_money(Decimal("0.001")) == "¥0.0010"


@pytest.mark.parametrize("amount,expected", [(Decimal("0"), "$0.00"), (Decimal("24.8486408"), "$24.85")])
def test_footer_uses_independent_snapshot_without_changing_codex(amount, expected):
    panel = codex_panel()
    original = (panel.status_text.text(), panel.updated_text.text(), panel.today_card.value.text(),
                panel.balance_card.value.text(), panel.refresh_button.isEnabled(),
                panel.minimumSizeHint(), panel.size(), panel.trend)
    data = snapshot(balance_cny=amount)
    panel.set_openai_balance(data, connected=True)
    APP.processEvents()
    assert not panel.openai_balance_text.isHidden()
    assert panel.openai_balance_text.accessibleName() == "API 余额 " + expected
    assert panel.openai_balance_text.parentWidget().objectName() == "statusBar"
    assert panel._status_bar.layout().indexOf(panel.openai_balance_text) == -1
    assert original == (panel.status_text.text(), panel.updated_text.text(), panel.today_card.value.text(),
                        panel.balance_card.value.text(), panel.refresh_button.isEnabled(),
                        panel.minimumSizeHint(), panel.size(), panel.trend)
    assert "2026-11-09 10:20:30" in panel.openai_balance_text.toolTip()
    assert "组织 …1234" in panel.openai_balance_text.accessibleDescription()
    panel.set_openai_balance(data, connected=False)
    assert panel.openai_balance_text.isHidden()
    assert not panel.openai_balance_text.text()
    assert not panel.openai_balance_text.toolTip()
    assert not panel.openai_balance_text.accessibleDescription()
    assert panel._openai_balance_data is None
    panel.close()


def test_footer_missing_or_replaced_identity_never_reuses_previous_amount():
    panel = codex_panel()
    panel.set_openai_balance(snapshot(), connected=True)
    panel.set_openai_balance(None, connected=True, organization="组织 …5678")
    assert panel.openai_balance_text.accessibleName() == "API 余额 --"
    assert "组织 …5678" in panel.openai_balance_text.accessibleDescription()
    assert "1234" not in panel.openai_balance_text.toolTip()
    assert "2026-11-09" not in panel.openai_balance_text.toolTip()
    panel.set_openai_balance(snapshot(account_key="identity-b", balance_cny=99, last_success_at=None,
                                     account_label="组织 …9999", status="error"), connected=True)
    assert panel.openai_balance_text.accessibleName() == "API 余额 --"
    assert "$99" not in panel.openai_balance_text.toolTip()
    panel.close()


@pytest.mark.parametrize("code,reason", [
    ("AUTH_EXPIRED", "重新连接"), ("FORBIDDEN", "权限不足"), ("RATE_LIMITED", "限流"),
    ("NETWORK_TIMEOUT", "超时"), ("NETWORK_ERROR", "网络连接失败"),
    ("SERVER_ERROR", "服务暂时异常"), ("INVALID_RESPONSE", "余额数据异常"),
    ("UNRECOGNIZED", "余额读取失败"),
])
def test_stale_footer_keeps_success_time_and_only_exposes_safe_error(code, reason):
    panel = codex_panel()
    data = snapshot(is_stale=True, status="error", account_label="org-private-value-1234 <img src=x>",
                    last_attempt_at=SUCCESS_AT + timedelta(hours=2),
                    errors=[FetchError(code, "Authorization: Bearer hidden-auth", "secret-response")])
    panel.set_openai_balance(data, connected=True)
    text = panel.openai_balance_text.accessibleDescription()
    tooltip = panel.openai_balance_text.toolTip()
    assert panel.openai_balance_text.accessibleName() == "API 余额 $24.85 · 未更新"
    assert reason in text and "未更新" in text
    assert "10:20:30" in text and "12:20:30" not in text
    assert "org-…1234" in text
    for secret in ("org-private-value-1234", "hidden-auth", "secret-response"):
        assert secret not in text + tooltip
    assert "<img" not in tooltip and "&lt;img" in tooltip
    panel.close()


def test_footer_hides_first_when_narrow_and_restores_without_relayout():
    panel = codex_panel()
    panel.set_openai_balance(snapshot(), connected=True)
    label = panel.openai_balance_text
    bind_text(panel.updated_text, "额度更新于刚刚")
    # 构造在 640px 放不下、820px 可放下的原状态行，避免依赖本机字体的固定字宽。
    target = PANEL_MIN_WIDTH - label.sizeHint().width() - panel.updated_text.sizeHint().width() - 40
    if panel.status_text.fontMetrics().horizontalAdvance(" · 接口") <= 0:
        pytest.skip("无头环境未加载可测量字体")
    source = "额度来源"
    while panel.status_text.fontMetrics().horizontalAdvance(source) < target:
        source += " · 接口"
    bind_text(panel.status_text, source)
    panel.resize(PANEL_MIN_WIDTH, panel.height())
    APP.processEvents()
    assert label.isHidden()
    original = (panel.status_text.geometry(), panel.updated_text.geometry(), panel.minimumSizeHint())
    panel.set_openai_balance(None, connected=False)
    APP.processEvents()
    assert original == (panel.status_text.geometry(), panel.updated_text.geometry(), panel.minimumSizeHint())
    panel.set_openai_balance(snapshot(), connected=True)
    panel.resize(PANEL_MAX_WIDTH, panel.height())
    APP.processEvents()
    assert not label.isHidden()
    assert panel.status_text.geometry().right() < label.geometry().left()
    assert label.geometry().right() < panel.updated_text.geometry().left()
    panel.close()


@pytest.mark.parametrize("provider", ["openai", "deepseek", "claude"])
def test_auxiliary_footer_is_only_visible_on_codex(provider):
    panel = codex_panel()
    panel.set_openai_balance(snapshot(), connected=True)
    panel.update_data(snapshot(provider))
    assert panel.openai_balance_text.isHidden()
    panel.update_data(snapshot("codex", quota_windows=[QuotaWindow("week", "周额度", 10)]))
    APP.processEvents()
    assert not panel.openai_balance_text.isHidden()
    panel.close()


def test_openai_view_reuses_balance_card_and_emits_connection_request():
    panel = MainPanel()
    spy = QSignalSpy(panel.connection_requested)
    data = snapshot(status="not_configured", last_success_at=None, balance_cny=None,
                    errors=[FetchError("NOT_CONFIGURED", "balance", "secret")])
    card = panel.balance_card
    panel.update_data(data)
    assert panel.balance_card is card
    assert card.title_label.text() == "API 余额"
    assert card.value.text() == "--"
    assert not panel.openai_connection_button.isHidden()
    assert panel.openai_balance_text.isHidden()
    panel.openai_connection_button.click()
    assert spy.count() == 1 and spy.at(0) == ["openai"]
    panel.update_data(snapshot(balance_cny=Decimal("0")))
    assert panel.openai_connection_button.isHidden()
    assert card.value.text() == "$0.00"
    assert panel.today_card.value.text() == "--"
    assert panel.month_card.value.text() == "--"
    assert all(value.text() == "--" for value in panel.statistics._values)
    assert "USD" in card.value.accessibleDescription()
    panel.update_data(snapshot("deepseek", balance_cny=Decimal("0.001")))
    assert card.value.text() == "¥0.0010"
    assert "组织" not in card.value.accessibleDescription()
    assert panel.openai_connection_button.isHidden()


def test_overview_openai_only_shows_balance_and_safe_details():
    page = ProviderOverview()
    data = snapshot(is_stale=True, refresh_error_codes=("AUTH_EXPIRED",))
    page.set_data({"openai": data, "deepseek": snapshot("deepseek", balance_cny=Decimal("0.001"))})
    card = page.cards["openai"]
    assert card._gauges[0][2].text() == "$24.85"
    assert card._gauges[0][3].isHidden()
    assert card._gauges[1][0].isHidden()
    assert "未更新" in card.findChild(QLabel, "overviewStatus").text()
    assert "USD" in card.accessibleDescription() and "组织 …1234" in card.accessibleDescription()
    assert page.cards["deepseek"]._gauges[0][2].text() == "¥0.0010"
    page.set_data({"openai": snapshot(status="not_configured", last_success_at=None)})
    button = card.findChild(QPushButton, "overviewConnection")
    assert button.text() == "连接账户"
    spy = QSignalSpy(page.connection_requested)
    button.click()
    assert spy.at(0) == ["openai"]


@pytest.mark.parametrize("data,expected", [
    (None, "--"), (snapshot(last_success_at=None), "--"),
    (snapshot(balance_cny=Decimal("0")), "$0.00"),
    (snapshot(balance_cny=Decimal("0.001")), "<$0.01"),
    (snapshot(quota_windows=[QuotaWindow("fake", "非 API 数据", 10)]), "$24.85"),
])
def test_api_ring_has_no_percentage_even_if_unrelated_quota_is_present(data, expected):
    assert ring_value("openai", data) == (expected, None)


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("width", [40, 58])
def test_api_badge_is_painted_on_neutral_ring_without_affecting_codex(monkeypatch, theme, width):
    configure_theme(APP, theme)
    rail = LightRingWidget()
    rail._rail_width = width
    rail.set_sources(["codex", "deepseek", "openai"])
    rail.set_data({"codex": snapshot("codex", quota_windows=[QuotaWindow("week", "周额度", 10)]),
                   "deepseek": snapshot("deepseek", balance_cny=Decimal("12.15")), "openai": snapshot()})
    rail.show()
    APP.processEvents()
    arcs, texts, tracks = [], [], []

    class RecordingPainter(QPainter):
        def drawArc(self, *args):
            arcs.append(args)
            return super().drawArc(*args)

        def drawText(self, *args):
            texts.append(args[-1])
            return super().drawText(*args)

        def drawEllipse(self, *args):
            tracks.append(self.pen().widthF())
            return super().drawEllipse(*args)

    monkeypatch.setattr("ui.light_ring.QPainter", RecordingPainter)
    item = rail.items["openai"]
    assert not item.grab().isNull()
    assert "API" in texts and not arcs
    assert tracks[0] == pytest.approx(1.6)
    assert item.value == "$24.85" and item.remaining is None
    assert "USD" in item.accessibleDescription() and "组织 …1234" in item.accessibleDescription()
    texts.clear()
    tracks.clear()
    rail.items["codex"].grab()
    assert arcs and "API" not in texts
    assert tracks[0] == pytest.approx(3.2)
    assert rail.items["codex"].value == "90%"
    assert rail.items["deepseek"].value == "¥12.15"
    assert rail.width() == width
    rail.close()


def test_long_api_amount_keeps_full_details_and_existing_compaction():
    rail = LightRingWidget()
    rail.set_sources(["openai"])
    rail.set_data({"openai": snapshot(balance_cny=Decimal("12345678.50"), is_stale=True,
                                      refresh_error_codes=("NETWORK_ERROR",))})
    item = rail.items["openai"]
    assert item.value == "$12345678.50"
    assert item.display_value == "$12.35M"
    assert item.stale
    assert "$12345678.50" in item.accessibleName()
    assert "未更新" in item.accessibleDescription()
    rail.show()
    rail.show_details("openai")
    card = rail.details.cards["openai"]
    assert card._gauges[0][2].text() == "$12345678.50"
    assert "USD" in card.accessibleDescription()
    assert all(button.isHidden() for button in card.findChildren(QPushButton))
    assert not rail.details._auto_timer.isActive()
    rail.close()


def test_api_ring_keeps_order_click_hide_and_reorder_contracts():
    state.merge_dict(config_manager.PANEL_LAYOUT_PATH, {"light_ring_order": ["deepseek", "codex"],
                                                      "pinned_providers": ["codex"]})
    rail = LightRingWidget()
    rail.set_sources(["codex", "deepseek", "openai"])
    assert rail.selected_sources() == ["deepseek", "codex", "openai"]
    rail.set_data({"openai": snapshot(status="not_configured", last_success_at=None, balance_cny=None)})
    assert rail.items["openai"].value == "--"
    assert "未连接" in rail.items["openai"].accessibleDescription()
    rail.show()
    APP.processEvents()
    spy = QSignalSpy(rail.provider_selected)
    QTest.mouseClick(rail.items["openai"], Qt.MouseButton.LeftButton)
    assert spy.at(0) == ["openai"]
    origin = rail.pos()
    rail.reorder_at("openai", rail.content.mapToGlobal(QPoint(0, -50)))
    assert rail.finish_drag()
    assert rail.pos() == origin
    assert rail.selected_sources() == ["openai", "deepseek", "codex"]
    assert state.load_dict(config_manager.PANEL_LAYOUT_PATH)["pinned_providers"] == ["codex"]
    assert spy.count() == 1
    rail.hide()
    assert not rail.popup.isVisible() and not rail._timer.isActive()
    rail.show()
    assert rail.selected_sources() == ["openai", "deepseek", "codex"]
    rail.close()


def test_details_mask_raw_organization_and_do_not_use_attempt_timestamp():
    data = snapshot(account_label="org-raw-organization-1234", last_attempt_at=SUCCESS_AT + timedelta(days=1),
                    errors=[FetchError("UNKNOWN_ERROR", "raw-source", "raw-secret-response")])
    details = openai_balance_details(data)
    assert "org-…1234" in details
    assert "org-raw-organization-1234" not in details
    assert "2026-11-09" in details and "2026-11-10" not in details
    assert "raw-secret-response" not in details
    assert openai_status_message(data).startswith("未更新")
