"""Configuration validation and public JSON serialization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from config.defaults import DEFAULT_CONFIG, FIELD_META, OFFICIAL_HOSTS, SECRET_KEYS
from deepseek_pricing import configured_periods, parse_time_text


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
    if kind == "choice":
        normalized = str(value).strip().lower()
        if normalized not in meta["choices"]:
            choices = ", ".join(meta["choices"])
            raise ValueError(f"{key} must be one of: {choices}")
        return normalized
    if kind == "time":
        return parse_time_text(value).strftime("%H:%M")
    return str(value)


def validate_config(values: dict[str, Any]) -> dict[str, Any]:
    merged = DEFAULT_CONFIG.copy()
    merged.update(values)
    for key in list(merged):
        merged[key] = validate_value(key, merged[key])
    active_provider = str(merged.get("ACTIVE_PROVIDER", "deepseek")).strip().lower()
    if active_provider not in {"deepseek", "mimo"}:
        raise ValueError("ACTIVE_PROVIDER 必须是 deepseek 或 mimo")
    merged["ACTIVE_PROVIDER"] = active_provider
    update_channel = str(merged.get("UPDATE_CHANNEL", "stable")).strip().lower()
    if update_channel not in {"stable", "prerelease"}:
        raise ValueError("UPDATE_CHANNEL must be stable or prerelease")
    merged["UPDATE_CHANNEL"] = update_channel
    configured_periods(merged)
    # 凭据会随请求发送；自定义地址至少必须是完整 HTTP(S) URL。
    for key in FIELD_META:
        if not key.endswith("_BASE"):
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


def public_values(values: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in values.items() if key not in SECRET_KEYS}
    result["credential_store"] = "windows-credential-manager"
    return result


def load_public_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("config.json 顶层必须是对象")
    value.pop("credential_store", None)
    compact_size = int(value.get("WIDGET_COMPACT_SIZE", 88))
    if compact_size < 88 or compact_size in (96, 108, 120):
        value["WIDGET_COMPACT_SIZE"] = 88
    panel_size = value.get("WIDGET_EXPANDED_SIZE", [820, 564])
    if isinstance(panel_size, (list, tuple)) and len(panel_size) == 2:
        if int(panel_size[0]) < 680 or int(panel_size[1]) < 564:
            value["WIDGET_EXPANDED_SIZE"] = [820, 564]
    return value


__all__ = [
    "is_official_base_url",
    "load_public_config",
    "public_values",
    "validate_config",
    "validate_value",
]
