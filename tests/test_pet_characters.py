import hashlib
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from core import pet_characters as characters

ROOT = Path(__file__).resolve().parents[1]


def _manifest(**overrides):
    return {
        "schema_version": 1,
        "id": "blue-whale-maid",
        "name": "蓝鲸女仆",
        "version": "0.0.1",
        "pet_protocol": characters.PET_PROTOCOL,
        **overrides,
    }


def _pack(path: Path, *, unsafe: str | None = None) -> Path:
    source = path.parent / "source"
    source.mkdir()
    (source / characters.MANIFEST_NAME).write_text(
        json.dumps(_manifest(), ensure_ascii=False), encoding="utf-8"
    )
    pet = source / "resources" / "pet"
    pet.mkdir(parents=True)
    (pet / "vup.lps").write_text("pet#blue-whale-maid:|path#vup:|", encoding="utf-8")
    for action in characters._REQUIRED_ACTION_DIRS:
        frame = pet / "vup" / action / "B" / "frame_000_125.png"
        frame.parent.mkdir(parents=True)
        frame.write_bytes(b"png")
    with zipfile.ZipFile(path, "w") as archive:
        for item in source.rglob("*"):
            if item.is_file():
                archive.write(item, item.relative_to(source).as_posix())
        if unsafe:
            archive.writestr(unsafe, b"escape")
    if unsafe and "\\" in unsafe:
        # ZipInfo normalizes backslashes; rewrite the central/local names to model an external ZIP.
        path.write_bytes(path.read_bytes().replace(unsafe.replace("\\", "/").encode(), unsafe.encode()))
    return path


@pytest.fixture
def isolated_characters(tmp_path, monkeypatch):
    values = {characters.CONFIG_KEY: characters.BUILTIN_ID}
    monkeypatch.setattr(characters.config_manager, "CONFIG_DIR", tmp_path / "data")
    monkeypatch.setattr(
        characters.config_manager, "get", lambda key, default=None: values.get(key, default)
    )

    def save(updates):
        values.update(updates)

    monkeypatch.setattr(characters.config_manager, "save_config", save)
    return values


def test_character_install_select_and_uninstall_falls_back_to_builtin(
    isolated_characters, tmp_path
):
    manifest = characters.install_pack(_pack(tmp_path / "character.zip"))
    assert manifest["version"] == "0.0.1"
    assert [item["id"] for item in characters.installed_characters()] == [
        characters.BUILTIN_ID,
        "blue-whale-maid",
    ]

    isolated_characters[characters.CONFIG_KEY] = "blue-whale-maid"
    resources = characters.selected_resources_directory()
    assert resources == characters.characters_directory() / "blue-whale-maid" / "resources"
    characters.uninstall("blue-whale-maid")

    assert isolated_characters[characters.CONFIG_KEY] == characters.BUILTIN_ID
    assert characters.selected_resources_directory() is None
    assert [item["id"] for item in characters.installed_characters()] == [characters.BUILTIN_ID]


@pytest.mark.parametrize("filename", ["../escape", "/escape", "C:/escape", "dir\\escape", "CON"])
def test_character_pack_rejects_unsafe_paths(isolated_characters, tmp_path, filename):
    archive = _pack(tmp_path / "character.zip", unsafe=filename)
    with pytest.raises((ValueError, OSError)):
        characters.install_pack(archive)
    root = characters.characters_directory()
    assert not root.exists() or not [path for path in root.iterdir() if not path.name.startswith(".")]


@pytest.mark.parametrize(
    "manifest",
    [
        {},
        _manifest(id="builtin"),
        _manifest(id="../escape"),
        _manifest(version="1"),
        _manifest(pet_protocol=True),
        _manifest(name=""),
    ],
)
def test_character_manifest_validation_rejects_invalid_values(manifest):
    with pytest.raises(ValueError):
        characters.validate_manifest(manifest)


def test_missing_selected_character_uses_builtin(isolated_characters):
    isolated_characters[characters.CONFIG_KEY] = "not-installed"
    assert characters.selected_character_id() == characters.BUILTIN_ID
    assert characters.selected_resources_directory() is None


def test_verified_character_download_installs_pack(isolated_characters, tmp_path):
    archive = _pack(tmp_path / "character.zip")
    content = archive.read_bytes()
    response = MagicMock()
    response.url = "https://release-assets.githubusercontent.com/character.zip"
    response.headers = {"content-length": str(len(content))}
    response.iter_content.return_value = [content[:100], content[100:]]
    response.__enter__.return_value = response
    progress = []
    entry = {
        "download_url": "https://github.com/example/character.zip",
        "sha256": hashlib.sha256(content).hexdigest(),
    }

    with patch("core.pet_characters.requests.get", return_value=response):
        manifest = characters.download_and_install(entry, progress.append, lambda: False)

    assert manifest["id"] == "blue-whale-maid"
    assert progress[-1]["downloaded"] == len(content)
    assert characters.installed_characters()[-1]["id"] == "blue-whale-maid"


def test_host_passes_selected_character_resources_to_child(qapp, tmp_path):
    from PySide6.QtCore import QProcess

    from ui.vpet_host import VPetHost

    executable = tmp_path / "TokenMeter.Pet.exe"
    executable.touch()
    resources = tmp_path / "character" / "resources"
    resources.mkdir(parents=True)
    host = VPetHost()
    host.process = Mock()
    host.process.state.return_value = QProcess.ProcessState.NotRunning
    with (
        patch("ui.vpet_host.host_executable", return_value=executable),
        patch("ui.vpet_host.pet_characters.selected_resources_directory", return_value=resources),
    ):
        host.start(tmp_path / "state")
    arguments = host.process.setArguments.call_args.args[0]
    assert arguments[arguments.index("--resources-dir") + 1] == str(resources.resolve())


def test_settings_lists_downloadable_and_installed_characters(qapp, tmp_path, monkeypatch):
    from config.defaults import DEFAULT_CONFIG
    from ui.qt_settings import SettingsWindow

    values = {**DEFAULT_CONFIG, characters.CONFIG_KEY: characters.BUILTIN_ID}
    builtin = {"id": characters.BUILTIN_ID, "name": "内置默认角色", "version": "", "builtin": True}
    blue = {
        "id": "blue-whale-maid", "name": "蓝鲸女仆", "version": "0.0.1",
        "description": "test", "download_url": "https://github.com/example.zip",
        "sha256": "a" * 64, "size": 123,
    }
    monkeypatch.setattr(characters.config_manager, "load_config", lambda: values.copy())
    monkeypatch.setattr(characters.config_manager, "all_config", lambda: values.copy())
    monkeypatch.setattr("ui.qt_settings.pet_extension.installed_manifest", lambda: {"version": "0.1.1"})
    monkeypatch.setattr("ui.qt_settings.pet_extension.removable_directories", lambda: [tmp_path])
    monkeypatch.setattr("ui.qt_settings.pet_characters.available_characters", lambda: [blue])
    monkeypatch.setattr("ui.qt_settings.pet_characters.installed_characters", lambda: [builtin])
    monkeypatch.setattr(
        "ui.qt_settings.pet_characters.selected_character_id", lambda: characters.BUILTIN_ID
    )

    window = SettingsWindow()
    try:
        index = window.pet_character_combo.findData("blue-whale-maid")
        assert index >= 0
        window.pet_character_combo.setCurrentIndex(index)
        window._refresh_character_buttons()
        assert window.pet_character_download_button.isEnabled()
        assert not window.pet_character_uninstall_button.isEnabled()

        monkeypatch.setattr(
            "ui.qt_settings.pet_characters.installed_characters",
            lambda: [builtin, {**blue, "builtin": False, "directory": tmp_path / "blue"}],
        )
        window._refresh_pet_controls()
        index = window.pet_character_combo.findData("blue-whale-maid")
        window.pet_character_combo.setCurrentIndex(index)
        window._refresh_character_buttons()
        assert not window.pet_character_download_button.isEnabled()
        assert window.pet_character_uninstall_button.isEnabled()
    finally:
        window.close()


def test_blue_whale_catalog_manifest_and_release_packaging_stay_aligned():
    source = ROOT / "pet_host" / "characters" / "blue-whale-maid"
    manifest = json.loads((source / "character.json").read_text(encoding="utf-8"))
    catalog = json.loads(
        (ROOT / "pet_host" / "characters" / "catalog.json").read_text(encoding="utf-8")
    )["characters"][0]
    assert (manifest["id"], manifest["version"]) == (catalog["id"], catalog["version"])
    assert len(catalog["sha256"]) == 64 and catalog["size"] > 0
    workflow = (ROOT / ".github" / "workflows" / "pet-character-release.yml").read_text(
        encoding="utf-8"
    )
    assert "pet-character-*-v*" in workflow
    assert "scripts/build_vpet_character.py" in workflow
    spec = (ROOT / "packaging" / "pyinstaller" / "TokenMeter.spec").read_text(encoding="utf-8")
    assert "pet_host/characters/catalog.json" in spec.replace("\\", "/")
