import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

os.environ["APPDATA"] = str(Path.cwd() / ".test-appdata")

from api import deepseek
from api import deepseek_official


def platform_config(key, default=None):
    return {
        "DEEPSEEK_AUTH": "Bearer test-token",
        "DEEPSEEK_COOKIE": "",
        "DEEPSEEK_BASE": "https://platform.deepseek.com",
    }.get(key, default)


def response(status=200, payload=None, content_type="application/json"):
    result = Mock()
    result.status_code = status
    result.ok = 200 <= status < 400
    result.headers = {"Content-Type": content_type}
    result.json.return_value = payload
    return result


class PlatformApiTests(unittest.TestCase):
    def setUp(self):
        config = patch.object(deepseek.config_manager, "get", side_effect=platform_config)
        config.start()
        self.addCleanup(config.stop)

    def test_retry_returns_final_status_for_classification(self):
        adapter = deepseek._SESSION.get_adapter("https://")
        self.assertFalse(adapter.max_retries.raise_on_status)

    def test_rate_limit_is_not_retried(self):
        adapter = deepseek._SESSION.get_adapter("https://")
        self.assertNotIn(429, adapter.max_retries.status_forcelist)
        self.assertFalse(adapter.max_retries.respect_retry_after_header)
        self.assertFalse(adapter.max_retries.is_retry("GET", 429, has_retry_after=True))

    def test_platform_compatibility_headers_are_preserved(self):
        headers = deepseek._headers()
        self.assertIn("Chrome/147", headers["user-agent"])
        self.assertEqual(headers["x-app-version"], "20240425.0")
        self.assertEqual(headers["x-client-platform"], "web")
        self.assertEqual(headers["sec-ch-ua-platform"], '"Windows"')

    def test_raw_platform_token_is_sent_as_bearer(self):
        with patch.object(
            deepseek.config_manager,
            "get",
            side_effect=lambda key, default=None: (
                "raw-platform-token" if key == "DEEPSEEK_AUTH" else platform_config(key, default)
            ),
        ):
            self.assertEqual(deepseek._headers()["authorization"], "Bearer raw-platform-token")

    def test_copied_authorization_header_is_normalized(self):
        self.assertEqual(
            deepseek.normalize_authorization("Authorization: Bearer platform-token"),
            "Bearer platform-token",
        )

    def test_local_storage_token_json_is_normalized(self):
        self.assertEqual(
            deepseek.normalize_authorization('{"value":"jwt-platform-token"}'),
            "Bearer jwt-platform-token",
        )

    @patch.object(deepseek._SESSION, "get")
    def test_valid_response_and_query_params(self, get):
        get.return_value = response(200, {"data": {"biz_data": {"series": []}}})
        self.assertEqual(deepseek.get_usage_amount(7, 2026), {"days": []})
        self.assertIn("/api/v0/usage/by_api_key/amount", get.call_args.args[0])
        self.assertEqual(set(get.call_args.kwargs["params"]), {"start", "end", "tz"})

    @patch.object(deepseek, "_month_range", return_value=(1785513600, 1788192000, 28800))
    @patch.object(deepseek._SESSION, "get")
    def test_new_amount_response_is_normalized(self, get, _range):
        get.return_value = response(200, {"data": {"biz_data": {"series": [{
            "api_key": {"tracking_id": "secret"},
            "model": "deepseek-v4-pro",
            "buckets": [{"time": 1785513600, "usage": {
                "PROMPT_CACHE_HIT_TOKEN": "2",
                "PROMPT_CACHE_MISS_TOKEN": "3",
                "RESPONSE_TOKEN": "4",
                "REQUEST": "1",
            }}],
        }]}}})

        result = deepseek.get_usage_amount(8, 2026)

        self.assertEqual(result["days"][0]["date"], "2026-08-01")
        self.assertEqual(result["days"][0]["data"][0]["model"], "deepseek-v4-pro")
        self.assertEqual(
            [item["type"] for item in result["days"][0]["data"][0]["usage"]],
            ["PROMPT_CACHE_HIT_TOKEN", "PROMPT_CACHE_MISS_TOKEN", "RESPONSE_TOKEN"],
        )

    @patch.object(deepseek, "_month_range", return_value=(1785513600, 1788192000, 28800))
    @patch.object(deepseek._SESSION, "get")
    def test_new_cost_response_is_normalized(self, get, _range):
        get.return_value = response(200, {"data": {"biz_data": {"data": [{
            "currency": "CNY",
            "series": [{
                "model": "deepseek-v4-flash",
                "buckets": [{"time": 1785513600, "cost": "1.85"}],
            }],
        }]}}})

        result = deepseek.get_usage_cost(8, 2026)

        self.assertEqual(
            result["days"][0]["data"][0]["usage"],
            [{"type": "cost_cny", "amount": "1.85"}],
        )

    @patch.object(deepseek._SESSION, "get")
    def test_application_auth_error_is_classified(self, get):
        get.return_value = response(200, {"code": 40003, "msg": "Authorization Failed"})
        with self.assertRaises(deepseek.APIError) as caught:
            deepseek.get_user_summary()
        self.assertEqual(caught.exception.code, "AUTH_EXPIRED")

    @patch.object(deepseek._SESSION, "get")
    def test_auth_error_is_classified(self, get):
        get.return_value = response(401, {})
        with self.assertRaises(deepseek.APIError) as caught:
            deepseek.get_user_summary()
        self.assertEqual(caught.exception.code, "AUTH_EXPIRED")

    @patch.object(deepseek._SESSION, "get")
    def test_non_json_response_is_rejected(self, get):
        get.return_value = response(200, {}, "text/html")
        with self.assertRaises(deepseek.APIError) as caught:
            deepseek.get_user_summary()
        self.assertEqual(caught.exception.code, "INVALID_RESPONSE")

    @patch.object(deepseek._SESSION, "get")
    def test_html_429_is_classified_as_platform_block(self, get):
        get.return_value = response(429, {}, "text/html; charset=utf-8")
        with self.assertRaises(deepseek.APIError) as caught:
            deepseek.get_user_summary()
        self.assertEqual(caught.exception.code, "PLATFORM_BLOCKED")

    @patch.object(deepseek._SESSION, "get", side_effect=requests.Timeout())
    def test_timeout_is_classified(self, _get):
        with self.assertRaises(deepseek.APIError) as caught:
            deepseek.get_user_summary()
        self.assertEqual(caught.exception.code, "NETWORK_TIMEOUT")

    @patch.object(deepseek._SESSION, "get")
    def test_missing_auth_is_rejected_before_request(self, get):
        with patch.object(deepseek.config_manager, "get", return_value=""):
            with self.assertRaises(deepseek.APIError) as caught:
                deepseek.get_user_summary()
        self.assertEqual(caught.exception.code, "NOT_CONFIGURED")
        get.assert_not_called()


class OfficialApiTests(unittest.TestCase):
    @patch("api.deepseek_official.config_manager.get", return_value="fake-key")
    @patch.object(deepseek_official._SESSION, "get")
    def test_balance_uses_bearer_api_key(self, get, _config):
        get.return_value = response(200, {"balance_infos": []})
        deepseek_official.get_balance()
        self.assertEqual(get.call_args.kwargs["headers"]["authorization"], "Bearer fake-key")


if __name__ == "__main__":
    unittest.main()
