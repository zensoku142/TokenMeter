import hashlib
from unittest.mock import patch

import pytest

from updater.client import (
    GitHubReleaseClient,
    ReleaseAsset,
    ReleaseInfo,
    SemVer,
    UpdateError,
    _release_from_payload,
)


def release(version="1.16.7", sha256="b" * 64):
    name = f"TokenMeter-Setup-v{version}-x64.exe"
    base = f"https://github.com/zensoku142/TokenMeter/releases/download/v{version}/"
    return ReleaseInfo(
        version, SemVer.parse(version), f"v{version}", "2026-10-30T05:54:46Z", "Fixes", False,
        ReleaseAsset(name, base + name, 12, sha256=sha256),
        ReleaseAsset("SHA256SUMS.txt", base + "SHA256SUMS.txt", 100),
    )


@pytest.fixture
def client(monkeypatch):
    from config import runtime as config_manager

    state = {}
    monkeypatch.setattr(config_manager, "load_update_state", lambda: state.copy())
    monkeypatch.setattr(config_manager, "save_update_state", state.update)
    instance = GitHubReleaseClient()
    yield instance, state
    instance._session.close()


def test_release_reads_only_valid_github_sha256_digest():
    source = release()
    payload = {
        "tag_name": source.tag_name,
        "assets": [
            {"name": source.setup_asset.name, "browser_download_url": source.setup_asset.download_url,
             "size": 12, "digest": "sha256:" + "AB" * 32, "updated_at": "2026-10-30T06:00:00Z"},
            {"name": source.checksum_asset.name, "browser_download_url": source.checksum_asset.download_url,
             "size": 100},
        ],
    }
    assert _release_from_payload(payload).setup_asset.sha256 == "ab" * 32
    assert _release_from_payload(payload).setup_asset.updated_at == "2026-10-30T06:00:00Z"
    for invalid in (None, "sha256:bad", "sha512:" + "a" * 64):
        payload["assets"][0]["digest"] = invalid
        assert _release_from_payload(payload).setup_asset.sha256 == ""


def test_installed_receipt_matches_version_and_current_executable(tmp_path):
    from updater.client import INSTALLED_RECEIPT_NAME, installed_setup_sha256

    executable = tmp_path / "TokenMeter.exe"
    executable.write_bytes(b"installed application")
    exe_sha = hashlib.sha256(executable.read_bytes()).hexdigest()
    receipt = tmp_path / INSTALLED_RECEIPT_NAME
    receipt.write_text(f"1.16.7\n{'a' * 64}\n{exe_sha}\n", encoding="utf-8")

    assert installed_setup_sha256("1.16.7", executable=executable) == "a" * 64
    assert installed_setup_sha256("1.16.6", executable=executable) == ""
    executable.write_bytes(b"older application")
    assert installed_setup_sha256("1.16.7", executable=executable) == ""
    receipt.write_text("invalid\n", encoding="utf-8")
    assert installed_setup_sha256("1.16.7", executable=executable) == ""
    receipt.unlink()
    assert installed_setup_sha256("1.16.7", executable=executable) == ""


@pytest.mark.parametrize(
    "current,remote,installed,remote_sha,available,hotfix",
    [
        ("1.16.7", "1.16.7", "a" * 64, "b" * 64, True, True),
        ("1.16.7", "1.16.7", "a" * 64, "a" * 64, False, False),
        ("1.16.7", "1.16.7", "", "b" * 64, False, False),
        ("1.16.7", "1.16.8", "a" * 64, "b" * 64, True, False),
        ("1.16.8", "1.16.7", "a" * 64, "b" * 64, False, False),
    ],
)
def test_hotfix_detection_preserves_normal_version_order(
    client, monkeypatch, current, remote, installed, remote_sha, available, hotfix,
):
    instance, _state = client
    remote_release = release(remote, remote_sha)
    monkeypatch.setattr("updater.client.installed_setup_sha256", lambda _version: installed, raising=False)
    with (patch.object(instance, "_load_latest_stable", return_value=remote_release),
          patch.object(instance, "_with_update_notes", return_value=remote_release)):
        result = instance.check_for_updates(current, "stable", use_cache=False)
    assert result.update_available is available
    assert result.latest_release.is_hotfix is hotfix


def test_hotfix_cache_is_invalidated_after_install(client, monkeypatch):
    instance, _state = client
    current_sha = ["a" * 64]
    monkeypatch.setattr("updater.client.installed_setup_sha256", lambda _version: current_sha[0], raising=False)
    with patch.object(instance, "_load_latest_stable", return_value=release()) as fetch:
        before = instance.check_for_updates("1.16.7", "stable", use_cache=False)
        assert before.update_available and before.latest_release.is_hotfix
        cached = instance.check_for_updates("1.16.7", "stable", use_cache=True)
        assert cached.cached and cached.update_available and cached.latest_release.is_hotfix
        assert fetch.call_count == 1
        current_sha[0] = "b" * 64
        after = instance.check_for_updates("1.16.7", "stable", use_cache=True)
        assert not after.update_available
        assert not after.latest_release.is_hotfix
        assert fetch.call_count == 2
        assert instance.check_for_updates("1.16.7", "stable", use_cache=True).cached
        assert fetch.call_count == 2


def test_legacy_install_without_receipt_keeps_version_only_detection(client, monkeypatch):
    instance, _state = client
    monkeypatch.setattr("updater.client.installed_setup_sha256", lambda _version: "")
    with (patch.object(instance, "_load_latest_stable", return_value=release(sha256="")),
          patch.object(instance, "_load_checksums") as checksums):
        result = instance.check_for_updates("1.16.7", "stable", use_cache=False)
    assert not result.update_available
    checksums.assert_not_called()


def test_missing_github_digest_uses_verified_release_checksums(client, monkeypatch):
    instance, _state = client
    remote_release = release(sha256="")
    monkeypatch.setattr("updater.client.installed_setup_sha256", lambda _version: "a" * 64, raising=False)
    with (patch.object(instance, "_load_latest_stable", return_value=remote_release),
          patch.object(instance, "_load_checksums", return_value={remote_release.setup_asset.name.lower(): "b" * 64})):
        result = instance.check_for_updates("1.16.7", "stable", use_cache=False)
    assert result.update_available and result.latest_release.is_hotfix
    assert result.latest_release.setup_asset.sha256 == "b" * 64


def test_missing_digest_and_checksum_does_not_claim_the_installation_is_current(client, monkeypatch):
    instance, _state = client
    monkeypatch.setattr("updater.client.installed_setup_sha256", lambda _version: "a" * 64)
    with (patch.object(instance, "_load_latest_stable", return_value=release(sha256="")),
          patch.object(instance, "_load_checksums", return_value={})):
        with pytest.raises(UpdateError, match="缺少安装包的校验值"):
            instance.check_for_updates("1.16.7", "stable", use_cache=False)


def test_skipping_a_release_records_its_installer_identity(monkeypatch):
    from config import runtime as config_manager
    from updater.client import mark_skipped_version

    saved = []
    monkeypatch.setattr(config_manager, "save_config", saved.append)
    mark_skipped_version("v1.16.7", "A" * 64)
    assert saved[-1] == {"UPDATE_SKIPPED_VERSION": "1.16.7", "UPDATE_SKIPPED_ASSET_SHA256": "a" * 64}
    mark_skipped_version("v1.16.7")
    assert saved[-1]["UPDATE_SKIPPED_ASSET_SHA256"] == ""


def test_download_rejects_a_changed_release_until_rechecked(client, monkeypatch, tmp_path):
    from config import runtime as config_manager

    instance, _state = client
    remote_release = release(sha256="a" * 64)
    monkeypatch.setattr(config_manager, "updates_dir", lambda: tmp_path)
    with (patch.object(instance, "_load_checksums", return_value={remote_release.setup_asset.name.lower(): "b" * 64}),
          patch.object(instance, "_download_asset") as download):
        with pytest.raises(UpdateError, match="重新检查更新"):
            instance.download_bundle(remote_release)
    download.assert_not_called()
