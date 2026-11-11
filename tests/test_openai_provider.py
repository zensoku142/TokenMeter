import json
import socket
import subprocess
import threading
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from api import browser_cookie
from api.providers import PROVIDERS, get_provider
from api.providers.openai import OpenAIProvider
from config import runtime as config_manager
from config.defaults import DEFAULT_CONFIG, FIELD_META, PROVIDER_IDS, SECRET_KEYS
from config.store import public_values

URL = "https://api.openai.com/v1/dashboard/billing/credit_grants"
AUTH = "Bearer synthetic-login-secret"
ORG = "org-synthetic-secret"
CONFIG = {"OPENAI_AUTH": AUTH, "OPENAI_ORGANIZATION": ORG}
HEADERS = {"authorization": AUTH, "openai-organization": ORG}


def response(payload=None, status=200):
    result = Mock(status_code=status)
    result.json.return_value = payload
    return result


@pytest.fixture(autouse=True)
def isolate_browser_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(config_manager, "CONFIG_DIR", tmp_path)


@pytest.fixture
def provider():
    instance = OpenAIProvider(dict(CONFIG))
    instance._session.get = Mock(return_value=response({"total_available": "24.8486408"}))
    yield instance
    instance.close()


def test_registry_and_secret_defaults_do_not_change_selection():
    assert "openai" in PROVIDERS and "openai" in PROVIDER_IDS
    assert DEFAULT_CONFIG["ACTIVE_PROVIDER"] == "deepseek"
    assert DEFAULT_CONFIG["BACKGROUND_PROVIDER_IDS"] == []
    assert DEFAULT_CONFIG["BACKGROUND_PROFILE_IDS"] == []
    instance = get_provider("openai", CONFIG)
    try:
        assert instance.name == "OpenAI API"
        assert instance.default_currency == "USD"
        assert instance.balance_label == "API 余额"
        assert not instance.supports_daily_usage
        assert not instance.supports_cost
        assert not instance.supports_subscription_quota
        assert instance.fetch_summary() == (None, None)
        assert instance.fetch_payloads([(11, 2026)]) == ([], [])
    finally:
        instance.close()
    for field, meta in OpenAIProvider.credential_fields.items():
        key = f"OPENAI_{field}"
        assert meta["secret"] and meta["browser_only"]
        assert DEFAULT_CONFIG[key] == ""
        assert key in SECRET_KEYS and FIELD_META[key]["secret"]
        assert key not in public_values({key: "secret"})
    assert OpenAIProvider.supports_browser_credential_acquisition
    assert OpenAIProvider.credential_acquisition_label
    assert OpenAIProvider.credential_acquisition_automatic is False


def test_balance_uses_only_fixed_get_and_captured_pair(provider):
    provider._config.update(OPENAI_BASE="https://untrusted.invalid", OPENAI_COOKIE="must-not-send")
    provider._session.cookies.set("session", "must-not-send")
    balance, error = provider.fetch_balance()
    assert error is None
    assert balance.currency == "USD"
    assert balance.amount == Decimal("24.8486408")
    assert f"{balance.amount:.2f}" == "24.85"
    provider._session.get.assert_called_once_with(
        URL,
        headers={"Authorization": AUTH, "OpenAI-Organization": ORG,
                 "Accept": "application/json", "Cookie": None},
        allow_redirects=False,
        timeout=(5, 15),
    )
    assert not provider._session.cookies
    assert provider._session.trust_env is True
    assert callable(provider._session.auth)


def test_prepared_request_never_sends_cookie_or_follows_redirect():
    instance = OpenAIProvider(CONFIG)
    instance._session.cookies.set("session", "must-not-send")
    instance._session.headers["Cookie"] = "also-must-not-send"
    adapter = Mock()
    redirected = requests.Response()
    redirected.status_code = 302
    redirected.url = URL
    redirected.headers["Location"] = "https://untrusted.invalid/collect"
    redirected._content = b"sensitive response"
    def send(request, **kwargs):
        redirected.request = request
        return redirected

    adapter.send.side_effect = send
    instance._session.mount("https://", adapter)
    try:
        balance, error = instance.fetch_balance()
        assert balance is None and error.code == "HTTP_ERROR"
        assert adapter.send.call_count == 1
        request = adapter.send.call_args.args[0]
        assert request.method == "GET" and request.url == URL
        assert request.headers["Authorization"] == AUTH
        assert request.headers["OpenAI-Organization"] == ORG
        assert "Cookie" not in request.headers
    finally:
        instance.close()


def test_captured_auth_preserves_environment_proxy_without_netrc_override(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7899")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7899")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    instance = OpenAIProvider(CONFIG)
    adapter = Mock()
    result = requests.Response()
    result.status_code = 200
    result.url = URL
    result._content = b'{"total_available":0}'
    adapter.send.return_value = result
    instance._session.mount("https://", adapter)
    try:
        with patch("requests.sessions.get_netrc_auth") as netrc:
            balance, error = instance.fetch_balance()
        assert error is None and balance.amount == 0
        netrc.assert_not_called()
        request = adapter.send.call_args.args[0]
        assert request.headers["Authorization"] == AUTH
        assert request.headers["OpenAI-Organization"] == ORG
        assert adapter.send.call_args.kwargs["proxies"]["https"] == "http://127.0.0.1:7899"
    finally:
        instance.close()


@pytest.mark.parametrize("value", [0, "0", "0.00", "-0.01", 24.8486408,
                                   Decimal("1.234567890123456789"), "1e-8"])
def test_preserves_valid_decimal_zero_and_negative_balance(provider, value):
    provider._session.get.return_value = response({"total_available": value, "total_paid_available": 99})
    balance, error = provider.fetch_balance()
    assert error is None
    assert balance.amount == Decimal(str(value))


def test_json_decimal_parsing_preserves_original_numeric_precision(provider):
    result = requests.Response()
    result.status_code = 200
    result._content = b'{"total_available":24.84864081234567890123456789,"total_paid_available":0}'
    provider._session.get.return_value = result
    balance, error = provider.fetch_balance()
    assert error is None
    assert balance.amount == Decimal("24.84864081234567890123456789")


@pytest.mark.parametrize("value", [None, "", " ", True, False, [], {}, "invalid", "NaN", "sNaN",
                                   "Infinity", "-Infinity", float("nan"), float("inf"),
                                   Decimal("NaN"), "1_000", "１２", "1\n2"])
def test_invalid_total_available_is_not_zero(provider, value):
    provider._session.get.return_value = response({"total_available": value, "total_paid_available": 1})
    balance, error = provider.fetch_balance()
    assert balance is None and error.code == "INVALID_RESPONSE"


@pytest.mark.parametrize("payload", [{}, {"total_paid_available": 5}, None, [], "invalid", 0])
def test_missing_or_invalid_structure_is_not_paid_credit_fallback(provider, payload):
    provider._session.get.return_value = response(payload)
    balance, error = provider.fetch_balance()
    assert balance is None and error.code == "INVALID_RESPONSE"


@pytest.mark.parametrize("missing", ["OPENAI_AUTH", "OPENAI_ORGANIZATION"])
@pytest.mark.parametrize("value", [None, "", "  ", False, 1, [], {}, "value\r\nCookie: bad"])
def test_configuration_requires_valid_complete_pair(provider, missing, value):
    provider._config[missing] = value
    assert not provider.is_configured()
    assert provider.snapshot_identity() == ""
    balance, error = provider.fetch_balance()
    assert balance is None and error.code == "NOT_CONFIGURED"
    provider._session.get.assert_not_called()


def test_snapshot_identity_is_normalized_pair_fingerprint(provider):
    identity = provider.snapshot_identity()
    assert provider.is_configured()
    assert len(identity) == 64 and AUTH not in identity and ORG not in identity
    provider._config.update(OPENAI_AUTH=f" {AUTH} ", OPENAI_ORGANIZATION=f" {ORG} ")
    assert provider.snapshot_identity() == identity
    provider._config["OPENAI_ORGANIZATION"] = "org-other"
    assert provider.snapshot_identity() != identity
    provider._config.update(CONFIG, OPENAI_AUTH="Bearer renewed")
    assert provider.snapshot_identity() != identity


@pytest.mark.parametrize("status,code", [(401, "AUTH_EXPIRED"), (403, "FORBIDDEN"),
                                        (429, "RATE_LIMITED"), (500, "SERVER_ERROR"),
                                        (503, "SERVER_ERROR"), (302, "HTTP_ERROR"),
                                        (307, "HTTP_ERROR"), (400, "HTTP_ERROR"),
                                        (204, "HTTP_ERROR")])
def test_http_errors_are_safe_and_keep_auth_codes_distinct(provider, caplog, status, code):
    result = response({"error": f"{AUTH} {ORG}"}, status)
    provider._session.get.return_value = result
    balance, error = provider.fetch_balance()
    assert balance is None and error.code == code
    result.json.assert_not_called()
    assert AUTH not in repr(error) + caplog.text
    assert ORG not in repr(error) + caplog.text
    result.close.assert_called_once()
    assert provider._config == CONFIG


@pytest.mark.parametrize("exception,code", [(requests.Timeout, "NETWORK_TIMEOUT"),
                                           (requests.ConnectionError, "NETWORK_ERROR"),
                                           (requests.RequestException, "NETWORK_ERROR"),
                                           (RuntimeError, "UNKNOWN_ERROR")])
def test_network_and_unexpected_errors_never_echo_raw_exception(provider, caplog, exception, code):
    provider._session.get.side_effect = exception(f"{AUTH} {ORG} Cookie: secret")
    balance, error = provider.fetch_balance()
    assert balance is None and error.code == code
    assert AUTH not in repr(error) + caplog.text
    assert ORG not in repr(error) + caplog.text


def test_invalid_json_is_safe_and_response_is_closed(provider, caplog):
    result = response()
    result.json.side_effect = ValueError(f"{AUTH} {ORG}")
    provider._session.get.return_value = result
    balance, error = provider.fetch_balance()
    assert balance is None and error.code == "INVALID_RESPONSE"
    assert AUTH not in repr(error) + caplog.text
    assert ORG not in repr(error) + caplog.text
    result.close.assert_called_once()


@pytest.mark.parametrize("value", ["raw-token", "{}", "null", "[]", "bad json",
                                   json.dumps({"AUTH": AUTH}), json.dumps({"ORGANIZATION": ORG}),
                                   json.dumps({"AUTH": AUTH, "ORGANIZATION": True})])
def test_acquired_payload_mapper_rejects_incomplete_and_untyped_payloads(value):
    assert OpenAIProvider.acquired_credential_values(value) == {}


def test_acquired_payload_mapper_accepts_only_the_two_secret_fields():
    value = json.dumps({"AUTH": AUTH, "ORGANIZATION": ORG, "COOKIE": "must-not-map"})
    assert OpenAIProvider.acquired_credential_values(value) == {"AUTH": AUTH, "ORGANIZATION": ORG}


def test_browser_acquisition_waits_then_closes_before_independent_http(monkeypatch):
    order = []
    event = Mock()
    event.wait.side_effect = lambda **kwargs: order.append("finish") or True
    session = Mock()
    session.capture_exact_request_headers.side_effect = lambda **kwargs: order.append("capture") or HEADERS
    session.close_and_verify.side_effect = lambda: order.append("closed")
    profile_paths = []

    def open_session(stop_event, **kwargs):
        order.append("open")
        assert stop_event is event
        profile_paths.append(Path(kwargs["user_data_dir"]))
        assert profile_paths[0] == config_manager.CONFIG_DIR / "openai-chrome"
        # 与实际 open_chrome_session 一样创建应用专用资料目录。
        profile_paths[0].mkdir(parents=True, exist_ok=True)
        assert kwargs["acquire_url"] == "https://platform.openai.com/home"
        return session

    http = Mock()
    http.get.side_effect = lambda *args, **kwargs: order.append("http") or response({"total_available": 0})
    monkeypatch.setattr(browser_cookie, "open_chrome_session", open_session)
    with patch("api.providers.openai.build_session", return_value=http):
        result = OpenAIProvider.acquire_credentials_via_chrome(event)
    assert order == ["open", "finish", "capture", "closed", "http"]
    assert isinstance(result, str)
    assert OpenAIProvider.acquired_credential_values(result) == {"AUTH": AUTH, "ORGANIZATION": ORG}
    session.capture_exact_request_headers.assert_called_once_with(
        url=URL, page_host="platform.openai.com",
        header_names=("authorization", "openai-organization"), timeout_seconds=10.0,
        cancel_event=None,
    )
    session.cookies.assert_not_called()
    session.fetch_json.assert_not_called()
    http.close.assert_called_once()
    assert profile_paths[0].is_dir()


@pytest.mark.parametrize("failure", ["capture", "validation"])
def test_failed_connection_reuses_same_browser_profile_on_retry(monkeypatch, failure):
    event = threading.Event()
    event.set()
    paths, sessions = [], []

    def open_session(*args, **kwargs):
        path = Path(kwargs["user_data_dir"])
        paths.append(path)
        marker = path / "synthetic-browser-state"
        if len(paths) == 1:
            path.mkdir(parents=True)
            marker.write_text("browser-owned-test-state", encoding="utf-8")
        else:
            assert marker.read_text(encoding="utf-8") == "browser-owned-test-state"
        session = Mock()
        session.capture_exact_request_headers.return_value = HEADERS
        if failure == "capture" and not sessions:
            session.capture_exact_request_headers.side_effect = RuntimeError("BROWSER_REQUEST_EMPTY")
        sessions.append(session)
        return session

    http = Mock()
    http.get.side_effect = ([response({}, 401), response({"total_available": 1})]
                            if failure == "validation" else [response({"total_available": 1})])
    code = "AUTH_EXPIRED" if failure == "validation" else "BROWSER_REQUEST_EMPTY"
    monkeypatch.setattr(browser_cookie, "open_chrome_session", open_session)
    with patch("api.providers.openai.build_session", return_value=http):
        with pytest.raises(RuntimeError, match=f"^{code}$"):
            OpenAIProvider.acquire_credentials_via_chrome(event)
        result = OpenAIProvider.acquire_credentials_via_chrome(event)
    assert paths == [config_manager.CONFIG_DIR / "openai-chrome"] * 2
    assert paths[0].is_dir()
    assert OpenAIProvider.acquired_credential_values(result) == {"AUTH": AUTH, "ORGANIZATION": ORG}
    for session in sessions:
        session.close_and_verify.assert_called_once()
        session.cookies.assert_not_called()
        session.fetch_json.assert_not_called()


@pytest.mark.parametrize("phase,code", [("capture", "BROWSER_REQUEST_AMBIGUOUS"),
                                       ("close", "BROWSER_NOT_CLOSED"),
                                       ("wait", "OPENAI_ACQUIRE_TIMEOUT")])
def test_acquisition_failure_never_starts_http_or_returns_credentials(phase, code):
    session = Mock()
    session.capture_exact_request_headers.return_value = HEADERS
    event = Mock()
    event.wait.return_value = phase != "wait"
    if phase == "capture":
        session.capture_exact_request_headers.side_effect = RuntimeError(code)
    if phase == "close":
        session.close_and_verify.side_effect = RuntimeError(code)
    with (patch.object(browser_cookie, "open_chrome_session", return_value=session),
          patch("api.providers.openai.build_session") as build,
          pytest.raises(RuntimeError, match=f"^{code}$")):
        OpenAIProvider.acquire_credentials_via_chrome(event)
    session.close_and_verify.assert_called_once()
    build.assert_not_called()
    if phase == "wait":
        session.capture_exact_request_headers.assert_not_called()


@pytest.mark.parametrize("status,code", [(401, "AUTH_EXPIRED"), (403, "FORBIDDEN"),
                                        (429, "RATE_LIMITED"), (200, "INVALID_RESPONSE")])
def test_new_credentials_must_pass_closed_browser_http_validation(status, code):
    session = Mock()
    session.capture_exact_request_headers.return_value = HEADERS
    event = threading.Event()
    event.set()
    http = Mock()
    http.get.return_value = response({}, status)
    with (patch.object(browser_cookie, "open_chrome_session", return_value=session),
          patch("api.providers.openai.build_session", return_value=http),
          pytest.raises(RuntimeError, match=f"^{code}$")):
        OpenAIProvider.acquire_credentials_via_chrome(event)
    session.close_and_verify.assert_called_once()
    http.close.assert_called_once()


@pytest.mark.parametrize("phase", ["before", "waiting", "capture", "close"])
def test_cancelled_acquisition_closes_without_starting_independent_http(phase):
    cancel = threading.Event()
    finish = threading.Event()
    finish.set()
    session = Mock()
    session.capture_exact_request_headers.return_value = HEADERS
    if phase == "before":
        cancel.set()
    elif phase == "capture":
        session.capture_exact_request_headers.side_effect = lambda **kwargs: cancel.set() or HEADERS
    elif phase == "close":
        session.close_and_verify.side_effect = cancel.set

    def open_session(*args, **kwargs):
        if phase == "waiting":
            cancel.set()
        return session

    with (patch.object(browser_cookie, "open_chrome_session", side_effect=open_session) as open_browser,
          patch("api.providers.openai.build_session") as build,
          pytest.raises(RuntimeError, match="^OPENAI_ACQUIRE_CANCELLED$")):
        OpenAIProvider.acquire_credentials_via_chrome(finish, cancel_event=cancel)
    build.assert_not_called()
    if phase == "before":
        open_browser.assert_not_called()
    else:
        session.close_and_verify.assert_called_once()
    if phase in {"before", "waiting"}:
        session.capture_exact_request_headers.assert_not_called()


def test_exact_capture_does_not_start_network_after_cancel():
    cancel = threading.Event()
    cancel.set()
    session = object.__new__(browser_cookie.ChromeSession)
    session._connection = Mock()
    with pytest.raises(RuntimeError, match="^OPENAI_ACQUIRE_CANCELLED$"):
        session.capture_exact_request_headers(url=URL, page_host="platform.openai.com",
            header_names=("authorization", "openai-organization"), cancel_event=cancel)
    session._connection.call.assert_not_called()


def test_acquisition_unexpected_exception_and_formatter_are_always_safe(caplog):
    raw = f"{AUTH} {ORG} Cookie: private"
    event = threading.Event()
    with (patch.object(browser_cookie, "open_chrome_session", side_effect=RuntimeError(raw)),
          pytest.raises(RuntimeError, match="^OPENAI_BROWSER_START_FAILED$") as caught):
        OpenAIProvider.acquire_credentials_via_chrome(event)
    assert caught.value.__suppress_context__
    for exc in (RuntimeError(raw), ValueError(raw), RuntimeError([raw])):
        message = OpenAIProvider.describe_acquire_error(exc)
        assert AUTH not in message + caplog.text and ORG not in message + caplog.text


def request_event(request_id="balance", headers=None, **overrides):
    request = {"url": URL, "method": "GET", "headers": HEADERS if headers is None else headers}
    params = {"requestId": request_id, "frameId": "main", "loaderId": "new", "request": request}
    for key in ("url", "method"):
        if key in overrides:
            request[key] = overrides.pop(key)
    params.update(overrides)
    return {"method": "Network.requestWillBeSent", "params": params}


def extra_event(request_id="balance", headers=None):
    return {"method": "Network.requestWillBeSentExtraInfo", "params": {
        "requestId": request_id, "headers": HEADERS if headers is None else headers,
        "associatedCookies": [{"cookie": {"value": "must-not-read"}}],
    }}


class ScriptedConnection:
    def __init__(self, events, *, final_loader="new", page_url="https://platform.openai.com/home"):
        self._events = [{"method": "old-queued-event"}]
        self.events = list(events)
        self.frame_reads = 0
        self.clock = 0.0
        self.calls = []
        self.final_loader = final_loader
        self.page_url = page_url

    def call(self, method, params=None):
        self.calls.append((method, params))
        if method == "Page.getFrameTree":
            self.frame_reads += 1
            return {"frameTree": {"frame": {"id": "main", "url": self.page_url,
                    "loaderId": "old" if self.frame_reads == 1 else self.final_loader}}}
        return {}

    def next_event(self, timeout_seconds):
        if self.events:
            return self.events.pop(0)
        self.clock += max(1.0, timeout_seconds)
        return None


def capture(events, **options):
    connection = ScriptedConnection(events, **options)
    session = object.__new__(browser_cookie.ChromeSession)
    session._connection = connection
    with patch.object(browser_cookie.time, "monotonic", side_effect=lambda: connection.clock):
        return session.capture_exact_request_headers(
            url=URL, page_host="platform.openai.com",
            header_names=("authorization", "openai-organization"), timeout_seconds=1,
        ), connection


@pytest.mark.parametrize("extra_first", [False, True])
def test_capture_merges_only_same_request_id_even_if_extra_info_is_first(extra_first):
    main = request_event(headers={"Authorization": AUTH})
    extra = extra_event(headers={"OpenAI-Organization": ORG, "Cookie": "must-not-capture"})
    result, connection = capture([extra, main] if extra_first else [main, extra])
    assert result == HEADERS
    assert connection._events == []
    assert ("Page.reload", {"ignoreCache": True}) in connection.calls
    assert connection.calls[-1][0] == "Network.disable"
    assert not any("Cookies" in method or "Storage" in method or method == "Runtime.evaluate"
                   for method, params in connection.calls)


def test_capture_never_mixes_headers_from_different_request_ids():
    with pytest.raises(RuntimeError, match="BROWSER_REQUEST_EMPTY"):
        capture([request_event("one", {"Authorization": AUTH}),
                 extra_event("two", {"OpenAI-Organization": ORG})])


@pytest.mark.parametrize("override", [
    {"url": "https://api.openai.com.evil.invalid/v1/dashboard/billing/credit_grants"},
    {"url": "https://api.openai.com@evil.invalid/v1/dashboard/billing/credit_grants"},
    {"url": URL + "/other"}, {"url": URL + "?extra=1"}, {"url": URL + "#extra"},
    {"url": URL.replace("https:", "http:")}, {"url": URL.replace(".com/", ".com:444/")},
    {"url": URL.replace("api.openai.com", "user@api.openai.com")},
    {"method": "POST"}, {"method": "OPTIONS"},
    {"frameId": "iframe"}, {"loaderId": "old"}, {"frameId": ""},
])
def test_capture_requires_exact_host_path_get_and_current_main_frame(override):
    with pytest.raises(RuntimeError, match="BROWSER_REQUEST_EMPTY"):
        capture([extra_event(), request_event(**override)])


def test_capture_ignores_old_loader_and_unrelated_requests_instead_of_last_header():
    result, _ = capture([
        request_event("old", {"authorization": "old-secret", "openai-organization": "old-org"}, loaderId="old"),
        request_event("selected"), extra_event("selected"),
        request_event("other", {"authorization": "wrong-secret", "openai-organization": "wrong-org"}, url=URL + "/other"),
        extra_event("other"),
    ])
    assert result == HEADERS


def test_capture_allows_repeated_same_pair_but_rejects_multiple_organizations():
    result, _ = capture([request_event("one"), request_event("two")])
    assert result == HEADERS
    for events in (
        [request_event("one"), request_event("two", {**HEADERS, "openai-organization": "org-other"})],
        [request_event("one"), request_event("two", {"authorization": AUTH})],
        [request_event(), extra_event(headers={**HEADERS, "authorization": "different-auth"})],
    ):
        with pytest.raises(RuntimeError, match="BROWSER_REQUEST_AMBIGUOUS"):
            capture(events)


@pytest.mark.parametrize("events", [
    [request_event(redirectResponse={"status": 302}), extra_event()],
    [request_event(), request_event(), extra_event()],
    [request_event(), extra_event(), extra_event(headers={**HEADERS, "openai-organization": "other"})],
])
def test_capture_rejects_redirect_reused_id_and_conflicting_extra_info(events):
    with pytest.raises(RuntimeError, match="BROWSER_REQUEST_AMBIGUOUS"):
        capture(events)


@pytest.mark.parametrize("options", [{"final_loader": "old"}, {"page_url": "https://login.other.invalid/"}])
def test_capture_requires_confirmed_reload_of_platform_page(options):
    with pytest.raises(RuntimeError, match="BROWSER_REQUEST_EMPTY"):
        capture([request_event()], **options)


def chrome_session():
    instance = object.__new__(browser_cookie.ChromeSession)
    instance._connection = Mock()
    instance._process = Mock()
    instance._process.wait.return_value = 0
    instance._process.poll.return_value = 0
    instance._websocket_url = "ws://127.0.0.1:9299/devtools/page/test"
    return instance


def test_close_and_verify_confirms_process_exit_and_port_refusal():
    session = chrome_session()
    connection = session._connection
    with patch.object(browser_cookie.socket, "create_connection", side_effect=ConnectionRefusedError) as connect:
        session.close_and_verify()
    assert session._process.wait.call_count == 2
    session._process.poll.assert_called_once()
    assert connect.call_count == 1
    assert connect.call_args.args == (("127.0.0.1", 9299),)
    assert 0.2 < connect.call_args.kwargs["timeout"] <= 3.0
    assert session._connection is None
    connection.call.assert_called_once_with("Browser.close")
    connection.close.assert_called_once()


def test_close_and_verify_reaps_process_even_if_cdp_close_disconnects():
    session = chrome_session()
    session._connection.call.side_effect = ConnectionResetError("private")
    with patch.object(browser_cookie.socket, "create_connection", side_effect=ConnectionRefusedError) as connect:
        session.close_and_verify()
    session._process.wait.assert_called_once_with(timeout=3.0)
    connect.assert_called_once()
    assert session._connection is None


def test_close_verification_with_real_closed_loopback_port():
    # 无 Chrome、无认证：复现 Windows 对已关闭端口约两秒才返回 10061 的行为。
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    session = chrome_session()
    session._websocket_url = f"ws://127.0.0.1:{port}/devtools/page/test"
    session.close_and_verify()
    assert session._process.poll.return_value == 0


def test_close_verification_does_not_accept_real_listening_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        session = chrome_session()
        session._websocket_url = f"ws://127.0.0.1:{listener.getsockname()[1]}/devtools/page/test"
        with pytest.raises(RuntimeError, match="^BROWSER_NOT_CLOSED$"):
            session.close_and_verify(timeout_seconds=0.1)


def test_close_verification_retries_timeout_but_still_requires_explicit_refusal():
    session = chrome_session()
    with (patch.object(browser_cookie.socket, "create_connection", side_effect=[TimeoutError, ConnectionRefusedError]) as connect,
          patch.object(browser_cookie.time, "monotonic", return_value=0),
          patch.object(threading.Event, "wait")):
        session.close_and_verify()
    assert connect.call_count == 2


@pytest.mark.parametrize("failure", ["process", "port", "probe_timeout"])
def test_close_and_verify_never_accepts_live_process_or_open_or_unconfirmed_port(failure):
    session = chrome_session()
    if failure == "process":
        session._process.wait.side_effect = subprocess.TimeoutExpired("synthetic", 1)
    with (patch.object(browser_cookie.socket, "create_connection") as connect,
          patch.object(browser_cookie.time, "monotonic", side_effect=[0, 0, 2, 2]),
          patch.object(threading.Event, "wait"),
          pytest.raises(RuntimeError, match="^BROWSER_NOT_CLOSED$")):
        if failure == "probe_timeout":
            connect.side_effect = TimeoutError("private")
        session.close_and_verify(timeout_seconds=1)
    if failure == "process":
        connect.assert_not_called()
