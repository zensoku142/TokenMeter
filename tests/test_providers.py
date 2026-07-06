import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ["APPDATA"] = str(Path.cwd() / ".test-appdata")

from api.deepseek import APIError
from api.providers.deepseek import DeepSeekProvider
from api.providers.mimo import MiMoProvider


def response(payload, status=200):
    result = Mock()
    result.status_code = status
    result.ok = 200 <= status < 400
    result.json.return_value = payload
    return result


class MiMoProviderTests(unittest.TestCase):
    def config(self, key, default=None):
        return {
            "MIMO_COOKIE": "api-platform_serviceToken=test; userId=1",
            "MIMO_API_PLATFORM_PH": "",
            "MIMO_BASE": "https://platform.xiaomimimo.com",
        }.get(key, default)

    @patch("api.providers.mimo.config_manager.get")
    def test_real_usage_shape_reports_month_and_remaining_tokens(self, get):
        get.side_effect = self.config
        provider = MiMoProvider()
        provider._session.get = Mock(return_value=response({
            "code": 0,
            "data": {
                "monthUsage": {"items": [
                    {"name": "month_total_token", "used": 8_020_433_896, "limit": 11_000_000_000}
                ]},
                "usage": {"items": [
                    {"name": "plan_total_token", "used": 4_841_862_467, "limit": 11_000_000_000},
                    {"name": "compensation_total_token", "used": 10, "limit": 10},
                ]},
            },
        }))

        balance, balance_error = provider.fetch_balance()
        summary, summary_error = provider.fetch_summary()

        self.assertIsNone(balance_error)
        self.assertIsNone(summary_error)
        self.assertIsNone(balance.amount)
        self.assertEqual(balance.token_estimate, 6_158_137_533)
        self.assertEqual(summary.month_tokens, 8_020_433_896)
        self.assertIsNone(summary.month_cost)
        self.assertEqual(provider._session.get.call_count, 1)

    @patch("api.providers.mimo.config_manager.get")
    def test_body_auth_error_is_not_reported_as_zero(self, get):
        get.side_effect = self.config
        provider = MiMoProvider()
        provider._session.get = Mock(return_value=response({"code": 401, "data": None}))
        balance, error = provider.fetch_balance()
        self.assertIsNone(balance)
        self.assertEqual(error.code, "AUTH_EXPIRED")

    @patch("api.providers.mimo.config_manager.get", return_value="")
    def test_missing_cookie_does_not_send_request(self, _get):
        provider = MiMoProvider()
        provider._session.get = Mock()
        self.assertFalse(provider.is_configured())
        provider._session.get.assert_not_called()


class DeepSeekProviderTests(unittest.TestCase):
    @patch("api.providers.deepseek.config_manager.get")
    @patch("api.providers.deepseek.platform_api.get_usage_cost")
    @patch("api.providers.deepseek.platform_api.get_usage_amount")
    def test_cost_failure_preserves_token_payload(self, amount, cost, get):
        get.side_effect = lambda key, default=None: {
            "DEEPSEEK_AUTH": "Bearer test",
            "DEEPSEEK_COOKIE": "",
        }.get(key, default)
        amount.return_value = {
            "days": [{"date": "2026-07-05", "data": [{
                "model": "deepseek-v4-pro",
                "usage": [
                    {"type": "PROMPT_CACHE_HIT_TOKEN", "amount": "2"},
                    {"type": "PROMPT_CACHE_MISS_TOKEN", "amount": "3"},
                    {"type": "RESPONSE_TOKEN", "amount": "4"},
                ],
            }]}],
        }
        cost.side_effect = APIError("NETWORK_TIMEOUT", "cost", "连接超时")
        payloads, errors = DeepSeekProvider().fetch_payloads([(7, 2026)])
        usages = payloads[0]["days"][0]["data"][0]["usage"]
        self.assertEqual(sum(row["amount"] for row in usages), 9)
        self.assertEqual(errors[0].code, "NETWORK_TIMEOUT")

    @patch("api.providers.deepseek.config_manager.get")
    @patch("api.providers.deepseek.platform_api.get_usage_cost")
    @patch("api.providers.deepseek.platform_api.get_usage_amount")
    def test_cost_response_is_mapped_to_cost_not_tokens(self, amount, cost, get):
        get.side_effect = lambda key, default=None: {
            "DEEPSEEK_AUTH": "Bearer test",
            "DEEPSEEK_COOKIE": "",
        }.get(key, default)
        amount.return_value = {"days": []}
        cost.return_value = {
            "days": [{"date": "2026-07-05", "data": [{
                "model": "deepseek-v4-pro",
                "usage": [
                    {"type": "PROMPT_CACHE_HIT_TOKEN", "amount": "0.01"},
                    {"type": "RESPONSE_TOKEN", "amount": "0.02"},
                ],
            }]}],
        }
        payloads, errors = DeepSeekProvider().fetch_payloads([(7, 2026)])
        self.assertEqual(errors, [])
        usage = payloads[0]["days"][0]["data"][0]["usage"]
        self.assertEqual(usage, [{"type": "cost_cny", "amount": "0.03"}])


if __name__ == "__main__":
    unittest.main()
