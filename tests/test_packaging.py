import ast
from pathlib import Path


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
    options = _call_keywords("TokenMeter.spec", "Analysis")
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
    options = _call_keywords("TokenMeter.spec", "EXE")

    assert options["name"] == "TokenMeter"
    assert options["icon"] == ["assets/TokenMeter.ico"]


def test_runtime_icon_is_packaged_for_qt_windows_and_tray():
    options = _call_keywords("TokenMeter.spec", "Analysis")

    assert ("assets/TokenMeter.ico", "assets") in options["datas"]


def test_updater_executable_is_packaged_separately():
    options = _call_keywords("TokenMeterUpdater.spec", "EXE")

    assert options["name"] == "TokenMeterUpdater"
    assert options["icon"] == ["assets/TokenMeter.ico"]


def test_both_specs_use_onedir_collect_layout():
    assert _call_keywords("TokenMeter.spec", "COLLECT")["name"] == "TokenMeter"
    assert _call_keywords("TokenMeterUpdater.spec", "COLLECT")["name"] == "TokenMeterUpdater"
    assert _call_keywords("TokenMeter.spec", "EXE")["exclude_binaries"] is True


def test_release_build_removes_smoke_test_data():
    script = (ROOT / "scripts" / "build_release.py").read_text(encoding="utf-8")
    assert 'smoke_data = executable.parent / "data"' in script
    assert "shutil.rmtree(smoke_data)" in script


def test_release_build_hashes_only_the_installer_after_smoke_test():
    script = (ROOT / "scripts" / "build_release.py").read_text(encoding="utf-8")
    assert 'INSTALLER_PATH = INSTALLER_OUTPUT_DIR / f"TokenMeter-Setup-v{APP_VERSION}-x64.exe"' in script
    assert "_write_sha256_file([INSTALLER_PATH])" in script
    assert "LEGACY_SHA_FILE.unlink(missing_ok=True)" in script
    assert 'Path(local_appdata) / "Programs" / "Inno Setup 6" / "ISCC.exe"' in script
    smoke_call = script.index("\n        smoke_test()\n")
    assert script.index("build_installer(required=False)") < smoke_call
    assert smoke_call < script.index("write_release_checksums(required=True)")


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
    assert "dist/TokenMeter-v*-windows-x64.exe" not in workflow
