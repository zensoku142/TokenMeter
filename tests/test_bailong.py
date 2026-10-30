import json
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from config.defaults import DEFAULT_CONFIG
from core import pet_extension as pet
from core.identity import PET_CHARACTER_ASSET_TEMPLATE
from scripts import build_release
from ui.vpet_host import VPetHost
from updater.client import GitHubReleaseClient, PetReleaseInfo, ReleaseAsset


def test_bailong_assets_cover_all_35_actions_without_chat_runtime():
    root = Path(__file__).resolve().parents[1]
    source = root / "pet_host/bailong"
    catalog = json.loads((source / "actions.json").read_text(encoding="utf-8"))
    items = [catalog["idle"], *catalog["additionalIdleAnimations"], *catalog["variants"],
             catalog["dragAnimation"], catalog["landingAnimation"], catalog["quotaAnimation"],
             *filter(None, catalog["workAnimations"]), *catalog["movementAnimations"]]
    assert len(items) == len({item["id"] for item in items}) == 35
    assert all((source / "webm" / Path(item["playbackFile"]).name).is_file() for item in items)
    assert not list(source.rglob("*.py"))
    assert not (root / "ui/bailong_host.py").exists()


def test_bailong_uses_existing_wpf_host(tmp_path):
    application = QApplication.instance() or QApplication([])
    host = VPetHost()
    root = Path(__file__).resolve().parents[1]
    with patch("ui.vpet_host.host_executable", return_value=root / "main.py"), \
         patch.object(host.process, "start"):
        host.start(tmp_path / "state", character="bailong")
    assert host.process.program() == str(root / "main.py")
    assert "--bailong-host" not in host.process.arguments()
    assert host.process.arguments()[-4:] == ["--character", "bailong", "--slot", "0"]
    host.stop()
    application.processEvents()


def test_three_roles_can_install_merge_and_remove_independently(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    for name in (*pet.REQUIRED_FILES, "coreclr.dll"):
        (source / name).write_bytes(b"runtime")
    resources = source / "resources"
    for role in ("vup", "whale", "bailong"):
        folder = resources / "pet" / role
        folder.mkdir(parents=True)
        (folder / "idle.png").write_bytes(role.encode())
        (folder.parent / f"{role}.lps").write_bytes(role.encode())
    identity = pet.resource_identity(resources, "a" * 40)
    (source / pet.RESOURCES_MANIFEST).write_text(json.dumps({
        "revision": identity["revision"], "resource_files": identity["files"], "resource_bytes": identity["bytes"],
    }))
    (source / pet.PACK_MANIFEST).write_text(json.dumps(build_release.PET_MANIFEST))
    monkeypatch.setattr(build_release, "PET_PACK_PATH", tmp_path / "full.zip")
    monkeypatch.setattr(build_release, "PET_HOST_PACK_PATH", tmp_path / "host.zip")
    full = build_release.package_pet_payload(source)
    manifest = json.loads((tmp_path / pet.PACK_MANIFEST).read_text())
    monkeypatch.setattr(pet.config_manager, "CONFIG_DIR", tmp_path / "data")

    def archive(role):
        return tmp_path / PET_CHARACTER_ASSET_TEMPLATE.format(character=role, version=manifest["version"])

    release = PetReleaseInfo(manifest["version"], manifest,
                             ReleaseAsset("full.zip", "https://example.com/full.zip", full.stat().st_size),
                             "a" * 64, character_assets={role: (ReleaseAsset(archive(role).name,
                                 f"https://example.com/{role}.zip", archive(role).stat().st_size), "b" * 64)
                                 for role in ("vup", "whale", "bailong")})

    def download(_client, _directory, **kwargs):
        return archive(kwargs["character"]) if kwargs["character"] else full

    with patch.object(GitHubReleaseClient, "download_pet_pack", download):
        pet.download_and_install(Mock(), lambda: False, release=release, character="bailong")
        pet.download_and_install(Mock(), lambda: False, release=release, character="vpet", add_character=True)
        assert pet.installed_characters() == {"bailong", "vpet"}
        pet.download_and_install(Mock(), lambda: False, release=release, character="whale", add_character=True)
    assert pet.installed_characters() == {"vpet", "whale", "bailong"}
    pet.save_selected_character("bailong")
    pet.remove_character("vpet")
    assert pet.installed_characters() == {"whale", "bailong"}
    assert pet.selected_character() == "bailong"
    with patch.object(GitHubReleaseClient, "download_pet_pack", download):
        pet.download_and_install(Mock(), lambda: False, release=release, replace_existing=True)
    assert pet.installed_characters() == {"whale", "bailong"}
    pet.remove_character("bailong")
    assert pet.installed_characters() == {"whale"}
    assert pet.selected_character() == "whale"


def test_model_selection_includes_bailong(tmp_path, monkeypatch):
    from ui.qt_settings import SettingsWindow

    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(pet.config_manager, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(pet.config_manager, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(pet.config_manager, "all_config", lambda: DEFAULT_CONFIG.copy())
    with patch.object(pet, "installed_characters", return_value={"vpet", "whale", "bailong"}):
        window = SettingsWindow()
        try:
            assert window.pet_bailong_default_button.isEnabled()
            window._select_pet_default("bailong")
            assert window.pet_bailong_default_button.text() == "当前默认"
            assert pet.selected_character() == "bailong"
        finally:
            window.close()
            application.processEvents()
