import ast
import os
import re
import subprocess
from pathlib import Path

import pytest

from scripts import build_release, build_vpet

ROOT = Path(__file__).resolve().parents[1]


def _call_keywords(path: str, name: str) -> dict[str, object]:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == name:
            return {
                keyword.arg: ast.literal_eval(keyword.value)
                for keyword in node.keywords
                if keyword.arg is not None
            }
    raise AssertionError(f"{name} call not found in {path}")


def test_pyqtgraph_startup_modules_are_packaged():
    options = _call_keywords("packaging/pyinstaller/TokenMeter.spec", "Analysis")
    excluded = set(options["excludes"])
    required = {
        "pyqtgraph.imageview",
        "pyqtgraph.multiprocess",
        "pyqtgraph.parametertree",
    }

    assert required.isdisjoint(excluded)
    assert {
        "PySide6.QtOpenGL",
        "PySide6.QtOpenGLWidgets",
    } <= set(options["hiddenimports"])


def test_main_executable_uses_stable_name_and_project_icon():
    options = _call_keywords("packaging/pyinstaller/TokenMeter.spec", "EXE")

    assert options["name"] == "TokenMeter"
    assert options["icon"] == ["../../assets/TokenMeter.ico"]


def test_runtime_icon_is_packaged_for_qt_windows_and_tray():
    options = _call_keywords("packaging/pyinstaller/TokenMeter.spec", "Analysis")

    assert ("../../assets/TokenMeter.ico", "assets") in options["datas"]


def test_updater_executable_is_packaged_separately():
    options = _call_keywords("packaging/pyinstaller/TokenMeterUpdater.spec", "EXE")

    assert options["name"] == "TokenMeterUpdater"
    assert options["icon"] == ["../../assets/TokenMeter.ico"]


def test_both_specs_use_onedir_collect_layout():
    assert _call_keywords("packaging/pyinstaller/TokenMeter.spec", "COLLECT")["name"] == "TokenMeter"
    assert (
        _call_keywords("packaging/pyinstaller/TokenMeterUpdater.spec", "COLLECT")["name"]
        == "TokenMeterUpdater"
    )
    assert _call_keywords("packaging/pyinstaller/TokenMeter.spec", "EXE")["exclude_binaries"] is True


def test_packaged_smoke_uses_exit_code_and_preserves_existing_data(tmp_path, monkeypatch):
    executable = tmp_path / "TokenMeter.exe"
    data = tmp_path / "data" / "keep.txt"
    data.parent.mkdir()
    data.write_text("existing user data")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert Path(kwargs["env"]["APPDATA"]).is_dir()
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(build_release.subprocess, "run", run)
    build_release._smoke_test_main(executable)
    command, kwargs = calls[0]
    assert command == [str(executable), "--smoke-test"]
    assert kwargs["check"] and kwargs["timeout"] == 60
    assert kwargs["cwd"] == tmp_path
    assert data.read_text() == "existing user data"
    assert not Path(kwargs["env"]["APPDATA"]).exists()


def test_packaged_smoke_rejects_startup_failure(tmp_path, monkeypatch):
    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(build_release.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        build_release._smoke_test_main(tmp_path / "TokenMeter.exe")


@pytest.mark.skipif(os.name != "nt", reason="Windows DLL search path")
def test_packaging_excludes_foreign_dll_search_paths_without_mutating_environment(monkeypatch):
    foreign = r"C:\foreign\poppler\bin;D:\java\bin"
    monkeypatch.setenv("PATH", foreign)
    env = build_release._isolated_dll_environment()
    assert "poppler" not in env["PATH"].lower()
    assert "java" not in env["PATH"].lower()
    assert str(Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32") in env["PATH"]
    assert os.environ["PATH"] == foreign


def test_installer_removes_only_known_legacy_icu_payloads():
    script = (ROOT / "packaging/installer/TokenMeter.iss").read_text(encoding="utf-8")
    section = script.split("[InstallDelete]", 1)[1].split("\n[", 1)[0]
    paths = re.findall(r'Type: files; Name: "([^"]+)"', section)
    assert set(paths) == {rf"{{app}}\_internal\{name}" for name in
                          ("icuuc.dll", "icuin.dll", "icu.dll", "icudt78.dll")}
    assert "filesandordirs" not in section and "*" not in section


def test_release_build_hashes_release_artifacts_after_smoke_test():
    script = (ROOT / "scripts" / "build_release.py").read_text(encoding="utf-8")
    assert 'INSTALLER_PATH = INSTALLER_OUTPUT_DIR / f"TokenMeter-Setup-v{APP_VERSION}-x64.exe"' in script
    assert "paths = [INSTALLER_PATH]" in script
    assert "paths.append(PET_PACK_PATH)" not in script
    assert "LEGACY_SHA_FILE.unlink(missing_ok=True)" in script
    assert 'Path(local_appdata) / "Programs" / "Inno Setup 6" / "ISCC.exe"' in script
    smoke_call = script.index("\n        smoke_test()\n")
    assert script.index("build_installer(required=False)") < smoke_call
    assert smoke_call < script.index("write_release_checksums(required=True)")


@pytest.mark.parametrize("include_pet", [False, True])
def test_main_release_checksums_exclude_pet_even_when_present(tmp_path, monkeypatch, include_pet):
    installer = tmp_path / "setup.exe"
    pet = tmp_path / "pet.zip"
    checksum = tmp_path / "SHA256SUMS.txt"
    installer.write_bytes(b"setup")
    if include_pet:
        pet.write_bytes(b"pet")
    monkeypatch.setattr(build_release, "INSTALLER_PATH", installer)
    monkeypatch.setattr(build_release, "PET_PACK_PATH", pet)
    monkeypatch.setattr(build_release, "SHA_FILE", checksum)
    assert build_release.write_release_checksums(required=True)
    lines = checksum.read_text().splitlines()
    assert len(lines) == 1
    assert lines[0] == f"{build_release._sha256(installer)} *setup.exe"


@pytest.mark.parametrize("with_vpet", [False, True])
def test_onedir_never_copies_pet_into_main_payload(tmp_path, monkeypatch, with_vpet):
    app = tmp_path / "dist/TokenMeter"
    app.mkdir(parents=True)
    for name in (build_release.MAIN_EXECUTABLE_NAME, build_release.UPDATER_EXECUTABLE_NAME):
        (app / name).touch()
    for name, value in {
        "DIST_DIR": tmp_path / "dist", "APP_DIST_DIR": app,
        "UPDATER_DIST_DIR": tmp_path / "updater", "INSTALLER_OUTPUT_DIR": tmp_path / "installer",
        "INSTALLER_PATH": tmp_path / "installer/setup.exe", "SHA_FILE": tmp_path / "installer/SHA.txt",
        "LEGACY_SHA_FILE": tmp_path / "dist/SHA.txt",
    }.items():
        monkeypatch.setattr(build_release, name, value)
    calls = []
    monkeypatch.setattr(build_release, "_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(build_release, "build_pet_pack", lambda: calls.append("pet"))
    build_release.build_onedir(with_vpet=with_vpet)
    assert not (app / "pet").exists()
    assert calls == (["pet"] if with_vpet else [])


def test_release_workflow_uses_installer_pipeline_order():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert (
        "python -m pip install -r requirements-dev.txt -r requirements-build.txt"
        in workflow
    )
    steps = [
        "- name: Run tests",
        "- name: Build PyInstaller onedir",
        "- name: Build Inno Setup installer",
        "- name: Smoke test onedir and installed application",
        "- name: Generate SHA256SUMS",
    ]
    positions = [workflow.index(step) for step in steps]
    assert positions == sorted(positions)
    assert "dist-installer/TokenMeter-Setup-v*-x64.exe" in workflow
    assert "--stage pet" not in workflow
    assert "TokenMeter-Pet" not in workflow
    assert "dist/TokenMeter-v*-windows-x64.exe" not in workflow


def test_pet_release_builds_only_extension_and_cannot_replace_main_latest():
    workflow = (ROOT / ".github/workflows/pet-release.yml").read_text(encoding="utf-8")
    assert '"pet-v*"' in workflow
    assert "python scripts/build_release.py --stage pet" in workflow
    assert "--stage onedir" not in workflow and "--stage installer" not in workflow
    assert "TokenMeter-Setup" not in workflow and "make_latest: false" in workflow
    assert "pet_host/extension.json" in workflow and "release-notes/" in workflow
    assert "dist-pet/extension.json" in workflow and "dist-pet/SHA256SUMS.txt" in workflow


def test_workflows_pin_actions_to_commit_shas():
    # 可变标签会让发布任务在无代码变更时执行不同的第三方代码，因此只接受完整提交 SHA。
    workflows = list((ROOT / ".github" / "workflows").glob("*.yml"))
    workflows.extend((ROOT / ".github" / "workflows").glob("*.yaml"))
    for path in workflows:
        workflow = path.read_text(encoding="utf-8")
        uses_lines = [line for line in workflow.splitlines() if line.strip().startswith("uses:")]
        pinned_refs = re.findall(
            r"^\s*uses:\s+[^@\s]+@[0-9a-f]{40}(?:\s|$)", workflow, re.MULTILINE
        )
        assert len(pinned_refs) == len(uses_lines), path.name
        assert workflow.count("persist-credentials: false") >= workflow.count(
            "actions/checkout@"
        ), path.name


@pytest.mark.parametrize("cached", [False, True])
def test_vpet_downloads_only_resources_and_preserves_vendored_edits(tmp_path, monkeypatch, cached):
    source = tmp_path / "build" / "vpet-upstream"
    vendor = tmp_path / "third_party" / "VPet"
    core = vendor / "VPet-Simulator.Core" / "Display" / "MainLogic.cs"
    core.parent.mkdir(parents=True)
    core.write_text("local contributor changes", encoding="utf-8")
    animations = ("Default", "MOVE")
    needed = [str(build_vpet.CORE_MOD / "pet/vup" / name) for name in animations]
    if cached:
        for path in needed:
            (source / path).mkdir(parents=True)
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ("git", "clone"):
            Path(args[-1]).mkdir()

    monkeypatch.setattr(build_vpet, "SOURCE", source)
    monkeypatch.setattr(build_vpet, "VENDORED_SOURCE", vendor)
    monkeypatch.setattr(build_vpet, "ANIMATION_DIRS", animations)
    monkeypatch.setattr(build_vpet, "run", run)
    monkeypatch.setattr(build_vpet, "source_revision", lambda: build_vpet.REVISION)
    build_vpet.ensure_source()

    if cached:
        assert calls == []
    else:
        assert calls[1] == (
            ("git", "fetch", "--depth", "1", "origin", build_vpet.REVISION), {"cwd": source}
        )
        assert calls[2] == (("git", "checkout", "--detach", build_vpet.REVISION), {"cwd": source})
        assert calls[3] == (("git", "sparse-checkout", "set", *needed), {"cwd": source})
    assert core.read_text(encoding="utf-8") == "local contributor changes"
    assert not (source / "VPet-Simulator.Core").exists()


def test_vpet_rejects_mismatched_resource_revision_without_changing_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(build_vpet, "SOURCE", tmp_path)
    monkeypatch.setattr(build_vpet, "source_revision", lambda: "other-revision")
    monkeypatch.setattr(build_vpet, "run", lambda *a, **kw: pytest.fail("checkout must not change"))

    with pytest.raises(RuntimeError, match="refusing to alter an existing checkout"):
        build_vpet.ensure_source()


@pytest.mark.parametrize("full_autonomy", [False, True])
def test_vpet_stages_cached_animations_and_vendored_notices(tmp_path, monkeypatch, full_autonomy):
    source = tmp_path / "build" / "vpet-upstream"
    vendor = tmp_path / "third_party" / "VPet"
    output = tmp_path / "build" / "vpet"
    animation = source / build_vpet.CORE_MOD / "pet/vup/Default/frame.png"
    animation.parent.mkdir(parents=True)
    animation.write_bytes(b"animation")
    animations = ("Default",)
    extra_frames = []
    if full_autonomy:
        assert "IDEL" in build_vpet.ANIMATION_DIRS
        animations += ("IDEL", "State", "MOVE")
        extra_frames = [
            f"IDEL/{name}/Happy/B/frame.png" for name in (
                "amusement_B", "aside", "Boring", "Bubbles", "happy_like520",
                "Meow", "meowlook", "Squat", "Tennis", "yawning",
            )
        ] + ["State/StateTWO/Nomal/B/frame.png", "MOVE/walk.left/Happy/B/frame.png"]
        for relative in extra_frames:
            frame = source / build_vpet.CORE_MOD / "pet/vup" / relative
            frame.parent.mkdir(parents=True)
            frame.write_bytes(relative.encode())
    config = source / build_vpet.CORE_MOD / "pet/vup.lps"
    config.write_text("pet: vup\nwork: removed\n", encoding="utf-8")
    vendor.mkdir(parents=True)
    (vendor / "LICENSE").write_text("vendored license", encoding="utf-8")
    (vendor / "README.md").write_text("upstream animation notices", encoding="utf-8")
    notices = tmp_path / "pet_host/THIRD_PARTY_NOTICES.md"
    notices.parent.mkdir()
    notices.write_text("integration notices", encoding="utf-8")
    stale = output / "resources/pet/obsolete.png"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old animation")
    monkeypatch.setattr(build_vpet, "ROOT", tmp_path)
    monkeypatch.setattr(build_vpet, "SOURCE", source)
    monkeypatch.setattr(build_vpet, "VENDORED_SOURCE", vendor)
    monkeypatch.setattr(build_vpet, "OUTPUT", output)
    monkeypatch.setattr(build_vpet, "ANIMATION_DIRS", animations)

    report = build_vpet.stage_resources()

    assert not stale.exists()
    assert (output / "resources/pet/vup/Default/frame.png").read_bytes() == b"animation"
    assert (output / "resources/pet/vup.lps").read_text(encoding="utf-8") == "pet: vup"
    assert (output / "VPet-LICENSE.txt").read_text(encoding="utf-8") == "vendored license"
    assert (output / "VPet-README.md").read_text(encoding="utf-8") == "upstream animation notices"
    assert (output / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8") == "integration notices"
    assert report["revision"] == build_vpet.REVISION
    assert report["resource_files"] == 2 + len(extra_frames)
    for relative in extra_frames:
        assert (output / "resources/pet/vup" / relative).read_bytes() == relative.encode()
    assert not (output / "resources/pet/vup/WORK").exists()
