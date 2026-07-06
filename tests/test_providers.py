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

        def balance_response(url, **kwargs):
            return response({
                "code": 0,
                "data": {
                    "balance": "124.07",
                    "frozenBalance": "0.00",
                    "currency": "CNY",
                    "giftBalance": "124.07",
                    "cashBalance": "0.00",
                },
            })

        def usage_response(url, **kwargs):
            return response({
                "code": 0,
                "data": {
                    "tokenUsage": {
                        "inputToken": 551046842,
                        "outputToken": 1503444,
                        "cacheToken": 544156672,
                        "totalToken": 552550286,
                    },
                    "costUsage": {
                        "totalCost": "43.30",
                        "currentMonthCost": "16.17",
                    },
                },
            })

        def detail_response(url, **kwargs):
            return response({
                "code": 0,
                "data": [
                    {
                        "date": "2026-07-04",
                        "model": "mimo-v2.5-pro",
                        "consumedAmount": "9.280356",
                        "inputHitToken": 62931840,
                        "inputMissToken": 2216402,
                        "outputToken": 176309,
                        "totalToken": 65324551,
                    },
                    {
                        "date": "2026-07-03",
                        "model": "mimo-v2.5-pro",
                        "consumedAmount": "6.647988",
                        "inputHitToken": 107101312,
                        "inputMissToken": 991771,
                        "outputToken": 165857,
                        "totalToken": 108258940,
                    },
                ],
            })

        def dispatcher(url, **kwargs):
            if "/api/v1/balance" in url:
                return balance_response(url, **kwargs)
            if "/api/v1/usage/detail/list" in url:
                return detail_response(url, **kwargs)
            return usage_response(url, **kwargs)

        provider._session.get = Mock(side_effect=dispatcher)
        provider._session.post = Mock(side_effect=dispatcher)

        balance, balance_error = provider.fetch_balance()
        summary, summary_error = provider.fetch_summary()
        payloads, payload_errors = provider.fetch_payloads([(7, 2026)])

        self.assertIsNone(balance_error)
        self.assertIsNone(summary_error)
        self.assertEqual(payload_errors, [])
        # 余额：账户余额来自 balance.balance，单位 CNY
        self.assertEqual(str(balance.amount), "124.07")
        self.assertEqual(balance.currency, "CNY")
        # 月度用量：来自 tokenUsage.totalToken
        self.assertEqual(summary.month_tokens, 552550286)
        self.assertEqual(str(summary.month_cost), "16.17")
        # 日明细：确认拿到了 2 天数据且 token/费用字段齐全
        self.assertTrue(payloads and payloads[0]["days"])
        first_day = payloads[0]["days"][0]
        usage_types = {u["type"] for u in first_day["data"][0]["usage"]}
        self.assertIn("PROMPT_CACHE_HIT_TOKEN", usage_types)
        self.assertIn("PROMPT_CACHE_MISS_TOKEN", usage_types)
        self.assertIn("RESPONSE_TOKEN", usage_types)
        self.assertIn("cost_cny", usage_types)

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
        provider._session.post = Mock()
        self.assertFalse(provider.is_configured())
        balance, _ = provider.fetch_balance()
        self.assertIsNone(balance)
        provider._session.get.assert_not_called()
        provider._session.post.assert_not_called()


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
