import hashlib
import json
import logging
import os
import shutil
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from core import pet_extension as pet
from core.identity import (
    APP_VERSION, GITHUB_RELEASES_API_URL, PET_HOST_RELEASE_ASSET_TEMPLATE,
    PET_RELEASE_ASSET_TEMPLATE, PET_RELEASE_TAG_PREFIX,
)
from scripts import build_release
from updater.client import (
    DownloadCancelled, GitHubReleaseClient, PetReleaseInfo, ReleaseAsset, UpdateError,
    validate_pet_manifest,
)

_resource_digest = hashlib.sha256()
for _resource_name in sorted(("pet/vup.lps", "pet/vup/Default/1.png")):
    _resource_digest.update(_resource_name.encode())
    _resource_digest.update(b"\0")
    _resource_digest.update(b"test payload")
TEST_RESOURCE_SHA = _resource_digest.hexdigest()


def payload(directory):
    directory.mkdir(parents=True, exist_ok=True)
    for name in (*pet.REQUIRED_FILES, "coreclr.dll", "resources/pet/vup/Default/1.png"):
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test payload")
    (directory / pet.PACK_MANIFEST).write_text(json.dumps(build_release.PET_MANIFEST))
    resources = [path for path in (directory / "resources").rglob("*") if path.is_file()]
    (directory / pet.RESOURCES_MANIFEST).write_text(json.dumps({
        "revision": "a" * 40,
        "resource_files": len(resources),
        "resource_bytes": sum(path.stat().st_size for path in resources),
    }))
    return directory


@pytest.fixture
def pack(tmp_path, monkeypatch):
    source = payload(tmp_path / "source")
    monkeypatch.setattr(build_release, "PET_PACK_PATH", tmp_path / "pet.zip")
    monkeypatch.setattr(build_release, "PET_HOST_PACK_PATH", tmp_path / "pet-host.zip")
    return build_release.package_pet_payload(source)


def test_build_install_remove_roundtrip_preserves_user_state(pack, tmp_path, monkeypatch):
    monkeypatch.setattr(pet.config_manager, "CONFIG_DIR", tmp_path / "data")
    monkeypatch.setattr(pet.sys, "frozen", False, raising=False)
    destination = pet.extension_directory()
    pet.install_pack(pack, destination)
    assert pet.installed_executable() == destination / pet.PET_EXECUTABLE
    user_data = tmp_path / "data/vpet/preferences.json"
    user_data.parent.mkdir()
    user_data.write_text("keep")
    cache = user_data.parent / "cache"
    cache.mkdir()
    (cache / "frame.png").write_bytes(b"cache")
    config = tmp_path / "data/config.json"
    config.write_text("keep config")
    pet.uninstall()
    assert not destination.exists()
    assert pet.installed_executable() is None
    assert user_data.read_text() == "keep"
    assert not cache.exists()
    assert config.read_text() == "keep config"
    assert not list(destination.parent.glob(".vpet-*"))
    pet.install_pack(pack, destination)
    assert pet.installed_executable() is not None


def test_release_pack_requires_bundled_runtime(tmp_path, monkeypatch):
    source = payload(tmp_path / "source")
    (source / "coreclr.dll").unlink()
    monkeypatch.setattr(build_release, "PET_PACK_PATH", tmp_path / "pet.zip")
    monkeypatch.setattr(build_release, "PET_HOST_PACK_PATH", tmp_path / "pet-host.zip")
    with pytest.raises(ValueError, match="runtime"):
        build_release.package_pet_payload(source)


@pytest.mark.parametrize("filename", [
    "../escape.txt", "/escape.txt", "C:/escape.txt", "dir\\escape.txt", "dir/file:stream",
    "dir/trailing. ", "TokenMeter.Pet.exe", "resources/../../escape", "CON", "bad?.txt",
])
def test_extract_rejects_unsafe_and_duplicate_paths(pack, tmp_path, filename):
    with zipfile.ZipFile(pack, "a") as archive:
        archive.writestr(filename, b"unsafe")
    if "\\" in filename:
        # Windows 的 ZipInfo 构造器会自动转正斜杠，直接改文件名字段才能模拟外部恶意 ZIP。
        pack.write_bytes(pack.read_bytes().replace(filename.replace("\\", "/").encode(), filename.encode()))
    destination = tmp_path / "extensions/vpet"
    with pytest.raises((ValueError, FileExistsError)):
        pet.install_pack(pack, destination)
    assert not destination.exists()
    assert not (tmp_path / "escape.txt").exists()
    assert not list(destination.parent.iterdir())


def test_extract_rejects_symlink_and_oversized_pack(pack, tmp_path, monkeypatch):
    link = zipfile.ZipInfo("link")
    link.create_system = 3
    link.external_attr = 0o120777 << 16
    with zipfile.ZipFile(pack, "a") as archive:
        archive.writestr(link, "../outside")
    destination = tmp_path / "extensions/vpet"
    with pytest.raises(ValueError, match="不安全"):
        pet.install_pack(pack, destination)
    huge = zipfile.ZipInfo("huge")
    huge.file_size = 3 * 1024**3
    with patch.object(zipfile.ZipFile, "infolist", return_value=[huge]):
        with pytest.raises(ValueError, match="大小限制"):
            pet.install_pack(pack, destination)
    assert not destination.exists()


def test_cancelled_install_leaves_no_partial_payload(pack, tmp_path):
    destination = tmp_path / "extensions/vpet"
    with pytest.raises(DownloadCancelled):
        pet.install_pack(pack, destination, lambda: True)
    assert not destination.exists()
    assert not list(destination.parent.iterdir())


def test_incompatible_or_incomplete_pack_never_becomes_available(pack, tmp_path):
    for field, value in [("protocol", 99), ("min_app_version", "99.0.0"),
                         ("max_app_version", APP_VERSION), ("platform", "linux")]:
        with zipfile.ZipFile(pack) as source, zipfile.ZipFile(tmp_path / "bad.zip", "w") as out:
            for item in source.infolist():
                content = source.read(item)
                if item.filename == pet.PACK_MANIFEST:
                    manifest = json.loads(content)
                    manifest[field] = value
                    content = json.dumps(manifest).encode()
                out.writestr(item, content)
        with pytest.raises(ValueError, match="不兼容"):
            pet.install_pack(tmp_path / "bad.zip", tmp_path / "vpet")
        assert not (tmp_path / "vpet").exists()
    with zipfile.ZipFile(pack) as source, zipfile.ZipFile(tmp_path / "bad.zip", "w") as out:
        for item in source.infolist():
            if not item.filename.endswith(".png"):
                out.writestr(item, source.read(item))
    with pytest.raises(ValueError, match="动画资源"):
        pet.install_pack(tmp_path / "bad.zip", tmp_path / "vpet")


def test_install_never_overwrites_existing_extension(pack, tmp_path):
    destination = tmp_path / "vpet"
    destination.mkdir()
    marker = destination / "keep.txt"
    marker.write_text("keep")
    with pytest.raises(ValueError, match="先卸载"):
        pet.install_pack(pack, destination)
    assert marker.read_text() == "keep"


def test_uninstall_legacy_bundle_but_never_developer_build(tmp_path, monkeypatch):
    monkeypatch.setattr(pet.config_manager, "CONFIG_DIR", tmp_path / "data")
    monkeypatch.setattr(pet.sys, "frozen", True, raising=False)
    monkeypatch.setattr(pet.sys, "executable", str(tmp_path / "app/TokenMeter.exe"))
    legacy = payload(tmp_path / "app/pet")
    source = payload(tmp_path / "build/vpet")
    pet.uninstall()
    assert not legacy.exists()
    assert source.exists()


def test_uninstall_refuses_redirected_directory(tmp_path, monkeypatch):
    target = payload(tmp_path / "outside")
    link = tmp_path / "vpet"
    monkeypatch.setattr(pet, "removable_directories", lambda: [link])
    # 不要求测试机拥有创建 Windows 符号链接的权限，模拟解析后的跳转即可验证保护条件。
    link.mkdir()
    original = Path.resolve
    with patch.object(Path, "resolve", lambda self, *args, **kwargs:
                      target if self == link else original(self, *args, **kwargs)):
        with pytest.raises(ValueError, match="链接"):
            pet.uninstall()
    assert (target / pet.PET_EXECUTABLE).exists()


def release_payload(version=build_release.PET_MANIFEST["version"], *, with_host=False):
    name = PET_RELEASE_ASSET_TEMPLATE.format(version=version)
    host_name = PET_HOST_RELEASE_ASSET_TEMPLATE.format(version=version)
    tag = PET_RELEASE_TAG_PREFIX + version
    return {"tag_name": tag, "assets": [
        {"name": filename, "size": 50, "browser_download_url":
         f"https://github.com/zensoku142/TokenMeter/releases/download/{tag}/{filename}"}
        for filename in ((name, host_name, pet.PACK_MANIFEST, "SHA256SUMS.txt")
                         if with_host else (name, pet.PACK_MANIFEST, "SHA256SUMS.txt"))
    ]}


def release_info(version=build_release.PET_MANIFEST["version"], **manifest):
    asset = release_payload(version)["assets"][0]
    resources = {"revision": "a" * 40, "files": 2, "bytes": 24,
                 "sha256": TEST_RESOURCE_SHA}
    return PetReleaseInfo(version, dict(build_release.PET_MANIFEST, version=version,
                                        resources=resources, **manifest),
                          ReleaseAsset(asset["name"], asset["browser_download_url"], asset["size"]),
                          "a" * 64)


def test_pet_download_uses_independent_release_and_verified_asset(tmp_path):
    client = GitHubReleaseClient()
    data = release_payload()
    name = data["assets"][0]["name"]
    with (
        patch.object(client, "_request_json", return_value=[data]) as metadata,
        patch.object(client, "_load_checksums", return_value={name.lower(): "a" * 64, pet.PACK_MANIFEST: "b" * 64}),
        patch.object(client, "_load_pet_manifest", return_value=build_release.PET_MANIFEST),
        patch.object(client, "_download_asset") as download,
    ):
        assert client.download_pet_pack(tmp_path) == tmp_path / name
    metadata.assert_called_once_with(f"{GITHUB_RELEASES_API_URL}?per_page=100&page=1")
    assert download.call_args.kwargs["expected_sha"] == "a" * 64
    assert download.call_args.args[0].name == name


def test_pet_release_exposes_verified_host_pack_when_resources_are_identified():
    client = GitHubReleaseClient()
    data = release_payload(with_host=True)
    full_name, host_name = (asset["name"] for asset in data["assets"][:2])
    manifest = {**build_release.PET_MANIFEST, "resources": {
        "revision": "a" * 40, "files": 2, "bytes": 24, "sha256": TEST_RESOURCE_SHA,
    }}
    with (
        patch.object(client, "_request_json", return_value=[data]),
        patch.object(client, "_load_checksums", return_value={
            full_name.lower(): "a" * 64, host_name.lower(): "b" * 64,
            pet.PACK_MANIFEST: "c" * 64,
        }),
        patch.object(client, "_load_pet_manifest", return_value=manifest),
    ):
        release = client.latest_pet_release()
    assert release.host_asset is not None and release.host_asset.name == host_name
    assert release.host_sha256 == "b" * 64


def test_pet_download_selects_host_asset_when_requested(tmp_path):
    client = GitHubReleaseClient()
    full = ReleaseAsset("full.zip", "https://example.com/full.zip", 500)
    host = ReleaseAsset("host.zip", "https://example.com/host.zip", 80)
    release = PetReleaseInfo("1.0.0", {}, full, "a" * 64, host, "b" * 64)
    with patch.object(client, "_download_asset") as download:
        result = client.download_pet_pack(tmp_path, release=release, host_only=True)
    assert result == tmp_path / "host.zip"
    assert download.call_args.args[:2] == (host, result)
    assert download.call_args.kwargs["expected_sha"] == "b" * 64
    assert download.call_args.kwargs["bytes_total"] == 80


def test_unverified_host_pack_falls_back_to_full_release():
    client = GitHubReleaseClient()
    data = release_payload(with_host=True)
    full_name = data["assets"][0]["name"]
    manifest = {**build_release.PET_MANIFEST, "resources": {
        "revision": "a" * 40, "files": 2, "bytes": 24, "sha256": TEST_RESOURCE_SHA,
    }}
    with (
        patch.object(client, "_request_json", return_value=[data]),
        patch.object(client, "_load_checksums", return_value={
            full_name.lower(): "a" * 64, pet.PACK_MANIFEST: "c" * 64,
        }),
        patch.object(client, "_load_pet_manifest", return_value=manifest),
    ):
        release = client.latest_pet_release()
    assert release.host_asset is None and release.host_sha256 is None


@pytest.mark.parametrize("bad_hash", [False, True])
def test_pet_download_stream_checks_actual_bytes_and_removes_partial_files(tmp_path, bad_hash):
    client = GitHubReleaseClient()
    data = release_payload()
    name = data["assets"][0]["name"]
    contents = b"pet archive contents"
    response = Mock()
    response.iter_content.return_value = [contents[:5], contents[5:]]
    checksum = "0" * 64 if bad_hash else hashlib.sha256(contents).hexdigest()
    with (
        patch.object(client, "_request_json", return_value=[data]),
        patch.object(client, "_load_checksums", return_value={name.lower(): checksum, pet.PACK_MANIFEST: "b" * 64}),
        patch.object(client, "_load_pet_manifest", return_value=build_release.PET_MANIFEST),
        patch.object(client, "_open_download_stream", return_value=(response, data["assets"][0]["browser_download_url"])),
    ):
        if bad_hash:
            with pytest.raises(UpdateError, match="SHA256"):
                client.download_pet_pack(tmp_path)
            assert not list(tmp_path.iterdir())
        else:
            assert client.download_pet_pack(tmp_path).read_bytes() == contents
    response.close.assert_called_once()


@pytest.mark.parametrize("problem", ["missing", "draft", "wrong_tag", "untrusted", "checksum"])
def test_pet_download_rejects_unavailable_or_unverified_asset(tmp_path, problem):
    client = GitHubReleaseClient()
    data = release_payload()
    if problem == "missing":
        data["assets"] = []
    elif problem == "draft":
        data["draft"] = True
    elif problem == "wrong_tag":
        data["tag_name"] = "v0.0.0"
    elif problem == "untrusted":
        data["assets"][0]["browser_download_url"] = "https://example.com/pet.zip"
    with (
        patch.object(client, "_request_json", return_value=data),
        patch.object(client, "_load_checksums", return_value={}),
        patch.object(client, "_download_asset") as download,
    ):
        with pytest.raises(UpdateError):
            client.download_pet_pack(tmp_path)
    download.assert_not_called()


@pytest.mark.parametrize("failure", [DownloadCancelled("cancel"), UpdateError("hash mismatch"), OSError("offline")])
def test_failed_download_cleans_cache_and_preserves_panel_state(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(pet.config_manager, "CONFIG_DIR", tmp_path)
    def fail(directory, **kwargs):
        (directory / "partial.zip").write_bytes(b"partial")
        raise failure
    with patch.object(GitHubReleaseClient, "download_pet_pack", side_effect=fail):
        with pytest.raises(type(failure)):
            pet.download_and_install(Mock(), lambda: False, release=release_info())
    assert not pet.extension_directory().exists()
    assert not list((tmp_path / "extensions").iterdir())


def test_settings_missing_pack_and_uninstall_lifecycle(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox
    from ui.qt_settings import SettingsWindow
    from config.defaults import DEFAULT_CONFIG

    app = QApplication.instance() or QApplication([])
    values = {**DEFAULT_CONFIG, "VPET_ENABLED": True}
    monkeypatch.setattr(pet.config_manager, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(pet.config_manager, "load_config", lambda: values.copy())
    monkeypatch.setattr(pet.config_manager, "all_config", lambda: values.copy())
    local_build = tmp_path / "TokenMeter.Pet.exe"
    local_build.touch()
    with patch("ui.vpet_host.host_executable", return_value=local_build):
        window = SettingsWindow()
        assert not window.vpet_check.isEnabled()
        assert not window.vpet_check.isChecked()
        assert window.pet_install_button.isEnabled()
        assert not window.pet_uninstall_button.isEnabled()
        assert window.pet_version_label.text() == "桌宠版本：未安装"
    destination = pet.extension_directory()
    destination.mkdir(parents=True)
    window._refresh_pet_controls()
    assert not window.vpet_check.isEnabled()
    assert window.pet_version_label.text() == "桌宠版本：不可用"
    payload(destination)
    window._refresh_pet_controls()
    assert window.vpet_check.isEnabled()
    assert not window.pet_install_button.isEnabled()
    assert window.pet_uninstall_button.isEnabled()
    assert window.pet_version_label.text() == f"桌宠版本：v{build_release.PET_MANIFEST['version']}"
    events = []
    window.on_saved = lambda: events.append("stop-host")
    with (
        patch("ui.qt_settings.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes),
        patch.object(pet.config_manager, "save_config", side_effect=lambda value: events.append(value)),
        patch.object(window, "_start_pet_task", side_effect=lambda op: events.append(op)),
    ):
        window._uninstall_pet()
    assert events == [{"VPET_ENABLED": False}, "stop-host", "uninstall"]
    assert not window.vpet_check.isChecked()
    window.close()
    app.processEvents()


def test_manifest_accepts_independent_version_and_enforces_compatibility_range():
    manifest = dict(build_release.PET_MANIFEST, version="8.2.1",
                    min_app_version="1.12.0", max_app_version="2.0.0")
    assert validate_pet_manifest(manifest, "1.15.0")["version"] == "8.2.1"
    for host_version in ("1.11.9", "2.0.0", "2.0.1"):
        with pytest.raises(ValueError, match="不兼容"):
            validate_pet_manifest(manifest, host_version)


@pytest.mark.parametrize("manifest", [None, [], {}, {"version": "bad"},
    dict(build_release.PET_MANIFEST, version="../../outside"),
    dict(build_release.PET_MANIFEST, protocol=True),
    dict(build_release.PET_MANIFEST, min_app_version=None),
    dict(build_release.PET_MANIFEST, max_app_version=3)])
def test_invalid_manifest_cannot_be_installed(manifest):
    with pytest.raises(ValueError):
        validate_pet_manifest(manifest)


def test_pack_has_separate_version_and_verified_manifest_without_main_installer(pack):
    with zipfile.ZipFile(pack) as archive:
        manifest = json.loads(archive.read(pet.PACK_MANIFEST))
    assert {key: manifest[key] for key in build_release.PET_MANIFEST} == build_release.PET_MANIFEST
    assert manifest["resources"] == {
        "revision": "a" * 40, "files": 2, "bytes": 24, "sha256": TEST_RESOURCE_SHA,
    }
    assert "app_version" not in manifest
    assert json.loads((pack.parent / pet.PACK_MANIFEST).read_text()) == manifest
    checksums = (pack.parent / "SHA256SUMS.txt").read_text()
    for path in (pack, pack.parent / "pet-host.zip", pack.parent / pet.PACK_MANIFEST):
        assert f"{hashlib.sha256(path.read_bytes()).hexdigest()} *{path.name}" in checksums


def test_host_pack_excludes_reusable_resources(pack):
    host = pack.parent / "pet-host.zip"
    with zipfile.ZipFile(host) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read(pet.PACK_MANIFEST))
    assert not any(name.startswith("resources/") for name in names)
    assert pet.RESOURCES_MANIFEST in names
    assert manifest["resources"]["revision"] == "a" * 40
    assert host.stat().st_size < pack.stat().st_size


def test_update_replaces_only_extension_and_preserves_user_preferences(pack, tmp_path, monkeypatch):
    monkeypatch.setattr(pet.config_manager, "CONFIG_DIR", tmp_path / "data")
    destination = payload(pet.extension_directory())
    marker = destination / "removed-animation.png"
    marker.write_bytes(b"obsolete")
    (destination / pet.PACK_MANIFEST).write_text(json.dumps(dict(build_release.PET_MANIFEST, version="0.0.1")))
    preferences = tmp_path / "data/vpet/layout.json"
    preferences.parent.mkdir()
    preferences.write_text("unchanged")
    expected = json.loads((pack.parent / pet.PACK_MANIFEST).read_text())
    pet.install_pack(pack, destination, replace_existing=True, expected_manifest=expected)
    assert pet.installed_manifest()["version"] == build_release.PET_MANIFEST["version"]
    assert not marker.exists()
    assert preferences.read_text() == "unchanged"
    assert not list(destination.parent.glob(".vpet-*"))


def test_update_downloads_host_pack_and_reuses_matching_resources(pack, tmp_path, monkeypatch):
    monkeypatch.setattr(pet.config_manager, "CONFIG_DIR", tmp_path / "data")
    destination = payload(pet.extension_directory())
    resource = destination / "resources/pet/vup/Default/1.png"
    original_resource = resource.read_bytes()
    obsolete = destination / "obsolete-host.dll"
    obsolete.write_bytes(b"remove")
    manifest = json.loads((pack.parent / pet.PACK_MANIFEST).read_text())
    host = pack.parent / "pet-host.zip"
    release = PetReleaseInfo(
        manifest["version"], manifest,
        ReleaseAsset("full.zip", "https://example.com/full.zip", pack.stat().st_size), "a" * 64,
        ReleaseAsset("host.zip", "https://example.com/host.zip", host.stat().st_size), "b" * 64,
    )
    with patch.object(GitHubReleaseClient, "download_pet_pack", return_value=host) as download:
        pet.download_and_install(Mock(), lambda: False, release=release, replace_existing=True)
    assert download.call_args.kwargs["host_only"] is True
    assert resource.read_bytes() == original_resource
    assert not obsolete.exists()
    assert pet.installed_manifest()["version"] == manifest["version"]


def test_update_falls_back_to_full_pack_when_local_resources_changed(pack, tmp_path, monkeypatch):
    monkeypatch.setattr(pet.config_manager, "CONFIG_DIR", tmp_path / "data")
    destination = payload(pet.extension_directory())
    (destination / "resources/pet/vup/Default/1.png").write_bytes(b"changed")
    manifest = json.loads((pack.parent / pet.PACK_MANIFEST).read_text())
    release = PetReleaseInfo(
        manifest["version"], manifest,
        ReleaseAsset("full.zip", "https://example.com/full.zip", pack.stat().st_size), "a" * 64,
        ReleaseAsset("host.zip", "https://example.com/host.zip", 1), "b" * 64,
    )
    with patch.object(GitHubReleaseClient, "download_pet_pack", return_value=pack) as download:
        pet.download_and_install(Mock(), lambda: False, release=release, replace_existing=True)
    assert download.call_args.kwargs["host_only"] is False
    assert pet.installed_manifest()["version"] == manifest["version"]


@pytest.mark.parametrize("failure", ["copy", "embedded-resources"])
def test_failed_host_update_keeps_complete_previous_payload(pack, tmp_path, failure):
    destination = payload(tmp_path / "extensions/vpet")
    executable = destination / pet.PET_EXECUTABLE
    executable.write_bytes(b"old executable")
    host = pack.parent / "pet-host.zip"
    manifest = json.loads((pack.parent / pet.PACK_MANIFEST).read_text())
    if failure == "embedded-resources":
        with zipfile.ZipFile(host, "a") as archive:
            archive.writestr("resources/unexpected.png", b"unexpected")
        with pytest.raises(ValueError, match="夹带|不兼容"):
            pet.install_pack(
                host, destination, replace_existing=True, expected_manifest=manifest,
                reuse_resources_from=destination,
            )
    else:
        with (patch.object(pet.shutil, "copy2", side_effect=OSError("copy failed")),
              pytest.raises(OSError, match="copy failed")):
            pet.install_pack(
                host, destination, replace_existing=True, expected_manifest=manifest,
                reuse_resources_from=destination,
            )
    assert executable.read_bytes() == b"old executable"
    assert not list(destination.parent.glob(".vpet-*"))


@pytest.mark.parametrize("failure", ["rename", "cancel", "manifest", "downgrade"])
def test_failed_update_keeps_previous_payload(pack, tmp_path, monkeypatch, failure):
    destination = payload(tmp_path / "extensions/vpet")
    executable = destination / pet.PET_EXECUTABLE
    executable.write_bytes(b"old executable")
    old = dict(build_release.PET_MANIFEST, version="9.0.0" if failure == "downgrade" else "0.0.1")
    (destination / pet.PACK_MANIFEST).write_text(json.dumps(old))
    rename = Path.rename

    def fail_publish(path, target):
        if path.name == "payload" and Path(target) == destination:
            raise PermissionError("locked")
        return rename(path, target)

    if failure == "rename":
        monkeypatch.setattr(Path, "rename", fail_publish)
    expected = dict(build_release.PET_MANIFEST, version="0.2.0") if failure == "manifest" else None
    # 在备份已完成的事务窗口取消，验证取消同样走回滚而不是删掉旧包。
    cancel = lambda: failure == "cancel" and pet._backup_directory(destination).exists()
    with pytest.raises((ValueError, PermissionError, DownloadCancelled)):
        pet.install_pack(pack, destination, cancel, replace_existing=True, expected_manifest=expected)
    assert executable.read_bytes() == b"old executable"
    assert json.loads((destination / pet.PACK_MANIFEST).read_text()) == old
    assert not list(destination.parent.glob(".vpet-*"))


def test_startup_recovers_old_pack_after_interrupted_swap(tmp_path, monkeypatch):
    monkeypatch.setattr(pet.config_manager, "CONFIG_DIR", tmp_path)
    destination = pet.extension_directory()
    backup = payload(pet._backup_directory(destination))
    (backup / pet.PET_EXECUTABLE).write_bytes(b"previous")
    assert pet.installed_executable() == destination / pet.PET_EXECUTABLE
    assert (destination / pet.PET_EXECUTABLE).read_bytes() == b"previous"
    assert not backup.exists()


def test_legacy_installed_pack_survives_host_upgrade_and_can_update(pack, tmp_path, monkeypatch):
    monkeypatch.setattr(pet.config_manager, "CONFIG_DIR", tmp_path)
    destination = payload(pet.extension_directory())
    (destination / pet.PACK_MANIFEST).write_text(json.dumps({
        "protocol": pet.PACK_PROTOCOL, "platform": "win-x64", "app_version": "1.0.0",
    }))
    assert pet.installed_executable() is not None
    pet.install_pack(pack, destination, replace_existing=True)
    monkeypatch.setattr(pet, "APP_VERSION", "1.15.0")
    assert pet.installed_manifest()["version"] == build_release.PET_MANIFEST["version"]


def test_latest_pet_selects_highest_compatible_stable_release(monkeypatch):
    monkeypatch.setattr("updater.client.APP_VERSION", "1.14.0")
    client = GitHubReleaseClient()
    data = [release_payload("0.1.0"), release_payload("0.2.0"),
            release_payload("0.3.0"), dict(release_payload("0.4.0"), prerelease=True),
            dict(release_payload("0.5.0"), draft=True), {"tag_name": "v99.0.0"}]
    checksums = {pet.PACK_MANIFEST: "b" * 64}
    checksums.update({item["assets"][0]["name"].lower(): "a" * 64 for item in data if "assets" in item})

    def manifest(asset, *_args):
        version = asset.download_url.split("pet-v")[1].split("/")[0]
        return dict(build_release.PET_MANIFEST, version=version,
                    min_app_version="99.0.0" if version == "0.3.0" else "1.0.0")

    with (patch.object(client, "_request_json", return_value=data),
          patch.object(client, "_load_checksums", return_value=checksums),
          patch.object(client, "_load_pet_manifest", side_effect=manifest),
          patch.object(client, "_download_asset") as download):
        assert client.latest_pet_release().version == "0.2.0"
    download.assert_not_called()


@pytest.mark.parametrize("app_version,marked_prerelease,expected", [
    ("1.14.0", True, "0.1.0"),
    ("1.14.0", False, "0.1.0"),
    ("1.14.0-beta.1", True, "0.2.0-beta.1"),
    ("1.14.0-beta.1", False, "0.2.0-beta.1"),
])
def test_pet_prereleases_only_available_to_preview_app(
    monkeypatch, app_version, marked_prerelease, expected,
):
    monkeypatch.setattr("updater.client.APP_VERSION", app_version)
    client = GitHubReleaseClient()
    data = [release_payload("0.1.0"),
            dict(release_payload("0.2.0-beta.1"), prerelease=marked_prerelease),
            dict(release_payload("0.3.0-beta.1"), prerelease=True, draft=True)]
    checksums = {pet.PACK_MANIFEST: "b" * 64}
    checksums.update({item["assets"][0]["name"].lower(): "a" * 64 for item in data})

    def manifest(asset, *_args):
        version = asset.download_url.split("pet-v")[1].split("/")[0]
        return dict(build_release.PET_MANIFEST, version=version, min_app_version="1.0.0")

    with (patch.object(client, "_request_json", return_value=data),
          patch.object(client, "_load_checksums", return_value=checksums),
          patch.object(client, "_load_pet_manifest", side_effect=manifest)):
        assert client.latest_pet_release().version == expected


def test_pet_discovery_pages_past_main_releases_and_supports_cancellation():
    client = GitHubReleaseClient()
    data = release_payload()
    checksums = {pet.PACK_MANIFEST: "b" * 64, data["assets"][0]["name"].lower(): "a" * 64}
    with (patch.object(client, "_request_json", side_effect=[[{"tag_name": "v1.0.0"}] * 100, [data]]) as metadata,
          patch.object(client, "_load_checksums", return_value=checksums),
          patch.object(client, "_load_pet_manifest", return_value=build_release.PET_MANIFEST)):
        assert client.latest_pet_release().version == build_release.PET_MANIFEST["version"]
    assert metadata.call_args.args[0].endswith("page=2")
    with patch.object(client, "_request_json") as metadata:
        with pytest.raises(DownloadCancelled):
            client.latest_pet_release(cancel_requested=lambda: True)
    metadata.assert_not_called()


@pytest.mark.parametrize("problem", ["hash", "oversize", "json", "cancel"])
def test_manifest_download_is_bounded_verified_and_cancellable(problem):
    client = GitHubReleaseClient()
    content = json.dumps(build_release.PET_MANIFEST).encode()
    if problem == "oversize":
        content = b"a" * 65537
    elif problem == "json":
        content = b"invalid json"
    digest = "0" * 64 if problem == "hash" else hashlib.sha256(content).hexdigest()
    response = Mock()
    response.iter_content.return_value = [content]
    asset = ReleaseAsset(pet.PACK_MANIFEST, "https://github.com/unused", len(content))
    with patch.object(client, "_open_download_stream", return_value=(response, asset.download_url)):
        with pytest.raises(UpdateError):
            client._load_pet_manifest(asset, digest, lambda: problem == "cancel")
    response.close.assert_called_once()


def test_windows_transient_lock_is_retried_but_permanent_failure_is_bounded(tmp_path, monkeypatch):
    source, target = tmp_path / "payload", tmp_path / "vpet"
    source.mkdir()
    original = Path.rename
    attempts = []
    error = PermissionError("temporary Windows handle")
    error.winerror = 32

    def rename(path, destination):
        attempts.append(path)
        if len(attempts) < 25:
            raise error
        return original(path, destination)

    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(pet.time, "sleep", lambda _seconds: None)
    pet._rename_payload(source, target)
    assert target.is_dir() and len(attempts) == 25
    with patch.object(Path, "rename", side_effect=error) as locked:
        with pytest.raises(PermissionError):
            pet._rename_payload(target, source)
    assert locked.call_count == 100 and target.is_dir()


def test_failed_update_download_preserves_old_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(pet.config_manager, "CONFIG_DIR", tmp_path)
    destination = payload(pet.extension_directory())
    marker = destination / "keep.txt"
    marker.write_text("old")
    with patch.object(GitHubReleaseClient, "download_pet_pack", side_effect=UpdateError("offline")):
        with pytest.raises(UpdateError):
            pet.download_and_install(Mock(), lambda: False, release=release_info(), replace_existing=True)
    assert marker.read_text() == "old"
    assert not list(destination.parent.glob(".vpet-*"))


def test_settings_worker_installs_without_auto_enabling_then_removes_pack(pack, tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from ui.qt_settings import SettingsWindow
    from ui import vpet_host
    from config.defaults import DEFAULT_CONFIG

    app = QApplication.instance() or QApplication([])
    values = DEFAULT_CONFIG.copy()
    monkeypatch.setattr(pet.config_manager, "CONFIG_DIR", tmp_path / "data")
    monkeypatch.setattr(pet.config_manager, "load_config", lambda: values.copy())
    monkeypatch.setattr(pet.config_manager, "all_config", lambda: values.copy())
    monkeypatch.setattr(vpet_host.sys, "frozen", True, raising=False)
    monkeypatch.setattr(vpet_host.sys, "executable", str(tmp_path / "app/TokenMeter.exe"))

    def download(directory, **kwargs):
        result = directory / "pet.zip"
        shutil.copy2(pack, result)
        return result

    window = SettingsWindow()
    try:
        with (patch.object(GitHubReleaseClient, "download_pet_pack", side_effect=download),
              patch.object(GitHubReleaseClient, "latest_pet_release", return_value=release_info())):
            window._start_pet_task("install")
            assert not window.pet_install_button.isEnabled()
            assert not window.vpet_check.isEnabled()
            assert window._pet_worker.wait(5000)
            app.processEvents()
        assert window._pet_worker is None
        assert window.vpet_check.isEnabled()
        assert not window.vpet_check.isChecked()
        assert window.pet_uninstall_button.isEnabled()
        assert not window.pet_install_button.isEnabled()
        assert "已安装" in window.pet_status_label.text()
        window._start_pet_task("uninstall")
        assert window._pet_worker.wait(5000)
        app.processEvents()
        assert window._pet_worker is None
        assert not window.vpet_check.isEnabled()
        assert window.pet_install_button.isEnabled()
        assert not pet.extension_directory().exists()
    finally:
        window.stop_pet_task()
        window.close()


@pytest.fixture
def pet_update_ui(tmp_path, monkeypatch, qapp):
    from PySide6.QtWidgets import QWidget
    from config.defaults import DEFAULT_CONFIG
    from ui.i18n import configure_language
    from ui.qt_update import AppUpdateController

    values = DEFAULT_CONFIG.copy()
    monkeypatch.setattr(pet.config_manager, "CONFIG_DIR", tmp_path / "data")
    monkeypatch.setattr(pet.config_manager, "CONFIG_PATH", tmp_path / "data/config.json")
    monkeypatch.setattr(pet.config_manager, "_config", values)
    monkeypatch.setattr(pet.config_manager, "_initialized", True)
    monkeypatch.setattr(pet.config_manager, "load_config", lambda: values.copy())
    monkeypatch.setattr(pet.config_manager, "load_update_state", lambda: {})
    monkeypatch.setattr(pet.config_manager, "logger", lambda: logging.getLogger(__name__))
    monkeypatch.setattr("ui.qt_update.is_packaged_windows_executable", lambda: True)
    configure_language(qapp, "zh-cn")
    owner = QWidget()
    controller = AppUpdateController(owner)
    yield controller
    controller.stop_pet_check()


@pytest.mark.parametrize("reason", ["missing", "disabled", "development", "app-download", "pet-task"])
def test_pet_auto_check_skips_ineligible_states(pet_update_ui, monkeypatch, reason):
    controller = pet_update_ui
    if reason != "missing":
        payload(pet.extension_directory())
    if reason == "disabled":
        monkeypatch.setitem(pet.config_manager._config, "UPDATE_AUTO_CHECK_ENABLED", False)
    elif reason == "development":
        monkeypatch.setattr("ui.qt_update.is_packaged_windows_executable", lambda: False)
    elif reason == "app-download":
        controller._download_worker = Mock()
    elif reason == "pet-task":
        controller.pet_task_active = True
    with patch.object(GitHubReleaseClient, "latest_pet_release") as discover:
        controller.check_pet_updates()
    assert controller._pet_check_worker is None
    discover.assert_not_called()


@pytest.mark.parametrize("main_failed", [False, True])
def test_main_auto_check_also_checks_disabled_but_installed_pet(pet_update_ui, qtbot, main_failed):
    from PySide6.QtWidgets import QMessageBox
    from updater.client import CheckResult

    controller = pet_update_ui
    payload(pet.extension_directory())
    assert not pet.config_manager.get("VPET_ENABLED")
    release = release_info("0.3.0")
    requested = []
    controller.pet_update_requested.connect(requested.append)
    with (patch.object(GitHubReleaseClient, "check_for_updates",
                       return_value=CheckResult(APP_VERSION, None, False, "latest"),
                       side_effect=UpdateError("offline") if main_failed else None),
          patch.object(GitHubReleaseClient, "latest_pet_release", return_value=release) as discover,
          patch("ui.qt_update.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as prompt):
        controller.check_for_updates(manual=False)
        qtbot.waitUntil(lambda: controller._check_worker is None and controller._pet_check_worker is None)
        controller.check_pet_updates()
        controller.check_pet_updates()
        discover.assert_called_once()
        prompt.assert_called_once()
        assert "0.3.0" in prompt.call_args.args[2]
    assert controller.latest_pet_release() == release
    assert requested == []


def test_main_update_defers_pet_check_until_restarted_app(pet_update_ui, qtbot):
    from PySide6.QtWidgets import QMessageBox, QWidget
    from ui.qt_update import AppUpdateController
    from updater.client import CheckResult

    controller = pet_update_ui
    payload(pet.extension_directory())

    def begin_app_download(*_args):
        controller._download_worker = Mock()

    with (patch.object(controller, "_prompt_for_release", side_effect=begin_app_download),
          patch("ui.qt_update.skipped_version", return_value=""),
          patch.object(GitHubReleaseClient, "latest_pet_release", return_value=release_info("0.3.0")) as discover,
          patch("ui.qt_update.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as prompt):
        controller._finish_check(CheckResult(APP_VERSION, Mock(version="9.0.0"), True, "new"), None,
                                 manual=False, parent=None)
        discover.assert_not_called()
        prompt.assert_not_called()
        next_owner = QWidget()
        next_controller = AppUpdateController(next_owner)
        next_controller._finish_check(CheckResult(APP_VERSION, None, False, "latest"), None,
                                      manual=False, parent=None)
        qtbot.waitUntil(lambda: next_controller._pet_check_worker is None)
    discover.assert_called_once()
    prompt.assert_called_once()


@pytest.mark.parametrize("manifest", [None, {"version": "0.3.0"}, {"version": "0.4.0"}])
def test_pet_auto_prompt_rechecks_installation_after_network_check(pet_update_ui, monkeypatch, manifest):
    controller = pet_update_ui
    controller._latest_pet_release = release_info("0.3.0")
    if manifest is not None:
        payload(pet.extension_directory())
    monkeypatch.setattr(pet, "installed_manifest", lambda: manifest)
    with patch("ui.qt_update.QMessageBox.question") as prompt:
        controller._prompt_for_pet_release()
    prompt.assert_not_called()


def test_pet_auto_prompt_repeats_after_restart_and_supports_legacy_install(pet_update_ui, monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QWidget
    from ui.qt_update import AppUpdateController

    payload(pet.extension_directory())
    monkeypatch.setattr(pet, "installed_manifest", lambda: {"app_version": "1.14.0-beta.1"})
    controller = pet_update_ui
    controller._latest_pet_release = release_info("0.3.0")
    next_owner = QWidget()
    next_controller = AppUpdateController(next_owner)
    next_controller._latest_pet_release = controller.latest_pet_release()
    with patch("ui.qt_update.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as prompt:
        controller._prompt_for_pet_release()
        controller._prompt_for_pet_release()
        assert prompt.call_count == 1
        next_controller._prompt_for_pet_release()
        assert prompt.call_count == 2
        controller._prompt_for_pet_release(manual=True)
        assert prompt.call_count == 3


def test_failed_pet_auto_check_is_quiet_and_manual_check_can_retry(pet_update_ui, qtbot):
    from PySide6.QtWidgets import QMessageBox

    controller = pet_update_ui
    payload(pet.extension_directory())
    with (patch.object(GitHubReleaseClient, "latest_pet_release", side_effect=UpdateError("offline")),
          patch("ui.qt_update.QMessageBox.warning") as warning):
        controller.check_pet_updates()
        qtbot.waitUntil(lambda: controller._pet_check_worker is None)
    warning.assert_not_called()
    with (patch.object(GitHubReleaseClient, "latest_pet_release", return_value=release_info("0.3.0")),
          patch("ui.qt_update.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as prompt):
        controller.check_pet_updates(manual=True)
        qtbot.waitUntil(lambda: controller._pet_check_worker is None)
    prompt.assert_called_once()


@pytest.mark.parametrize("state", ["download", "prompt", "pet-task"])
def test_pet_prompt_waits_for_active_update_flow(pet_update_ui, monkeypatch, state):
    from PySide6.QtWidgets import QMessageBox

    controller = pet_update_ui
    payload(pet.extension_directory())
    controller._latest_pet_release = release_info("0.3.0")
    controller._pet_checked_in_session = True
    field = {"download": "_download_worker", "prompt": "_prompt_active", "pet-task": "pet_task_active"}[state]
    with patch("ui.qt_update.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as prompt:
        monkeypatch.setattr(controller, field, Mock() if state == "download" else True)
        controller.check_pet_updates()
        prompt.assert_not_called()
        monkeypatch.setattr(controller, field, None if state == "download" else False)
        controller.check_pet_updates()
        prompt.assert_called_once()


def test_main_download_cancellation_resumes_pet_prompt(pet_update_ui):
    from PySide6.QtWidgets import QMessageBox

    controller = pet_update_ui
    payload(pet.extension_directory())
    controller._latest_pet_release = release_info("0.3.0")
    controller._pet_checked_in_session = True
    controller._download_worker = Mock()
    with patch("ui.qt_update.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as prompt:
        controller._finish_download(None, DownloadCancelled("cancelled"), controller.parent())
    prompt.assert_called_once()


def test_pet_auto_check_shutdown_cancels_worker_and_suppresses_prompt(pet_update_ui, qtbot):
    from threading import Event

    controller = pet_update_ui
    payload(pet.extension_directory())
    started = Event()

    def discover(*, cancel_requested):
        started.set()
        assert controller._pet_check_worker.isRunning()
        while not cancel_requested():
            Event().wait(0.005)
        raise DownloadCancelled("cancelled")

    with (patch.object(GitHubReleaseClient, "latest_pet_release", side_effect=discover),
          patch("ui.qt_update.QMessageBox.question") as prompt,
          patch("ui.qt_update.QMessageBox.warning") as warning):
        controller.check_pet_updates()
        qtbot.waitUntil(started.is_set)
        worker = controller._pet_check_worker
        controller.stop_pet_check()
        assert not worker.isRunning()
        qtbot.waitUntil(lambda: controller._pet_check_worker is None)
    prompt.assert_not_called()
    warning.assert_not_called()


def test_confirmed_auto_pet_update_reuses_settings_install_and_preserves_preferences(
    pet_update_ui, pack, qtbot,
):
    from PySide6.QtWidgets import QMessageBox
    from ui.qt_settings import SettingsWindow

    controller = pet_update_ui
    destination = payload(pet.extension_directory())
    manifest = dict(build_release.PET_MANIFEST, version="0.0.1")
    (destination / pet.PACK_MANIFEST).write_text(json.dumps(manifest))
    preferences = pet.config_manager.CONFIG_DIR / "vpet/layout.json"
    preferences.parent.mkdir()
    preferences.write_text('{"cloudMode":"hover"}')
    original_config = pet.config_manager.all_config()
    window = SettingsWindow(update_controller=controller)
    controller.pet_update_requested.connect(window.start_pet_update)
    events = []
    window.pet_update_started.connect(lambda: events.append("pause"))
    window.pet_update_finished.connect(lambda: events.append("resume"))

    def download(directory, **_kwargs):
        result = directory / "pet.zip"
        shutil.copy2(pack, result)
        return result

    try:
        with (patch.object(GitHubReleaseClient, "latest_pet_release", return_value=release_info()),
              patch.object(GitHubReleaseClient, "download_pet_pack", side_effect=download),
              patch("ui.qt_update.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes) as prompt):
            controller.check_pet_updates()
            qtbot.waitUntil(lambda: events == ["pause", "resume"])
        prompt.assert_called_once()
        assert window.tabs.currentIndex() == window._pet_tab_index
        assert pet.installed_manifest()["version"] == build_release.PET_MANIFEST["version"]
        assert preferences.read_text() == '{"cloudMode":"hover"}'
        assert pet.config_manager.all_config() == original_config
        assert not controller.pet_task_active
        assert "已更新" in window.pet_status_label.text()
    finally:
        window.stop_pet_task()


def test_app_upgrade_can_offer_update_for_now_incompatible_installed_pet(pet_update_ui, qtbot):
    from PySide6.QtWidgets import QMessageBox
    from ui.qt_settings import SettingsWindow

    controller = pet_update_ui
    destination = payload(pet.extension_directory())
    manifest = dict(build_release.PET_MANIFEST, max_app_version=APP_VERSION)
    (destination / pet.PACK_MANIFEST).write_text(json.dumps(manifest))
    assert pet.installed_manifest() is None
    window = SettingsWindow(update_controller=controller)
    controller.pet_update_requested.connect(window.start_pet_update)
    try:
        with (patch.object(GitHubReleaseClient, "latest_pet_release", return_value=release_info("0.3.0")),
              patch("ui.qt_update.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes),
              patch.object(window, "_start_pet_task") as start):
            controller.check_pet_updates()
            qtbot.waitUntil(lambda: controller._pet_check_worker is None)
        start.assert_called_once_with("update")
        assert window._pet_release.version == "0.3.0"
    finally:
        window.stop_pet_task()


def test_pet_and_main_update_cannot_replace_files_concurrently(pet_update_ui):
    from ui.qt_settings import SettingsWindow

    controller = pet_update_ui
    payload(pet.extension_directory())
    window = SettingsWindow(update_controller=controller)
    try:
        controller._download_worker = Mock()
        with patch("ui.qt_update.QMessageBox.information") as busy:
            window.start_pet_update(release_info("0.3.0"))
        assert window._pet_worker is None
        busy.assert_called_once()

        controller._download_worker = None
        controller.pet_task_active = True
        with (patch("ui.qt_update.QMessageBox.information") as busy,
              patch("ui.qt_update.UpdateDownloadWorker") as download):
            controller.download_release(Mock())
        busy.assert_called_once()
        download.assert_not_called()
    finally:
        controller.pet_task_active = False
        window.stop_pet_task()
