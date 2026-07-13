"""Shared Chrome DevTools Protocol helpers for platform Cookie acquisition."""

from __future__ import annotations

import http.client
import json
import os
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


_CDP_PORT_RANGE = range(9222, 9323)
_CDP_TIMEOUT_SECONDS = 10
_ACQUIRE_TOTAL_TIMEOUT_SECONDS = 180


def normalize_cookie(raw: str) -> str:
    return "; ".join(
        token.strip()
        for token in " ".join(str(raw).splitlines()).split(";")
        if token.strip()
    )


def default_user_data_dir(profile_name: str) -> str:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return str(Path(base) / "TokenSpider" / profile_name)


def find_chrome_executable(use_edge: bool = False) -> str:
    """Find a Chromium browser without relying on the user's main profile."""

    candidates: list[str] = []
    try:
        import winreg

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

    local_appdata = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    program_files = (
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    )
    if use_edge:
        candidates.extend(
            str(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
            for base in program_files
        )
    else:
        candidates.extend(
            str(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
            for base in program_files
        )
        candidates.append(str(Path(local_appdata) / "Google" / "Chrome" / "Application" / "chrome.exe"))
    return next((path for path in candidates if path and os.path.isfile(path)), "")


def pick_free_cdp_port() -> int:
    for port in _CDP_PORT_RANGE:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return -1


def _http_json(host: str, port: int, path: str, timeout: float) -> Any:
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        data = response.read()
    finally:
        conn.close()
    if response.status != 200:
        raise RuntimeError(f"CDP_HTTP_{response.status}")
    try:
        return json.loads(data.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("CDP_INVALID_JSON") from exc


def _wait_browser_ready(port: int, stop_event: threading.Event, timeout: float = 15.0) -> None:
    deadline = threading.Event()
    timer = threading.Timer(timeout, deadline.set)
    timer.daemon = True
    timer.start()
    try:
        while not stop_event.is_set() and not deadline.is_set():
            try:
                _http_json("127.0.0.1", port, "/json/version", 1.0)
                return
            except (OSError, RuntimeError, http.client.HTTPException):
                stop_event.wait(0.25)
        raise RuntimeError("BROWSER_NOT_READY")
    finally:
        timer.cancel()


def _pick_websocket_endpoint(port: int, preferred_host: str) -> str:
    items = _http_json("127.0.0.1", port, "/json", _CDP_TIMEOUT_SECONDS)
    if not isinstance(items, list):
        raise RuntimeError("CDP_UNEXPECTED_JSON")
    candidate = ""
    for entry in items:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") not in {"page", "app", "background_page", "other"}:
            continue
        websocket_url = str(entry.get("webSocketDebuggerUrl") or "")
        if not websocket_url:
            continue
        if preferred_host in str(entry.get("url") or ""):
            return websocket_url
        if not candidate:
            candidate = websocket_url
    if candidate:
        return candidate
    raise RuntimeError("CDP_NO_PAGE_TARGET")


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < count:
        chunk = sock.recv(count - len(chunks))
        if not chunk:
            raise RuntimeError("CDP_CONN_CLOSED")
        chunks.extend(chunk)
    return bytes(chunks)


def _cdp_fetch_cookies_via_ws(websocket_url: str) -> list[dict[str, Any]]:
    parsed = urlparse(websocket_url)
    if parsed.scheme != "ws" or not parsed.hostname or not parsed.port:
        raise RuntimeError("CDP_INVALID_WS_URL")
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {parsed.hostname}:{parsed.port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock = socket.create_connection((parsed.hostname, int(parsed.port)), timeout=_CDP_TIMEOUT_SECONDS)
    try:
        sock.sendall(request.encode("ascii"))
        response = bytearray()
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(2048)
            if not chunk:
                raise RuntimeError("CDP_WS_HANDSHAKE_FAILED")
            response.extend(chunk)
        header_text = response[: response.index(b"\r\n\r\n")].decode("ascii", errors="replace")
        if "101" not in header_text:
            raise RuntimeError("CDP_WS_HANDSHAKE_FAILED")

        payload = json.dumps({"id": 1, "method": "Network.getAllCookies"}).encode("utf-8")
        if len(payload) > 0x7F:
            raise RuntimeError("CDP_PAYLOAD_TOO_LARGE")
        # CDP accepts the zero mask, while the frame still satisfies the client-mask rule.
        sock.sendall(bytes((0x81, 0x80 | len(payload))) + bytes(4) + payload)

        first, second = _recv_exact(sock, 2)
        if not first & 0x80 or first & 0x0F not in (1, 2):
            raise RuntimeError("CDP_UNEXPECTED_FRAME")
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            (length,) = struct.unpack(">H", _recv_exact(sock, 2))
        elif length == 127:
            (length,) = struct.unpack(">Q", _recv_exact(sock, 8))
        mask = _recv_exact(sock, 4) if masked else b""
        body = _recv_exact(sock, length)
        if masked:
            body = bytes(value ^ mask[index % 4] for index, value in enumerate(body))
        try:
            response_data = json.loads(body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("CDP_INVALID_JSON") from exc
        cookies = (
            response_data.get("result", {}).get("cookies")
            if isinstance(response_data, dict)
            else None
        )
        if not isinstance(cookies, list):
            raise RuntimeError("CDP_UNEXPECTED_RESPONSE")
        return cookies
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _cdp_send_text(websocket_url: str, payload: dict[str, Any]) -> None:
    parsed = urlparse(websocket_url)
    if parsed.scheme != "ws" or not parsed.hostname or not parsed.port:
        return
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {parsed.hostname}:{parsed.port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    try:
        sock = socket.create_connection((parsed.hostname, int(parsed.port)), timeout=_CDP_TIMEOUT_SECONDS)
    except OSError:
        return
    try:
        sock.sendall(request.encode("ascii"))
        response = bytearray()
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(2048)
            if not chunk:
                return
            response.extend(chunk)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(body) > 0x7F:
            return
        sock.sendall(bytes((0x81, 0x80 | len(body))) + bytes(4) + body)
    except OSError:
        return
    finally:
        try:
            sock.close()
        except OSError:
            pass


def format_cookie_string(
    cookies: list[dict[str, Any]],
    *,
    allowed_domains: tuple[str, ...],
    cookie_names: tuple[str, ...] | None = None,
) -> str:
    """Return only first-party cookies, keeping provider-defined order when needed."""

    values: dict[str, str] = {}
    order: list[str] = []
    normalized_domains = tuple(domain.lstrip(".").lower() for domain in allowed_domains)
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name") or "")
        if not name or (cookie_names is not None and name not in cookie_names):
            continue
        domain = str(cookie.get("domain") or "").lstrip(".").lower()
        if domain and not any(domain == item or domain.endswith(f".{item}") for item in normalized_domains):
            continue
        value = str(cookie.get("value") or "").strip('"')
        if not value and name in values:
            continue
        values[name] = value
        if name not in order:
            order.append(name)
    names = cookie_names if cookie_names is not None else tuple(order)
    return "; ".join(f"{name}={values[name]}" for name in names if values.get(name))


def _unexpired_cookies(
    cookies: list[dict[str, Any]], now: float | None = None
) -> list[dict[str, Any]]:
    current_time = time.time() if now is None else now
    result: list[dict[str, Any]] = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        try:
            expires_at = float(cookie.get("expires"))
        except (TypeError, ValueError):
            expires_at = -1
        if expires_at > 0 and expires_at <= current_time:
            continue
        result.append(cookie)
    return result


def has_valid_required_cookies(
    cookies: list[dict[str, Any]],
    *,
    allowed_domains: tuple[str, ...],
    cookie_names: tuple[str, ...],
    now: float | None = None,
) -> bool:
    """Return whether all required first-party cookies are currently usable."""

    valid_names: set[str] = set()
    normalized_domains = tuple(domain.lstrip(".").lower() for domain in allowed_domains)
    for cookie in _unexpired_cookies(cookies, now):
        name = str(cookie.get("name") or "")
        if name not in cookie_names:
            continue
        domain = str(cookie.get("domain") or "").lstrip(".").lower()
        if domain and not any(
            domain == item or domain.endswith(f".{item}")
            for item in normalized_domains
        ):
            continue
        if not str(cookie.get("value") or "").strip('"'):
            continue
        valid_names.add(name)
    return all(name in valid_names for name in cookie_names)


def acquire_cookie_via_chrome(
    stop_event: threading.Event,
    *,
    acquire_url: str,
    profile_name: str,
    allowed_domains: tuple[str, ...],
    cookie_names: tuple[str, ...] | None,
    empty_cookie_error: str,
    use_edge: bool = False,
    user_data_dir: str | None = None,
    auto_collect: bool = False,
    headless: bool = False,
    total_timeout_seconds: float | None = None,
) -> str:
    """Open an isolated browser profile, then read scoped first-party cookies via CDP.

    Manual collection waits for the caller to signal completion. Automatic
    collection polls until every provider-required cookie is present and valid.
    """

    executable = find_chrome_executable(use_edge=use_edge)
    if not executable:
        raise RuntimeError("CHROME_NOT_FOUND")
    data_dir = Path(user_data_dir or default_user_data_dir(profile_name))
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError("USER_DATA_DIR_FAILED") from exc
    port = pick_free_cdp_port()
    if port < 0:
        raise RuntimeError("NO_FREE_CDP_PORT")

    args = [
        executable,
        f"--user-data-dir={data_dir}",
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        "--no-first-run",
        "--disable-default-apps",
    ]
    if headless:
        args.extend(("--headless=new", "--disable-gpu", acquire_url))
    else:
        args.append(f"--app={acquire_url}")
    startupinfo = None
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    except AttributeError:
        pass
    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            startupinfo=startupinfo,
        )
    except OSError as exc:
        raise RuntimeError("CHROME_LAUNCH_FAILED") from exc

    websocket_url = ""
    try:
        _wait_browser_ready(port, stop_event)
        preferred_host = urlparse(acquire_url).hostname or ""
        total_timeout = (
            _ACQUIRE_TOTAL_TIMEOUT_SECONDS
            if total_timeout_seconds is None
            else max(1.0, float(total_timeout_seconds))
        )
        if not auto_collect:
            stop_event.wait(timeout=total_timeout)
            websocket_url = _pick_websocket_endpoint(port, preferred_host)
            cookie_text = format_cookie_string(
                _cdp_fetch_cookies_via_ws(websocket_url),
                allowed_domains=allowed_domains,
                cookie_names=cookie_names,
            )
            if not cookie_text:
                raise RuntimeError(empty_cookie_error)
            return normalize_cookie(cookie_text)

        deadline = time.monotonic() + total_timeout
        required_names = cookie_names or ()
        while not stop_event.is_set() and time.monotonic() < deadline:
            websocket_url = _pick_websocket_endpoint(port, preferred_host)
            cookies = _unexpired_cookies(_cdp_fetch_cookies_via_ws(websocket_url))
            if required_names and has_valid_required_cookies(
                cookies,
                allowed_domains=allowed_domains,
                cookie_names=required_names,
            ):
                return normalize_cookie(
                    format_cookie_string(
                        cookies,
                        allowed_domains=allowed_domains,
                        cookie_names=cookie_names,
                    )
                )
            stop_event.wait(0.5)
        raise RuntimeError(empty_cookie_error)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("ACQUIRE_UNEXPECTED") from exc
    finally:
        if websocket_url:
            _cdp_send_text(websocket_url, {"id": 1, "method": "Browser.close"})
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            # A failed browser shutdown must not leave a hidden acquisition process running.
            try:
                process.terminate()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
        except OSError:
            pass
