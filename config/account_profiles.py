"""Independent monitor profiles; secrets stay in Windows Credential Manager."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from api.providers import PROVIDERS
from config import credentials
from config import runtime as config_manager
from config.defaults import DEFAULT_CONFIG
from config.store import validate_value


def _path() -> Path:
    return config_manager.CONFIG_DIR / "account-profiles.json"


def _valid_id(value) -> bool:
    return isinstance(value, str) and len(value) == 32 and all(char in "0123456789abcdef" for char in value)


def load_profiles() -> list[dict]:
    path = _path()
    if not path.exists():
        return []
    with path.open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Profile file too large")
    profiles = json.loads(raw)
    if not isinstance(profiles, list):
        raise ValueError("Invalid profile file")
    result = []
    seen = set()
    for profile in profiles:
        if (not isinstance(profile, dict) or not _valid_id(profile.get("id"))
                or not _valid_id(profile.get("revision")) or profile.get("provider") not in PROVIDERS
                or not isinstance(profile.get("fields"), dict) or not isinstance(profile.get("name"), str)
                or profile["id"] in seen):
            raise ValueError("Invalid profile entry")
        seen.add(profile["id"])
        result.append(profile)
    return result


def _write_profiles(profiles):
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    payload = json.dumps(profiles, ensure_ascii=False, indent=2)
    if len(payload.encode("utf-8")) > 1024 * 1024:
        raise ValueError("Profile file too large")
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _secret_key(profile, field):
    return f"PROFILE_{profile['id']}_{profile['revision']}_{field}"


def profile_fields(profile) -> dict[str, str]:
    values = {}
    for field, meta in PROVIDERS[profile["provider"]].credential_fields.items():
        values[field] = credentials.read_credential(_secret_key(profile, field)) if meta.get("secret") else str(profile["fields"].get(field, DEFAULT_CONFIG.get(f"{profile['provider'].upper()}_{field}", "")))
    return values


def _clear_secrets(profile):
    for field, meta in PROVIDERS[profile["provider"]].credential_fields.items():
        if meta.get("secret"):
            try:
                credentials.write_credential(_secret_key(profile, field), "")
            except OSError:
                config_manager.logger().warning("Profile credential cleanup failed")


def save_profile(provider: str, name: str, fields: dict[str, str], profile_id: str | None = None) -> dict:
    if provider not in PROVIDERS or not name.strip():
        raise ValueError("Provider and name required")
    profiles = load_profiles()
    old = next((profile for profile in profiles if profile["id"] == profile_id), None)
    if profile_id is not None and old is None:
        raise ValueError("Profile not found")
    metadata = PROVIDERS[provider].credential_fields
    normalized = {field: str(validate_value(f"{provider.upper()}_{field}", str(fields.get(field, DEFAULT_CONFIG.get(f"{provider.upper()}_{field}", ""))).strip())) for field in metadata}
    if not any(normalized[field] for field, meta in metadata.items()
               if meta.get("secret") or meta.get("directory") or field.endswith("_FILE")):
        # 档案必须明确指定登录来源，不能静默退回全局默认账号或其他 CLI 登录。
        raise ValueError("Explicit credentials or login path required")
    profile = {"id": profile_id or uuid4().hex, "revision": uuid4().hex, "provider": provider,
               "name": name.strip()[:80], "fields": {field: value for field, value in normalized.items() if not metadata[field].get("secret")}}
    try:
        # 每次保存使用新的凭据版本；元数据原子替换前不覆盖旧凭据，失败时原档案仍可使用。
        for field, value in normalized.items():
            if metadata[field].get("secret") and value:
                credentials.write_credential(_secret_key(profile, field), value)
        updated = [profile if item["id"] == profile["id"] else item for item in profiles]
        if old is None:
            updated.append(profile)
        _write_profiles(updated)
    except Exception:
        _clear_secrets(profile)
        raise
    if old is not None:
        _clear_secrets(old)
    return profile


def delete_profile(profile_id: str) -> None:
    profiles = load_profiles()
    old = next((profile for profile in profiles if profile["id"] == profile_id), None)
    if old is None:
        return
    _write_profiles([profile for profile in profiles if profile["id"] != profile_id])
    _clear_secrets(old)


def profile_config(profile) -> dict:
    provider = profile["provider"]
    values = dict(DEFAULT_CONFIG)
    for field, value in profile_fields(profile).items():
        values[f"{provider.upper()}_{field}"] = value
    values["ACTIVE_PROVIDER"] = provider
    values["_ACCOUNT_PROFILE_ID"] = profile["id"]
    values["QUOTA_FORECAST_ENABLED"] = config_manager.get("QUOTA_FORECAST_ENABLED", False)
    return values
