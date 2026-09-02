"""GitHub release update helpers for the packaged Windows build."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urljoin, urlparse

import requests

from config import runtime as config_manager
from core.identity import (
    APP_DISPLAY_NAME,
    APP_VERSION,
    GITHUB_LATEST_RELEASE_API_URL,
    GITHUB_RELEASES_API_URL,
    GITHUB_REPOSITORY,
    MAIN_EXECUTABLE_NAME,
    PET_HOST_RELEASE_ASSET_TEMPLATE,
    PET_MANIFEST_ASSET_NAME,
    PET_PROTOCOL,
    PET_RELEASE_ASSET_TEMPLATE,
    PET_RELEASE_TAG_PREFIX,
    SETUP_RELEASE_ASSET_TEMPLATE,
    SHA256_RELEASE_ASSET_NAME,
)

RELEASE_CHANNEL_STABLE = "stable"
RELEASE_CHANNEL_PRERELEASE = "prerelease"
AUTO_CHECK_INTERVAL = timedelta(hours=24)
DOWNLOAD_CHUNK_SIZE = 1024 * 128
HTTP_TIMEOUT = (5, 30)
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_RELEASE_NOTES_PAGES = 10
MAX_DOWNLOAD_BYTES = 2 * 1024**3
MANIFEST_VERSION = 1

_SEMVER_RE = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?$"
)
_SETUP_NAME_RE = re.compile(
    r"^TokenMeter-Setup-v(?P<version>[0-9A-Za-z.-]+)-x64\.exe$",
    re.IGNORECASE,
)
_SHA256_LINE_RE = re.compile(r"^(?P<sha>[A-Fa-f0-9]{64})\s+\*?(?P<name>.+)$")
_ALLOWED_API_HOSTS = {"api.github.com"}
_ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
_DOWNLOAD_PATH_PREFIX = f"/{GITHUB_REPOSITORY}/releases/download/"


class UpdateError(RuntimeError):
    """Raised when release metadata or installation steps are invalid."""


class DownloadCancelled(UpdateError):
    """Raised when the user cancels a download."""


@dataclass(frozen=True)
class SemVer:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str | int, ...] = ()

    @classmethod
    def parse(cls, value: str) -> "SemVer":
        match = _SEMVER_RE.match(value.strip())
        if not match:
            raise ValueError(f"invalid semantic version: {value}")
        prerelease = match.group("prerelease")
        parts: list[str | int] = []
        if prerelease:
            for item in prerelease.split("."):
                parts.append(int(item) if item.isdigit() else item.lower())
        return cls(
            major=int(match.group("major")),
            minor=int(match.group("minor")),
            patch=int(match.group("patch")),
            prerelease=tuple(parts),
        )

    def __lt__(self, other: "SemVer") -> bool:
        if (self.major, self.minor, self.patch) != (other.major, other.minor, other.patch):
            return (self.major, self.minor, self.patch) < (
                other.major,
                other.minor,
                other.patch,
            )
        if not self.prerelease and other.prerelease:
            return False
        if self.prerelease and not other.prerelease:
            return True
        return _compare_prerelease(self.prerelease, other.prerelease) < 0

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return (
            self.major,
            self.minor,
            self.patch,
            self.prerelease,
        ) == (
            other.major,
            other.minor,
            other.patch,
            other.prerelease,
        )

    def normalized(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        if not self.prerelease:
            return base
        suffix = ".".join(str(item) for item in self.prerelease)
        return f"{base}-{suffix}"


def _compare_prerelease(left: tuple[str | int, ...], right: tuple[str | int, ...]) -> int:
    for left_item, right_item in zip(left, right):
        if left_item == right_item:
            continue
        if isinstance(left_item, int) and isinstance(right_item, int):
            return -1 if left_item < right_item else 1
        if isinstance(left_item, int):
            return -1
        if isinstance(right_item, int):
            return 1
        return -1 if str(left_item) < str(right_item) else 1
    if len(left) == len(right):
        return 0
    return -1 if len(left) < len(right) else 1


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    semver: SemVer
    tag_name: str
    published_at: str
    body: str
    is_prerelease: bool
    setup_asset: ReleaseAsset
    checksum_asset: ReleaseAsset
    # 保留最新 Release 的原始正文；汇总说明只用于展示，不改变下载目标或原有调用方。
    update_notes: str = ""
    notes_incomplete: bool = False


@dataclass(frozen=True)
class CheckResult:
    current_version: str
    latest_release: ReleaseInfo | None
    update_available: bool
    message: str
    cached: bool = False


@dataclass(frozen=True)
class PetReleaseInfo:
    version: str
    manifest: dict
    asset: ReleaseAsset
    sha256: str
    host_asset: ReleaseAsset | None = None
    host_sha256: str | None = None


def validate_pet_manifest(manifest: object, app_version: str = APP_VERSION) -> dict:
    if not isinstance(manifest, dict):
        raise ValueError("桌宠扩展版本清单无效")
    if (type(manifest.get("protocol")) is not int
            or manifest["protocol"] != PET_PROTOCOL or manifest.get("platform") != "win-x64"):
        raise ValueError("桌宠扩展包与当前版本不兼容")
    version = manifest.get("version")
    minimum = manifest.get("min_app_version")
    maximum = manifest.get("max_app_version")
    if not isinstance(version, str) or not isinstance(minimum, str):
        raise ValueError("桌宠扩展版本清单无效")
    if normalize_version(version) != version:
        raise ValueError("桌宠扩展版本号无效")
    # 扩展有独立版本；主程序只需处于兼容区间，max_app_version 为不包含的上界。
    if compare_versions(app_version, minimum) < 0:
        raise ValueError("桌宠扩展包与当前版本不兼容")
    if maximum is not None and (not isinstance(maximum, str)
                               or compare_versions(app_version, maximum) >= 0):
        raise ValueError("桌宠扩展包与当前版本不兼容")
    resources = manifest.get("resources")
    if resources is not None and (
        not isinstance(resources, dict)
        or not isinstance(resources.get("revision"), str)
        or not re.fullmatch(r"[0-9a-f]{40}", resources["revision"])
        or type(resources.get("files")) is not int or resources["files"] <= 0
        or type(resources.get("bytes")) is not int or resources["bytes"] <= 0
        or not isinstance(resources.get("sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", resources["sha256"])
    ):
        raise ValueError("桌宠扩展资源清单无效")
    return dict(manifest)


@dataclass(frozen=True)
class DownloadedAsset:
    asset: ReleaseAsset
    path: Path
    sha256: str


@dataclass(frozen=True)
class DownloadBundle:
    release: ReleaseInfo
    setup_asset: DownloadedAsset
    cache_dir: Path


def normalize_version(value: str) -> str:
    return SemVer.parse(value).normalized()


def compare_versions(local_version: str, remote_version: str) -> int:
    local = SemVer.parse(local_version)
    remote = SemVer.parse(remote_version)
    if local == remote:
        return 0
    return -1 if local < remote else 1


def format_bytes(size: int) -> str:
    if size <= 0:
        return "未知"
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def format_speed(size_per_second: float) -> str:
    if size_per_second <= 0:
        return "0 B/s"
    return f"{format_bytes(int(size_per_second))}/s"


def release_display_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M")


def is_packaged_windows_executable() -> bool:
    return sys.platform == "win32" and getattr(sys, "frozen", False)


def stable_target_path(current_executable: Path | None = None) -> Path:
    current = (current_executable or Path(sys.executable)).resolve()
    # Overwrite a legacy stable executable in place so existing shortcuts keep
    # working; versioned downloads migrate to the new TokenMeter stable name.
    if current.name.lower() in {"tokenmeter.exe", "tokenspider.exe", "tokenscope.exe"}:
        return current
    return current.with_name(MAIN_EXECUTABLE_NAME)


def is_safe_cleanup_path(path: Path, allowed_root: Path) -> bool:
    """Allow only relative descendants whose resolved target stays in the cache."""
    if path.is_absolute():
        return False
    root = allowed_root.resolve(strict=False)
    target = (root / path).resolve(strict=False)
    if target == root:
        return False
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def cleanup_pending_update() -> None:
    manifest = config_manager.load_pending_update_cleanup()
    if not manifest:
        if config_manager.PENDING_UPDATE_CLEANUP_PATH.exists():
            config_manager.logger().warning("Invalid deferred update cleanup manifest")
            config_manager.clear_pending_update_cleanup()
        return
    if int(manifest.get("version", 0)) != MANIFEST_VERSION:
        config_manager.clear_pending_update_cleanup()
        return
    cleanup_paths = manifest.get("cleanup_paths", [])
    if not isinstance(cleanup_paths, list):
        config_manager.logger().warning("Invalid deferred update cleanup path list")
        config_manager.clear_pending_update_cleanup()
        return
    allowed_root = config_manager.updates_dir().resolve(strict=False)
    # The updater creates only this fixed backup beside the installed executable;
    # never trust a manifest-provided absolute path as the whitelist source.
    allowed_backups = {
        stable_target_path().with_suffix(Path(MAIN_EXECUTABLE_NAME).suffix + ".bak").resolve(
            strict=False
        )
    }
    for raw_path in cleanup_paths:
        if not isinstance(raw_path, str):
            config_manager.logger().warning(
                "Skipped invalid deferred update cleanup path: %r", raw_path
            )
            continue
        raw = Path(raw_path)
        resolved = (
            raw.resolve(strict=False)
            if raw.is_absolute()
            else (allowed_root / raw).resolve(strict=False)
        )
        if resolved not in allowed_backups and not is_safe_cleanup_path(raw, allowed_root):
            config_manager.logger().warning(
                "Skipped unsafe deferred update cleanup path: %s", raw
            )
            continue
        path = resolved
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        except OSError:
            config_manager.logger().warning("Deferred update cleanup failed: %s", path)
    config_manager.clear_pending_update_cleanup()


class GitHubReleaseClient:
    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "User-Agent": f"{APP_DISPLAY_NAME}/{APP_VERSION}",
            }
        )

    def check_for_updates(
        self,
        current_version: str,
        channel: str,
        *,
        use_cache: bool,
    ) -> CheckResult:
        channel = (channel or RELEASE_CHANNEL_STABLE).strip().lower()
        cached_state = config_manager.load_update_state()
        now = datetime.now(timezone.utc)
        # 汇总范围随已安装版本变化；旧缓存或升级前的缓存必须重新生成说明。
        if (use_cache and _cache_is_fresh(cached_state, now, channel)
                and cached_state.get("notes_current_version") == normalize_version(current_version)):
            cached_release = _release_from_state(cached_state)
            return CheckResult(
                current_version=current_version,
                latest_release=cached_release,
                update_available=bool(
                    cached_release and compare_versions(current_version, cached_release.version) < 0
                ),
                message=str(cached_state.get("last_message") or "已使用缓存的更新结果"),
                cached=True,
            )

        if channel == RELEASE_CHANNEL_PRERELEASE:
            release = self._load_latest_from_list()
        else:
            release = self._load_latest_stable()
        update_available = compare_versions(current_version, release.version) < 0
        if update_available:
            release = self._with_update_notes(current_version, release, channel)
        message = (
            f"发现新版本 v{release.version}"
            if update_available
            else f"当前已是最新版本 v{current_version}"
        )
        self._save_check_state(channel, release, now, message, current_version=current_version)
        return CheckResult(
            current_version=current_version,
            latest_release=release,
            update_available=update_available,
            message=message,
        )

    def download_bundle(
        self,
        release: ReleaseInfo,
        *,
        progress: Callable[[dict[str, object]], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> DownloadBundle:
        cache_dir = config_manager.updates_dir() / f"v{release.version}"
        cache_dir.mkdir(parents=True, exist_ok=True)
        checksum_map = self._load_checksums(release)
        expected = checksum_map.get(release.setup_asset.name.lower())
        if not expected:
            raise UpdateError("SHA256SUMS.txt 缺少安装包的校验值")

        asset = release.setup_asset
        final_path = cache_dir / asset.name
        actual_sha = _validate_cached_file(final_path, expected)
        if actual_sha:
            if progress:
                progress(
                    {
                        "stage": asset.name,
                        "downloaded": asset.size,
                        "total": asset.size,
                        "current": asset.size,
                        "current_total": asset.size,
                        "speed": 0.0,
                        "reused": True,
                    }
                )
        else:
            actual_sha = self._download_asset(
                asset,
                final_path,
                expected_sha=expected,
                bytes_before=0,
                bytes_total=asset.size,
                progress=progress,
                cancel_requested=cancel_requested,
            )

        return DownloadBundle(
            release=release,
            setup_asset=DownloadedAsset(asset=asset, path=final_path, sha256=actual_sha),
            cache_dir=cache_dir,
        )

    def download_pet_pack(
        self,
        directory: Path,
        *,
        release: PetReleaseInfo | None = None,
        progress: Callable[[dict[str, object]], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        host_only: bool = False,
    ) -> Path:
        release = release or self.latest_pet_release(cancel_requested=cancel_requested)
        if cancel_requested and cancel_requested():
            raise DownloadCancelled("已取消下载")
        asset = release.host_asset if host_only else release.asset
        expected_sha = release.host_sha256 if host_only else release.sha256
        if asset is None or expected_sha is None:
            raise UpdateError("当前桌宠版本未提供可复用资源的宿主更新包")
        destination = directory / asset.name
        self._download_asset(
            asset, destination, expected_sha=expected_sha,
            bytes_before=0, bytes_total=asset.size,
            progress=progress, cancel_requested=cancel_requested,
        )
        return destination

    def latest_pet_release(
        self, *, cancel_requested: Callable[[], bool] | None = None,
    ) -> PetReleaseInfo:
        candidates = []
        page = 1
        # 体验版需要能安装同批次的预发布桌宠；正式版继续排除预发布包，避免误升级。
        allow_prerelease = bool(SemVer.parse(APP_VERSION).prerelease)
        # 主程序与扩展共用仓库；分页避免主程序发版较多后找不到仍兼容的桌宠版本。
        while True:
            if cancel_requested and cancel_requested():
                raise DownloadCancelled("已取消下载")
            payload = self._request_json(f"{GITHUB_RELEASES_API_URL}?per_page=100&page={page}")
            if not isinstance(payload, list):
                raise UpdateError("GitHub Releases 返回格式不正确")
            for item in payload:
                if (not isinstance(item, dict) or item.get("draft")
                        or (item.get("prerelease") and not allow_prerelease)):
                    continue
                tag = str(item.get("tag_name") or "")
                if not tag.startswith(PET_RELEASE_TAG_PREFIX):
                    continue
                try:
                    version = SemVer.parse(tag[len(PET_RELEASE_TAG_PREFIX):])
                except ValueError:
                    continue
                if ((allow_prerelease or not version.prerelease)
                        and tag == PET_RELEASE_TAG_PREFIX + version.normalized()):
                    candidates.append((version, item))
            if len(payload) < 100:
                break
            page += 1
        for version, item in sorted(candidates, key=lambda entry: entry[0], reverse=True):
            if cancel_requested and cancel_requested():
                raise DownloadCancelled("已取消下载")
            assets = item.get("assets")
            if not isinstance(assets, list):
                continue
            # 只接受这个扩展 Tag 下的附件，不能拿其他版本的包冒充更新。
            prefix = (f"https://github.com/{GITHUB_REPOSITORY}/releases/download/"
                      f"{PET_RELEASE_TAG_PREFIX}{version.normalized()}/")
            by_name = {asset.name: asset for raw in assets
                       if (asset := _asset_from_payload(raw))
                       and asset.download_url == prefix + asset.name}
            name = PET_RELEASE_ASSET_TEMPLATE.format(version=version.normalized())
            if not {name, PET_MANIFEST_ASSET_NAME, SHA256_RELEASE_ASSET_NAME} <= by_name.keys():
                continue
            checksums = self._load_checksums(by_name[SHA256_RELEASE_ASSET_NAME])
            if not all(checksums.get(key.lower()) for key in (name, PET_MANIFEST_ASSET_NAME)):
                raise UpdateError("SHA256SUMS.txt 缺少桌宠扩展包或版本清单的校验值")
            manifest = self._load_pet_manifest(
                by_name[PET_MANIFEST_ASSET_NAME], checksums[PET_MANIFEST_ASSET_NAME.lower()],
                cancel_requested,
            )
            try:
                manifest = validate_pet_manifest(manifest)
            except ValueError:
                continue
            if manifest["version"] != version.normalized():
                raise UpdateError("桌宠版本清单与发布标签不一致")
            host_name = PET_HOST_RELEASE_ASSET_TEMPLATE.format(version=version.normalized())
            host_asset = by_name.get(host_name)
            host_sha = checksums.get(host_name.lower()) if host_asset is not None else None
            # 宿主小包是可选优化；缺失、未校验或没有资源身份时继续兼容完整包。
            if host_sha is None or not isinstance(manifest.get("resources"), dict):
                host_asset = None
                host_sha = None
            return PetReleaseInfo(
                version.normalized(), manifest, by_name[name], checksums[name.lower()],
                host_asset, host_sha,
            )
        raise UpdateError("尚未发布与当前主程序兼容的桌宠扩展包")

    def _load_pet_manifest(
        self, asset: ReleaseAsset, expected_sha: str, cancel_requested: Callable[[], bool] | None,
    ) -> object:
        response, _ = self._open_download_stream(asset.download_url)
        content = bytearray()
        try:
            for chunk in response.iter_content(chunk_size=8192):
                if cancel_requested and cancel_requested():
                    raise DownloadCancelled("已取消下载")
                content.extend(chunk)
                # 检查更新只需很小的清单，不能先下载整个运行时或接受无限增长的响应。
                if len(content) > 65536:
                    raise UpdateError("桌宠版本清单超过大小限制")
        finally:
            response.close()
        if hashlib.sha256(content).hexdigest() != expected_sha.lower():
            raise UpdateError("桌宠版本清单的 SHA256 校验失败")
        try:
            return json.loads(content)
        except ValueError as exc:
            raise UpdateError("桌宠扩展版本清单无效") from exc

    def _load_latest_stable(self) -> ReleaseInfo:
        payload = self._request_json(GITHUB_LATEST_RELEASE_API_URL)
        # 即使有人误把桌宠 Release 标成 latest，也不能让原有主程序更新失效。
        if isinstance(payload, dict) and str(payload.get("tag_name", "")).startswith(PET_RELEASE_TAG_PREFIX):
            return self._load_latest_from_list(stable_only=True)
        return _release_from_payload(payload)

    def _load_latest_from_list(self, *, stable_only: bool = False) -> ReleaseInfo:
        candidates: list[ReleaseInfo] = []
        page = 1
        while not candidates:
            suffix = "" if page == 1 else f"&page={page}"
            payload = self._request_json(f"{GITHUB_RELEASES_API_URL}?per_page=20{suffix}")
            if not isinstance(payload, list):
                raise UpdateError("GitHub Releases 返回格式不正确")
            for item in payload:
                if (not isinstance(item, dict) or item.get("draft")
                        or (stable_only and item.get("prerelease"))):
                    continue
                try:
                    release = _release_from_payload(item)
                    if not stable_only or not release.semver.prerelease:
                        candidates.append(release)
                except UpdateError:
                    continue
            if len(payload) < 20:
                break
            page += 1
        if not candidates:
            raise UpdateError("没有找到可用的 Release 附件")
        return max(candidates, key=lambda item: item.semver)

    def _with_update_notes(
        self, current_version: str, release: ReleaseInfo, channel: str,
    ) -> ReleaseInfo:
        current = SemVer.parse(current_version)
        notes = {release.semver: release.body}
        notes_size = len(release.body.encode("utf-8"))
        incomplete = not bool(release.body.strip())
        try:
            # GitHub 列表并非按语义版本排序；不能遇到一个旧版就停，以免漏掉后补发的版本。
            # 最多读取 1000 条记录，正文总量沿用元数据上限，避免历史增长拖住更新检查。
            for page in range(1, MAX_RELEASE_NOTES_PAGES + 1):
                payload = self._request_json(f"{GITHUB_RELEASES_API_URL}?per_page=100&page={page}")
                if not isinstance(payload, list):
                    raise UpdateError("GitHub Releases 返回格式不正确")
                for item in payload:
                    if (not isinstance(item, dict) or item.get("draft")
                            or str(item.get("tag_name", "")).startswith(PET_RELEASE_TAG_PREFIX)):
                        continue
                    try:
                        version = SemVer.parse(_release_version_from_payload(item))
                    except (UpdateError, ValueError):
                        continue
                    if (channel != RELEASE_CHANNEL_PRERELEASE
                            and (item.get("prerelease") or version.prerelease)):
                        continue
                    if not current < version or release.semver < version or version in notes:
                        continue
                    # 历史说明不依赖旧安装包是否仍在；下载始终使用已验证的最新附件。
                    body = str(item.get("body") or "").strip()
                    notes_size += len(body.encode("utf-8"))
                    if notes_size > MAX_METADATA_BYTES:
                        raise UpdateError("GitHub 更新信息超过大小限制")
                    notes[version] = body
                    incomplete = incomplete or not bool(body)
                if len(payload) < 100:
                    break
            else:
                incomplete = True
        except (UpdateError, requests.RequestException):
            # 历史查询失败不能让已发现的安装包消失；保留已获取的说明并在弹窗提示缺失。
            incomplete = True
        return replace(
            release,
            update_notes="\n\n".join(
                f"## v{version.normalized()}\n\n{notes[version]}" for version in sorted(notes, reverse=True)
            ),
            notes_incomplete=incomplete,
        )

    def _request_json(self, url: str) -> object:
        try:
            response = self._session.get(url, timeout=HTTP_TIMEOUT, stream=True)
        except requests.Timeout as exc:
            raise UpdateError("连接 GitHub 超时，请稍后重试") from exc
        except requests.RequestException as exc:
            raise UpdateError("无法连接 GitHub，请检查网络后重试") from exc
        if response.status_code == 403:
            response.close()
            raise UpdateError("GitHub API 限流，请稍后再试")
        if response.status_code == 404:
            response.close()
            raise UpdateError("GitHub Release 不存在或仓库地址无效")
        if response.status_code >= 400:
            response.close()
            raise UpdateError(f"GitHub API 请求失败（HTTP {response.status_code}）")
        try:
            content = bytearray()
            for chunk in response.iter_content(chunk_size=8192):
                content.extend(chunk)
                if len(content) > MAX_METADATA_BYTES:
                    raise UpdateError("GitHub 更新信息超过大小限制")
            return json.loads(content)
        except ValueError as exc:
            raise UpdateError("GitHub API 返回了无法解析的数据") from exc
        finally:
            response.close()

    def _load_checksums(self, release: ReleaseInfo | ReleaseAsset) -> dict[str, str]:
        # 主程序更新与可选扩展共用官方来源检查、重定向限制和 SHA256 校验。
        asset = release.checksum_asset if isinstance(release, ReleaseInfo) else release
        response, _ = self._open_download_stream(asset.download_url)
        try:
            content_bytes = bytearray()
            for chunk in response.iter_content(chunk_size=8192):
                content_bytes.extend(chunk)
                if len(content_bytes) > MAX_METADATA_BYTES:
                    raise UpdateError("SHA256 校验清单超过大小限制")
            content = content_bytes.decode("utf-8")
        finally:
            response.close()
        mapping: dict[str, str] = {}
        for line in content.splitlines():
            match = _SHA256_LINE_RE.match(line.strip())
            if not match:
                continue
            mapping[match.group("name").strip().lower()] = match.group("sha").lower()
        return mapping

    def _download_asset(
        self,
        asset: ReleaseAsset,
        final_path: Path,
        *,
        expected_sha: str,
        bytes_before: int,
        bytes_total: int,
        progress: Callable[[dict[str, object]], None] | None,
        cancel_requested: Callable[[], bool] | None,
    ) -> str:
        response, resolved_url = self._open_download_stream(asset.download_url)
        if not _is_allowed_download_url(resolved_url):
            response.close()
            raise UpdateError("下载地址不是 GitHub 官方 Release 附件")
        temp_path = final_path.with_suffix(final_path.suffix + ".part")
        sha256 = hashlib.sha256()
        downloaded = 0
        started_at = time.monotonic()
        try:
            with temp_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if cancel_requested and cancel_requested():
                        raise DownloadCancelled("已取消下载")
                    if not chunk:
                        continue
                    # 流式响应也必须有实际字节上限；哈希只在下载结束后才能发现错误。
                    limit = min(asset.size, MAX_DOWNLOAD_BYTES) if asset.size > 0 else MAX_DOWNLOAD_BYTES
                    if downloaded + len(chunk) > limit:
                        raise UpdateError(f"{asset.name} 超过下载大小限制")
                    handle.write(chunk)
                    sha256.update(chunk)
                    downloaded += len(chunk)
                    if progress:
                        elapsed = max(time.monotonic() - started_at, 0.001)
                        progress(
                            {
                                "stage": asset.name,
                                "downloaded": bytes_before + downloaded,
                                "total": bytes_total,
                                "current": downloaded,
                                "current_total": asset.size,
                                "speed": downloaded / elapsed,
                                "reused": False,
                            }
                        )
            digest = sha256.hexdigest().lower()
            if digest != expected_sha.lower():
                raise UpdateError(f"{asset.name} 的 SHA256 校验失败")
            temp_path.replace(final_path)
            return digest
        except Exception:
            temp_path.unlink(missing_ok=True)
            final_path.unlink(missing_ok=True)
            raise
        finally:
            response.close()

    def _open_download_stream(self, url: str) -> tuple[requests.Response, str]:
        current_url = url
        for _ in range(6):
            if not _is_allowed_download_url(current_url, require_release_path=True):
                raise UpdateError("只允许从 GitHub 官方 Release 地址下载更新")
            try:
                response = self._session.get(
                    current_url,
                    timeout=HTTP_TIMEOUT,
                    stream=True,
                    allow_redirects=False,
                )
            except requests.Timeout as exc:
                raise UpdateError("下载更新超时，请稍后重试") from exc
            except requests.RequestException as exc:
                raise UpdateError("下载更新失败，请检查网络后重试") from exc
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise UpdateError("GitHub 返回了无效的重定向地址")
                current_url = urljoin(current_url, location)
                continue
            if response.status_code >= 400:
                response.close()
                raise UpdateError(f"下载更新失败（HTTP {response.status_code}）")
            return response, current_url
        raise UpdateError("下载更新时遇到了过多重定向")

    def _save_check_state(
        self,
        channel: str,
        release: ReleaseInfo,
        checked_at: datetime,
        message: str,
        *,
        current_version: str = APP_VERSION,
    ) -> None:
        config_manager.save_update_state(
            {
                "last_checked_at": checked_at.isoformat(),
                "last_channel": channel,
                "last_message": message,
                "latest_version": release.version,
                "latest_tag_name": release.tag_name,
                "latest_published_at": release.published_at,
                "latest_body": release.body,
                "latest_update_notes": release.update_notes,
                "notes_incomplete": release.notes_incomplete,
                "notes_current_version": normalize_version(current_version),
                "latest_is_prerelease": release.is_prerelease,
                "latest_setup_asset_name": release.setup_asset.name,
                "latest_setup_asset_url": release.setup_asset.download_url,
                "latest_setup_asset_size": release.setup_asset.size,
                "latest_checksum_asset_name": release.checksum_asset.name,
                "latest_checksum_asset_url": release.checksum_asset.download_url,
                "latest_checksum_asset_size": release.checksum_asset.size,
            }
        )


def launch_installer(bundle: DownloadBundle) -> None:
    current_executable = Path(sys.executable).resolve()
    install_dir = current_executable.parent
    updates_root = config_manager.updates_dir().resolve(strict=False)
    cache_dir = bundle.cache_dir.resolve(strict=False)
    try:
        cleanup_paths = [str(cache_dir.relative_to(updates_root))]
    except ValueError as exc:
        raise UpdateError("更新缓存目录不在允许的清理范围内") from exc
    try:
        from data import history

        backup_path = history.backup_usage_database(bundle.release.version)
    except Exception as exc:
        config_manager.logger().exception("Pre-update usage database backup failed")
        raise UpdateError("更新前备份本地 usage.db 失败，已取消更新") from exc
    if backup_path is not None:
        config_manager.logger().info("Pre-update usage database backup: %s", backup_path)
    config_manager.save_pending_update_cleanup(
        {
            "version": MANIFEST_VERSION,
            "cleanup_paths": cleanup_paths,
        }
    )
    command = [
        str(bundle.setup_asset.path),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        f"/DIR={install_dir}",
        "/TOKENMETERUPDATE",
    ]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(command, close_fds=False, creationflags=creation_flags)
    except OSError as exc:
        config_manager.clear_pending_update_cleanup()
        raise UpdateError("无法启动更新安装包") from exc


def skipped_version() -> str:
    return str(config_manager.get("UPDATE_SKIPPED_VERSION", "")).strip()


def mark_skipped_version(version: str) -> None:
    config_manager.save_config({"UPDATE_SKIPPED_VERSION": normalize_version(version)})


def remember_prompted_version(version: str) -> None:
    config_manager.save_update_state({"last_prompted_version": normalize_version(version)})


def last_prompted_version() -> str:
    return str(config_manager.load_update_state().get("last_prompted_version", "")).strip()


def status_summary(current_version: str) -> str:
    state = config_manager.load_update_state()
    checked_at = str(state.get("last_checked_at") or "").strip()
    message = str(state.get("last_message") or "").strip()
    if not checked_at:
        return f"当前版本 v{current_version}，尚未检查更新"
    try:
        timestamp = datetime.fromisoformat(checked_at).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        timestamp = checked_at
    return f"{message}（上次检查：{timestamp}）"


def _cache_is_fresh(state: dict[str, object], now: datetime, channel: str) -> bool:
    checked_at = str(state.get("last_checked_at") or "").strip()
    if not checked_at or str(state.get("last_channel") or "") != channel:
        return False
    try:
        timestamp = datetime.fromisoformat(checked_at)
    except ValueError:
        return False
    return now - timestamp <= AUTO_CHECK_INTERVAL


def _release_from_state(state: dict[str, object]) -> ReleaseInfo | None:
    version = str(state.get("latest_version") or "").strip()
    if not version:
        return None
    try:
        semver = SemVer.parse(version)
    except ValueError:
        return None
    try:
        setup_asset = ReleaseAsset(
            name=str(state["latest_setup_asset_name"]),
            download_url=str(state["latest_setup_asset_url"]),
            size=int(str(state.get("latest_setup_asset_size", 0))),
        )
        checksum_asset = ReleaseAsset(
            name=str(state["latest_checksum_asset_name"]),
            download_url=str(state["latest_checksum_asset_url"]),
            size=int(str(state.get("latest_checksum_asset_size", 0))),
        )
    except KeyError:
        return None
    return ReleaseInfo(
        version=semver.normalized(),
        semver=semver,
        tag_name=str(state.get("latest_tag_name") or f"v{version}"),
        published_at=str(state.get("latest_published_at") or ""),
        body=str(state.get("latest_body") or ""),
        update_notes=str(state.get("latest_update_notes") or ""),
        notes_incomplete=bool(state.get("notes_incomplete")),
        is_prerelease=bool(state.get("latest_is_prerelease")),
        setup_asset=setup_asset,
        checksum_asset=checksum_asset,
    )


def _release_from_payload(payload: object) -> ReleaseInfo:
    if not isinstance(payload, dict):
        raise UpdateError("GitHub Release 返回格式不正确")
    if payload.get("draft"):
        raise UpdateError("草稿 Release 不能用于在线更新")
    if str(payload.get("tag_name", "")).startswith(PET_RELEASE_TAG_PREFIX):
        raise UpdateError("桌宠扩展 Release 不能用于主程序更新")
    version = _release_version_from_payload(payload)
    semver = SemVer.parse(version)
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("Release 附件列表无效")
    setup_asset = _select_setup_asset(version, assets)
    checksum_asset = _select_checksum_asset(assets)
    return ReleaseInfo(
        version=semver.normalized(),
        semver=semver,
        tag_name=str(payload.get("tag_name") or f"v{version}"),
        published_at=str(payload.get("published_at") or ""),
        body=str(payload.get("body") or "").strip(),
        is_prerelease=bool(payload.get("prerelease")),
        setup_asset=setup_asset,
        checksum_asset=checksum_asset,
    )


def _release_version_from_payload(payload: dict[str, object]) -> str:
    for key in ("tag_name", "name"):
        raw = str(payload.get(key) or "").strip()
        if not raw:
            continue
        try:
            return SemVer.parse(raw).normalized()
        except ValueError:
            continue
    raise UpdateError("Release 版本号不是有效的语义化版本")


def _select_setup_asset(version: str, assets: Iterable[object]) -> ReleaseAsset:
    expected_name = SETUP_RELEASE_ASSET_TEMPLATE.format(version=version).lower()
    for asset in assets:
        candidate = _asset_from_payload(asset)
        if not candidate:
            continue
        if candidate.name.lower() == expected_name:
            return candidate
        match = _SETUP_NAME_RE.match(candidate.name)
        if match and match.group("version").lower() == version.lower():
            return candidate
    raise UpdateError("没有找到匹配的 Windows x64 安装包")


def _select_checksum_asset(assets: Iterable[object]) -> ReleaseAsset:
    for asset in assets:
        candidate = _asset_from_payload(asset)
        if candidate and candidate.name == SHA256_RELEASE_ASSET_NAME:
            return candidate
    raise UpdateError("没有找到 SHA256SUMS.txt")


def _asset_from_payload(payload: object) -> ReleaseAsset | None:
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("name") or "").strip()
    if not name:
        return None
    url = str(payload.get("browser_download_url") or "").strip()
    if not url:
        return None
    if not _is_allowed_download_url(url, require_release_path=True):
        return None
    return ReleaseAsset(name=name, download_url=url, size=int(payload.get("size") or 0))


def _is_allowed_download_url(url: str, *, require_release_path: bool = False) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if require_release_path and host == "github.com":
        return parsed.path.startswith(_DOWNLOAD_PATH_PREFIX)
    if host in _ALLOWED_API_HOSTS:
        return True
    if host not in _ALLOWED_DOWNLOAD_HOSTS:
        return False
    if host == "github.com":
        return parsed.path.startswith(_DOWNLOAD_PATH_PREFIX)
    return True


def _validate_cached_file(path: Path, expected_sha: str) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    digest_value = digest.hexdigest().lower()
    if digest_value != expected_sha.lower():
        path.unlink(missing_ok=True)
        return None
    return digest_value
