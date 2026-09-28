"""Install and remove the optional VPet payload without touching user preferences."""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

from config import runtime as config_manager
from core.identity import APP_VERSION, PET_MANIFEST_ASSET_NAME, PET_PROTOCOL
from updater.client import (
    DownloadCancelled, GitHubReleaseClient, PetReleaseInfo, compare_versions, validate_pet_manifest,
)

PET_EXECUTABLE = "TokenMeter.Pet.exe"
PACK_MANIFEST = PET_MANIFEST_ASSET_NAME
PACK_PROTOCOL = PET_PROTOCOL
RESOURCES_MANIFEST = "resources-manifest.json"
REQUIRED_FILES = (
    PET_EXECUTABLE, "TokenMeter.Pet.dll", "TokenMeter.Pet.deps.json",
    "TokenMeter.Pet.runtimeconfig.json", "VPet-Simulator.Core.dll",
)
CHARACTER_PATHS = {"vpet": "vup", "whale": "whale"}


def extension_directory() -> Path:
    return config_manager.CONFIG_DIR / "extensions" / "vpet"


def selected_character() -> str:
    layout = config_manager.CONFIG_DIR / "vpet" / "layout.json"
    try:
        value = json.loads(layout.read_text(encoding="utf-8"))
        selected = "whale" if isinstance(value, dict) and value.get("character") == "whale" else "vpet"
    except (OSError, ValueError):
        selected = "vpet"
    available = installed_characters()
    return selected if not available or selected in available else next(iter(sorted(available)))


def installed_characters() -> set[str]:
    if installed_manifest() is None:
        return set()
    root = extension_directory() / "resources" / "pet"
    return {character for character, path in CHARACTER_PATHS.items()
            if (root / f"{path}.lps").is_file() and (root / path).is_dir()}


def save_selected_character(character: str) -> None:
    if character not in {"vpet", "whale"}:
        raise ValueError("未知桌宠角色")
    available = installed_characters()
    if available and character not in available:
        raise ValueError("请先下载该桌宠角色")
    layout = config_manager.CONFIG_DIR / "vpet" / "layout.json"
    if layout.exists():
        value = json.loads(layout.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("桌宠布局格式无效")
    else:
        value = {}
    value["character"] = character
    layout.parent.mkdir(parents=True, exist_ok=True)
    temporary = layout.with_name(layout.name + ".selection.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(layout)


def removable_directories() -> list[Path]:
    paths = [extension_directory()]
    if getattr(sys, "frozen", False):
        # 兼容旧试用安装包的 pet 目录，但绝不把开发构建目录当作可卸载资源。
        paths.append(Path(sys.executable).parent / "pet")
    return paths


def validate_payload(directory: Path) -> None:
    if not all((directory / name).is_file() for name in REQUIRED_FILES):
        raise ValueError("桌宠扩展包缺少必要文件")
    manifest_path = directory / PACK_MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    if not isinstance(manifest, dict):
        raise ValueError("桌宠角色清单无效")
    characters = manifest.get("installed_characters", ["vup"])
    if (not isinstance(characters, list) or not characters
            or any(not isinstance(role, str) or role not in {"vup", "whale"} for role in characters)
            or len(set(characters)) != len(characters)):
        raise ValueError("桌宠角色清单无效")
    for role in characters:
        if (not (directory / f"resources/pet/{role}.lps").is_file()
                or not any((directory / "resources/pet" / role).rglob("*.png"))):
            raise ValueError("桌宠扩展包缺少动画资源")


def _backup_directory(directory: Path) -> Path:
    return directory.with_name(f".{directory.name}-previous")


def _checked_directory(directory: Path) -> Path:
    resolved = directory.resolve()
    if directory.is_symlink() or directory.is_junction() or resolved != directory.absolute():
        raise ValueError("拒绝修改链接指向的桌宠目录")
    return resolved


def _recover_interrupted_update(directory: Path) -> None:
    backup = _backup_directory(directory)
    # 进程在两次重命名之间退出时，下次启动先恢复旧包，避免永久留下缺失状态。
    if backup.exists() and not directory.exists():
        _rename_payload(_checked_directory(backup), _checked_directory(directory))


def _rename_payload(source: Path, destination: Path) -> None:
    # 扩展包含大量运行时 DLL，Windows 安全扫描可能占用数秒；最多等待 10 秒后仍失败则回滚。
    for attempt in range(100):
        try:
            source.rename(destination)
            return
        except OSError as exc:
            if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 99:
                raise
            time.sleep(0.1)


def installed_manifest() -> dict | None:
    directory = extension_directory()
    try:
        _recover_interrupted_update(directory)
        _checked_directory(directory)
        manifest = json.loads((directory / PACK_MANIFEST).read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            return None
        if "version" in manifest:
            validate_pet_manifest(manifest, APP_VERSION)
        # 已安装的早期试用包没有独立版本号；保留其协议兼容性，允许之后原地升级。
        elif (not isinstance(manifest.get("app_version"), str)
              or manifest.get("protocol") != PACK_PROTOCOL or manifest.get("platform") != "win-x64"):
            return None
        validate_payload(directory)
        return manifest
    except (OSError, ValueError, AttributeError):
        pass
    return None


def installed_executable() -> Path | None:
    return extension_directory() / PET_EXECUTABLE if installed_manifest() is not None else None


def _check_cancel(cancel_requested: Callable[[], bool]) -> None:
    if cancel_requested():
        raise DownloadCancelled("已取消下载")


def reusable_resources(directory: Path, manifest: dict) -> bool:
    expected = manifest.get("resources")
    if not isinstance(expected, dict):
        return False
    try:
        report = json.loads((directory / RESOURCES_MANIFEST).read_text(encoding="utf-8"))
        resources = directory / "resources"
        if not resources.is_dir() or resources.is_symlink() or resources.is_junction():
            return False
        files = []
        for path in resources.rglob("*"):
            if path.is_symlink() or path.is_junction():
                return False
            if path.is_file():
                files.append(path)
        resource_hash = hashlib.sha256()
        for path in sorted(files, key=lambda item: item.relative_to(resources).as_posix()):
            resource_hash.update(path.relative_to(resources).as_posix().encode("utf-8"))
            resource_hash.update(b"\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    resource_hash.update(chunk)
        return (
            report.get("revision") == expected.get("revision")
            and report.get("resource_files") == expected.get("files") == len(files)
            and report.get("resource_bytes") == expected.get("bytes")
            == sum(path.stat().st_size for path in files)
            and resource_hash.hexdigest() == expected.get("sha256")
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _replace_payload(stage: Path, destination: Path, manifest: dict,
                     cancel_requested: Callable[[], bool]) -> None:
    _check_cancel(cancel_requested)
    if not destination.exists():
        _rename_payload(stage, destination)
        return
    old_manifest_path = destination / PACK_MANIFEST
    if old_manifest_path.is_file():
        old_manifest = json.loads(old_manifest_path.read_text(encoding="utf-8"))
        old_version = old_manifest.get("version") if isinstance(old_manifest, dict) else None
        if old_version and compare_versions(manifest["version"], old_version) < 0:
            raise ValueError("不能将桌宠扩展降级到旧版本")
    backup = _checked_directory(_backup_directory(destination))
    if backup.exists():
        shutil.rmtree(backup)
    # 新目录校验通过后才改动原目录；替换失败立即恢复旧包。
    _rename_payload(destination, backup)
    try:
        _check_cancel(cancel_requested)
        _rename_payload(stage, destination)
    except BaseException:
        _rename_payload(backup, destination)
        raise
    try:
        shutil.rmtree(backup)
    except OSError:
        config_manager.logger().warning("Pet updated; previous payload cleanup deferred: %s", backup)


def install_pack(
    archive: Path, destination: Path, cancel_requested: Callable[[], bool] = lambda: False,
    *, replace_existing: bool = False, expected_manifest: dict | None = None,
    reuse_resources_from: Path | None = None,
    merge_resources_from: Path | None = None, merged_manifest: dict | None = None,
) -> None:
    destination = _checked_directory(destination)
    _recover_interrupted_update(destination)
    if destination.exists() and not replace_existing:
        raise ValueError("请先卸载已有桌宠扩展包")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # 在同一磁盘的临时目录解压并校验，最后重命名发布；失败和取消不会留下半安装状态。
    with tempfile.TemporaryDirectory(prefix=".vpet-install-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "payload"
        stage.mkdir()
        with zipfile.ZipFile(archive) as pack:
            entries = pack.infolist()
            if len(entries) > 50000 or sum(item.file_size for item in entries) > 2 * 1024**3:
                raise ValueError("桌宠扩展包超过解压大小限制")
            seen = set()
            for item in entries:
                _check_cancel(cancel_requested)
                path = PurePosixPath(item.filename)
                # Windows 路径别名、盘符、ADS、链接及重复条目均不能越过独立扩展目录。
                if (path.is_absolute() or not path.parts or item.orig_filename != item.filename
                        or "\\" in item.filename
                        or any(part in {".", ".."} or part.endswith((".", " "))
                               or any(char in part for char in ':<>"|?*')
                               or Path(part).is_reserved() for part in path.parts)
                        or stat.S_ISLNK(item.external_attr >> 16)
                        or item.filename.lower() in seen):
                    raise ValueError("桌宠扩展包包含不安全路径")
                seen.add(item.filename.lower())
                target = stage.joinpath(*path.parts)
                if not target.resolve().is_relative_to(stage.resolve()):
                    raise ValueError("桌宠扩展包包含不安全路径")
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with pack.open(item) as source, target.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        _check_cancel(cancel_requested)
                        output.write(chunk)
        manifest = json.loads((stage / PACK_MANIFEST).read_text(encoding="utf-8"))
        validate_pet_manifest(manifest, APP_VERSION)
        if expected_manifest is not None and manifest != expected_manifest:
            raise ValueError("桌宠扩展包与已校验的版本清单不一致")
        if reuse_resources_from is not None:
            # 宿主小包绝不能夹带资源覆盖本地副本；资源身份和实际文件总量一致后才复用。
            if (stage / "resources").exists() or not reusable_resources(reuse_resources_from, manifest):
                raise ValueError("本地桌宠动画资源与宿主更新包不兼容")
            def copy_resource(source: str, target: str) -> str:
                _check_cancel(cancel_requested)
                return shutil.copy2(source, target)
            shutil.copytree(reuse_resources_from / "resources", stage / "resources",
                            copy_function=copy_resource)
        if merge_resources_from is not None:
            old_manifest = json.loads((merge_resources_from / PACK_MANIFEST).read_text(encoding="utf-8"))
            roles = old_manifest.get("installed_characters")
            if (not isinstance(roles, list) or len(roles) != 1 or merged_manifest is None
                    or old_manifest.get("version") != manifest["version"]
                    or not reusable_resources(merge_resources_from, old_manifest)):
                raise ValueError("已有角色资源与新角色包不兼容")
            role = roles[0]
            if role not in {"vup", "whale"} or role in manifest.get("installed_characters", []):
                raise ValueError("已有角色资源与新角色包不兼容")
            # 只从已校验的旧包复制另一角色；完整资源摘要在提交前再次核对。
            def copy_other_resource(source: str, target: str) -> str:
                _check_cancel(cancel_requested)
                return shutil.copy2(source, target)
            shutil.copytree(merge_resources_from / "resources/pet" / role,
                            stage / "resources/pet" / role, copy_function=copy_other_resource)
            shutil.copy2(merge_resources_from / f"resources/pet/{role}.lps",
                         stage / f"resources/pet/{role}.lps")
            manifest = dict(merged_manifest, installed_characters=["vup", "whale"])
            (stage / PACK_MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            identity = manifest["resources"]
            (stage / RESOURCES_MANIFEST).write_text(json.dumps({
                "revision": identity["revision"], "resource_files": identity["files"],
                "resource_bytes": identity["bytes"],
            }), encoding="utf-8")
            if not reusable_resources(stage, manifest):
                raise ValueError("合并后的桌宠资源校验失败")
        validate_payload(stage)
        _replace_payload(stage, destination, manifest, cancel_requested)


def download_and_install(
    progress: Callable[[dict[str, object]], None], cancel_requested: Callable[[], bool],
    *, release: PetReleaseInfo | None = None, replace_existing: bool = False,
    character: str | None = None, add_character: bool = False,
) -> None:
    if character is not None and character not in CHARACTER_PATHS:
        raise ValueError("未知桌宠角色")
    destination = extension_directory()
    if destination.exists() and not (replace_existing or add_character):
        raise ValueError("请先卸载已有桌宠扩展包")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # 下载缓存与解压目录都随操作清理，卸载后不会遗留另一份大体积 ZIP。
    with tempfile.TemporaryDirectory(prefix=".vpet-download-", dir=destination.parent) as temporary:
        client = GitHubReleaseClient()
        try:
            release = release or client.latest_pet_release(cancel_requested=cancel_requested)
            role = CHARACTER_PATHS[character] if character else None
            installed = installed_manifest()
            installed_roles = set(installed.get("installed_characters", [])) if installed else set()
            if add_character and (not installed or not role or role in installed_roles
                                  or installed.get("version") != release.version):
                raise ValueError("请先安装或更新桌宠扩展，再添加另一角色")
            use_role_pack = role in release.character_assets and (
                add_character or not replace_existing or installed_roles == {role})
            reuse_resources = (
                replace_existing and not use_role_pack and release.host_asset is not None
                and reusable_resources(destination, release.manifest)
            )
            archive = client.download_pet_pack(
                Path(temporary), release=release, progress=progress,
                cancel_requested=cancel_requested, host_only=reuse_resources,
                character=role if use_role_pack else None,
            )
            expected = (dict(release.manifest,
                             resources=release.manifest["character_resources"][role],
                             installed_characters=[role]) if use_role_pack else release.manifest)
            install_pack(archive, destination, cancel_requested,
                         replace_existing=replace_existing or add_character,
                         expected_manifest=expected,
                         reuse_resources_from=destination if reuse_resources else None,
                         merge_resources_from=destination if add_character and use_role_pack else None,
                         merged_manifest=release.manifest if add_character and use_role_pack else None)
        finally:
            client._session.close()


def remove_character(character: str) -> None:
    if character not in CHARACTER_PATHS:
        raise ValueError("未知桌宠角色")
    installed = installed_manifest()
    available = installed_characters()
    if installed is None or character not in available:
        raise ValueError("该桌宠角色尚未安装")
    if not isinstance(installed.get("character_resources"), dict):
        # 旧宿主启动时会无条件加载双角色资源；必须先更新宿主再允许单独删除。
        raise ValueError("请先更新桌宠扩展，再单独删除角色")
    if len(available) == 1:
        uninstall()
        return
    destination = _checked_directory(extension_directory())
    role = CHARACTER_PATHS[character]
    remaining = CHARACTER_PATHS[next(iter(available - {character}))]
    with tempfile.TemporaryDirectory(prefix=".vpet-remove-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "payload"
        shutil.copytree(destination, stage)
        shutil.rmtree(_checked_directory(stage / "resources/pet" / role))
        (stage / f"resources/pet/{role}.lps").unlink()
        resources = stage / "resources"
        revision = (installed.get("resources") or {}).get("revision") or "0" * 40
        digest = hashlib.sha256()
        files = sorted((path for path in resources.rglob("*") if path.is_file()),
                       key=lambda path: path.relative_to(resources).as_posix())
        for path in files:
            digest.update(path.relative_to(resources).as_posix().encode("utf-8"))
            digest.update(b"\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        identity = {"revision": revision, "files": len(files),
                    "bytes": sum(path.stat().st_size for path in files), "sha256": digest.hexdigest()}
        manifest = dict(installed, installed_characters=[remaining], resources=identity)
        (stage / PACK_MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        (stage / RESOURCES_MANIFEST).write_text(json.dumps({
            "revision": revision, "resource_files": identity["files"],
            "resource_bytes": identity["bytes"],
        }), encoding="utf-8")
        if not reusable_resources(stage, manifest):
            raise ValueError("删除角色后的资源校验失败")
        validate_payload(stage)
        _replace_payload(stage, destination, manifest, lambda: False)
    try:
        save_selected_character("vpet" if remaining == "vup" else "whale")
    except (OSError, ValueError) as exc:
        config_manager.logger().warning("Pet character removed; preference cleanup deferred: %s", exc)
    try:
        # 旧角色帧缓存无法按文件名区分；删除后统一重建，避免磁盘上仍占用被删角色的空间。
        caches = [config_manager.CONFIG_DIR / "vpet" / "cache"]
        caches.extend(_instance_caches(character))
        for cache in caches:
            if cache.exists():
                shutil.rmtree(_checked_directory(cache))
    except (OSError, ValueError) as exc:
        config_manager.logger().warning("Pet character removed; cache cleanup deferred: %s", exc)


def _instance_caches(character: str | None = None) -> list[Path]:
    instances = config_manager.CONFIG_DIR / "vpet" / "instances"
    if not instances.exists():
        return []
    _checked_directory(instances)
    caches = []
    for directory in instances.iterdir():
        role, separator, index = directory.name.partition("-")
        if (separator and role in CHARACTER_PATHS and (character is None or role == character)
                and index.isdecimal() and directory.is_dir()
                and not directory.is_symlink() and not directory.is_junction()):
            cache = directory / "cache"
            if not cache.is_symlink() and not cache.is_junction():
                caches.append(cache)
    return caches


def uninstall() -> None:
    # 动画缓存可重新生成，随扩展删除以释放磁盘；layout 等偏好仍留在 vpet 根目录。
    for directory in [*removable_directories(), _backup_directory(extension_directory()),
                      config_manager.CONFIG_DIR / "vpet" / "cache", *_instance_caches()]:
        if not directory.exists():
            continue
        # 卸载只能清理固定 payload 目录，不能跟随链接删除用户数据或开发源码。
        resolved = _checked_directory(directory)
        shutil.rmtree(resolved)
