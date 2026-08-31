import os
import hashlib
from dataclasses import replace
from pathlib import Path

os.environ["APPDATA"] = str(Path.cwd() / ".test-appdata")

import pytest
from unittest.mock import Mock, patch

from updater.client import (
    DownloadBundle,
    DownloadedAsset,
    GITHUB_LATEST_RELEASE_API_URL,
    GitHubReleaseClient,
    UpdateError,
    _release_from_payload,
    _is_allowed_download_url,
    cleanup_pending_update,
    compare_versions,
    format_bytes,
    is_safe_cleanup_path,
    normalize_version,
    launch_installer,
    stable_target_path,
)


def test_semver_comparison_supports_prefix_and_prerelease():
    assert normalize_version("v1.1.9") == "1.1.9"
    assert normalize_version("1.2.0-beta.1") == "1.2.0-beta.1"
    assert compare_versions("1.2.0", "1.2.0") == 0
    assert compare_versions("1.1.9", "1.2.0") < 0
    assert compare_versions("1.2.0-beta.1", "1.2.0") < 0
    assert compare_versions("1.2.0", "1.2.0-beta.1") > 0


def _setup_release(version="1.3.0"):
    setup_name = f"TokenMeter-Setup-v{version}-x64.exe"
    return _release_from_payload(
        {
            "tag_name": f"v{version}",
            "published_at": "2026-07-06T07:00:00Z",
            "body": "Bug fixes",
            "prerelease": False,
            "assets": [
                {
                    "name": setup_name,
                    "browser_download_url": f"https://github.com/zensoku142/TokenMeter/releases/download/v{version}/{setup_name}",
                    "size": 12,
                },
                {
                    "name": "SHA256SUMS.txt",
                    "browser_download_url": f"https://github.com/zensoku142/TokenMeter/releases/download/v{version}/SHA256SUMS.txt",
                    "size": 2,
                },
            ],
        }
    )


def test_release_asset_selection_requires_setup_installer():
    release = _setup_release()

    assert release.version == "1.3.0"
    assert release.setup_asset.name == "TokenMeter-Setup-v1.3.0-x64.exe"
    assert release.checksum_asset.name == "SHA256SUMS.txt"


def test_release_without_setup_installer_is_rejected():
    with pytest.raises(UpdateError, match="安装包"):
        _release_from_payload(
            {
                "tag_name": "v1.3.0",
                "assets": [
                    {
                        "name": "TokenMeter-v1.3.0-windows-x64.exe",
                        "browser_download_url": "https://github.com/zensoku142/TokenMeter/releases/download/v1.3.0/TokenMeter-v1.3.0-windows-x64.exe",
                        "size": 12,
                    },
                    {
                        "name": "SHA256SUMS.txt",
                        "browser_download_url": "https://github.com/zensoku142/TokenMeter/releases/download/v1.3.0/SHA256SUMS.txt",
                        "size": 2,
                    },
                ],
            }
        )


def test_pet_release_never_becomes_a_main_program_update():
    from updater.client import _release_from_payload

    with pytest.raises(UpdateError, match="桌宠"):
        _release_from_payload({"tag_name": "pet-v0.1.0", "name": "99.0.0", "assets": []})


def test_main_update_survives_pet_releases_marked_latest_and_full_first_page():
    from updater.client import GitHubReleaseClient

    release = _setup_release("2.0.0")
    stable = {"tag_name": "v2.0.0", "assets": [
        {"name": asset.name, "browser_download_url": asset.download_url, "size": asset.size}
        for asset in (release.setup_asset, release.checksum_asset)
    ]}
    pet = {"tag_name": "pet-v0.1.0", "name": "99.0.0", "assets": []}
    client = GitHubReleaseClient()
    with patch.object(client, "_request_json", side_effect=[pet, [pet] * 20, [stable]]) as metadata:
        assert client._load_latest_stable().version == "2.0.0"
    assert metadata.call_args.args[0].endswith("page=2")


def test_main_stable_fallback_excludes_prerelease_versions():
    client = GitHubReleaseClient()
    with patch.object(client, "_request_json", return_value=[
        {"tag_name": "v99.0.0", "prerelease": True, "assets": []}
    ]):
        with pytest.raises(UpdateError):
            client._load_latest_from_list(stable_only=True)


def _notes_payload(version, body="History", **extra):
    # 历史附件可以被移除；汇总仍应展示其正文，但不能把它选作下载目标。
    return {"tag_name": f"v{version}", "body": body, "assets": [], **extra}


@pytest.fixture
def update_client(monkeypatch):
    from config import runtime as config_manager

    state = {}
    monkeypatch.setattr(config_manager, "load_update_state", lambda: state.copy())
    monkeypatch.setattr(config_manager, "save_update_state", state.update)
    client = GitHubReleaseClient()
    yield client, state
    client._session.close()


@pytest.mark.parametrize("channel", ["stable", "prerelease"])
def test_update_notes_cover_upgrade_range_and_preserve_download_target(update_client, channel):
    client, state = update_client
    latest = replace(_setup_release("1.14.1"), body="Latest pet update reminder")
    history = [
        _notes_payload("1.14.0", "Pet, account isolation and HTTPS"),
        _notes_payload("1.13.2", "Already installed"),
        _notes_payload("1.15.0", "Future release"),
        _notes_payload("1.14.1", "Different latest snapshot"),
        _notes_payload("1.14.0", "Duplicate version"),
        _notes_payload("1.14.0-beta.1", "Preview features"),
        _notes_payload("1.13.9", "Marked preview", prerelease=True),
        _notes_payload("1.13.8", "Draft release", draft=True),
        {"tag_name": "pet-v1.14.0", "name": "1.14.0", "body": "Pet-only release"},
        {"tag_name": "invalid", "body": "Invalid version"},
        None,
    ]
    with (patch.object(client, "_load_latest_stable", return_value=latest),
          patch.object(client, "_load_latest_from_list", return_value=latest),
          patch.object(client, "_request_json", return_value=history)):
        result = client.check_for_updates("1.13.2", channel, use_cache=False)
    release = result.latest_release
    assert result.update_available
    assert release.version == latest.version and release.tag_name == latest.tag_name
    assert release.setup_asset == latest.setup_asset and release.checksum_asset == latest.checksum_asset
    assert release.body == latest.body
    assert release.update_notes.startswith("## v1.14.1\n\nLatest pet update reminder")
    assert "## v1.14.0\n\nPet, account isolation and HTTPS" in release.update_notes
    assert release.update_notes.index("## v1.14.1") < release.update_notes.index("## v1.14.0\n")
    for text in ("Already installed", "Future release", "Different latest snapshot", "Duplicate version",
                 "Draft release", "Pet-only release", "Invalid version"):
        assert text not in release.update_notes
    assert ("Preview features" in release.update_notes) == (channel == "prerelease")
    assert ("Marked preview" in release.update_notes) == (channel == "prerelease")
    assert not release.notes_incomplete
    assert state["notes_current_version"] == "1.13.2"


def test_history_pagination_does_not_stop_at_old_or_pet_releases(update_client):
    client, _state = update_client
    latest = _setup_release("1.14.1")
    old = _notes_payload("1.0.0")
    pet = {"tag_name": "pet-v0.1.0"}
    with patch.object(client, "_request_json", side_effect=[
        [old, pet] * 50, [_notes_payload("1.14.0", "Late-published version")],
    ]) as metadata:
        release = client._with_update_notes("1.13.2", latest, "stable")
    assert "Late-published version" in release.update_notes
    assert metadata.call_count == 2
    assert metadata.call_args.args[0].endswith("page=2")
    assert not release.notes_incomplete


@pytest.mark.parametrize("failure", [UpdateError("offline"), {"invalid": "response"}])
def test_history_failure_preserves_available_notes_and_update(update_client, failure):
    client, _state = update_client
    latest = _setup_release("1.14.1")
    first_page = [_notes_payload("1.14.0", "Fetched history")] * 100
    with (patch.object(client, "_load_latest_stable", return_value=latest),
          patch.object(client, "_request_json", side_effect=[first_page, failure])):
        result = client.check_for_updates("1.13.2", "stable", use_cache=False)
    assert result.update_available
    assert result.latest_release.setup_asset == latest.setup_asset
    assert result.latest_release.body == latest.body
    assert "Fetched history" in result.latest_release.update_notes
    assert result.latest_release.notes_incomplete


def test_first_history_request_failure_falls_back_to_latest_notes(update_client):
    client, _state = update_client
    latest = _setup_release("1.14.1")
    with patch.object(client, "_request_json", side_effect=UpdateError("rate limited")):
        release = client._with_update_notes("1.13.2", latest, "stable")
    assert release.update_notes == "## v1.14.1\n\nBug fixes"
    assert release.notes_incomplete


def test_history_requests_and_total_notes_size_are_bounded(update_client, monkeypatch):
    import updater.client as module

    client, _state = update_client
    latest = _setup_release("1.14.1")
    monkeypatch.setattr(module, "MAX_RELEASE_NOTES_PAGES", 2)
    with patch.object(client, "_request_json", return_value=[_notes_payload("1.0.0")] * 100) as metadata:
        release = client._with_update_notes("1.13.2", latest, "stable")
    assert metadata.call_count == 2 and release.notes_incomplete
    monkeypatch.setattr(module, "MAX_METADATA_BYTES", 30)
    with patch.object(client, "_request_json", return_value=[_notes_payload("1.14.0", "x" * 31)]):
        release = client._with_update_notes("1.13.2", latest, "stable")
    assert release.notes_incomplete
    assert "x" * 31 not in release.update_notes
    assert latest.body in release.update_notes


def test_missing_history_body_remains_visible_as_an_incomplete_version(update_client):
    client, _state = update_client
    with patch.object(client, "_request_json", return_value=[_notes_payload("1.14.0", None)]):
        release = client._with_update_notes("1.13.2", _setup_release("1.14.1"), "stable")
    assert "## v1.14.0" in release.update_notes
    assert release.notes_incomplete


def test_update_notes_cache_is_scoped_to_installed_version_and_channel(update_client):
    client, state = update_client
    latest = _setup_release("1.14.1")
    with (patch.object(client, "_load_latest_stable", return_value=latest) as fetch_latest,
          patch.object(client, "_request_json", return_value=[_notes_payload("1.14.0")]) as history):
        first = client.check_for_updates("1.13.2", "stable", use_cache=False)
        cached = client.check_for_updates("v1.13.2", "stable", use_cache=True)
        assert cached.cached and cached.latest_release == first.latest_release
        assert fetch_latest.call_count == history.call_count == 1
        upgraded = client.check_for_updates("1.14.0", "stable", use_cache=True)
        assert not upgraded.cached
        assert "## v1.14.0" not in upgraded.latest_release.update_notes
        assert fetch_latest.call_count == history.call_count == 2
        # 没有范围标记的旧客户端缓存也必须重建，不能继续只展示最新版本正文。
        state.pop("notes_current_version")
        assert not client.check_for_updates("1.14.0", "stable", use_cache=True).cached
        assert fetch_latest.call_count == history.call_count == 3
        with patch.object(client, "_load_latest_from_list", return_value=latest) as preview:
            assert not client.check_for_updates("1.14.0", "prerelease", use_cache=True).cached
        preview.assert_called_once()


@pytest.mark.parametrize("current", ["1.14.1", "1.15.0"])
def test_no_update_does_not_fetch_release_history(update_client, current):
    client, _state = update_client
    with (patch.object(client, "_load_latest_stable", return_value=_setup_release("1.14.1")),
          patch.object(client, "_request_json") as history):
        result = client.check_for_updates(current, "stable", use_cache=False)
    assert not result.update_available
    assert result.latest_release.update_notes == ""
    history.assert_not_called()


def test_download_bundle_downloads_only_verified_setup_to_update_cache(tmp_path):
    release = _setup_release()
    client = GitHubReleaseClient()
    digest = "a" * 64
    setup_path = tmp_path / "updates" / "v1.3.0" / release.setup_asset.name

    def fake_download(asset, final_path, **kwargs):
        assert asset == release.setup_asset
        assert kwargs["expected_sha"] == digest
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_bytes(b"setup")
        return digest

    with (
        patch("updater.client.config_manager.updates_dir", return_value=tmp_path / "updates"),
        patch.object(client, "_load_checksums", return_value={release.setup_asset.name.lower(): digest}),
        patch.object(client, "_download_asset", side_effect=fake_download) as download,
    ):
        bundle = client.download_bundle(release)

    download.assert_called_once()
    assert bundle.setup_asset.path == setup_path
    assert bundle.setup_asset.sha256 == digest


def test_download_bundle_rejects_checksum_manifest_without_setup(tmp_path):
    release = _setup_release()
    client = GitHubReleaseClient()
    with (
        patch("updater.client.config_manager.updates_dir", return_value=tmp_path / "updates"),
        patch.object(client, "_load_checksums", return_value={"other.exe": "a" * 64}),
        patch.object(client, "_download_asset") as download,
    ):
        with pytest.raises(UpdateError, match="校验值"):
            client.download_bundle(release)
    download.assert_not_called()


def test_launch_installer_uses_silent_update_parameters_and_original_install_dir(tmp_path):
    release = _setup_release()
    setup_path = tmp_path / "data" / "updates" / "v1.3.0" / release.setup_asset.name
    setup_path.parent.mkdir(parents=True)
    setup_path.write_bytes(b"setup")
    current_exe = tmp_path / "Custom Install 目录" / "TokenMeter.exe"
    bundle = DownloadBundle(
        release=release,
        setup_asset=DownloadedAsset(release.setup_asset, setup_path, hashlib.sha256(b"setup").hexdigest()),
        cache_dir=setup_path.parent,
    )

    with (
        patch("updater.client.sys.executable", str(current_exe)),
        patch("updater.client.config_manager.updates_dir", return_value=tmp_path / "data" / "updates"),
        patch(
            "data.history.backup_usage_database",
            return_value=tmp_path / "data" / "backups" / "usage.db",
        ) as backup_usage,
        patch("updater.client.config_manager.save_pending_update_cleanup") as save_cleanup,
        patch("updater.client.subprocess.Popen") as popen,
    ):
        launch_installer(bundle)

    command = popen.call_args.args[0]
    assert command == [
        str(setup_path),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        f"/DIR={current_exe.parent}",
        "/TOKENMETERUPDATE",
    ]
    backup_usage.assert_called_once_with("1.3.0")
    save_cleanup.assert_called_once()


def test_installer_launch_failure_keeps_current_program_and_clears_cleanup_state(tmp_path):
    release = _setup_release()
    setup_path = tmp_path / "data" / "updates" / "v1.3.0" / release.setup_asset.name
    setup_path.parent.mkdir(parents=True)
    setup_path.write_bytes(b"setup")
    current_exe = tmp_path / "TokenMeter" / "TokenMeter.exe"
    current_exe.parent.mkdir()
    current_exe.write_bytes(b"current-version")
    bundle = DownloadBundle(
        release=release,
        setup_asset=DownloadedAsset(release.setup_asset, setup_path, "a" * 64),
        cache_dir=setup_path.parent,
    )
    clear = Mock()

    with (
        patch("updater.client.sys.executable", str(current_exe)),
        patch("updater.client.config_manager.updates_dir", return_value=tmp_path / "data" / "updates"),
        patch("data.history.backup_usage_database", return_value=None),
        patch("updater.client.config_manager.save_pending_update_cleanup"),
        patch("updater.client.config_manager.clear_pending_update_cleanup", clear),
        patch("updater.client.subprocess.Popen", side_effect=OSError("blocked")),
    ):
        with pytest.raises(UpdateError, match="安装包"):
            launch_installer(bundle)

    assert current_exe.read_bytes() == b"current-version"
    clear.assert_called_once()


def test_pre_update_backup_failure_blocks_installer_launch(tmp_path):
    release = _setup_release()
    setup_path = tmp_path / "data" / "updates" / "v1.3.0" / release.setup_asset.name
    setup_path.parent.mkdir(parents=True)
    setup_path.write_bytes(b"setup")
    current_exe = tmp_path / "TokenMeter" / "TokenMeter.exe"
    bundle = DownloadBundle(
        release=release,
        setup_asset=DownloadedAsset(release.setup_asset, setup_path, "a" * 64),
        cache_dir=setup_path.parent,
    )

    with (
        patch("updater.client.sys.executable", str(current_exe)),
        patch("updater.client.config_manager.updates_dir", return_value=tmp_path / "data" / "updates"),
        patch("data.history.backup_usage_database", side_effect=OSError("disk full")),
        patch("updater.client.config_manager.save_pending_update_cleanup") as save_cleanup,
        patch("updater.client.subprocess.Popen") as popen,
    ):
        with pytest.raises(UpdateError, match="usage.db"):
            launch_installer(bundle)

    save_cleanup.assert_not_called()
    popen.assert_not_called()


def test_release_asset_selection_prefers_tokenmeter_and_requires_updater_removed():
    """The old two-EXE protocol must not silently return through future refactors."""
    release = _setup_release()
    assert not hasattr(release, "app_asset")
    assert not hasattr(release, "updater_asset")


@pytest.mark.parametrize("name", ["TokenMeter.exe", "TokenSpider.exe", "TokenScope.exe"])
def test_stable_target_path_preserves_existing_stable_shortcut_target(tmp_path, name):
    current = tmp_path / name
    assert stable_target_path(current) == current.resolve()


def test_stable_target_path_migrates_versioned_download_to_tokenmeter(tmp_path):
    current = tmp_path / "TokenSpider-v1.9.1-windows-x64.exe"
    assert stable_target_path(current) == (tmp_path / "TokenMeter.exe").resolve()


def test_update_urls_only_allow_new_repository_release_paths():
    assert GITHUB_LATEST_RELEASE_API_URL == (
        "https://api.github.com/repos/zensoku142/TokenMeter/releases/latest"
    )
    assert _is_allowed_download_url(
        "https://github.com/zensoku142/TokenMeter/releases/download/v2.0.0/TokenMeter.exe",
        require_release_path=True,
    )
    assert not _is_allowed_download_url(
        "https://github.com/zensoku142/TokenSpider/releases/download/v1.9.1/TokenSpider.exe",
        require_release_path=True,
    )


def test_format_bytes_uses_human_readable_units():
    assert format_bytes(0) == "未知"
    assert format_bytes(512) == "512 B"
    assert format_bytes(1024 * 1024) == "1.0 MB"


def _run_cleanup(tmp_path, cleanup_paths):
    updates = tmp_path / "updates"
    updates.mkdir(exist_ok=True)
    clear = Mock()
    with (
        patch("updater.client.config_manager.load_pending_update_cleanup", return_value={
            "version": 1,
            "cleanup_paths": cleanup_paths,
        }),
        patch("updater.client.config_manager.updates_dir", return_value=updates),
        patch("updater.client.config_manager.clear_pending_update_cleanup", clear),
        patch("updater.client.stable_target_path", return_value=tmp_path / "TokenSpider.exe"),
    ):
        cleanup_pending_update()
    clear.assert_called_once()
    return updates


def test_update_cleanup_removes_only_relative_cache_descendants(tmp_path):
    updates = tmp_path / "updates"
    child_dir = updates / "v2" / "nested"
    child_dir.mkdir(parents=True)
    child_file = updates / "old.exe"
    child_file.write_text("cache", encoding="utf-8")

    _run_cleanup(tmp_path, ["v2", "old.exe", "missing.exe"])

    assert not (updates / "v2").exists()
    assert not child_file.exists()


@pytest.mark.parametrize("unsafe", ["..\\outside.txt", ".", "\\", "C:\\"])
def test_update_cleanup_rejects_traversal_roots_and_absolute_paths(tmp_path, unsafe):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")

    _run_cleanup(tmp_path, [unsafe, str(outside), str(Path.home())])

    assert outside.read_text(encoding="utf-8") == "keep"


def test_update_cleanup_rejects_symlink_resolving_to_outside(tmp_path):
    updates = tmp_path / "updates"
    updates.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = updates / "linked"
    original_resolve = Path.resolve
    resolved_link = original_resolve(link, strict=False)
    resolved_outside = original_resolve(outside, strict=False)

    def resolve_symlink(path, strict=False):
        resolved = original_resolve(path, strict=strict)
        return resolved_outside if resolved == resolved_link else resolved

    # Mocking resolve models both symbolic links and Windows directory junctions
    # without requiring elevated link-creation privileges in the test runner.
    with patch.object(Path, "resolve", new=resolve_symlink):
        assert not is_safe_cleanup_path(Path("linked"), updates)


def test_update_cleanup_ignores_and_clears_damaged_manifest(tmp_path):
    manifest = tmp_path / "pending-update-cleanup.json"
    manifest.write_text("{broken", encoding="utf-8")
    clear = Mock()
    with (
        patch("updater.client.config_manager.load_pending_update_cleanup", return_value={}),
        patch("updater.client.config_manager.PENDING_UPDATE_CLEANUP_PATH", manifest),
        patch("updater.client.config_manager.clear_pending_update_cleanup", clear),
    ):
        cleanup_pending_update()
    clear.assert_called_once()
