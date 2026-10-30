"""Query configured CC Switch accounts through its running Tauri window."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from contextlib import closing, suppress
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from api.browser_cookie import (
    _CDP_PORT_RANGE,
    _CdpConnection,
    _http_json,
    pick_free_cdp_port,
)
from config import runtime as config_manager

# 查询持锁后可能恢复连接；可重入锁保证多账号不会同时启动 CC 或覆盖桥接端口。
_lock = threading.RLock()
_retry_after = 0.0
_connect_error = "CC_SWITCH_NOT_CONNECTED"


def database_path() -> Path:
    return Path.home() / ".cc-switch" / "cc-switch.db"


def codex_accounts() -> list[dict[str, str]]:
    path = database_path()
    if not path.is_file():
        return []
    accounts = {}
    try:
        # 只读供应商名称和账号绑定；OAuth 令牌始终由 CC Switch 自己管理。
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            rows = db.execute(
                "SELECT name, meta FROM providers WHERE app_type='codex' "
                "ORDER BY sort_index, created_at LIMIT 500"
            )
            for name, raw in rows:
                try:
                    meta = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                binding = meta.get("authBinding") if isinstance(meta, dict) else None
                if not isinstance(binding, dict):
                    continue
                account_id = binding.get("accountId")
                if (binding.get("source") == "managed_account"
                        and binding.get("authProvider") == "codex_oauth"
                        and isinstance(account_id, str) and account_id.strip()):
                    accounts.setdefault(account_id, {"id": account_id, "name": str(name)})
    except (OSError, sqlite3.Error):
        return []
    return list(accounts.values())


def account_identity(account_id: str) -> str:
    if not any(account["id"] == account_id for account in codex_accounts()):
        return ""
    # CC 的本地账号 ID 区分同一 workspace 下的不同用户，且不随令牌续期变化。
    scope = f"cc-switch:{database_path().resolve()}:{account_id}"
    return hashlib.sha256(scope.encode()).hexdigest()


def _state_path() -> Path:
    return config_manager.CONFIG_DIR / "cc-switch-bridge.json"


def _saved_port() -> int:
    try:
        with _state_path().open("rb") as stream:
            payload = json.loads(stream.read(1024))
        port = payload.get("port") if isinstance(payload, dict) else None
        return port if type(port) is int and 1024 <= port <= 65535 else 0
    except (OSError, ValueError):
        return 0


def _endpoint(port: int, *, timeout: float = 1) -> str:
    if not port:
        raise RuntimeError("CC_SWITCH_NOT_CONNECTED")
    targets = _http_json("127.0.0.1", port, "/json/list", timeout)
    if not isinstance(targets, list):
        raise RuntimeError("CC_SWITCH_NOT_CONNECTED")
    for target in targets:
        if not isinstance(target, dict) or target.get("type") != "page":
            continue
        page = urlparse(str(target.get("url") or ""))
        ws = str(target.get("webSocketDebuggerUrl") or "")
        parsed = urlparse(ws)
        title = str(target.get("title") or "")
        # 部分版本保留最初的 HTML 标题，窗口显示名则已经改为 CC Switch。
        is_cc = "cc switch" in title.lower() or title == "Claude Code 供应商切换器"
        # 调试端口可能已被其他程序复用；只连接本机 CC Switch 的正式 Tauri 页面。
        if (page.hostname in {"tauri.localhost", "localhost"}
                and page.scheme in {"http", "https", "tauri"}
                and is_cc
                and parsed.scheme == "ws" and parsed.hostname in {"127.0.0.1", "localhost"}
                and parsed.port == port):
            return ws
    raise RuntimeError("CC_SWITCH_NOT_CONNECTED")


def _executable() -> str:
    try:
        import winreg

        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_CLASSES_ROOT):
            key_path = ("Software\\Classes\\" if root == winreg.HKEY_CURRENT_USER else "")
            try:
                with winreg.OpenKey(root, key_path + r"ccswitch\shell\open\command") as key:
                    command, _ = winreg.QueryValueEx(key, "")
                # 协议注册项含占位参数；仅取已注册的 exe，绝不交给 shell 执行。
                executable = command.split('"')[1] if command.startswith('"') else command.split()[0]
                if Path(executable).is_file() and executable.lower().endswith(".exe"):
                    return executable
            except OSError:
                continue
    except ImportError:
        pass
    return shutil.which("cc-switch") or ""


def connect() -> None:
    global _retry_after, _connect_error

    if not codex_accounts():
        raise RuntimeError("CC_SWITCH_NOT_CONFIGURED")
    with _lock:
        try:
            _endpoint(_saved_port())
            _retry_after = 0
            return
        except (OSError, RuntimeError, ValueError, http.client.HTTPException):
            pass
        if time.monotonic() < _retry_after:
            raise RuntimeError(_connect_error)
        # 多个账号共用连接；失败后短暂退避，避免每个账号都启动一个单实例进程。
        _retry_after = time.monotonic() + 60
        _connect_error = "CC_SWITCH_NOT_CONNECTED"
        # 数据目录变更或端口记录丢失时，先复用本机现有桥接，不能误判为需要重启 CC。
        for port in _CDP_PORT_RANGE:
            try:
                _endpoint(port, timeout=0.1)
            except (OSError, RuntimeError, ValueError, http.client.HTTPException):
                continue
            path = _state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"port": port}), encoding="utf-8")
            _retry_after = 0
            return
        executable = _executable()
        if not executable:
            raise RuntimeError("CC_SWITCH_NOT_INSTALLED")
        port = pick_free_cdp_port()
        if port < 0:
            raise RuntimeError("CC_SWITCH_NOT_CONNECTED")
        env = dict(os.environ)
        # 仅此次 CC 子进程启用本机桥接；不修改注册表、全局环境或 CC 配置。
        env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
            f"--remote-debugging-port={port} --remote-debugging-address=127.0.0.1"
        )
        startup = None
        if os.name == "nt":
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
        process = subprocess.Popen(
            [executable], env=env, startupinfo=startup,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # 窗口慢加载时下一次连接仍可复用此次启动，避免反复启动单实例进程。
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"port": port}), encoding="utf-8")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                _endpoint(port)
                _retry_after = 0
                return
            except (OSError, RuntimeError, ValueError, http.client.HTTPException):
                if process.poll() is not None:
                    break
                time.sleep(0.2)
        # 已运行的单实例 CC 不会接受新启动参数；不能为获取额度强杀用户进程。
        _connect_error = "CC_SWITCH_RESTART_REQUIRED"
        raise RuntimeError(_connect_error)


def fetch_codex_quota(account_id: str) -> dict:
    if not account_identity(account_id):
        raise RuntimeError("CC_SWITCH_NOT_CONFIGURED")
    with _lock:
        connection = None
        key = "__tokenmeter_quota_" + uuid4().hex
        try:
            # 首次采集、程序重启和 CC 退出后都通过同一路径自动恢复，无需手动连接。
            connect()
            connection = _CdpConnection(_endpoint(_saved_port()))
            # 仅调用已核对的额度命令，并只取额度字段；不读取或复制 CC 的 OAuth 凭据。
            expression = (
                "(() => {const job = {done:false};"
                f"globalThis[{json.dumps(key)}] = job;"
                "window.__TAURI_INTERNALS__.invoke('get_codex_oauth_quota',"
                f"{{accountId:{json.dumps(account_id)}}})"
                ".then(q => {job.quota = {success:q.success,credentialStatus:q.credentialStatus,"
                "tiers:q.tiers,creditsBalance:q.creditsBalance,queriedAt:q.queriedAt};job.done=true;})"
                ".catch(() => {job.failed=true;job.done=true;});return true;})()"
            )
            started = connection.call("Runtime.evaluate", {"expression": expression})
            if "exceptionDetails" in started:
                raise RuntimeError("CC_SWITCH_QUERY_FAILED")
            # CC 的令牌续期和额度请求可能超过单次 CDP 超时；轮询任务避免阻塞传输。
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                result = connection.call("Runtime.evaluate", {
                    "expression": f"globalThis[{json.dumps(key)}]", "returnByValue": True,
                })
                value = result.get("result", {}).get("value")
                if not isinstance(value, dict):
                    raise RuntimeError("CC_SWITCH_QUERY_FAILED")
                if value.get("done"):
                    if value.get("failed") or not isinstance(value.get("quota"), dict):
                        raise RuntimeError("CC_SWITCH_QUERY_FAILED")
                    return value["quota"]
                time.sleep(0.2)
            raise RuntimeError("CC_SWITCH_QUERY_TIMEOUT")
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise RuntimeError("CC_SWITCH_NOT_CONNECTED") from exc
        finally:
            if connection is not None:
                with suppress(OSError, RuntimeError):
                    connection.call("Runtime.evaluate", {"expression": f"delete globalThis[{json.dumps(key)}]"})
                connection.close()
