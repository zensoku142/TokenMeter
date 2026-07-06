"""Runtime configuration, Windows credential storage, and logging."""

from __future__ import annotations

import ast
import ctypes
import json
import logging
import os
import sys
from ctypes import wintypes
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

APP_NAME = "TokenSpider"


SECRET_KEYS = (
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_AUTH",
    "DEEPSEEK_COOKIE",
    "MIMO_COOKIE",
    "MIMO_API_PLATFORM_PH",
    "MIMO_API_KEY",
)
OFFICIAL_HOSTS = {
    "platform.deepseek.com",
    "api.deepseek.com",
    "platform.xiaomimimo.com",
}
DEFAULT_CONFIG: dict[str, Any] = {
    "DEEPSEEK_API_KEY": "",
    "DEEPSEEK_AUTH": "",
    "DEEPSEEK_COOKIE": "",
    "DEEPSEEK_BASE": "https://platform.deepseek.com",
    "MIMO_COOKIE": "",
    "MIMO_API_PLATFORM_PH": "",
    "MIMO_API_KEY": "",
    "MIMO_BASE": "https://platform.xiaomimimo.com",
    "REFRESH_INTERVAL": 60_000,
    "WIDGET_COMPACT_SIZE": 96,
    "WIDGET_EXPANDED_SIZE": (820, 564),
    "BG_COLOR": "#071427",
    "ACCENT_COLOR": "#2f6fe4",
    "TEXT_COLOR": "#edf4ff",
    "ACTIVE_PROVIDER": "deepseek",
    "EDGE_HIDE_ENABLED": True,
}
FIELD_META: dict[str, dict[str, Any]] = {
    **{key: {"kind": "text", "secret": key in SECRET_KEYS} for key in DEFAULT_CONFIG},
    "REFRESH_INTERVAL": {"kind": "int", "min": 5_000},
    "WIDGET_COMPACT_SIZE": {"kind": "int", "min": 96, "max": 124},
    "WIDGET_EXPANDED_SIZE": {"kind": "tuple_int"},
    "BG_COLOR": {"kind": "color"},
    "ACCENT_COLOR": {"kind": "color"},
    "TEXT_COLOR": {"kind": "color"},
    "EDGE_HIDE_ENABLED": {"kind": "bool"},
}


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def config_dir() -> Path:
    # 配置固定放在用户目录，避免安装目录权限变化，也避免凭证随程序目录被复制。
    path = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


CONFIG_DIR = config_dir()
WIDGET_STATE_PATH = CONFIG_DIR / "widget-state.json"
CONFIG_PATH = CONFIG_DIR / "config.json"
LOG_PATH = CONFIG_DIR / "TokenSpider.log"
LEGACY_CONFIG_PATH = app_dir() / "config.py"
_config: dict[str, Any] = DEFAULT_CONFIG.copy()
_logger_ready = False


def logger() -> logging.Logger:
    global _logger_ready
    log = logging.getLogger(APP_NAME)
    if not _logger_ready:
        handler = RotatingFileHandler(
            LOG_PATH, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False
        _logger_ready = True
    return log


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_CREDENTIALW_TYPE = 1  # CRED_TYPE_GENERIC
# Initialise the advapi32 DLL binding on first import (i.e. the main
# thread). Doing this lazily inside worker threads can trigger an
# `Error calling Python override of QThread::run()` because Windows
# credential APIs expect to be called from a thread that has already
# performed module initialisation.
_advapi32 = None
if os.name == "nt":
    try:
        _advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        _advapi32.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)),
        ]
        _advapi32.CredReadW.restype = wintypes.BOOL
        _advapi32.CredWriteW.argtypes = [
            ctypes.POINTER(_CREDENTIALW),
            wintypes.DWORD,
        ]
        _advapi32.CredWriteW.restype = wintypes.BOOL
        _advapi32.CredDeleteW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        _advapi32.CredDeleteW.restype = wintypes.BOOL
        _advapi32.CredFree.argtypes = [ctypes.c_void_p]
    except Exception:
        _advapi32 = None


def _credential_target(key: str) -> str:
    return f"{APP_NAME}/{key}"


def _read_credential(key: str) -> str:
    if os.name != "nt":
        return os.environ.get(key, "")
    if _advapi32 is None:
        return ""
    pointer = ctypes.POINTER(_CREDENTIALW)()
    try:
        if not _advapi32.CredReadW(
            _credential_target(key),
            _CREDENTIALW_TYPE,
            0,
            ctypes.byref(pointer),
        ):
            return ""
        credential = pointer.contents
        if not credential.CredentialBlob or not credential.CredentialBlobSize:
            return ""
        raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return raw.decode("utf-16-le")
    except Exception:
        return ""
    finally:
        if pointer:
            try:
                _advapi32.CredFree(pointer)
            except Exception:
                pass


def _write_credential(key: str, value: str) -> None:
    if os.name != "nt":
        if value:
            raise OSError("非 Windows 环境请通过同名环境变量提供凭证")
        return
    if _advapi32 is None:
        if value:
            raise OSError("Windows 凭据管理器不可用，凭据未保存")
        return
    if not value:
        if not _advapi32.CredDeleteW(_credential_target(key), _CREDENTIALW_TYPE, 0):
            error = ctypes.get_last_error()
            # 1168 表示凭据本来就不存在，属于幂等删除成功。
            if error != 1168:
                raise ctypes.WinError(error)
        return
    raw = value.encode("utf-16-le")
    blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    credential = _CREDENTIALW()
    credential.Type = _CREDENTIALW_TYPE
    credential.TargetName = _credential_target(key)
    credential.CredentialBlobSize = len(raw)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = 2
    credential.UserName = APP_NAME
    if not _advapi32.CredWriteW(ctypes.byref(credential), 0):
        raise ctypes.WinError()


def _parse_legacy_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id.isupper():
                values[target.id] = ast.literal_eval(node.value)
    return values


def validate_value(key: str, value: Any) -> Any:
    meta = FIELD_META.get(key, {"kind": "text"})
    kind = meta["kind"]
    if kind == "int":
        value = int(value)
        if "min" in meta and value < meta["min"]:
            raise ValueError(f"{key} 不能小于 {meta['min']}")
        if "max" in meta and value > meta["max"]:
            raise ValueError(f"{key} 不能大于 {meta['max']}")
        return value
    if kind == "tuple_int":
        if isinstance(value, list):
            value = tuple(value)
        if isinstance(value, str):
            parts = [p.strip() for p in value.replace("x", ",").split(",") if p.strip()]
            if len(parts) != 2:
                raise ValueError(f"{key} 必须是宽,高格式")
            value = (int(parts[0]), int(parts[1]))
        if not isinstance(value, tuple) or len(value) != 2 or not all(
            isinstance(v, int) for v in value
        ):
            raise ValueError(f"{key} 必须是两个整数")
        if value[0] < 280 or value[1] < 360:
            raise ValueError(f"{key} 尺寸过小")
        return value
    if kind == "color":
        value = str(value).strip()
        if len(value) != 7 or not value.startswith("#"):
            raise ValueError(f"{key} 必须是 #RRGGBB 颜色")
        int(value[1:], 16)
        return value
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        raise ValueError(f"{key} 必须是布尔值")
    return str(value)


# 旧调用方可能仍引用私有函数；保留别名，设置窗口已改用公开入口。
_validate_value = validate_value


def validate_config(values: dict[str, Any]) -> dict[str, Any]:
    merged = DEFAULT_CONFIG.copy()
    merged.update(values)
    for key in list(merged):
        merged[key] = validate_value(key, merged[key])
    active_provider = str(merged.get("ACTIVE_PROVIDER", "deepseek")).strip().lower()
    if active_provider not in {"deepseek", "mimo"}:
        raise ValueError("ACTIVE_PROVIDER 必须是 deepseek 或 mimo")
    merged["ACTIVE_PROVIDER"] = active_provider
    # Provider 凭据会随请求发送，因此自定义地址至少必须是完整的 HTTP(S) URL；
    # 是否信任非官方主机由设置窗口在保存前再次向用户确认。
    for key in FIELD_META:
        if not str(key).endswith("_BASE"):
            continue
        value = str(merged.get(key, "")).strip()
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"{key} 必须是有效的 HTTP(S) 地址")
    return merged


def is_official_base_url(value: str) -> bool:
    return (urlparse(value).hostname or "").lower() in OFFICIAL_HOSTS


def _public_values(values: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in values.items() if key not in SECRET_KEYS}
    result["credential_store"] = "windows-credential-manager"
    return result


def _write_json(path: Path, values: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _load_public_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("config.json 顶层必须是对象")
    value.pop("credential_store", None)
    compact_size = int(value.get("WIDGET_COMPACT_SIZE", 96))
    # 108 和 120 是前两版默认值；只迁移默认值以保留其他合法自定义尺寸。
    if compact_size < 96 or compact_size in (108, 120):
        value["WIDGET_COMPACT_SIZE"] = 96
    panel_size = value.get("WIDGET_EXPANDED_SIZE", [820, 564])
    if isinstance(panel_size, (list, tuple)) and len(panel_size) == 2:
        if int(panel_size[0]) < 680 or int(panel_size[1]) < 564:
            value["WIDGET_EXPANDED_SIZE"] = [820, 564]
    return value


def _migrate_legacy_config() -> dict[str, Any]:
    values = _parse_legacy_config(LEGACY_CONFIG_PATH)
    if not values:
        return {}
    for key in SECRET_KEYS:
        secret = str(values.pop(key, ""))
        if secret:
            _write_credential(key, secret)
    logger().info("Migrated legacy config to user data directory")
    return values


def ensure_config_file() -> None:
    if CONFIG_PATH.exists():
        return
    values = DEFAULT_CONFIG.copy()
    legacy_values = _migrate_legacy_config()
    values.update(legacy_values)
    _write_json(CONFIG_PATH, _public_values(validate_config(values)))
    if legacy_values:
        try:
            # 凭据和普通配置均落盘成功后移除明文旧文件，避免迁移后仍残留一份密钥。
            LEGACY_CONFIG_PATH.unlink()
        except OSError:
            logger().warning("Legacy config could not be removed: %s", LEGACY_CONFIG_PATH)
    logger().info("Created config at %s", CONFIG_PATH)


def load_widget_position() -> tuple[int, int] | None:
    try:
        value = json.loads(WIDGET_STATE_PATH.read_text(encoding="utf-8"))
        return int(value["x"]), int(value["y"])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def save_widget_position(x: int, y: int) -> None:
    try:
        # 位置状态独立于用户配置，拖动时不会触发配置备份或凭据写入。
        WIDGET_STATE_PATH.write_text(
            json.dumps({"x": int(x), "y": int(y)}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        logger().warning("Widget position could not be saved")


def load_config() -> dict[str, Any]:
    global _config
    ensure_config_file()
    try:
        values = _load_public_config()
        for key in SECRET_KEYS:
            values[key] = _read_credential(key)
        _config = validate_config(values)
    except Exception as exc:
        logger().exception("Config load failed, using previous/default values: %s", exc)
    return _config.copy()


def get(key: str, default: Any = None) -> Any:
    return _config.get(key, default)


def all_config() -> dict[str, Any]:
    return _config.copy()


def _prune_backups() -> None:
    backups = sorted(CONFIG_DIR.glob("config.json.bak-*"), reverse=True)
    for path in backups[3:]:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # 备份清理失败不能回滚已经原子写入的有效配置。
            logger().warning("Old config backup could not be removed: %s", path)


def save_config(values: dict[str, Any]) -> dict[str, Any]:
    global _config
    ensure_config_file()
    merged = _config.copy()
    merged.update(values)
    validated = validate_config(merged)
    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    backup_path = CONFIG_DIR / f"config.json.bak-{stamp}"
    temp_path = CONFIG_DIR / "config.json.tmp"
    _write_json(backup_path, _public_values(_config))
    old_secrets = {key: _config.get(key, "") for key in SECRET_KEYS}
    try:
        for key in SECRET_KEYS:
            _write_credential(key, validated[key])
        _write_json(temp_path, _public_values(validated))
        temp_path.replace(CONFIG_PATH)
        _config = validated.copy()
        _prune_backups()
        logger().info("Config saved successfully")
        return _config.copy()
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        # 多个凭据必须作为一组回滚，避免中途失败后出现新旧凭据混用。
        for key, value in old_secrets.items():
            try:
                _write_credential(key, value)
            except Exception:
                logger().exception("Credential rollback failed for %s", key)
        logger().exception("Config save failed; public config was not replaced: %s", exc)
        raise


load_config()
