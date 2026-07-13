import threading
import os
from pathlib import Path
from unittest.mock import patch

os.environ["APPDATA"] = str(Path.cwd() / ".test-appdata")

from api import browser_cookie
from api.providers.deepseek import DeepSeekProvider


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
