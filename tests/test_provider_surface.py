"""Quota semantics stay consistent between the floating ball and pet protocol."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication

from api.providers import PROVIDERS
from api.providers.base import QuotaMetric, QuotaWindow
from data.store import PerProviderData, TokenData
from ui.i18n import configure_language, language_controller
from ui.qt_ball import FloatingUsageBall
from ui.qt_panel import MainPanel
from ui.qt_theme import theme_controller
from ui.qt_widget import FloatingWidget
from ui.vpet_host import usage_message


@pytest.fixture
def app():
    application = QApplication.instance() or QApplication([])
    previous_language = language_controller().preference if language_controller() else "zh-cn"
    previous_theme = theme_controller().mode
    configure_language(application, "zh-cn")
    yield application
    configure_language(application, previous_language)
    theme_controller().set_mode(previous_theme)


def provider_data(provider_id, **values):
    return TokenData(
        per_provider=[PerProviderData(provider_id, provider_id)],
        **values,
    )


def update_ball(data, *, refreshing=False):
    ball = FloatingUsageBall()
    widget = SimpleNamespace(
        _sync_vpet_usage=Mock(), _data=data, _refreshing=refreshing,
        ball=ball, panel=None,
    )
    FloatingWidget._apply_update(widget)
    return ball


@pytest.mark.parametrize("remaining", [float("nan"), float("inf"), float("-inf"), True, "invalid"])
def test_invalid_ball_quota_is_unknown_without_animating(app, remaining):
    ball = FloatingUsageBall()
    ball.set_quota_state(remaining, "", "订阅额度")
    assert ball._quota_remaining is None
    assert not ball._wave_timer.isActive()


@pytest.mark.parametrize("used", [None, "invalid", True, -1, float("nan"), float("inf"), float("-inf")])
def test_invalid_used_quota_is_unknown_across_surfaces(app, used):
    data = provider_data("codex", status="ok", last_success_at=datetime.now(),
                         quota_windows=[QuotaWindow("week", "Weekly", used)])
    ball = update_ball(data)
    assert ball._quota_remaining is None
    assert not ball._wave_timer.isActive()
    panel = MainPanel()
    panel.update_data(data)
    assert panel.today_card.value.text() == "--"
    assert "100%" not in panel.today_card.detail.text()
    assert usage_message(data, False, "codex")["primary"] == "--"


@pytest.mark.parametrize("used,remaining", [("25", 75), (125, 0)])
def test_numeric_and_overage_quota_stay_consistent_across_surfaces(app, used, remaining):
    data = provider_data("codex", status="ok", last_success_at=datetime.now(),
                         quota_windows=[QuotaWindow("week", "Weekly", used)])
    ball = update_ball(data)
    assert ball._quota_remaining == remaining
    panel = MainPanel()
    panel.update_data(data)
    assert f"剩余 {remaining}%" in panel.today_card.detail.text()
    assert usage_message(data, False, "codex")["primary"] == f"剩余 {remaining}%"


@pytest.mark.parametrize("stale", [False, True])
def test_ball_cache_flag_does_not_change_animation_or_tooltip(app, stale):
    data = provider_data(
        "codex", status="ok", is_stale=stale, last_success_at=datetime.now(),
        quota_windows=[QuotaWindow("weekly", "周额度", 55)],
    )
    ball = update_ball(data)
    ball.show()
    assert ball._quota_remaining == 45
    assert ball.toolTip() == "45%"
    assert ball._wave_timer.isActive()
    ball.hide()


@pytest.mark.parametrize("provider_id", ["codex", "cursor", "copilot", "claude"])
def test_subscription_errors_never_turn_into_pet_balance(provider_id):
    message = usage_message(provider_data(provider_id, status="error"), False, provider_id)
    assert message["primary"] == "--"
    assert message["secondary"] == "额度暂不可用"
    assert "余额" not in message["primary"]


def test_pet_subscription_mode_uses_registry_capability(monkeypatch):
    monkeypatch.setitem(PROVIDERS, "synthetic", SimpleNamespace(supports_subscription_quota=True))
    message = usage_message(provider_data("synthetic", status="error"), False, "synthetic")
    assert message["secondary"] == "额度暂不可用"


@pytest.mark.parametrize("status,stale", [("ok", False), ("ok", True), ("error", True)])
def test_unlimited_copilot_remains_text_on_pet_and_ball(app, status, stale):
    data = provider_data(
        "copilot", status=status, is_stale=stale, last_success_at=datetime.now(),
        quota_metrics=[QuotaMetric("聊天额度", "不限量", "以平台实际策略为准")],
    )
    message = usage_message(data, False, "copilot")
    assert message["primary"] == "不限量"
    assert message["secondary"] == "以平台实际策略为准"
    ball = update_ball(data)
    assert ball._quota_mode
    assert ball._quota_remaining is None
    assert ball._quota_value_text == "不限量"
    assert ball.accessibleDescription() == "不限量"
    assert ball.toolTip() == "不限量"
    assert not ball._wave_timer.isActive()


def test_loading_and_unrelated_metrics_are_not_unlimited(app):
    data = provider_data("copilot", status="loading", quota_metrics=[QuotaMetric("聊天", "不限量")])
    assert usage_message(data, True, "copilot")["primary"] == "--"
    ball = update_ball(data, refreshing=True)
    assert ball._quota_remaining is None
    assert ball._quota_value_text == ""
    data.quota_metrics = [QuotaMetric("快照时间", "2026-09-05")]
    assert usage_message(data, False, "copilot")["primary"] == "--"
    ball = update_ball(data)
    assert ball._quota_value_text == ""


def test_numeric_window_has_priority_over_unlimited_category(app):
    data = provider_data(
        "copilot", status="ok", quota_windows=[QuotaWindow("premium", "高级额度", 25)],
        quota_metrics=[QuotaMetric("代码补全", "不限量")],
    )
    assert usage_message(data, False, "copilot")["primary"] == "剩余 75%"
    ball = update_ball(data)
    assert ball._quota_remaining == 75
    assert ball._quota_value_text == ""


@pytest.mark.parametrize("provider_id,label", [("openrouter", "密钥剩余额度"), ("deepseek", "余额")])
def test_balance_labels_keep_key_limits_distinct_from_account_balance(app, provider_id, label):
    data = provider_data(provider_id, status="ok", balance_cny=12.5, currency="USD")
    assert usage_message(data, False, provider_id)["primary"] == f"{label} $12.50"
    ball = update_ball(data)
    assert ball._secondary_label == label
    assert ball._balance == "$12.50"


@pytest.mark.parametrize("theme,language", [("light", "zh-cn"), ("dark", "en")])
def test_text_quota_renders_and_clears_without_changing_numeric_defaults(app, theme, language):
    theme_controller().set_mode(theme)
    configure_language(app, language)
    ball = FloatingUsageBall()
    ball.set_quota_state(None, "", "聊天额度", value_text="不限量")
    assert ball._quota_remaining is None
    assert not ball.grab().isNull()
    assert ball.accessibleDescription() == ("Unlimited" if language == "en" else "不限量")
    ball.set_quota_state(75, "重置未知")
    assert ball._quota_value_text == ""
    assert ball.accessibleDescription() == "75%"
    ball.set_quota_state(None, "", value_text="不限量")
    ball.clear_quota_state()
    assert ball._quota_value_text == ""
    ball.set_quota_state(None, "额度暂不可用")
    assert ball._quota_value_text == ""
    assert ball.accessibleDescription() == ("Unknown" if language == "en" else "未知")
