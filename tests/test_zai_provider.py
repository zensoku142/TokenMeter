import unittest
from unittest.mock import Mock

import requests

from api.providers.zai import ZaiProvider


class ZaiProviderTests(unittest.TestCase):
    def setUp(self):
        self.provider = ZaiProvider({"ZAI_API_KEY": "example-secret"})
        self.addCleanup(self.provider.close)
        self.response = Mock(status_code=200)
        self.provider._session.get = Mock(return_value=self.response)

    def limits(self, entries):
        self.response.json.return_value = {
            "success": True,
            "code": 200,
            "data": {"limits": entries},
        }

    def test_reads_known_percentages_without_guessing_units_or_reset(self):
        self.limits(
            [
                {
                    "type": "TOKENS_LIMIT",
                    "percentage": 25,
                    "unit": 3,
                    "number": 5,
                    "nextResetTime": 999,
                },
                {"type": "TOKENS_LIMIT", "percentage": 80.5, "unit": 6, "number": 1},
                {"type": "TIME_LIMIT", "percentage": 10, "usage": 100},
            ]
        )
        quota, error = self.provider.fetch_quota()
        self.assertIsNone(error)
        self.assertEqual([window.used_percent for window in quota.windows], [25, 80.5, 10])
        self.assertEqual(
            [window.title for window in quota.windows], ["套餐额度 1", "套餐额度 2", "工具调用额度"]
        )
        self.assertTrue(
            all(
                window.window_minutes is None and window.resets_at is None
                for window in quota.windows
            )
        )
        self.assertEqual(self.provider.fetch_balance(), (None, None))
        self.assertEqual(self.provider.fetch_summary(), (None, None))
        call = self.provider._session.get.call_args
        self.assertEqual(call.args[0], "https://api.z.ai/api/monitor/usage/quota/limit")
        self.assertEqual(call.kwargs["headers"]["Authorization"], "example-secret")
        self.assertFalse(call.kwargs["allow_redirects"])

    def test_domestic_api_root_is_supported(self):
        self.provider._config = {"ZAI_API_KEY": "domestic", "ZAI_BASE": "https://open.bigmodel.cn/"}
        self.limits([{"type": "TOKENS_LIMIT", "percentage": 0}])
        self.assertIsNone(self.provider.fetch_quota()[1])
        self.assertEqual(
            self.provider._session.get.call_args.args[0],
            "https://open.bigmodel.cn/api/monitor/usage/quota/limit",
        )

    def test_rejects_insecure_and_ambiguous_base_before_sending_key(self):
        for base in (
            "http://api.z.ai",
            "https://api.z.ai/api/anthropic",
            "https://api.z.ai?secret=x",
            "https://api.z.ai#fragment",
            "https://user:password@api.z.ai",
            "https://api.z.ai:invalid",
            "https://[invalid",
        ):
            with self.subTest(base=base):
                self.provider._config = {"ZAI_API_KEY": "secret", "ZAI_BASE": base}
                self.assertEqual(self.provider.fetch_quota()[1].code, "INVALID_CONFIG")
        self.provider._session.get.assert_not_called()

    def test_unconfigured_does_not_request(self):
        self.provider._config = {}
        self.assertEqual(self.provider.fetch_quota()[1].code, "NOT_CONFIGURED")
        self.provider._session.get.assert_not_called()

    def test_http_failure_and_redirect_are_not_success(self):
        for status, code in (
            (301, "INVALID_RESPONSE"),
            (401, "AUTH_EXPIRED"),
            (403, "AUTH_EXPIRED"),
            (429, "RATE_LIMITED"),
            (503, "SERVER_ERROR"),
        ):
            with self.subTest(status=status):
                self.response.status_code = status
                quota, error = self.provider.fetch_quota()
                self.assertIsNone(quota)
                self.assertEqual(error.code, code)
        self.response.json.assert_not_called()

    def test_network_failures_are_redacted(self):
        for failure, code in (
            (requests.Timeout("example-secret"), "NETWORK_TIMEOUT"),
            (requests.ConnectionError("example-secret"), "NETWORK_ERROR"),
        ):
            with self.subTest(code=code):
                self.provider._session.get.side_effect = failure
                quota, error = self.provider.fetch_quota()
                self.assertIsNone(quota)
                self.assertEqual(error.code, code)
                self.assertNotIn("example-secret", error.message)

    def test_missing_and_unknown_types_have_explicit_unavailable_result(self):
        for entries in (
            [],
            [{"type": "CREDIT_LIMIT", "percentage": 25}],
            [{"type": "UNKNOWN", "percentage": 50}],
        ):
            with self.subTest(entries=entries):
                self.limits(entries)
                quota, error = self.provider.fetch_quota()
                self.assertIsNone(quota)
                self.assertEqual(error.code, "QUOTA_UNAVAILABLE")

    def test_unknown_types_do_not_hide_known_quota_or_claim_full_support(self):
        self.limits(
            [{"type": "TOKENS_LIMIT", "percentage": 0}, {"type": "CREDIT_LIMIT", "percentage": 90}]
        )
        quota, error = self.provider.fetch_quota()
        self.assertIsNone(error)
        self.assertEqual(len(quota.windows), 1)
        self.assertEqual(quota.windows[0].used_percent, 0)
        self.assertIn("暂不支持", quota.metrics[0].value)

    def test_zero_tool_entitlement_does_not_create_window(self):
        self.limits([{"type": "TIME_LIMIT", "percentage": 0, "usage": 0}])
        self.assertEqual(self.provider.fetch_quota()[1].code, "QUOTA_UNAVAILABLE")

    def test_invalid_percentages_are_not_coerced_to_zero(self):
        for value in (None, True, "25", -1, 101, float("nan"), float("inf"), 10**1000):
            with self.subTest(value=value):
                self.limits([{"type": "TOKENS_LIMIT", "percentage": value}])
                quota, error = self.provider.fetch_quota()
                self.assertIsNone(quota)
                self.assertEqual(error.code, "INVALID_RESPONSE")

    def test_invalid_envelope_and_limit_entries_are_rejected(self):
        for payload in (
            None,
            [],
            {"data": {}},
            {"data": {"limits": None}},
            {"data": {"limits": [None]}},
            {"success": "true", "data": {"limits": []}},
            {"code": True, "data": {"limits": []}},
        ):
            with self.subTest(payload=payload):
                self.response.json.return_value = payload
                self.assertEqual(self.provider.fetch_quota()[1].code, "INVALID_RESPONSE")

    def test_business_failure_is_not_successful_quota(self):
        for payload in (
            {"success": False},
            {"code": 401},
            {"error": {"message": "example-secret"}},
        ):
            with self.subTest(payload=payload):
                self.response.json.return_value = payload
                quota, error = self.provider.fetch_quota()
                self.assertIsNone(quota)
                self.assertEqual(error.code, "API_ERROR")
                self.assertNotIn("example-secret", error.message)


if __name__ == "__main__":
    unittest.main()
