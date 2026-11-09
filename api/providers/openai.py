"""OpenAI 控制台组织的独立 USD 可用余额来源。"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from api import browser_cookie
from api.providers.base import FetchError, Provider, ProviderBalance, build_session
from config import runtime as config_manager

_CREDIT_GRANTS_URL = "https://api.openai.com/v1/dashboard/billing/credit_grants"
_ACQUIRE_URL = "https://platform.openai.com/home"
_ERROR_MESSAGES = {
    "NOT_CONFIGURED": "尚未连接 OpenAI API，请通过专用浏览器连接账户",
    "AUTH_EXPIRED": "OpenAI API 登录已失效，请重新连接账户",
    "FORBIDDEN": "当前 OpenAI API 组织不可访问或权限不足，请确认所选组织",
    "RATE_LIMITED": "OpenAI API 请求过于频繁，请稍后重试",
    "NETWORK_TIMEOUT": "连接 OpenAI API 超时，请稍后重试",
    "NETWORK_ERROR": "无法连接 OpenAI API，请检查网络后重试",
    "SERVER_ERROR": "OpenAI API 服务暂时异常，请稍后重试",
    "HTTP_ERROR": "OpenAI API 余额请求未成功，请稍后重试",
    "INVALID_RESPONSE": "OpenAI API 返回的余额数据无效，请稍后重试",
    "UNKNOWN_ERROR": "读取 OpenAI API 余额失败，请稍后重试",
}
_ACQUIRE_MESSAGES = {
    **{code: "关闭浏览器后的独立验证未通过：" + message for code, message in _ERROR_MESSAGES.items()},
    "OPENAI_BROWSER_START_FAILED": "专用浏览器初始化失败，尚未开始独立验证",
    "OPENAI_BROWSER_CAPTURE_FAILED": "浏览器余额请求采集失败，尚未开始独立验证",
    "OPENAI_HTTP_VALIDATION_FAILED": "关闭浏览器后的独立验证发生异常，本次连接未保存",
    "CDP_CONN_CLOSED": "余额采集时专用浏览器调试连接已断开",
    "CDP_COMMAND_FAILED": "专用浏览器拒绝了余额采集指令",
    "CDP_COMMAND_TIMEOUT": "等待专用浏览器响应余额采集指令超时",
    "CDP_UNEXPECTED_FRAME": "余额采集时浏览器调试消息无法解析",
    "CDP_INVALID_JSON": "余额采集时浏览器调试消息格式无效",
    "CDP_UNEXPECTED_RESPONSE": "余额采集时浏览器返回了无效调试响应",
    "CDP_UNEXPECTED_JSON": "专用浏览器调试页面列表无效",
    "CDP_NO_PAGE_TARGET": "未找到专用浏览器的可采集页面",
    "CDP_INVALID_WS_URL": "专用浏览器调试地址无效",
    "CDP_WS_HANDSHAKE_FAILED": "无法建立专用浏览器调试连接",
    "CHROME_NOT_FOUND": "未检测到 Chrome，请先安装浏览器后连接账户",
    "USER_DATA_DIR_FAILED": "无法创建专用浏览器临时目录",
    "NO_FREE_CDP_PORT": "无法分配专用浏览器调试端口",
    "CHROME_LAUNCH_FAILED": "专用浏览器启动失败，请检查权限或安全软件",
    "BROWSER_NOT_READY": "专用浏览器尚未就绪，请重新连接",
    "BROWSER_NOT_CLOSED": "无法确认专用浏览器已完全退出，本次连接未保存，请重试",
    "BROWSER_REQUEST_EMPTY": "未捕获到当前组织的余额请求，请登录、选好组织并看到余额后完成采集",
    "BROWSER_REQUEST_AMBIGUOUS": "无法唯一确认当前组织，请关闭其他组织页面、选好组织后重新连接",
    "OPENAI_ACQUIRE_TIMEOUT": "等待完成采集超时，请重新连接账户",
    "OPENAI_ACQUIRE_CANCELLED": "连接已取消，原有连接未更改",
    "OPENAI_ACQUIRE_FAILED": "采集 OpenAI API 登录凭据失败，请重新连接账户",
}


def _header_value(value: Any) -> str:
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) > 126 for char in value):
        return ""
    return value.strip()


def _credential_pair(authorization: Any, organization: Any) -> dict[str, str]:
    auth, org = _header_value(authorization), _header_value(organization)
    return {"AUTH": auth, "ORGANIZATION": org} if auth and org else {}


def _balance_amount(payload: Any) -> Decimal:
    if not isinstance(payload, dict):
        raise ValueError
    value = payload.get("total_available")
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError
    if isinstance(value, str) and not re.fullmatch(
        r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value.strip()
    ):
        raise ValueError
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError from None
    if not amount.is_finite():
        raise ValueError
    return amount


class OpenAIProvider(Provider):
    id = "openai"
    name = "OpenAI API"
    default_currency = "USD"
    official_api_hosts = {"api.openai.com"}
    dashboard_url = _ACQUIRE_URL
    balance_label = "API 余额"
    balance_description = "当前连接组织的 OpenAI API 美元可用余额"
    support_description = "仅支持组织 API 可用余额；登录失效后需重新连接"
    supports_browser_credential_acquisition = True
    credential_acquisition_label = "OpenAI API 登录凭据"
    credential_acquisition_automatic = False
    credential_fields = {
        "AUTH": {
            "label": "登录认证",
            "secret": True,
            "browser_only": True,
            "browser_acquisition": True,
            "hint": "仅通过专用浏览器连接，不支持手动填写",
        },
        "ORGANIZATION": {
            "label": "所属组织",
            "secret": True,
            "browser_only": True,
            "hint": "在专用浏览器中选择组织后自动采集",
        },
    }

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(config)
        self._session = build_session()
        # 显式认证处理器阻止 netrc 覆盖捕获的头，同时保留系统代理和 CA 配置。
        self._session.auth = lambda request: request

    def _credentials(self) -> dict[str, str]:
        return _credential_pair(
            self.config_get("OPENAI_AUTH", ""), self.config_get("OPENAI_ORGANIZATION", "")
        )

    def is_configured(self) -> bool:
        return bool(self._credentials())

    def snapshot_identity(self) -> str:
        credentials = self._credentials()
        if not credentials:
            return ""
        return hashlib.sha256(json.dumps(credentials, sort_keys=True).encode()).hexdigest()

    @property
    def balance_account_label(self) -> str:
        organization = self._credentials().get("ORGANIZATION", "")
        return "org-…" + organization[-4:] if organization else ""

    def close(self) -> None:
        self._session.close()

    def fetch_balance(self) -> tuple[ProviderBalance | None, FetchError | None]:
        credentials = self._credentials()
        if not credentials:
            return None, FetchError("NOT_CONFIGURED", self.balance_label, _ERROR_MESSAGES["NOT_CONFIGURED"])
        code = "UNKNOWN_ERROR"
        try:
            self._session.cookies.clear()
            response = self._session.get(
                _CREDIT_GRANTS_URL,
                headers={
                    "Authorization": credentials["AUTH"],
                    "OpenAI-Organization": credentials["ORGANIZATION"],
                    "Accept": "application/json",
                    "Cookie": None,
                },
                allow_redirects=False,
                timeout=(5, 15),
            )
            try:
                status = response.status_code
                if status == 200:
                    try:
                        amount = _balance_amount(response.json(parse_float=Decimal))
                    except (ValueError, TypeError, InvalidOperation):
                        code = "INVALID_RESPONSE"
                    else:
                        return ProviderBalance("USD", amount), None
                else:
                    code = {
                        401: "AUTH_EXPIRED", 403: "FORBIDDEN", 429: "RATE_LIMITED",
                    }.get(status, "SERVER_ERROR" if status >= 500 else "HTTP_ERROR")
            finally:
                response.close()
        except requests.Timeout:
            code = "NETWORK_TIMEOUT"
        except requests.RequestException:
            code = "NETWORK_ERROR"
        except Exception:
            # 异常可能附带认证头、组织或响应正文，禁止回显与记录原始异常。
            code = "UNKNOWN_ERROR"
        finally:
            self._session.cookies.clear()
        return None, FetchError(code, self.balance_label, _ERROR_MESSAGES[code])

    @staticmethod
    def acquired_credential_values(value: str) -> dict[str, str]:
        try:
            payload = json.loads(value)
        except (TypeError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return _credential_pair(payload.get("AUTH"), payload.get("ORGANIZATION"))

    @staticmethod
    def acquire_credentials_via_chrome(
        stop_event: threading.Event, *, cancel_event: threading.Event | None = None,
    ) -> str:
        def check_cancelled() -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("OPENAI_ACQUIRE_CANCELLED")

        stage = "OPENAI_BROWSER_START_FAILED"
        try:
            check_cancelled()
            # 与 DeepSeek/MiMo 一样复用应用专用目录；不读取用户常用 Chrome 的资料。
            # 登录状态由 Chrome 自己维护，应用不导出 Cookie 或浏览器存储。
            session = browser_cookie.open_chrome_session(
                stop_event,
                acquire_url=_ACQUIRE_URL,
                profile_name="openai-chrome",
                user_data_dir=str(config_manager.CONFIG_DIR / "openai-chrome"),
            )
            try:
                stage = "OPENAI_BROWSER_CAPTURE_FAILED"
                if not stop_event.wait(timeout=180):
                    raise RuntimeError("OPENAI_ACQUIRE_TIMEOUT")
                check_cancelled()
                headers = session.capture_exact_request_headers(
                    url=_CREDIT_GRANTS_URL,
                    page_host="platform.openai.com",
                    header_names=("authorization", "openai-organization"),
                    timeout_seconds=10.0,
                    cancel_event=cancel_event,
                )
                check_cancelled()
                credentials = _credential_pair(
                    headers.get("authorization"), headers.get("openai-organization")
                )
                if not credentials:
                    raise RuntimeError("BROWSER_REQUEST_EMPTY")
            finally:
                session.close_and_verify()
            # 只退出本次浏览器，不删除专用登录资料；确认退出后仍必须独立 HTTP 验证。
            stage = "OPENAI_HTTP_VALIDATION_FAILED"
            check_cancelled()
            provider = OpenAIProvider({f"OPENAI_{key}": value for key, value in credentials.items()})
            try:
                balance, error = provider.fetch_balance()
                if error is not None:
                    raise RuntimeError(error.code)
                if balance is None:
                    raise RuntimeError("INVALID_RESPONSE")
            finally:
                provider.close()
            check_cancelled()
            return json.dumps(credentials, separators=(",", ":"))
        except Exception as exc:
            code = exc.args[0] if isinstance(exc, RuntimeError) and exc.args else ""
            if not isinstance(code, str) or code not in _ACQUIRE_MESSAGES:
                code = stage
            raise RuntimeError(code) from None

    @staticmethod
    def acquire_error_code(exc: Exception) -> str:
        """Only fixed codes may cross worker signals or reach diagnostic logs."""
        code = exc.args[0] if isinstance(exc, RuntimeError) and exc.args else ""
        return code if isinstance(code, str) and code in _ACQUIRE_MESSAGES else "OPENAI_ACQUIRE_FAILED"

    @staticmethod
    def describe_acquire_error(exc: Exception) -> str:
        code = exc.args[0] if isinstance(exc, RuntimeError) and exc.args else ""
        if not isinstance(code, str):
            code = ""
        return _ACQUIRE_MESSAGES.get(code, _ACQUIRE_MESSAGES["OPENAI_ACQUIRE_FAILED"])


__all__ = ["OpenAIProvider"]
