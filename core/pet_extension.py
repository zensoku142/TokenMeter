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
    "resources/pet/vup.lps",
)


def extension_directory() -> Path:
    return config_manager.CONFIG_DIR / "extensions" / "vpet"


def removable_directories() -> list[Path]:
    paths = [extension_directory()]
    if getattr(sys, "frozen", False):
        # 兼容旧试用安装包的 pet 目录，但绝不把开发构建目录当作可卸载资源。
        paths.append(Path(sys.executable).parent / "pet")
    return paths


def validate_payload(directory: Path) -> None:
    if not all((directory / name).is_file() for name in REQUIRED_FILES):
        raise ValueError("桌宠扩展包缺少必要文件")
    if not any((directory / "resources/pet/vup").rglob("*.png")):
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


def install_pack(
    archive: Path, destination: Path, cancel_requested: Callable[[], bool] = lambda: False,
    *, replace_existing: bool = False, expected_manifest: dict | None = None,
    reuse_resources_from: Path | None = None,
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
        validate_payload(stage)
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
        # 下载和解压完成前不动旧包；替换失败立即回滚，不能把备份放进会自动清理的临时目录。
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
            # 新包已经提交成功；占用导致的备份清理失败可在下次更新或卸载时重试。
            config_manager.logger().warning("Pet updated; previous payload cleanup deferred: %s", backup)


def download_and_install(
    progress: Callable[[dict[str, object]], None], cancel_requested: Callable[[], bool],
    *, release: PetReleaseInfo | None = None, replace_existing: bool = False,
) -> None:
    destination = extension_directory()
    if destination.exists() and not replace_existing:
        raise ValueError("请先卸载已有桌宠扩展包")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # 下载缓存与解压目录都随操作清理，卸载后不会遗留另一份大体积 ZIP。
    with tempfile.TemporaryDirectory(prefix=".vpet-download-", dir=destination.parent) as temporary:
        client = GitHubReleaseClient()
        try:
            release = release or client.latest_pet_release(cancel_requested=cancel_requested)
            reuse_resources = (
                replace_existing and release.host_asset is not None
                and reusable_resources(destination, release.manifest)
            )
            archive = client.download_pet_pack(
                Path(temporary), release=release, progress=progress,
                cancel_requested=cancel_requested, host_only=reuse_resources,
            )
            install_pack(archive, destination, cancel_requested,
                         replace_existing=replace_existing, expected_manifest=release.manifest,
                         reuse_resources_from=destination if reuse_resources else None)
        finally:
            client._session.close()


def uninstall() -> None:
    # 动画缓存可重新生成，随扩展删除以释放磁盘；layout 等偏好仍留在 vpet 根目录。
    for directory in [*removable_directories(), _backup_directory(extension_directory()),
                      config_manager.CONFIG_DIR / "vpet" / "cache"]:
        if not directory.exists():
            continue
        # 卸载只能清理固定 payload 目录，不能跟随链接删除用户数据或开发源码。
        resolved = _checked_directory(directory)
        shutil.rmtree(resolved)
