"""Install, select, and remove independent VPet character resource packs."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.parse import urlparse

import requests

from config import runtime as config_manager
from core.identity import PET_PROTOCOL
from updater.client import DownloadCancelled, compare_versions

BUILTIN_ID = "builtin"
CONFIG_KEY = "VPET_CHARACTER"
MANIFEST_NAME = "character.json"
MAX_UNPACKED_BYTES = 1024**3
_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_VERSION = re.compile(r"(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*)){2}(?:-[0-9A-Za-z.-]+)?\Z")
_DOWNLOAD_HOSTS = {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
_REQUIRED_ACTION_DIRS = {
    "Default", "MOVE", "Raise", "Say", "SideHide_Left_Main", "SideHide_Left_Rise",
    "SideHide_Right_Main", "SideHide_Right_Rise", "StartUP", "State", "Touch_Body",
    "Touch_Head", "IDEL",
}


def characters_directory() -> Path:
    return config_manager.CONFIG_DIR / "extensions" / "vpet-characters"


def catalog_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "assets" / "pet-characters.json"
    return Path(__file__).resolve().parents[1] / "pet_host" / "characters" / "catalog.json"


def validate_manifest(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("角色清单必须是对象")
    required = {"schema_version", "id", "name", "version", "pet_protocol"}
    if not required.issubset(value):
        raise ValueError("角色清单缺少必要字段")
    character_id = value["id"]
    version = value["version"]
    if value["schema_version"] != 1:
        raise ValueError("不支持的角色包格式")
    if not isinstance(character_id, str) or not _ID.fullmatch(character_id) or character_id == BUILTIN_ID:
        raise ValueError("角色 ID 无效")
    if not isinstance(value["name"], str) or not value["name"].strip():
        raise ValueError("角色名称无效")
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ValueError("角色版本无效")
    if isinstance(value["pet_protocol"], bool) or value["pet_protocol"] != PET_PROTOCOL:
        raise ValueError("角色包与当前桌宠协议不兼容")
    for optional in ("description", "author"):
        if optional in value and not isinstance(value[optional], str):
            raise ValueError(f"角色清单 {optional} 无效")
    return dict(value)


def validate_payload(directory: Path) -> dict[str, object]:
    manifest = validate_manifest(json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8")))
    pet = directory / "resources" / "pet"
    if not (pet / "vup.lps").is_file():
        raise ValueError("角色包缺少 vup.lps")
    resource = pet / "vup"
    if not _REQUIRED_ACTION_DIRS.issubset({path.name for path in resource.iterdir() if path.is_dir()}):
        raise ValueError("角色包动作目录不完整")
    if not any(resource.rglob("*.png")):
        raise ValueError("角色包缺少动画图片")
    return manifest


def _checked_directory(directory: Path) -> Path:
    resolved = directory.resolve(strict=False)
    if directory.is_symlink() or directory.is_junction() or resolved != directory.absolute():
        raise ValueError("拒绝修改链接指向的角色目录")
    return resolved


def installed_characters() -> list[dict[str, object]]:
    result: list[dict[str, object]] = [{
        "id": BUILTIN_ID, "name": "内置默认角色", "version": "", "builtin": True,
    }]
    root = characters_directory()
    if not root.is_dir() or root.is_symlink() or root.is_junction():
        return result
    for directory in sorted(root.iterdir(), key=lambda path: path.name.casefold()):
        if not directory.is_dir() or directory.is_symlink() or directory.is_junction():
            continue
        try:
            manifest = validate_payload(directory)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if manifest["id"] != directory.name:
            continue
        result.append({**manifest, "builtin": False, "directory": directory})
    return result


def selected_character_id() -> str:
    selected = str(config_manager.get(CONFIG_KEY, BUILTIN_ID) or BUILTIN_ID).strip().lower()
    installed = {str(item["id"]) for item in installed_characters()}
    return selected if selected in installed else BUILTIN_ID


def selected_resources_directory() -> Path | None:
    selected = selected_character_id()
    if selected == BUILTIN_ID:
        return None
    return characters_directory() / selected / "resources"


def available_characters() -> list[dict[str, object]]:
    try:
        value = json.loads(catalog_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        return []
    characters = value.get("characters")
    if not isinstance(characters, list):
        return []
    result = []
    for item in characters:
        if not isinstance(item, dict):
            continue
        try:
            validate_manifest({**item, "schema_version": 1, "pet_protocol": PET_PROTOCOL})
        except ValueError:
            continue
        digest = item.get("sha256")
        size = item.get("size")
        if not isinstance(digest, str) or (digest != "PENDING" and not re.fullmatch(r"[0-9a-f]{64}", digest)):
            continue
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            continue
        result.append(dict(item))
    return result


def _check_cancel(cancel_requested: Callable[[], bool]) -> None:
    if cancel_requested():
        raise DownloadCancelled("已取消下载")


def install_pack(
    archive: Path, cancel_requested: Callable[[], bool] = lambda: False,
) -> dict[str, object]:
    root = _checked_directory(characters_directory())
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".vpet-character-", dir=root) as temporary:
        stage = Path(temporary) / "payload"
        stage.mkdir()
        with zipfile.ZipFile(archive) as pack:
            entries = pack.infolist()
            if len(entries) > 50000 or sum(item.file_size for item in entries) > MAX_UNPACKED_BYTES:
                raise ValueError("角色包超过解压大小限制")
            seen: set[str] = set()
            for item in entries:
                _check_cancel(cancel_requested)
                path = PurePosixPath(item.filename)
                if (
                    path.is_absolute() or not path.parts or item.orig_filename != item.filename
                    or "\\" in item.filename
                    or any(part in {"", ".", ".."} or part.endswith((".", " "))
                           or any(char in part for char in ':<>"|?*') or Path(part).is_reserved()
                           for part in path.parts)
                    or stat.S_ISLNK(item.external_attr >> 16)
                    or item.filename.casefold() in seen
                ):
                    raise ValueError("角色包包含不安全路径")
                seen.add(item.filename.casefold())
                target = stage.joinpath(*path.parts)
                if not target.resolve().is_relative_to(stage.resolve()):
                    raise ValueError("角色包包含不安全路径")
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with pack.open(item) as source, target.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        _check_cancel(cancel_requested)
                        output.write(chunk)
        manifest = validate_payload(stage)
        destination = _checked_directory(root / str(manifest["id"]))
        backup = _checked_directory(root / f".{manifest['id']}-previous")
        if destination.exists():
            current = validate_payload(destination)
            if compare_versions(str(manifest["version"]), str(current["version"])) < 0:
                raise ValueError("不能将角色包降级到旧版本")
            if backup.exists():
                shutil.rmtree(backup)
            destination.rename(backup)
        try:
            stage.rename(destination)
        except BaseException:
            if backup.exists() and not destination.exists():
                backup.rename(destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        return manifest


def download_and_install(
    entry: dict[str, object], progress: Callable[[dict[str, object]], None],
    cancel_requested: Callable[[], bool],
) -> dict[str, object]:
    url = entry.get("download_url")
    expected = entry.get("sha256")
    if expected == "PENDING":
        raise ValueError("该角色包尚未发布")
    if not isinstance(url, str) or urlparse(url).scheme != "https" or urlparse(url).hostname not in _DOWNLOAD_HOSTS:
        raise ValueError("角色下载地址无效")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("角色包缺少校验值")
    root = characters_directory()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".vpet-character-download-", dir=root) as temporary:
        archive = Path(temporary) / "character.zip"
        digest = hashlib.sha256()
        downloaded = 0
        with requests.get(url, stream=True, timeout=(10, 60), allow_redirects=True) as response:
            response.raise_for_status()
            if urlparse(response.url).scheme != "https" or urlparse(response.url).hostname not in _DOWNLOAD_HOSTS:
                raise ValueError("角色下载发生了不安全重定向")
            total = int(response.headers.get("content-length") or 0)
            if total > MAX_UNPACKED_BYTES:
                raise ValueError("角色下载包过大")
            with archive.open("xb") as output:
                for chunk in response.iter_content(1024 * 1024):
                    _check_cancel(cancel_requested)
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > MAX_UNPACKED_BYTES:
                        raise ValueError("角色下载包过大")
                    digest.update(chunk)
                    output.write(chunk)
                    progress({"downloaded": downloaded, "total": total})
        if digest.hexdigest() != expected:
            raise ValueError("角色包校验失败")
        return install_pack(archive, cancel_requested)


def uninstall(character_id: str) -> None:
    if character_id == BUILTIN_ID or not _ID.fullmatch(character_id):
        raise ValueError("内置角色不能卸载")
    directory = _checked_directory(characters_directory() / character_id)
    if selected_character_id() == character_id:
        # 先落盘回退，删除失败也不会让下次启动指向半删除目录。
        config_manager.save_config({CONFIG_KEY: BUILTIN_ID})
    if directory.exists():
        shutil.rmtree(directory)
