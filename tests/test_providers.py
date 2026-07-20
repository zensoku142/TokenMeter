import os
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ["APPDATA"] = str(Path.cwd() / ".test-appdata")

from api.deepseek import APIError
from api.providers.deepseek import DeepSeekProvider
from api.providers.mimo import MiMoProvider
import config_manager


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
        with patch.object(
            MiMoProvider, "_fetch_browser_context", side_effect=RuntimeError("BROWSER_NOT_READY")
        ):
            balance, error = provider.fetch_balance()
        self.assertIsNone(balance)
        self.assertEqual(error.code, "AUTH_EXPIRED")

    @patch("api.providers.mimo.config_manager.get")
    def test_auth_error_uses_verified_browser_context_without_persisting_it(self, get):
        get.side_effect = self.config
        provider = MiMoProvider()
        provider._session.get = Mock(return_value=response({"code": 401, "data": None}))
        browser_context = Mock(
            data={"balance": "12.5", "currency": "CNY"},
            cookie="session=fresh; api-platform_ph=ph",
            api_platform_ph="ph",
        )

        with patch.object(MiMoProvider, "_fetch_browser_context", return_value=browser_context) as recover:
            balance, error = provider.fetch_balance()

        self.assertIsNone(error)
        self.assertEqual(str(balance.amount), "12.5")
        recover.assert_called_once_with(path="/api/v1/balance", body=None, base_url="https://platform.xiaomimimo.com")
        self.assertEqual(provider._browser_cookie, "session=fresh; api-platform_ph=ph")
        self.assertEqual(provider._browser_api_platform_ph, "ph")

    def test_manual_mimo_collection_keeps_all_first_party_cookie_names(self):
        with patch("api.providers.mimo.browser_cookie.acquire_cookie_via_chrome") as acquire:
            acquire.return_value = "session=fresh"
            MiMoProvider.acquire_cookie_via_chrome(threading.Event())

        self.assertIsNone(acquire.call_args.kwargs["cookie_names"])

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
    def provider_config(self):
        return {
            "DEEPSEEK_API_KEY": "",
            "DEEPSEEK_AUTH": "Bearer test",
            "DEEPSEEK_COOKIE": "session=test",
        }

    @patch("api.providers.deepseek.official_api.build_session")
    @patch("api.providers.deepseek.platform_api.build_session")
    def test_settings_provider_instances_do_not_share_sessions(
        self, build_platform_session, build_official_session
    ):
        platform_sessions = [Mock(), Mock()]
        official_sessions = [Mock(), Mock()]
        build_platform_session.side_effect = platform_sessions
        build_official_session.side_effect = official_sessions

        first = DeepSeekProvider({"ACTIVE_PROVIDER": "deepseek"})
        second = DeepSeekProvider({"ACTIVE_PROVIDER": "deepseek"})

        self.assertIs(first._platform_session, platform_sessions[0])
        self.assertIs(second._platform_session, platform_sessions[1])
        self.assertIsNot(first._platform_session, second._platform_session)
        self.assertIsNot(first._official_session, second._official_session)

    @patch("api.providers.deepseek.official_api.build_session")
    @patch("api.providers.deepseek.platform_api.build_session")
    def test_close_releases_both_sessions(
        self, build_platform_session, build_official_session
    ):
        platform_session = Mock()
        official_session = Mock()
        build_platform_session.return_value = platform_session
        build_official_session.return_value = official_session

        provider = DeepSeekProvider()
        provider.close()

        platform_session.close.assert_called_once_with()
        official_session.close.assert_called_once_with()

    @patch("api.providers.deepseek.platform_api.get_user_summary")
    def test_summary_cache_is_shared_only_within_one_refresh(self, get_summary):
        get_summary.return_value = {
            "normal_wallets": [
                {"currency": "CNY", "balance": "8", "token_estimation": 10}
            ],
            "monthly_costs": [{"amount": "2"}],
            "monthly_token_usage": 30,
        }
        provider = DeepSeekProvider(self.provider_config())
        try:
            provider.reset_refresh_cache()
            provider.fetch_balance()
            provider.fetch_summary()
            self.assertEqual(get_summary.call_count, 1)

            provider.reset_refresh_cache()
            provider.fetch_summary()
            self.assertEqual(get_summary.call_count, 2)
        finally:
            provider.close()

    @patch("api.providers.deepseek.platform_api.get_user_summary")
    def test_previous_summary_error_does_not_pollute_next_refresh(self, get_summary):
        get_summary.side_effect = [
            APIError("NETWORK_TIMEOUT", "summary", "timeout"),
            {"monthly_costs": [], "monthly_token_usage": 7},
        ]
        provider = DeepSeekProvider(self.provider_config())
        try:
            provider.reset_refresh_cache()
            first, first_error = provider.fetch_summary()
            provider.reset_refresh_cache()
            second, second_error = provider.fetch_summary()
        finally:
            provider.close()

        self.assertIsNone(first)
        self.assertEqual(first_error.code, "NETWORK_TIMEOUT")
        self.assertEqual(second.month_tokens, 7)
        self.assertIsNone(second_error)

    @patch("api.providers.deepseek.config_manager.logger")
    @patch("api.providers.deepseek.platform_api.get_user_summary")
    @patch("api.providers.deepseek.official_api.get_balance")
    def test_official_balance_failure_returns_web_fallback_warning(
        self, get_official_balance, get_summary, logger
    ):
        config = self.provider_config()
        config["DEEPSEEK_API_KEY"] = "sk-test"
        get_official_balance.side_effect = APIError(
            "AUTH_EXPIRED", "balance", "expired"
        )
        get_summary.return_value = {
            "normal_wallets": [
                {"currency": "CNY", "balance": "12.5", "token_estimation": 4}
            ]
        }
        provider = DeepSeekProvider(config)
        try:
            balance, warning = provider.fetch_balance()
        finally:
            provider.close()

        self.assertEqual(str(balance.amount), "12.5")
        self.assertEqual(warning.code, "OFFICIAL_BALANCE_FALLBACK")
        logged = str(logger.mock_calls)
        self.assertNotIn("sk-test", logged)
        self.assertNotIn("Bearer test", logged)
        self.assertNotIn("session=test", logged)

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
        self.assertFalse(payloads[0]["_complete"])
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


    @patch("api.providers.mimo.config_manager.get")
    def test_ph_is_extracted_from_cookie_and_appended_to_url(self, get):
        ph = "kmi9pTH8JkU4%2FTg3Yjo8Yw%3D%3D"
        cookie = f"api-platform_serviceToken=test; userId=1; api-platform_ph={ph}"

        def cfg(key, default=None):
            return {
                "MIMO_COOKIE": cookie,
                "MIMO_API_PLATFORM_PH": "",
                "MIMO_BASE": "https://platform.xiaomimimo.com",
            }.get(key, default)

        get.side_effect = cfg
        provider = MiMoProvider()
        self.assertEqual(provider.extract_cookie_value(cookie, "api-platform_ph"), ph)
        # 请求头里 cookie 保持原样，不会重复注入 ph
        headers = provider._platform_headers()
        self.assertIn(f"api-platform_ph={ph}", headers["cookie"])
        self.assertIn("api-platform_ph=" + ph, provider._url("/api/v1/balance"))

    @patch("api.providers.mimo.config_manager.get")
    def test_ph_falls_back_to_credential_when_missing_from_cookie(self, get):
        ph = "fallback-ph=="

        def cfg(key, default=None):
            return {
                "MIMO_COOKIE": "api-platform_serviceToken=test; userId=1",
                "MIMO_API_PLATFORM_PH": ph,
                "MIMO_BASE": "https://platform.xiaomimimo.com",
            }.get(key, default)

        get.side_effect = cfg
        provider = MiMoProvider()
        self.assertEqual(provider.extract_cookie_value("xxx", "api-platform_ph"), "")
        headers = provider._platform_headers()
        # 注入后的 cookie 中应包含 ``api-platform_ph``
        self.assertIn("api-platform_ph=", headers["cookie"])
        # URL 同样会带上 ph
        self.assertIn("api-platform_ph=" + ph, provider._url("/api/v1/usage"))

    def test_cookie_normalization_squeezes_whitespace(self):
        raw = "a=1;\n b=2;\nc=3"
        normalized = MiMoProvider.normalize_cookie(raw)
        self.assertEqual(normalized, "a=1; b=2; c=3")
        # 双引号包裹的值会被正确抽取
        self.assertEqual(
            MiMoProvider.extract_cookie_value('api-platform_ph="abc=="; userId=1', "api-platform_ph"),
            "abc==",
        )


    @patch("api.browser_cookie.find_chrome_executable")
    def test_acquire_cookie_fails_gracefully_when_no_chrome(self, find_executable: Mock) -> None:
        find_executable.return_value = ""
        import threading

        with self.assertRaises(RuntimeError):
            MiMoProvider.acquire_cookie_via_chrome(threading.Event())

    def test_cookie_helper_handles_multiple_formats(self) -> None:
        # 1) 单行 name=value; name2=value2
        self.assertEqual(
            MiMoProvider.extract_cookie_value(
                "a=1; api-platform_ph=some-token; c=3", "api-platform_ph"
            ),
            "some-token",
        )
        # 2) 换行与多余空白
        self.assertEqual(
            MiMoProvider.extract_cookie_value("a=1;\n api-platform_ph=xxx;\nc=3", "api-platform_ph"),
            "xxx",
        )
        # 3) 被双引号括住
        self.assertEqual(
            MiMoProvider.extract_cookie_value('api-platform_ph="abc=="; userId=1', "api-platform_ph"),
            "abc==",
        )
        # 4) 不包含字段时返回空串
        self.assertEqual(
            MiMoProvider.extract_cookie_value("userId=1; other=2", "api-platform_ph"),
            "",
        )

    def test_normalize_cookie_squeezes_whitespace(self) -> None:
        self.assertEqual(
            MiMoProvider.normalize_cookie("a=1; \n b=2 ; \n c=3"),
            "a=1; b=2; c=3",
        )
        self.assertEqual(MiMoProvider.normalize_cookie(""), "")

    @patch("api.providers.mimo.MiMoProvider.acquire_cookie_via_chrome")
    def test_error_message_lookup(self, acquire_mock: Mock) -> None:
        acquire_mock.side_effect = RuntimeError("CHROME_NOT_FOUND")
        try:
            acquire_mock()
        except RuntimeError as exc:
            message = MiMoProvider.describe_acquire_error(exc)
            self.assertIn("Chrome", message)

    def test_cdp_prefers_mimo_url_over_others(self) -> None:
        """``_pick_websocket_endpoint`` 应优先选择 MiMo 域名下的 target。"""
        fake_response = [
            {
                "type": "background_page",
                "url": "chrome-extension://abc/background.html",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9288/devtools/page/A",
            },
            {
                "type": "page",
                "url": "https://platform.xiaomimimo.com/console/usage",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9288/devtools/page/B",
            },
            {
                "type": "other",
                "url": "https://example.com",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9288/devtools/page/C",
            },
        ]

        captured: list[str] = []

        def fake_http(host: str, port: int, path: str, timeout: float) -> object:  # noqa: ARG001
            captured.append(path)
            # 只有 ``/json`` 返回目标列表；其他路径 ``/json/version`` 也应返回合法 JSON。
            if path == "/json":
                return fake_response
            return {"Browser": "Chrome/149"}

        with patch.object(MiMoProvider, "_http_json", side_effect=fake_http):
            got = MiMoProvider._pick_websocket_endpoint(9288)
        self.assertEqual(got, "ws://127.0.0.1:9288/devtools/page/B")
        self.assertIn("/json", captured)

    def test_cdp_falls_back_to_first_acceptable_target(self) -> None:
        """如果列表中没有 MiMo 域名，回退到第一个可用 target 而不是直接报错。"""
        fake_response = [
            {
                "type": "background_page",
                "url": "chrome-extension://abc/background.html",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9288/devtools/page/A",
            },
            {"type": "service_worker", "webSocketDebuggerUrl": "ws://127.0.0.1:9288/devtools/page/SW"},
        ]

        def fake_http(host: str, port: int, path: str, timeout: float) -> object:  # noqa: ARG001
            return fake_response

        with patch.object(MiMoProvider, "_http_json", side_effect=fake_http):
            got = MiMoProvider._pick_websocket_endpoint(9288)
        # ``service_worker`` 不在可接受集合内，只能取 background_page。
        self.assertEqual(got, "ws://127.0.0.1:9288/devtools/page/A")

    def test_cdp_raises_when_no_suitable_target(self) -> None:
        """所有条目都没有 webSocketDebuggerUrl 时，应抛 ``CDP_NO_PAGE_TARGET``。"""
        fake_response = [
            {"type": "page", "url": "https://example.com"},
            {"type": "other", "url": "about:blank"},
        ]

        def fake_http(host: str, port: int, path: str, timeout: float) -> object:  # noqa: ARG001
            return fake_response

        with patch.object(MiMoProvider, "_http_json", side_effect=fake_http):
            with self.assertRaises(RuntimeError) as ctx:
                MiMoProvider._pick_websocket_endpoint(9288)
        self.assertEqual(str(ctx.exception), "CDP_NO_PAGE_TARGET")

    def test_cdp_send_text_handles_broken_ws_url(self) -> None:
        """``_cdp_send_text`` 不应因非法 URL 或 socket 问题抛异常。"""
        # 非法 scheme 不会建立连接；方法应安静返回。
        MiMoProvider._cdp_send_text("http://127.0.0.1:1", {"id": 1, "method": "Browser.close"})
        # 端口不可用；``socket.create_connection`` 抛 OSError 被内部吞掉。
        MiMoProvider._cdp_send_text(
            "ws://127.0.0.1:1/devtools/page/0000", {"id": 1, "method": "Browser.close"}
        )

    def test_format_cookie_string_relaxes_domain_and_strips_quotes(self) -> None:
        """``_format_cookie_string`` 必须能识别 ``xiaomimimo.com`` 任意子域，
        并把 ``api-platform_ph`` 引号去掉；否则会导致请求头/URL 里
        出现 ``""...""`` 或被 domain 过滤掉。
        """
        cookies = [
            {"name": "api-platform_ph", "value": '"abc%2Fdef%3D123"', "domain": ".xiaomimimo.com"},
            {"name": "api-platform_serviceToken", "value": "token-value", "domain": "platform.xiaomimimo.com"},
            {"name": "userId", "value": "12345678", "domain": "xiaomimimo.com"},
            {"name": "_ga", "value": "GA1.2.0", "domain": "xiaomimimo.com"},  # 非关键字段，忽略
            {"name": "other_session", "value": "x", "domain": "other.example.com"},
        ]
        got = MiMoProvider._format_cookie_string(cookies)
        # api-platform_ph 必须去引号，并保留原本的百分编码。
        self.assertIn('api-platform_ph=abc%2Fdef%3D123', got)
        # serviceToken 和 userId 必须被包含（落在 ``xiaomimimo.com`` 子域上）。
        self.assertIn("api-platform_serviceToken=token-value", got)
        self.assertIn("userId=12345678", got)
        # 非关键字段不会出现在 cookie 中。
        self.assertNotIn("_ga=", got)
        self.assertNotIn("other_session=", got)

    def test_url_strips_quotes_from_api_platform_ph(self) -> None:
        """``_url`` 拼接 ``api-platform_ph`` 前应去掉外层引号，防止
        query 里出现 ``"`` 字符导致 404。
        """
        provider = MiMoProvider()

        class _ConfigCache(dict):
            def get(self, key, default=""):  # type: ignore[override]
                return super().get(key, default)

        fake = _ConfigCache(MIMO_COOKIE='a=1; api-platform_ph="xx/yy==zz"; userId=8')
        # 临时替换 ``config_manager`` 的读接口。
        original = config_manager.get
        try:
            config_manager.get = fake.get  # type: ignore[method-assign]
            url = provider._url("/api/v1/balance")
        finally:
            config_manager.get = original  # type: ignore[method-assign]
        self.assertTrue(url.startswith("https://platform.xiaomimimo.com/api/v1/balance?api-platform_ph="))
        # 不能出现双引号；原本的 "/" 和 "=" 必须保留。
        self.assertNotIn('"', url)
        self.assertIn("xx/yy==zz", url)


if __name__ == "__main__":
    unittest.main()
