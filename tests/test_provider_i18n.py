import re

import pytest

from api.providers.api_balance import MoonshotProvider, OpenRouterProvider
from api.providers.claude import ClaudeProvider
from api.providers.copilot import CopilotProvider
from api.providers.elevenlabs import ElevenLabsProvider
from api.providers.gemini import GeminiProvider
from api.providers.kimi import KimiProvider
from api.providers.minimax import MiniMaxProvider
from api.providers.zai import ZaiProvider
from ui.i18n import tr


@pytest.mark.parametrize("language", ["en", "zh-tw", "ja", "ko"])
def test_provider_settings_metadata_translates_without_changing_api_urls(monkeypatch, language):
    monkeypatch.setattr("ui.i18n.current_language", lambda: language)
    for provider in (
        OpenRouterProvider, MoonshotProvider, CopilotProvider, ClaudeProvider, ZaiProvider,
        KimiProvider, MiniMaxProvider, ElevenLabsProvider, GeminiProvider,
    ):
        sources = [provider.support_description]
        for meta in provider.credential_fields.values():
            sources.extend((meta["label"], meta["hint"]))
        for source in sources:
            if not re.search(r"[\u4e00-\u9fff]", source):
                continue
            translated = tr(source)
            assert translated != source, source
            if language == "en":
                assert not re.search(r"[\u4e00-\u9fff]", translated), translated
            for url in re.findall(r"https://[A-Za-z0-9./-]+", source):
                assert url in translated


def test_runtime_provider_error_templates_preserve_provider_name(monkeypatch):
    monkeypatch.setattr("ui.i18n.current_language", lambda: "en")
    assert tr("Moonshot / Kimi API API Key 无效或权限不足，请检查凭据") == (
        "Moonshot / Kimi API API key is invalid or lacks permission. Check your credentials"
    )
    assert tr("OpenRouter 返回了无效数据或接口结构已变化") == (
        "OpenRouter returned invalid data or its API format has changed"
    )
    assert tr("GitHub Copilot 额度：GitHub 登录已失效，请更新登录令牌") == (
        "GitHub Copilot quota: GitHub sign-in has expired. Update your sign-in token"
    )


def test_snapshot_setup_hint_translates_as_a_whole(monkeypatch):
    monkeypatch.setattr("ui.i18n.current_language", lambda: "en")
    # 通用“默认 {url}”模板不能把配置说明吞进 URL，留下未翻译的后半句。
    hint = ClaudeProvider.credential_fields["STATUSLINE_FILE"]["hint"]
    assert tr(hint) == (
        "Uses the snapshot first when specified. Default: ~/.claude/tokenmeter-usage.json; "
        "configure claude_statusline.py first"
    )


def test_glm_numbered_quota_titles_keep_separate_windows(monkeypatch):
    monkeypatch.setattr("ui.i18n.current_language", lambda: "en")
    quota = ZaiProvider._parse_quota({"limits": [
        {"type": "TOKENS_LIMIT", "percentage": 20},
        {"type": "TOKENS_LIMIT", "percentage": 40},
        {"type": "TIME_LIMIT", "percentage": 30},
        {"type": "TIME_LIMIT", "percentage": 50},
    ]})
    assert [tr(window.title) for window in quota.windows] == [
        "Plan quota 1", "Plan quota 2", "Tool call quota 1", "Tool call quota 2",
    ]


@pytest.mark.parametrize("language", ["en", "zh-tw", "ja", "ko"])
def test_provider_picker_text_and_numbered_count_are_localized(monkeypatch, language):
    from ui.provider_translations import PROVIDER_MESSAGES

    monkeypatch.setattr("ui.i18n.current_language", lambda: language)
    sources = (
        "切换 AI 平台", "搜索平台名称，如 Claude、Kimi、智谱", "搜索 AI 平台",
        "全部平台", "已配置", "常用平台", "未配置", "API 用量", "AI 平台列表",
        "没有匹配的平台，试试其他名称或查看全部平台",
        "Enter 切换 · Ctrl+D 收藏 · Esc 关闭",
    )
    for source in sources:
        assert source in PROVIDER_MESSAGES
        translated = tr(source)
        assert translated
        if language == "en":
            assert not re.search(r"[\u4e00-\u9fff]", translated), translated
    assert "14" in tr("14 个平台")
    assert tr("14 个平台") != "14 个平台"
    assert tr("{n} 个平台", n=14) == tr("14 个平台")
    for unchanged in ("kimi", "moonshot", "elevenlabs", "Gemini CLI", "https://api.kimi.com/coding/v1"):
        assert tr(unchanged) == unchanged


def test_new_dynamic_quota_labels_preserve_models_numbers_and_products(monkeypatch):
    monkeypatch.setattr("ui.i18n.current_language", lambda: "en")
    assert tr("60 分钟额度") == "60-minute quota"
    assert tr("MiniMax-M2.7 · 周期额度") == "MiniMax-M2.7 · Periodic quota"
    assert tr("MiniMax-M2.7 · 周额度") == "MiniMax-M2.7 · Weekly quota"
    assert tr("MiniMax-M2.7 · 5 小时额度") == "MiniMax-M2.7 · 5-hour quota"
    assert tr("Sonnet 每周额度") == "Sonnet weekly quota"
    assert tr("加成后剩余 120%（基础额度剩余 60%）") == (
        "120% remaining with boost (60% of base quota remaining)"
    )
    for text in (
        "字符积分", "订阅字符积分", "已用 / 订阅上限", "无可用订阅额度",
        "不在当前套餐中", "不限额",
        "Kimi Coding 未返回可量化的订阅额度",
        "MiniMax Token Plan 未返回可量化的套餐额度",
        "MiniMax Token Plan 接口返回错误，请检查套餐密钥与站点",
        "ElevenLabs 额度接口返回错误",
        "ElevenLabs 请检查 HTTPS API 地址和凭据格式",
    ):
        assert not re.search(r"[\u4e00-\u9fff]", tr(text)), text


@pytest.mark.parametrize("language", ["en", "zh-tw", "ja", "ko"])
def test_cli_oauth_errors_are_localized_without_translating_technical_identifiers(monkeypatch, language):
    monkeypatch.setattr("ui.i18n.current_language", lambda: language)
    common = (
        "NOT_CONFIGURED", "AUTH_EXPIRED", "INVALID_CREDENTIALS", "FILE_ERROR",
        "PERMISSION_DENIED", "RATE_LIMITED", "NETWORK_TIMEOUT", "NETWORK_ERROR",
        "SERVER_ERROR", "INVALID_RESPONSE", "QUOTA_UNAVAILABLE",
    )
    for factory, codes in (
        (ClaudeProvider._oauth_error, (*common, "SCOPE_REQUIRED")),
        (GeminiProvider._error, (*common, "UNSUPPORTED_AUTH", "CONSUMER_TIER_DEPRECATED", "PROJECT_REQUIRED")),
    ):
        for code in codes:
            error = factory(code)
            for source in (error.source, error.message):
                translated = tr(source)
                assert translated != source, source
                if language == "en":
                    assert not re.search(r"[\u4e00-\u9fff]", translated), translated
                for literal in ("user:profile", "setup-token", "/login", "Access Token", "Antigravity"):
                    if literal in source:
                        assert literal in translated
