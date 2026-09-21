import threading
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

os.environ["APPDATA"] = str(Path.cwd() / ".test-appdata")

from config import runtime as config_manager
from api import browser_cookie
from api.providers.deepseek import DeepSeekProvider
from api.providers.mimo import MiMoProvider


def test_deepseek_cookie_filter_keeps_only_first_party_domains():
    cookies = [
        {"name": "session", "value": "active", "domain": ".deepseek.com"},
        {"name": "platform", "value": "yes", "domain": "platform.deepseek.com"},
        {"name": "other", "value": "skip", "domain": "example.com"},
    ]

    value = browser_cookie.format_cookie_string(
        cookies,
        allowed_domains=("platform.deepseek.com", "deepseek.com"),
    )

    assert value == "session=active; platform=yes"


def test_deepseek_cookie_acquisition_keeps_bearer_token_separate():
    with patch("api.providers.deepseek.browser_cookie.acquire_cookie_via_chrome") as acquire:
        acquire.return_value = "session=active"
        result = DeepSeekProvider.acquire_cookie_via_chrome(threading.Event())

    assert result == "session=active"
    assert DeepSeekProvider.acquired_cookie_values(result) == {"COOKIE": "session=active"}
    assert acquire.call_args.kwargs["profile_name"] == "deepseek-chrome"
    assert acquire.call_args.kwargs["allowed_domains"] == ("platform.deepseek.com", "deepseek.com")
    assert acquire.call_args.kwargs["user_data_dir"] == str(
        config_manager.CONFIG_DIR / "deepseek-chrome"
    )


def test_deepseek_browser_acquisition_captures_and_validates_platform_token():
    session = Mock()
    session.capture_request_headers.return_value = {
        "Authorization": "Bearer fresh-platform-token"
    }
    session.fetch_json.return_value = browser_cookie.BrowserFetchResult(
        200,
        {"code": 0, "data": {"biz_code": 0, "biz_data": {"normal_wallets": []}}},
        "session=active",
    )
    stop_event = threading.Event()
    stop_event.set()

    with patch("api.providers.deepseek.browser_cookie.open_chrome_session", return_value=session):
        result = DeepSeekProvider.acquire_credentials_via_chrome(stop_event)

    assert result == "Bearer fresh-platform-token"
    assert DeepSeekProvider.acquired_credential_values(result) == {
        "AUTH": "Bearer fresh-platform-token"
    }
    session.capture_request_headers.assert_called_once_with(
        url_prefix="https://platform.deepseek.com/api/v0/",
        timeout_seconds=10.0,
    )
    session.close.assert_called_once_with()


def test_deepseek_browser_acquisition_rejects_invalid_platform_token():
    session = Mock()
    session.capture_request_headers.return_value = {"authorization": "raw-token"}
    session.fetch_json.return_value = browser_cookie.BrowserFetchResult(
        200,
        {"code": 40003, "msg": "Authorization Failed (invalid token)"},
        "",
    )
    stop_event = threading.Event()
    stop_event.set()

    with (
        patch("api.providers.deepseek.browser_cookie.open_chrome_session", return_value=session),
        pytest.raises(RuntimeError, match="DEEPSEEK_AUTH_INVALID"),
    ):
        DeepSeekProvider.acquire_credentials_via_chrome(stop_event)

    session.close.assert_called_once_with()


def test_browser_profiles_follow_the_active_application_data_directory():
    custom_dir = Path.cwd() / ".test-appdata" / "custom"
    with patch.object(config_manager, "CONFIG_DIR", custom_dir):
        assert browser_cookie.default_user_data_dir("profile") == str(custom_dir / "profile")
        assert MiMoProvider.default_user_data_dir() == str(custom_dir / "mimo-chrome")


def test_required_cookie_validation_rejects_expired_values():
    names = ("api-platform_ph", "api-platform_serviceToken", "api-platform_slh", "userId")
    cookies = [
        {"name": name, "value": f"value-{name}", "domain": ".platform.xiaomimimo.com", "expires": 900}
        for name in names
    ]

    assert not browser_cookie.has_valid_required_cookies(
        cookies,
        allowed_domains=("xiaomimimo.com",),
        cookie_names=names,
        now=1_000,
    )

    for cookie in cookies:
        cookie["expires"] = 1_100
    assert browser_cookie.has_valid_required_cookies(
        cookies,
        allowed_domains=("xiaomimimo.com",),
        cookie_names=names,
        now=1_000,
    )


def test_required_cookie_validation_rejects_unix_epoch_expiration():
    cookies = [
        {
            "name": "api-platform_serviceToken",
            "value": "token",
            "domain": ".xiaomimimo.com",
            "expires": 0,
        }
    ]

    assert not browser_cookie.has_valid_required_cookies(
        cookies,
        allowed_domains=("xiaomimimo.com",),
        cookie_names=("api-platform_serviceToken",),
        now=1_000,
    )


def test_required_cookie_validation_requires_every_named_cookie():
    cookies = [
        {"name": "api-platform_serviceToken", "value": "token", "domain": ".xiaomimimo.com", "expires": -1},
    ]

    assert not browser_cookie.has_valid_required_cookies(
        cookies,
        allowed_domains=("xiaomimimo.com",),
        cookie_names=("api-platform_serviceToken", "userId"),
        now=1_000,
    )
