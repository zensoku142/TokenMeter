"""Runtime configuration facade and initialization orchestration."""

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

from app_identity import APP_STORAGE_NAME, SINGLE_INSTANCE_MUTEX
from config.credentials import (
    credential_target as _credential_target,
    read_credential as _read_credential,
    read_credential_target as _read_credential_target,
    write_credential as _write_credential,
)
from config.defaults import DEFAULT_CONFIG, FIELD_META, OFFICIAL_HOSTS, SECRET_KEYS
from config.migration import (
    migrate_data_dir as _migrate_data_dir,
    normalize_data_dir as _normalize_data_dir,
    validate_separate_dirs as _validate_separate_dirs,
)
from config.store import (
    is_official_base_url,
    load_public_config,
    public_values as _public_values,
    validate_config,
    validate_value,
)
from config import state as state_store
from data_directory import (
    application_dir,
    legacy_data_dir,
    resolve_data_dir,
)
import data_directory

APP_NAME = APP_STORAGE_NAME


def app_dir() -> Path:
    return application_dir()


DEFAULT_CONFIG_DIR = legacy_data_dir()
LOCATION_PATH = DEFAULT_CONFIG_DIR / "location.json"
_LOCATION_VERSION = 1


def _load_location_state() -> dict[str, Any]:
    try:
        values = json.loads(LOCATION_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return values if isinstance(values, dict) else {}


def _write_location_state(values: dict[str, Any]) -> None:
    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"version": _LOCATION_VERSION, **values}
    temp_path = LOCATION_PATH.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temp_path.replace(LOCATION_PATH)


def _another_instance_running() -> bool:
    if sys.platform != "win32":
        return False
    synchronize = 0x00100000
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    handle = kernel32.OpenMutexW(synchronize, False, SINGLE_INSTANCE_MUTEX)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def _data_entries(
    path: Path, *, exclude_migration_backups: bool = False
) -> list[Path]:
    if not path.exists():
        return []
    location_path = LOCATION_PATH.resolve(strict=False)
    return [
        item
        for item in path.iterdir()
        if item.resolve(strict=False) != location_path
        and (
            not exclude_migration_backups
            or not item.name.startswith("migration-backup-")
        )
    ]


def validate_data_dir_target(value: str | os.PathLike[str]) -> Path:
    target = _normalize_data_dir(value)
    current = CONFIG_DIR.resolve(strict=False)
    _validate_separate_dirs(current, target)
    if target == current:
        return target
    target.mkdir(parents=True, exist_ok=True)
    probe_path = target / ".tokenmeter-write-test"
    try:
        probe_path.write_text("ok", encoding="utf-8")
        probe_path.unlink()
    except OSError as exc:
        raise ValueError(f"应用数据目录不可写：{exc}") from exc
    default_dir = DEFAULT_CONFIG_DIR.resolve(strict=False)
    if target != default_dir and _data_entries(target):
        raise ValueError("新的应用数据目录必须为空")
    return target


def _initialize_data_dir() -> tuple[Path, dict[str, Any]]:
    state = _load_location_state()
    pending_value = state.get("pending_data_dir")
    explicit_value = state.get("data_dir")
    # 旧版本把默认 AppData 目录也写成显式位置；它仍属于待迁移的旧目录。
    if explicit_value:
        try:
            normalized = _normalize_data_dir(explicit_value)
            if normalized == DEFAULT_CONFIG_DIR.resolve(strict=False):
                explicit_value = None
        except ValueError:
            explicit_value = None
    try:
        active_dir = resolve_data_dir(explicit_dir=explicit_value)
        state = {"data_dir": str(active_dir)}
    except (OSError, ValueError) as exc:
        active_dir = DEFAULT_CONFIG_DIR.resolve(strict=False)
        active_dir.mkdir(parents=True, exist_ok=True)
        state = {"data_dir": str(active_dir), "migration_error": str(exc)}

    if pending_value and not _another_instance_running():
        source_dir = active_dir
        try:
            pending_dir = _normalize_data_dir(pending_value)
            _migrate_data_dir(source_dir, pending_dir)
            next_state = {"data_dir": str(pending_dir)}
            _write_location_state(next_state)
        except (OSError, ValueError) as exc:
            active_dir = source_dir
            state = {"data_dir": str(source_dir), "migration_error": str(exc)}
            try:
                _write_location_state(state)
            except OSError:
                pass
        else:
            # 数据目录切换也保留原目录，避免迁移成功后立即失去回滚能力。
            active_dir = pending_dir
            state = next_state

    try:
        active_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        active_dir = DEFAULT_CONFIG_DIR.resolve(strict=False)
        active_dir.mkdir(parents=True, exist_ok=True)
        state = {"data_dir": str(active_dir), "migration_error": str(exc)}
        _write_location_state(state)
    return active_dir, state


def config_dir() -> Path:
    # 启动阶段已经通过固定指针解析实际数据目录。
    return CONFIG_DIR


CONFIG_DIR = DEFAULT_CONFIG_DIR.resolve(strict=False)
_location_state: dict[str, Any] = {}
WIDGET_STATE_PATH = CONFIG_DIR / "widget-state.json"
PANEL_LAYOUT_PATH = CONFIG_DIR / "panel-layout.json"
CONFIG_PATH = CONFIG_DIR / "config.json"
LOG_PATH = CONFIG_DIR / "TokenSpider.log"
UPDATE_STATE_PATH = CONFIG_DIR / "update-state.json"
UPDATE_CACHE_DIR = CONFIG_DIR / "updates"
PENDING_UPDATE_CLEANUP_PATH = CONFIG_DIR / "pending-update-cleanup.json"
UPDATER_LOG_PATH = CONFIG_DIR / "TokenScopeUpdater.log"
LEGACY_CONFIG_PATH = app_dir() / "config.py"
_config: dict[str, Any] = DEFAULT_CONFIG.copy()
_logger_ready = False
_initialized = False


def _set_runtime_paths(config_dir: Path) -> None:
    global CONFIG_DIR, WIDGET_STATE_PATH, PANEL_LAYOUT_PATH, CONFIG_PATH, LOG_PATH
    global UPDATE_STATE_PATH, UPDATE_CACHE_DIR, PENDING_UPDATE_CLEANUP_PATH
    global UPDATER_LOG_PATH
    CONFIG_DIR = config_dir
    WIDGET_STATE_PATH = CONFIG_DIR / "widget-state.json"
    PANEL_LAYOUT_PATH = CONFIG_DIR / "panel-layout.json"
    CONFIG_PATH = CONFIG_DIR / "config.json"
    LOG_PATH = CONFIG_DIR / "TokenSpider.log"
    UPDATE_STATE_PATH = CONFIG_DIR / "update-state.json"
    UPDATE_CACHE_DIR = CONFIG_DIR / "updates"
    PENDING_UPDATE_CLEANUP_PATH = CONFIG_DIR / "pending-update-cleanup.json"
    UPDATER_LOG_PATH = CONFIG_DIR / "TokenScopeUpdater.log"


def initialize() -> None:
    """Initialize the active data directory, logging, and persisted config once."""

    global _initialized, _location_state
    if _initialized:
        return
    active_dir, location_state = _initialize_data_dir()
    _set_runtime_paths(active_dir)
    _location_state = location_state
    # 初始化标记必须先设置；配置读取失败后的日志记录不能递归触发初始化。
    _initialized = True
    logger()
    load_config()


def pending_data_dir() -> Path | None:
    value = _location_state.get("pending_data_dir")
    if not value:
        return None
    try:
        return _normalize_data_dir(value)
    except ValueError:
        return None


def data_dir_migration_error() -> str:
    return str(_location_state.get("migration_error") or "")


def schedule_data_dir_change(value: str | os.PathLike[str]) -> bool:
    global _location_state
    target = validate_data_dir_target(value)
    current = CONFIG_DIR.resolve(strict=False)
    if target == current:
        state = {"data_dir": str(current)}
        changed = bool(_location_state.get("pending_data_dir"))
    else:
        state = {"data_dir": str(current), "pending_data_dir": str(target)}
        changed = _location_state.get("pending_data_dir") != str(target)
    _write_location_state(state)
    _location_state = state
    return changed


def logger() -> logging.Logger:
    global _logger_ready
    log = logging.getLogger(APP_NAME)
    if not _logger_ready and _initialized:
        handler = RotatingFileHandler(
            LOG_PATH, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False
        _logger_ready = True
        log.info("Active data directory: %s", CONFIG_DIR)
        if data_directory.LAST_MIGRATION_ERROR:
            log.warning(
                "Legacy data migration failed; legacy directory remains active: %s",
                data_directory.LAST_MIGRATION_ERROR,
            )
    return log


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


# 旧调用方可能仍引用私有函数；保留别名，设置窗口已改用公开入口。
_validate_value = validate_value


def _write_json(path: Path, values: dict[str, Any]) -> None:
    state_store.write_json(path, values)


def _load_public_config() -> dict[str, Any]:
    return load_public_config(CONFIG_PATH)


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
    return state_store.load_widget_position(WIDGET_STATE_PATH)


def save_widget_position(x: int, y: int) -> None:
    try:
        # 位置状态独立于用户配置，拖动时不会触发配置备份或凭据写入。
        state_store.save_widget_position(WIDGET_STATE_PATH, x, y)
    except OSError:
        logger().warning("Widget position could not be saved")


def load_panel_layout_state() -> dict[str, Any]:
    return state_store.load_dict(PANEL_LAYOUT_PATH)


def save_panel_layout_state(values: dict[str, Any]) -> None:
    try:
        # 面板排序变化频率高于普通设置，单独落盘可避免触发配置备份与凭据回滚流程。
        _write_json(PANEL_LAYOUT_PATH, values)
    except OSError:
        logger().warning("Panel layout state could not be saved")


def updates_dir() -> Path:
    UPDATE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return UPDATE_CACHE_DIR


def load_update_state() -> dict[str, Any]:
    return state_store.load_dict(UPDATE_STATE_PATH)


def save_update_state(values: dict[str, Any]) -> None:
    state_store.merge_dict(UPDATE_STATE_PATH, values)


def load_pending_update_cleanup() -> dict[str, Any]:
    return state_store.load_dict(PENDING_UPDATE_CLEANUP_PATH)


def save_pending_update_cleanup(values: dict[str, Any]) -> None:
    _write_json(PENDING_UPDATE_CLEANUP_PATH, values)


def clear_pending_update_cleanup() -> None:
    try:
        state_store.clear(PENDING_UPDATE_CLEANUP_PATH)
    except OSError:
        logger().warning("Pending update cleanup manifest could not be removed")


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


def save_ui_theme(mode: str) -> str:
    """Atomically persist only the public theme preference."""
    global _config
    normalized = validate_value("UI_THEME", mode)
    temp_path = CONFIG_PATH.with_name(f"{CONFIG_PATH.name}.theme.tmp")
    try:
        if CONFIG_PATH.exists():
            public_values = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if not isinstance(public_values, dict):
                raise ValueError("config.json top level must be an object")
        else:
            public_values = _public_values(DEFAULT_CONFIG)

        # Read from disk instead of an in-memory settings draft, and strip any
        # accidentally persisted secrets so a theme click can never expose them.
        for key in SECRET_KEYS:
            public_values.pop(key, None)
        public_values["credential_store"] = "windows-credential-manager"
        public_values["UI_THEME"] = normalized
        _write_json(temp_path, public_values)
        temp_path.replace(CONFIG_PATH)
    except Exception:
        temp_path.unlink(missing_ok=True)
        logger().exception("Theme preference could not be saved")
        raise

    updated = _config.copy()
    updated["UI_THEME"] = normalized
    _config = updated
    return normalized

