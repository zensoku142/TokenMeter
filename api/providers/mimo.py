"""Xiaomi MiMo platform provider.

Uses the platform API at ``platform.xiaomimimo.com`` to fetch balance,
monthly usage summary, and per-day usage details — all authenticated via
browser cookie.

A ``acquire_cookie_via_chrome`` helper is provided so the settings dialog
can launch the user's own Chrome/Edge and let them log in through the
browser, then return the required ``api-platform_*`` cookie strings
directly back into the credential UI. That helper uses only the Python
standard library (``subprocess``, ``http.client``, ``socket``, ``json``)
and does not require Playwright or any third-party package.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import struct
import subprocess
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

import config_manager
from api import browser_cookie
from api.providers.base import (
    FetchError,
    Provider,
    ProviderBalance,
    ProviderSummary,
    build_session,
    _decimal,
)

_MIMO_PLATFORM = "https://platform.xiaomimimo.com"
# 浏览器获取 Cookie 时打开的目标页面。
MIMO_ACQUIRE_URL = f"{_MIMO_PLATFORM}/console/usage"
# 需要从浏览器上下文中提取的 cookie 名称；与 cookie_tool 保持一致。
MIMO_ACQUIRE_KEYS = (
    "api-platform_ph",
    "api-platform_serviceToken",
    "api-platform_slh",
    "userId",
)
# 匹配 ``domain`` 字段；MiMo 平台 cookie 落在平台域名或其子域上。
MIMO_ACQUIRE_DOMAINS = ("platform.xiaomimimo.com", ".xiaomimimo.com", ".platform.xiaomimimo.com")
# CDP 随机调试端口探测区间。
_MIMO_CDP_PORT_RANGE = range(9222, 9323)
# CDP 握手超时（秒），避免线程被阻塞太久。
_MIMO_CDP_TIMEOUT_SECONDS = 10
# Cookie 采集总超时（秒），兜底防止 worker 长时间不退出。
_MIMO_ACQUIRE_TOTAL_TIMEOUT_SECONDS = 180


class MiMoProvider(Provider):
    id = "mimo"
    name = "小米 MiMo"
    default_currency = "CNY"
    default_base = _MIMO_PLATFORM
    official_api_hosts = {"platform.xiaomimimo.com"}
    # TokenScope2 的接口会同时返回余额 (balance)、月度费用和逐日用量，
    # 所以这里把 supports 开关打开，让 data.store 可以走通用路径，
    # 与 DeepSeek 一致聚合 today_cost_cny / today_tokens / daily_usage。
    supports_daily_usage = True
    supports_cost = True
    supports_cookie_acquisition = True
    credential_fields = {
        "COOKIE": {
            "label": "Cookie",
            "secret": True,
            "multiline": True,
            "hint": "登录 platform.xiaomimimo.com 后复制浏览器 Cookie",
        },
        "API_PLATFORM_PH": {
            "label": "api-platform_ph",
            "secret": False,
            "hint": "浏览器请求 URL 中 ?api-platform_ph= 后面的值",
        },
        "API_KEY": {
            "label": "推理 API Key（用量查询不使用）",
            "secret": True,
            "optional": True,
            "hint": "仅保留旧配置，不会发送到控制台用量接口",
        },
        "BASE": {
            "label": "平台地址",
            "secret": False,
            "hint": "默认 https://platform.xiaomimimo.com",
        },
    }

    def __init__(self) -> None:
        self._session = build_session()

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def normalize_cookie(raw: str) -> str:
        """把粘贴得到的 Cookie 规范化为 ``k=v; k2=v2``。

        会去除多余空白、制表与换行，保证请求头和 ``api-platform_ph`` 查找
        时不会因格式失败。
        """
        tokens = [
            token.strip()
            for token in " ".join(str(raw).splitlines()).split(";")
            if token.strip()
        ]
        return "; ".join(tokens)

    @staticmethod
    def extract_cookie_value(raw: str, name: str) -> str:
        """在 ``k=v; k2=v2`` 字符串中定位 ``name`` 的值。

        会去掉值周围的双引号；未找到返回空字符串。对 ``api-platform_ph``
        这类值可能含 ``/``、``=``、百分编码，因此按第一个 ``=`` 分割。
        """
        for token in " ".join(str(raw).splitlines()).split(";"):
            token = token.strip()
            if not token or "=" not in token:
                continue
            key, _, value = token.partition("=")
            if key.strip() == name:
                return value.strip().strip('"')
        return ""

    @staticmethod
    def acquired_cookie_values(cookie: str) -> dict[str, str]:
        normalized = browser_cookie.normalize_cookie(cookie)
        return {
            "COOKIE": normalized,
            "API_PLATFORM_PH": MiMoProvider.extract_cookie_value(
                normalized, "api-platform_ph"
            ),
        }

    def is_configured(self) -> bool:
        return bool(
            str(config_manager.get("MIMO_COOKIE", "")).strip()
            or str(config_manager.get("MIMO_API_KEY", "")).strip()
        )

    # --------------------------------------------------------- chrome helpers
    @staticmethod
    def default_user_data_dir() -> str:
        """返回浏览器独立用户数据目录（默认 ``%APPDATA%/TokenSpider/mimo-chrome``）。"""
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return str(Path(base) / "TokenSpider" / "mimo-chrome")

    @staticmethod
    def find_chrome_executable(use_edge: bool = False) -> str:
        """探测本机 Chrome（或 Edge）可执行文件路径。找不到返回空串。"""
        candidates: list[str] = []
        # 优先 Windows 注册表：App Paths 里记录了常见浏览器路径。
        try:
            import winreg  # 仅 Windows 可用，PyInstaller 打包后仍可用。
            app_name = "msedge.exe" if use_edge else "chrome.exe"
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{app_name}",
            ) as key:
                path, _ = winreg.QueryValueEx(key, "")
                if path and os.path.isfile(path):
                    candidates.append(path)
        except (FileNotFoundError, OSError, PermissionError):
            pass
        except Exception:
            pass
        # 常见安装位置兜底；在 Windows 10/11 上 Chrome 可能装在 Program Files
        # 或 %LOCALAPPDATA%，Edge 装在 Program Files。
        local_appdata = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        program_files = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        ]
        if use_edge:
            for base in program_files:
                candidates.append(str(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"))
        else:
            for base in program_files:
                candidates.append(str(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"))
            candidates.append(str(Path(local_appdata) / "Google" / "Chrome" / "Application" / "chrome.exe"))
        for path in candidates:
            if path and os.path.isfile(path):
                return path
        return ""

    @staticmethod
    def pick_free_cdp_port() -> int:
        """在 ``_MIMO_CDP_PORT_RANGE`` 里随机选一个空闲端口；找不到返回 -1。"""
        for port in _MIMO_CDP_PORT_RANGE:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                try:
                    sock.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    continue
        return -1

    @staticmethod
    def _http_json(host: str, port: int, path: str, timeout: float) -> Any:
        """对调试端口做一次简单 HTTP GET 并返回 JSON。"""
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            data = resp.read()
        finally:
            conn.close()
        if resp.status != 200:
            raise RuntimeError(f"CDP_HTTP_{resp.status}")
        try:
            return json.loads(data.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError("CDP_INVALID_JSON") from exc

    @staticmethod
    def _wait_browser_ready(port: int, stop_event: threading.Event, timeout: float = 15.0) -> None:
        """等待调试端口返回 200，否则抛 ``RuntimeError``。"""
        deadline = threading.Event()
        timer = threading.Timer(timeout, lambda: deadline.set())
        timer.daemon = True
        timer.start()
        try:
            while not stop_event.is_set() and not deadline.is_set():
                try:
                    MiMoProvider._http_json("127.0.0.1", port, "/json/version", 1.0)
                    return
                except (OSError, RuntimeError, http.client.HTTPException):
                    stop_event.wait(0.25)
            raise RuntimeError("BROWSER_NOT_READY")
        finally:
            timer.cancel()

    @staticmethod
    def _pick_websocket_endpoint(port: int) -> str:
        """获取 CDP 中可用 target 对应的 WebSocket 端点。

        Chrome 对 ``--app`` 模式可能把 URL 记在 ``page``、``app`` 或
        ``background_page`` 类型中，因此不能只用 ``type == "page"``。
        """
        items = MiMoProvider._http_json("127.0.0.1", port, "/json", _MIMO_CDP_TIMEOUT_SECONDS)
        if not isinstance(items, list):
            raise RuntimeError("CDP_UNEXPECTED_JSON")
        acceptable = {"page", "app", "background_page", "other"}
        candidate = None
        for entry in items:
            if not isinstance(entry, dict):
                continue
            kind = entry.get("type")
            url = entry.get("url") or ""
            ws = entry.get("webSocketDebuggerUrl")
            if not ws:
                continue
            if kind in acceptable:
                # 优先挑一个属于目标站点的 target；没有则选第一个可用。
                if _MIMO_PLATFORM in url:
                    return str(ws)
                if candidate is None:
                    candidate = str(ws)
        if candidate:
            return candidate
        raise RuntimeError("CDP_NO_PAGE_TARGET")

    @staticmethod
    def _cdp_fetch_cookies_via_ws(ws_url: str) -> list[dict[str, Any]]:
        """通过 CDP WebSocket 发送 ``Network.getAllCookies`` 并返回 cookies 列表。

        说明
        ----
        - Chrome ``--app`` 模式下响应头为 ``101 WebSocket Protocol Handshake``，
          因此只检查状态码是否为 ``101`` 而不是 ``"101 Switching Protocols"``。
        - ``Network.getCookies`` 受当前页面域限制，可能返回空；改用
          ``Network.getAllCookies`` 拿到整个浏览器上下文中的 cookie，与
          ``cookie_tool`` 中 ``context.cookies()`` 的行为保持一致。
        """
        parsed = urlparse(ws_url)
        if parsed.scheme != "ws" or not parsed.hostname or not parsed.port:
            raise RuntimeError("CDP_INVALID_WS_URL")
        host = parsed.hostname
        port = int(parsed.port)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        # 1) 做 HTTP 升级握手。RFC 6455 允许使用任意 16 字节 base64 串作 key，
        #    这里采用固定值以避免引入额外依赖。
        key_b64 = "dGhlIHNhbXBsZSBub25jZQ=="
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key_b64}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock = socket.create_connection((host, port), timeout=_MIMO_CDP_TIMEOUT_SECONDS)
        try:
            sock.sendall(request.encode("ascii"))
            # 读取响应头直到空行。
            buf = bytearray()
            while True:
                chunk = sock.recv(2048)
                if not chunk:
                    raise RuntimeError("CDP_WS_HANDSHAKE_FAILED")
                buf.extend(chunk)
                if b"\r\n\r\n" in buf:
                    break
            head_end = buf.index(b"\r\n\r\n")
            head_text = buf[:head_end].decode("ascii", errors="replace")
            # Chrome / Chromium 的响应头形式因版本而异，核心是返回 ``101``。
            if "101" not in head_text:
                raise RuntimeError("CDP_WS_HANDSHAKE_FAILED")
            # 2) 发送一个文本帧，请求 ``Network.getAllCookies``；相比
            #    ``Network.getCookies``，它不受当前页面域限制，更稳定。
            payload = json.dumps(
                {"id": 1, "method": "Network.getAllCookies"},
                ensure_ascii=False,
            ).encode("utf-8")
            # 文本帧：FIN=1, opcode=1, mask=1；使用 7 位长度（对 payload 足够）。
            payload_len = len(payload)
            if payload_len > 0xFFFF:
                raise RuntimeError("CDP_PAYLOAD_TOO_LARGE")
            header = bytearray()
            header.append(0x81)  # FIN=1, opcode=1
            header.append(0x80 | (payload_len & 0x7F))
            masking_key = bytes(4)  # 零掩码即可
            header.extend(masking_key)
            # 客户端按协议必须 mask payload；这里 mask 是 0，mask 操作等价于不改动。
            masked_payload = bytearray(payload)
            for i in range(payload_len):
                masked_payload[i] ^= masking_key[i % 4]
            sock.sendall(bytes(header) + bytes(masked_payload))
            # 3) 读取响应帧：期望一个 FIN=1 的文本帧；额外容忍 binary 与 0 长帧。
            raw = MiMoProvider._recv_exact(sock, 2)
            first_byte = raw[0]
            fin = bool(first_byte & 0x80)
            opcode = first_byte & 0x0F
            second_byte = raw[1]
            masked = bool(second_byte & 0x80)
            length = second_byte & 0x7F
            if not fin or opcode not in (1, 2):  # 1=text, 2=binary
                raise RuntimeError("CDP_UNEXPECTED_FRAME")
            if length == 126:
                (length,) = struct.unpack(">H", MiMoProvider._recv_exact(sock, 2))
            elif length == 127:
                (length,) = struct.unpack(">Q", MiMoProvider._recv_exact(sock, 8))
            if masked:
                # 服务端理论上不应 mask；这里仍兼容处理以防万一。
                key = MiMoProvider._recv_exact(sock, 4)
            body = MiMoProvider._recv_exact(sock, length)
            if masked:
                body = bytes(b ^ key[i % 4] for i, b in enumerate(body))
            try:
                result = json.loads(body.decode("utf-8", errors="replace"))
            except json.JSONDecodeError as exc:
                raise RuntimeError("CDP_INVALID_JSON") from exc
            if not isinstance(result, dict) or "result" not in result:
                raise RuntimeError("CDP_UNEXPECTED_RESPONSE")
            inner = result["result"]
            # ``Network.getAllCookies`` 的返回字段名是 ``cookies``。
            if not isinstance(inner, dict) or "cookies" not in inner:
                raise RuntimeError("CDP_UNEXPECTED_RESPONSE")
            cookies = inner["cookies"]
            if not isinstance(cookies, list):
                raise RuntimeError("CDP_UNEXPECTED_RESPONSE")
            return cookies
        finally:
            try:
                sock.close()
            except OSError:
                pass

    @staticmethod
    def _recv_exact(sock: socket.socket, n: int) -> bytes:
        """从 ``sock`` 读 ``n`` 字节，不足则抛。"""
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise RuntimeError("CDP_CONN_CLOSED")
            buf.extend(chunk)
        return bytes(buf)

    @staticmethod
    def _format_cookie_string(cookies: list[dict[str, Any]]) -> str:
        """把 CDP 读回的 cookies 列表按 ``MIMO_ACQUIRE_KEYS`` 顺序拼成一行。

        为避免遗漏 ``api-platform_ph`` 这类可能落在 ``xiaomimimo.com``
        子域上的 cookie，这里把 ``domain`` 判断放宽为「包含
        ``xiaomimimo.com`` 的任意子域或主机名」，同时仍然严格按
        ``MIMO_ACQUIRE_KEYS`` 过滤字段名，避免把无关的第三方 cookie
        拼进请求头。
        """
        name_to_value: dict[str, str] = {}
        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            name = str(cookie.get("name") or "")
            if name not in MIMO_ACQUIRE_KEYS:
                continue
            domain = str(cookie.get("domain") or "").lower()
            # 宽松判断：只要包含 xiaomimimo.com 主机部分，就视为来自 MiMo。
            if domain and "xiaomimimo.com" not in domain:
                continue
            value = str(cookie.get("value") or "")
            # ``api-platform_ph`` 有时会被百分编码；我们在拼到请求头里之前做
            # 一次去引号处理，但保留原字符串，避免把 ``%2F`` 这类合法编码弄坏。
            if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            # 避免被后面的同字段、但值为空的 cookie 覆盖。
            if value or name not in name_to_value:
                name_to_value[name] = value
        parts = [f"{k}={name_to_value.get(k, '')}" for k in MIMO_ACQUIRE_KEYS if k in name_to_value]
        return "; ".join(parts)

    @staticmethod
    def _cdp_send_text(ws_url: str, payload: dict[str, Any]) -> None:
        """建立 WebSocket 握手后发送一个 ``text`` 帧并立即关闭连接。

        仅用于触发 ``Browser.close`` 这类「发送后立即关闭」的 CDP 命令，
        不读响应；读响应走 ``_cdp_fetch_cookies_via_ws``。
        """
        parsed = urlparse(ws_url)
        if parsed.scheme != "ws" or not parsed.hostname or not parsed.port:
            return
        host = parsed.hostname
        port = int(parsed.port)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        try:
            sock = socket.create_connection((host, port), timeout=_MIMO_CDP_TIMEOUT_SECONDS)
        except OSError:
            return
        try:
            sock.sendall(request.encode("ascii"))
            buf = bytearray()
            while True:
                chunk = sock.recv(2048)
                if not chunk:
                    return
                buf.extend(chunk)
                if b"\r\n\r\n" in buf:
                    break
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            body_len = len(body)
            if body_len > 0xFFFF:
                return
            header = bytearray()
            header.append(0x81)
            header.append(0x80 | (body_len & 0x7F))
            header.extend(bytes(4))
            try:
                sock.sendall(bytes(header) + body)
            except OSError:
                return
        finally:
            try:
                sock.close()
            except OSError:
                pass

    @staticmethod
    def acquire_cookie_via_chrome(
        stop_event: threading.Event,
        use_edge: bool = False,
        user_data_dir: str | None = None,
        auto_collect: bool = False,
        headless: bool = False,
        total_timeout_seconds: float | None = None,
    ) -> str:
        return browser_cookie.acquire_cookie_via_chrome(
            stop_event,
            acquire_url=MIMO_ACQUIRE_URL,
            profile_name="mimo-chrome",
            allowed_domains=("xiaomimimo.com",),
            cookie_names=MIMO_ACQUIRE_KEYS,
            empty_cookie_error="MIMO_COOKIE_EMPTY",
            use_edge=use_edge,
            user_data_dir=user_data_dir,
            auto_collect=auto_collect,
            headless=headless,
            total_timeout_seconds=total_timeout_seconds,
        )

    # ---------------------------------------------------------- chrome errors
    ACQUIRE_ERROR_MESSAGES = {
        "CHROME_NOT_FOUND": "未检测到 Chrome 或 Edge，请先安装浏览器，或手动粘贴 Cookie",
        "USER_DATA_DIR_FAILED": "无法创建浏览器用户数据目录",
        "NO_FREE_CDP_PORT": "无法分配调试端口（9222~9322）",
        "CHROME_LAUNCH_FAILED": "浏览器启动失败，请检查权限或安全软件是否拦截",
        "BROWSER_NOT_READY": "浏览器调试接口未就绪，请稍后重试",
        "CDP_CONN_CLOSED": "浏览器调试连接意外关闭",
        "CDP_WS_HANDSHAKE_FAILED": "浏览器调试握手失败",
        "CDP_INVALID_JSON": "浏览器调试返回非 JSON 响应",
        "CDP_UNEXPECTED_FRAME": "浏览器调试返回未预期帧类型",
        "CDP_INVALID_RESPONSE": "浏览器调试返回 cookies 结构异常",
        "CDP_UNEXPECTED_RESPONSE": "浏览器调试响应字段缺失",
        "CDP_PAYLOAD_TOO_LARGE": "调试请求体积过大",
        "CDP_HTTP_404": "调试接口未就绪（404）",
        "MIMO_COOKIE_EMPTY": "当前浏览器会话尚未登录 MiMo，请登录后再采集",
        "ACQUIRE_UNEXPECTED": "采集 Cookie 时出现未预期错误",
    }

    @classmethod
    def describe_acquire_error(cls, exc: Exception) -> str:
        code = str(exc) if isinstance(exc, RuntimeError) else "ACQUIRE_UNEXPECTED"
        return cls.ACQUIRE_ERROR_MESSAGES.get(code, f"采集失败：{code}")

    def _base_url(self) -> str:
        custom = str(config_manager.get("MIMO_BASE", "")).strip()
        # 迁移早期版本默认指向 api.xiaomimimo.com；用量/余额端点只在 platform
        # platform.xiaomimimo.com 提供，因此把旧默认值替换为当前默认值。
        if custom in {"https://api.xiaomimimo.com", "api.xiaomimimo.com"}:
            custom = ""
        return custom or _MIMO_PLATFORM

    def _platform_headers(self) -> dict[str, str]:
        cookie_raw = str(config_manager.get("MIMO_COOKIE", "")).strip()
        cookie = self.normalize_cookie(cookie_raw)
        # 若 Cookie 中已经自带 ``api-platform_ph``，以它为准，不再向 Cookie
        # 头注入额外值；否则尝试从 ``MIMO_API_PLATFORM_PH`` 注入。两种方式
        # 只会取一个，避免在 Cookie 中出现重复的 api-platform_ph 项。
        ph = self.extract_cookie_value(cookie, "api-platform_ph")
        if not ph:
            ph = str(config_manager.get("MIMO_API_PLATFORM_PH", "")).strip()
            if ph:
                # 去外层引号，防止把 """" 拼到请求头里。
                ph_decoded = ph.strip().strip('"').strip().replace("%2F", "/").replace("%3D", "=")
                cookie = f'{cookie}; api-platform_ph="{ph_decoded}"'
        return {
            "accept": "*/*",
            "accept-language": "zh",
            "content-type": "application/json",
            "x-timezone": "Asia/Shanghai",
            "origin": self._base_url(),
            "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", '
            '"Not)A;Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "cookie": cookie,
            "referer": f"{self._base_url()}/console/usage",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/149.0.0.0 Safari/537.36"
            ),
        }

    def _url(self, path: str) -> str:
        """构造完整 URL，并在末尾附加 ``api-platform_ph``。

        ``ph`` 直接作为原始查询串附加，避免对用户从浏览器复制的百分
        比编码（如 ``%2F``）被二次编码；但会去掉外层双引号，防止
        拼到 URL 上时把 ``"`` 带进去导致 404。
        """
        base = self._base_url()
        url = f"{base}{path}"
        cookie_raw = str(config_manager.get("MIMO_COOKIE", "")).strip()
        ph = self.extract_cookie_value(self.normalize_cookie(cookie_raw), "api-platform_ph")
        if not ph:
            ph = str(config_manager.get("MIMO_API_PLATFORM_PH", "")).strip()
        if ph:
            # 去引号并修剪空白，防止 URL 出现 "%22" 或空格导致 404。
            ph = ph.strip().strip('"').strip()
        if ph:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}api-platform_ph={ph}"
        return url

    def _get(self, path: str) -> Any:
        if not self.is_configured():
            raise RuntimeError("NOT_CONFIGURED")
        try:
            response = self._session.get(
                self._url(path),
                headers=self._platform_headers(),
                timeout=(5, 15),
            )
        except requests.Timeout as exc:
            raise RuntimeError("NETWORK_TIMEOUT") from exc
        except requests.RequestException as exc:
            raise RuntimeError("NETWORK_ERROR") from exc
        return self._check_response(response)

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        if not self.is_configured():
            raise RuntimeError("NOT_CONFIGURED")
        try:
            response = self._session.post(
                self._url(path),
                json=body,
                headers=self._platform_headers(),
                timeout=(5, 15),
            )
        except requests.Timeout as exc:
            raise RuntimeError("NETWORK_TIMEOUT") from exc
        except requests.RequestException as exc:
            raise RuntimeError("NETWORK_ERROR") from exc
        return self._check_response(response)

    @staticmethod
    def _check_response(response: requests.Response) -> Any:
        if response.status_code in (401, 403):
            raise RuntimeError("AUTH_EXPIRED")
        if response.status_code == 429:
            raise RuntimeError("RATE_LIMITED")
        if response.status_code >= 500:
            raise RuntimeError("SERVER_ERROR")
        if not response.ok:
            raise RuntimeError(f"HTTP_{response.status_code}")
        try:
            payload = response.json()
        except requests.JSONDecodeError as exc:
            raise RuntimeError("INVALID_RESPONSE") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("INVALID_RESPONSE")
        if payload.get("code") in (401, "401"):
            raise RuntimeError("AUTH_EXPIRED")
        if payload.get("code") not in (0, "0", None):
            raise RuntimeError("API_ERROR")
        data = payload.get("data")
        if not isinstance(data, (dict, list)):
            raise RuntimeError("INVALID_RESPONSE")
        return data

    @staticmethod
    def _error(source: str, exc: Exception) -> FetchError:
        code = str(exc)
        messages = {
            "NOT_CONFIGURED": "尚未配置 MiMo Cookie",
            "AUTH_EXPIRED": "MiMo 登录状态已失效，请重新复制 Cookie",
            "NETWORK_TIMEOUT": "连接 MiMo 超时",
            "NETWORK_ERROR": "无法连接 MiMo",
            "RATE_LIMITED": "MiMo 请求过于频繁，请稍后重试",
            "SERVER_ERROR": "MiMo 服务暂时异常",
            "INVALID_RESPONSE": "MiMo 返回结构已变化",
            "API_ERROR": "MiMo 返回业务错误",
        }
        return FetchError(code, source, messages.get(code, f"MiMo 请求失败（{code}）"))

    # -------------------------------------------------------------- fetches
    def fetch_balance(self) -> tuple[ProviderBalance | None, FetchError | None]:
        try:
            data = self._get("/api/v1/balance")
        except Exception as exc:
            return None, self._error("MiMo 余额", exc)
        balance_str = str(data.get("balance", "0") or "0")
        currency = str(data.get("currency", "CNY") or "CNY")
        balance = _decimal(balance_str)
        # 账户为按量付费模式时，平台不返回套餐剩余 token；
        # 以 amount 作为余额主字段，让 UI 的账户余额/费用部分正常展示。
        return ProviderBalance(
            currency=currency,
            amount=balance,
            token_estimate=0,
        ), None

    def fetch_summary(self) -> tuple[ProviderSummary | None, FetchError | None]:
        try:
            data = self._get("/api/v1/usage")
        except Exception as exc:
            return None, self._error("MiMo 用量", exc)
        cost_usage = data.get("costUsage") or {}
        token_usage = data.get("tokenUsage") or {}
        month_cost = _decimal(cost_usage.get("currentMonthCost"))
        month_tokens = int(str(token_usage.get("totalToken", 0) or 0))
        return ProviderSummary(
            month_cost=month_cost,
            month_tokens=month_tokens,
            remaining_tokens=0,
        ), None

    def fetch_payloads(
        self, months: list[tuple[int, int]]
    ) -> tuple[list[dict[str, Any]], list[FetchError]]:
        """抓取指定月份的每日用量，合并为标准 ``{days, total}`` 结构。

        每一行包含：date/model/consumedAmount/inputHitToken/inputMissToken/
        outputToken/totalToken。按日期聚合后交给 data.store 做统一展示。
        """
        payloads: list[dict[str, Any]] = []
        errors: list[FetchError] = []
        for month, year in sorted(set(months)):
            try:
                rows = self._post(
                    "/api/v1/usage/detail/list",
                    {"year": year, "month": month},
                )
            except RuntimeError as exc:
                errors.append(self._error("MiMo 用量明细", exc))
                continue
            except Exception as exc:
                errors.append(self._error("MiMo 用量明细", exc))
                continue
            if not isinstance(rows, list):
                errors.append(FetchError("INVALID_RESPONSE", "MiMo 用量明细", "返回格式错误"))
                continue
            by_date: dict[str, dict[str, Any]] = {}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                day_str = str(row.get("date", ""))
                if not day_str:
                    continue
                if day_str not in by_date:
                    by_date[day_str] = {"date": day_str, "data": []}
                model = str(row.get("model", "unknown"))
                consumed = str(row.get("consumedAmount", "0") or "0")
                input_hit = int(row.get("inputHitToken", 0) or 0)
                input_miss = int(row.get("inputMissToken", 0) or 0)
                output = int(row.get("outputToken", 0) or 0)
                by_date[day_str]["data"].append({
                    "model": model,
                    "usage": [
                        {"type": "PROMPT_CACHE_HIT_TOKEN", "amount": str(input_hit)},
                        {"type": "PROMPT_CACHE_MISS_TOKEN", "amount": str(input_miss)},
                        {"type": "RESPONSE_TOKEN", "amount": str(output)},
                        {"type": "cost_cny", "amount": consumed},
                    ],
                })
            days = sorted(by_date.values(), key=lambda d: d["date"])
            payloads.append({"days": days, "total": []})
        return payloads, errors


__all__ = ["MiMoProvider"]
