from datetime import datetime
from decimal import Decimal
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QFont, QFontInfo, QFontMetrics, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton
from test_refresh import widget_stub

from api.providers.base import FetchError, QuotaMetric, QuotaWindow
from config import runtime as config_manager
from config.defaults import DEFAULT_CONFIG
from config.store import validate_config
from data.store import TokenData
from ui.light_ring import LightRingWidget, ring_value
from ui.qt_settings import SettingsWindow
from ui.qt_theme import configure_theme, current_theme
from ui.qt_widget import FloatingWidget

APP = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def preferences(monkeypatch, tmp_path):
    values = DEFAULT_CONFIG.copy()
    monkeypatch.setattr(config_manager, "_config", values)
    monkeypatch.setattr(config_manager, "PANEL_LAYOUT_PATH", tmp_path / "layout.json")
    monkeypatch.setattr(config_manager, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr("ui.codex_reset_news.fetch_status", lambda: None)
    monkeypatch.setattr(config_manager, "load_config", lambda: values.copy())
    monkeypatch.setattr(config_manager, "save_widget_position", lambda *_args: None)
    monkeypatch.setattr(config_manager, "load_widget_position", lambda: None)
    monkeypatch.setattr(config_manager, "save_config", lambda changes: (values.update(changes) or values.copy()))
    configure_theme(APP, "light")
    yield values


def quota(used=87, *, account="", windows=None):
    return TokenData(
        account_key=account,
        last_success_at=datetime.now(),
        quota_windows=windows or [QuotaWindow("5h", "5 小时额度", used, window_minutes=300)],
    )


@pytest.mark.parametrize("used,expected", [(87, "13%"), (0, "100%"), (120, "0%"), (float("nan"), "--"), (True, "--")])
def test_primary_quota_matches_original_ball_and_rejects_unknown_values(used, expected):
    data = quota(windows=[QuotaWindow("week", "周额度", 2, window_minutes=10080),
                          QuotaWindow("session", "5 小时额度", used, window_minutes=300)])
    assert ring_value("codex", data)[0] == expected
    assert ring_value("claude", data)[0] == expected


def test_missing_values_and_balances_do_not_invent_quota_percentages():
    assert ring_value("codex", None) == ("--", None)
    assert ring_value("codex", TokenData()) == ("--", None)
    data = TokenData(last_success_at=datetime.now(), balance_cny=Decimal("128.50"))
    assert ring_value("deepseek", data) == ("¥128.50", None)
    assert ring_value("codex", data) == ("--", None)


def test_single_multi_sizes_theme_accent_and_brand_color():
    rail = LightRingWidget()
    rail.set_sources(["claude"])
    rail.set_data({"claude": quota(30)})
    rail.show()
    APP.processEvents()
    assert rail.width() == 58
    assert rail.height() == 106
    item = rail.items["claude"]
    image = item.grab().toImage()
    accent = QColor(current_theme().accent).rgb()
    assert any(image.pixel(x, y) == accent for x in range(image.width()) for y in range(3, 44))
    # 品牌图标在环内仍保留彩色，不能被灰度或主题主色覆盖。
    assert any(abs(image.pixelColor(x, y).red() - image.pixelColor(x, y).blue()) > 40
               for x in range(20, 35) for y in range(14, 32))
    controller = configure_theme(APP, "light")
    controller.preview_appearance("light", "#9955CC", 100)
    image = item.grab().toImage()
    new_accent = QColor(current_theme().accent).rgb()
    assert new_accent != accent
    assert any(image.pixel(x, y) == new_accent for x in range(image.width()) for y in range(3, 44))
    rail.set_sources(["claude", "codex", "cursor"])
    rail.set_data({"claude": quota(), "codex": quota(), "cursor": quota()})
    APP.processEvents()
    assert rail.height() == 246
    assert rail.selected_providers() == ["claude", "codex", "cursor"]
    assert rail.x() + rail.width() == rail.screen().availableGeometry().right() + 1


def test_hover_reads_cache_stale_status_and_click_or_drag(monkeypatch):
    rail = LightRingWidget()
    rail.set_sources(["codex", "claude"])
    stale = quota()
    stale.is_stale = True
    stale.errors = [FetchError("NETWORK_ERROR", "quota", "失败")]
    rail.set_data({"codex": stale, "claude": quota(20)})
    rail.show()
    APP.processEvents()
    requested = Mock()
    rail.auto_refresh_requested.connect(requested)
    rail.show_details("codex")
    assert rail.popup.isVisible()
    assert rail.items["codex"].value == "13%"
    assert rail.items["codex"].stale
    assert not rail.details._auto_timer.isActive()
    requested.assert_not_called()
    opened = Mock()
    rail.provider_selected.connect(opened)
    QTest.mouseClick(rail.items["claude"], Qt.MouseButton.LeftButton)
    opened.assert_called_once_with("claude")
    opened.reset_mock()
    rail.start_drag(QPoint(100, 100))
    rail.move_drag(QPoint(200, 100))
    assert rail.finish_drag()
    QTest.qWait(220)
    opened.assert_not_called()
    assert config_manager.PANEL_LAYOUT_PATH.exists()
    assert not rail.popup.isVisible()
    rail.hide()
    assert not rail._timer.isActive()


def test_settings_keep_ball_default_and_persist_single_or_multi_choices(preferences):
    assert validate_config({})["FLOATING_STYLE"] == "ball"
    preferences.update(FLOATING_STYLE="light_ring", BACKGROUND_PROVIDER_IDS=["codex", "claude"])
    settings = SettingsWindow()
    assert settings.floating_style_combo.currentData() == "light_ring"
    assert settings.background_provider_checks["codex"].isChecked()
    assert settings._values()["BACKGROUND_PROVIDER_IDS"] == ["codex", "claude"]
    assert not settings.edge_hide_check.isEnabled()
    settings.floating_style_combo.setCurrentIndex(settings.floating_style_combo.findData("ball"))
    values = settings._values()
    assert values["FLOATING_STYLE"] == "ball"
    assert values["EDGE_HIDE_ENABLED"] is True
    assert values["BACKGROUND_PROVIDER_IDS"] == ["codex", "claude"]
    assert settings.edge_hide_check.isEnabled()
    settings.reload_provider_configuration("claude", True)
    assert not settings.background_provider_checks["claude"].isEnabled()
    assert not settings.background_provider_checks["claude"].isChecked()


def test_controller_shares_account_checked_snapshots_and_refresh_targets(monkeypatch):
    widget = widget_stub()
    widget.panel = None
    rail = widget._light_ring = Mock()
    rail.selected_providers.return_value = ["codex", "claude"]
    rail.selected_sources.return_value = ["codex", "claude"]
    safe = quota(account="current-claude")
    widget._provider_results = {"codex": quota(account="old-account"), "claude": safe}
    monkeypatch.setattr(TokenData, "account_key_for_config", lambda values: "current-" + values["ACTIVE_PROVIDER"])
    monkeypatch.setattr(TokenData, "cached_snapshot", Mock(return_value=None))
    monkeypatch.setattr(TokenData, "persisted_snapshot", Mock(return_value=None))
    widget._update_provider_overview()
    rail.set_data.assert_called_once_with({"codex": None, "claude": safe})
    widget._overview_refresh_queue = []
    widget._overview_refresh_active = None
    widget._advance_overview_refresh = Mock()
    monkeypatch.setattr("ui.qt_widget.configured_provider_ids", lambda: ["codex", "claude", "cursor"])
    widget._refresh_overview(automatic=True, provider_ids=rail.selected_providers())
    assert widget._overview_refresh_queue == ["codex", "claude"]
    widget._overview_refresh_queue.append("cursor")
    widget._stop_overview_auto_refresh()
    assert widget._overview_refresh_queue == ["codex", "claude"]
    rail.isVisible.return_value = False
    widget._stop_overview_auto_refresh()
    assert widget._overview_refresh_queue == []


@pytest.mark.parametrize("source", ["memory", "disk"])
def test_ring_restores_last_success_before_refresh_finishes(monkeypatch, source):
    widget = widget_stub()
    widget.panel = None
    rail = widget._light_ring = LightRingWidget()
    rail.set_sources(["codex"])
    rail.show()
    APP.processEvents()
    saved = quota(35, account="same-account")
    saved.is_stale = True
    saved.quota_source = "cache"
    monkeypatch.setattr(TokenData, "account_key_for_config", lambda _values: "same-account")
    memory = Mock(return_value=saved if source == "memory" else None)
    disk = Mock(return_value=saved)
    monkeypatch.setattr(TokenData, "cached_snapshot", memory)
    monkeypatch.setattr(TokenData, "persisted_snapshot", disk)

    widget._update_provider_overview()

    assert rail.items["codex"].value == "65%"
    assert rail.items["codex"].stale
    assert rail._snapshots["codex"] is saved
    assert widget._provider_results["codex"] is saved
    memory.assert_called_once_with("codex", "same-account")
    assert disk.call_count == (source == "disk")
    widget._update_provider_overview()
    assert memory.call_count == 1


def test_ring_does_not_restore_another_accounts_snapshot(monkeypatch):
    widget = widget_stub()
    widget.panel = None
    rail = widget._light_ring = LightRingWidget()
    rail.set_sources(["codex"])
    rail.show()
    APP.processEvents()
    monkeypatch.setattr(TokenData, "account_key_for_config", lambda _values: "new-account")
    monkeypatch.setattr(TokenData, "cached_snapshot", Mock(return_value=None))
    monkeypatch.setattr(TokenData, "persisted_snapshot", Mock(return_value=quota(account="old-account")))

    widget._update_provider_overview()

    assert rail.items["codex"].value == "--"
    assert rail._snapshots["codex"] is None
    assert "codex" not in widget._provider_results


def test_style_switch_and_tray_restore_without_recreating_ball(monkeypatch, preferences):
    from ui.qt_update import AppUpdateController

    monkeypatch.setattr(FloatingWidget, "_start_provider_refresh", Mock(return_value=False))
    monkeypatch.setattr(AppUpdateController, "schedule_startup_check", lambda _self: None)
    monkeypatch.setattr("ui.qt_widget.configured_provider_ids", lambda: ["codex", "claude", "deepseek"])
    widget = FloatingWidget()
    ball = widget.ball
    assert widget.isVisible()
    preferences.update(FLOATING_STYLE="light_ring", BACKGROUND_PROVIDER_IDS=["codex", "claude"])
    widget._sync_ball_visibility()
    APP.processEvents()
    rail = widget._light_ring
    assert rail.isVisible()
    assert not widget.isVisible()
    assert rail.selected_providers() == ["codex", "claude"]
    widget.set_visible_from_tray()
    assert not rail.isVisible()
    assert not widget.isVisible()
    widget.set_visible_from_tray()
    assert rail.isVisible()
    assert not widget.isVisible()
    preferences["FLOATING_STYLE"] = "ball"
    widget._sync_ball_visibility()
    assert widget.isVisible()
    assert not rail.isVisible()
    assert widget.ball is ball
    assert not rail._timer.isActive()
    preferences["FLOATING_STYLE"] = "light_ring"
    widget._sync_ball_visibility()
    widget._open_light_ring_provider("codex")
    assert widget._expanded
    assert widget.isVisible()
    assert rail.isVisible()
    widget.collapse_panel()
    assert not widget.isVisible()
    assert rail.isVisible()
    preferences.update(FLOATING_STYLE="light_ring", BACKGROUND_PROVIDER_IDS=[])
    widget._sync_ball_visibility()
    assert rail.selected_providers() == ["codex"]


def test_long_balance_keeps_narrow_width_and_full_value_in_details():
    rail = LightRingWidget()
    rail.set_sources(["deepseek"])
    rail.show()
    APP.processEvents()
    rail.set_data({"deepseek": TokenData(last_success_at=datetime.now(), balance_cny=Decimal("12345678.50"))})
    assert rail.width() == 58
    assert rail.items["deepseek"].value == "¥12345678.50"
    assert rail.items["deepseek"].display_value == "¥12.35M"
    area = rail.screen().availableGeometry()
    assert rail.x() + rail.width() == area.right() + 1
    rail.save_position()
    reopened = LightRingWidget()
    reopened.set_sources(["deepseek"])
    reopened.set_data(rail._snapshots)
    reopened.show()
    APP.processEvents()
    assert reopened.pos() == rail.pos()
    assert reopened.items["deepseek"].remaining is None


def test_credits_do_not_expand_other_platforms_or_lose_full_value(monkeypatch):
    from ui.formatting import format_quota_metric

    rail = LightRingWidget()
    credit_source = "profile:" + "a" * 32
    rail.set_sources(["codex", "deepseek", credit_source], providers={credit_source: "codex"})
    credit = TokenData(last_success_at=datetime.now(),
                       quota_metrics=[QuotaMetric("剩余积分", "2388.03", value_kind="credits")])
    # 布局测试接收已有读数，不依赖某个积分平台是否注册或如何采集。
    original = ring_value
    monkeypatch.setattr("ui.light_ring.ring_value", lambda pid, data: (
        (format_quota_metric(credit.quota_metrics[0]), None) if data is credit else original(pid, data)))
    rail.set_data({"codex": quota(84),
                   "deepseek": TokenData(last_success_at=datetime.now(), balance_cny=Decimal("19.56")),
                   credit_source: credit})
    rail.show()
    APP.processEvents()
    assert rail.width() == 58
    assert rail.items[credit_source].display_value == "2.39k"
    assert "2388.03" in rail.items[credit_source].accessibleName()
    assert rail.items[credit_source].remaining is None
    assert rail.items["deepseek"].display_value == "¥19.56"
    assert rail.items["codex"].display_value == "16%"
    rail.show_details(credit_source)
    summary = rail.details.cards[credit_source].findChild(QLabel, "overviewSummary")
    assert "2388.03" in summary.text()


def test_popup_fits_card_without_blank_footer_as_window_count_changes():
    rail = LightRingWidget()
    rail.set_sources(["codex"])
    rail.show()
    for windows in ([QuotaWindow("month", "每周额度", 93)],
                    [QuotaWindow("5h", "5 小时额度", 87, window_minutes=300),
                     QuotaWindow("week", "周额度", 44, window_minutes=10080)],
                    [QuotaWindow("month", "每周额度", 93)]):
        rail.set_data({"codex": quota(windows=windows)})
        rail.show_details("codex")
        APP.processEvents()
        card = rail.details.cards["codex"]
        assert rail.popup.height() == card.height()
        assert card.mapToGlobal(QPoint(0, card.height())).y() == rail.popup.y() + rail.popup.height()
        assert all(button.isHidden() for button in card.findChildren(QPushButton))


def test_profile_entries_keep_independent_values_and_aliases():
    rail = LightRingWidget()
    first, second = "profile:" + "a" * 32, "profile:" + "b" * 32
    rail.set_sources([first, second], providers={first: "codex", second: "codex"},
                     labels={first: "个人账号", second: "工作账号"})
    rail.set_data({first: quota(30), second: quota(91)})
    rail.show()
    APP.processEvents()
    assert rail.items[first].value == "70%"
    assert rail.items[second].value == "9%"
    rail.show_details(second)
    assert "工作账号" in rail.details.cards[second].findChild(QLabel, "overviewProviderName").text()
    assert rail.selected_providers() == []


def test_reorder_persists_for_platforms_and_accounts_and_keeps_other_state():
    from config import state

    state.merge_dict(config_manager.PANEL_LAYOUT_PATH, {"pinned_providers": ["claude"]})
    account = "profile:" + "a" * 32
    ids = ["claude", "codex", account]
    rail = LightRingWidget()
    rail.set_sources(ids, providers={account: "codex"})
    rail.show()
    rail.reorder_at(account, rail.mapToGlobal(QPoint(0, -50)))
    assert rail.finish_drag()
    assert rail.selected_sources() == [account, "claude", "codex"]
    assert rail._row_animations
    QTest.qWait(200)
    assert not rail._row_animations
    assert [item.y() for item in rail.items.values()] == [0, 70, 140]
    assert state.load_dict(config_manager.PANEL_LAYOUT_PATH)["pinned_providers"] == ["claude"]
    reopened = LightRingWidget()
    reopened.set_sources(ids, providers={account: "codex"})
    assert reopened.selected_sources() == [account, "claude", "codex"]
    reopened.set_sources(["codex", account, "cursor"], providers={account: "codex"})
    assert reopened.selected_sources() == [account, "codex", "cursor"]


def test_edge_shoulders_mirror_and_snap_animates():
    rail = LightRingWidget()
    rail.set_sources(["codex"])
    rail.show()
    APP.processEvents()
    area = rail.screen().availableGeometry()
    image = rail.grab().toImage()
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(image.width() - 1, 8).alpha() > 0
    rail.move(area.left(), rail.y())
    rail._resize_to_content()
    image = rail.grab().toImage()
    assert image.pixelColor(image.width() - 1, 0).alpha() == 0
    assert image.pixelColor(0, 8).alpha() > 0
    rail.start_drag(QPoint(100, 100))
    rail.move_drag(QPoint(115, 105))
    assert rail.finish_drag()
    assert rail._snap_animation.duration() == 180
    QTest.qWait(220)
    assert rail.x() == area.left()


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("edge", ["left", "right"])
@pytest.mark.parametrize("width", [40, 58])
def test_docked_ring_keeps_shadow_along_straight_edge(theme, edge, width):
    configure_theme(APP, theme)
    rail = LightRingWidget()
    rail._rail_width = width
    rail.set_sources(["codex", "claude", "deepseek"])
    rail.show()
    APP.processEvents()
    area = rail.screen().availableGeometry()
    x = area.left() if edge == "left" else area.right() + 1 - rail.width()
    rail.move(x, rail.y())
    rail._resize_to_content()
    APP.processEvents()

    image = rail.grab().toImage()
    ratio = image.devicePixelRatio()
    y = round(rail.height() * ratio / 2)
    # 直线段外侧应有半透明阴影，而不是在窗口边界直接裁成不透明描边。
    pixels = [image.pixelColor(i if edge == "right" else image.width() - 1 - i, y).alpha()
              for i in range(max(1, round(ratio)))]
    assert any(0 < alpha < 255 for alpha in pixels)
    rail.close()


def test_ring_profile_selection_reuses_account_scoped_tasks(monkeypatch):
    widget = widget_stub()
    widget.panel = None
    rail = widget._light_ring = Mock()
    ids = ["a" * 32, "b" * 32]
    keys = ["profile:" + value for value in ids]
    rail.selected_sources.return_value = keys
    rail.selected_providers.return_value = []
    profiles = [{"id": value, "revision": "c" * 32, "provider": "codex", "name": value[:1], "fields": {}} for value in ids]
    widget._vpet_profiles = {profile["id"]: profile for profile in profiles}
    widget._vpet_source_options = [{"id": key} for key in keys]
    widget._vpet_profile_results = {}
    widget._vpet_profile_due = {}
    widget._vpet_profile_in_flight = {}
    widget._sync_vpet_usage = Mock()
    widget._notify_low_quota = Mock()
    monkeypatch.setattr("ui.qt_widget.profile_store.profile_config", lambda profile: {"ACTIVE_PROVIDER": "codex", "_ACCOUNT_PROFILE_ID": profile["id"]})
    monkeypatch.setattr("ui.qt_widget.profile_store.load_profiles", lambda: profiles)
    monkeypatch.setattr(TokenData, "account_key_for_config", lambda values: values["_ACCOUNT_PROFILE_ID"])
    widget._refresh_vpet_bound_sources()
    assert widget._thread_pool.start.call_count == 2
    widget._refresh_vpet_bound_sources()
    assert widget._thread_pool.start.call_count == 2
    widget._finish_vpet_profile(ids[0], "c" * 32, quota(30, account=ids[0]))
    widget._finish_vpet_profile(ids[1], "c" * 32, quota(90, account=ids[1]))
    values = rail.set_data.call_args.args[0]
    assert values[keys[0]].account_key == ids[0]
    assert values[keys[1]].account_key == ids[1]
    profiles[0]["revision"] = "d" * 32
    widget._update_provider_overview()
    assert rail.set_data.call_args.args[0][keys[0]] is None
    fresh = quota(84, account=ids[0])
    widget._notify_profile_quota(ids[0], "账户", "codex", fresh)
    assert rail.set_data.call_args.args[0][keys[0]] is fresh
    assert ring_value("codex", rail.set_data.call_args.args[0][keys[0]])[0] == "16%"


def test_shared_source_settings_migrate_selections_and_keep_custom_ring_color(preferences):
    migrated = validate_config({"BACKGROUND_PROVIDER_IDS": ["deepseek"],
                                "FLOATING_RING_PROVIDER_IDS": ["codex", "deepseek"],
                                "FLOATING_RING_PROFILE_IDS": ["a" * 32]})
    assert migrated["BACKGROUND_PROVIDER_IDS"] == ["deepseek", "codex"]
    assert migrated["BACKGROUND_PROFILE_IDS"] == ["a" * 32]
    assert "FLOATING_RING_PROVIDER_IDS" not in migrated
    preferences.update(FLOATING_STYLE="light_ring", BACKGROUND_PROVIDER_IDS=["codex"],
                       FLOATING_RING_FOLLOW_THEME=False, FLOATING_RING_COLOR="#AA44CC")
    settings = SettingsWindow()
    assert not hasattr(settings, "ring_provider_checks")
    assert settings.background_provider_checks["codex"].isChecked()
    assert not settings.ring_follow_theme_check.isChecked()
    assert settings.ring_color_row.isEnabled()
    assert settings._values()["FLOATING_RING_COLOR"] == "#AA44CC"
    rail = LightRingWidget()
    rail.set_sources(["codex"])
    rail.set_data({"codex": quota(25)})
    rail.show()
    image = rail.items["codex"].grab().toImage()
    custom = QColor("#AA44CC").rgb()
    assert any(image.pixel(x, y) == custom for x in range(image.width()) for y in range(3, 44))


def test_codex_popup_always_has_public_reset_section_when_enabled():
    from test_codex_reset_news import payload

    rail = LightRingWidget()
    rail.set_sources(["codex"])
    rail.set_data({"codex": quota()})
    rail.set_reset_news(payload(), enabled=True)
    rail.show()
    rail.show_details("codex")
    APP.processEvents()
    card = rail.details.cards["codex"]
    footer = card.findChild(QLabel, "publicResetNews")
    assert "上次公开重置" in footer.text()
    assert "已过去" in footer.text()
    assert "https://codex-resets.com/" in footer.text()
    assert card.height() == rail.popup.height()
    rail.set_reset_news(None, enabled=False)
    assert footer.isHidden()


def wheel(widget, delta, modifiers=Qt.KeyboardModifier.ControlModifier):
    position = QPointF(10, 10)
    event = QWheelEvent(position, QPointF(widget.mapToGlobal(position.toPoint())), QPoint(), QPoint(0, delta),
                        Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


@pytest.mark.parametrize("edge", ["left", "right"])
def test_ctrl_wheel_resizes_and_persists_without_losing_edge_or_values(edge):
    from config import state

    state.merge_dict(config_manager.PANEL_LAYOUT_PATH, {"pinned_providers": ["claude"]})
    rail = LightRingWidget()
    rail.set_sources(["codex", "deepseek"])
    rail.set_data({"codex": quota(84),
                   "deepseek": TokenData(last_success_at=datetime.now(), balance_cny=Decimal("19.56"))})
    rail.show()
    APP.processEvents()
    area = rail.screen().availableGeometry()
    if edge == "left":
        rail.move(area.left(), rail.y())
    original_height = rail.height()

    # 高精度滚轮的半格先累积，完整一格再改变宽度。
    wheel(rail.items["codex"], -60)
    assert rail.width() == 58
    wheel(rail.items["codex"], -60)
    assert rail.width() == 56
    wheel(rail.scroll.viewport(), -120 * 20)
    APP.processEvents()
    assert rail.width() == 40
    assert rail.height() == original_height
    assert rail.items["codex"].display_value == "16%"
    assert rail.items["deepseek"].display_value == "¥19.56"
    font = QFont("Segoe UI")
    font.setPixelSize(10)
    # 某些无头 Qt 环境没有加载任何字体，需在有字体时才比较真实字宽。
    if QFontInfo(font).family():
        assert QFontMetrics(font).horizontalAdvance("¥19.56") <= rail.items["deepseek"].width() - 4
    if edge == "left":
        assert rail.x() == area.left()
    else:
        assert rail.x() + rail.width() == area.right() + 1

    # 隐藏前刷新防抖写入；保留已有布局状态，并以新宽度恢复贴边位置。
    rail.hide()
    saved = state.load_dict(config_manager.PANEL_LAYOUT_PATH)
    assert saved["light_ring_width"] == 40
    assert saved["pinned_providers"] == ["claude"]
    reopened = LightRingWidget()
    reopened.set_sources(["codex", "deepseek"])
    reopened.show()
    APP.processEvents()
    assert reopened.width() == 40
    if edge == "left":
        assert reopened.x() == area.left()
    else:
        assert reopened.x() + reopened.width() == area.right() + 1
    wheel(reopened.items["codex"], 120 * 100)
    assert reopened.width() == 120
    if edge == "left":
        assert reopened.x() == area.left()
    else:
        assert reopened.x() + reopened.width() == area.right() + 1
    reopened.close()


def test_plain_wheel_still_scrolls_overflowing_ring():
    rail = LightRingWidget()
    sources = [f"profile:{i:032x}" for i in range(20)]
    rail.set_sources(sources, providers={key: "codex" for key in sources})
    rail.show()
    APP.processEvents()
    scrollbar = rail.scroll.verticalScrollBar()
    assert scrollbar.maximum() > 0
    wheel(rail.scroll.viewport(), -120, Qt.KeyboardModifier.NoModifier)
    assert scrollbar.value() > 0
    assert rail.width() == 58
    rail.close()


def test_widening_near_edge_reserves_space_for_shoulders():
    rail = LightRingWidget()
    rail.set_sources(["codex", "deepseek"])
    rail.show()
    APP.processEvents()
    area = rail.screen().availableGeometry()
    rail.move(area.right() + 1 - rail.width() - 10, rail.y())
    rail._resize_to_content()
    assert rail._root_layout.contentsMargins().top() == 8
    wheel(rail.items["codex"], 120 * 10)
    APP.processEvents()
    assert rail.x() + rail.width() == area.right() + 1
    assert rail._root_layout.contentsMargins().top() == 22
    rail.close()


@pytest.mark.parametrize("saved_width", [None, True, "40", 39, 121])
def test_invalid_saved_ring_width_falls_back_to_default(saved_width):
    from config import state

    state.merge_dict(config_manager.PANEL_LAYOUT_PATH, {"light_ring_width": saved_width})
    rail = LightRingWidget()
    assert rail.width() == 58
    rail.close()
