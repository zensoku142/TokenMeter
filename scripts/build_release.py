"""Build release artifacts for GitHub Releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.identity import (
    APP_VERSION,
    MAIN_EXECUTABLE_NAME,
    PET_HOST_RELEASE_ASSET_TEMPLATE,
    PET_RELEASE_ASSET_TEMPLATE,
    UPDATER_EXECUTABLE_NAME,
)
from core.pet_extension import PACK_MANIFEST, validate_payload
from updater.client import validate_pet_manifest

DIST_DIR = ROOT / "dist"
MAIN_SPEC = ROOT / "packaging" / "pyinstaller" / "TokenMeter.spec"
UPDATER_SPEC = ROOT / "packaging" / "pyinstaller" / "TokenMeterUpdater.spec"
APP_DIST_DIR = DIST_DIR / "TokenMeter"
UPDATER_DIST_DIR = DIST_DIR / "TokenMeterUpdater"
INSTALLER_SCRIPT = ROOT / "packaging" / "installer" / "TokenMeter.iss"
INSTALLER_OUTPUT_DIR = ROOT / "dist-installer"
INSTALLER_PATH = INSTALLER_OUTPUT_DIR / f"TokenMeter-Setup-v{APP_VERSION}-x64.exe"
# 桌宠版本只来自自己的清单，普通主程序发版不会重新编号或构建扩展。
PET_MANIFEST = json.loads((ROOT / "pet_host" / PACK_MANIFEST).read_text(encoding="utf-8"))
PET_PACK_PATH = ROOT / "dist-pet" / PET_RELEASE_ASSET_TEMPLATE.format(version=PET_MANIFEST["version"])
PET_HOST_PACK_PATH = ROOT / "dist-pet" / PET_HOST_RELEASE_ASSET_TEMPLATE.format(version=PET_MANIFEST["version"])
PET_RESOURCES_MANIFEST = "resources-manifest.json"
SHA_FILE = INSTALLER_OUTPUT_DIR / "SHA256SUMS.txt"
LEGACY_SHA_FILE = DIST_DIR / "SHA256SUMS.txt"


def _run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=ROOT, check=True, env=env)


def _isolated_dll_environment() -> dict[str, str]:
    env = os.environ.copy()
    if os.name == "nt":
        # 构建机 PATH 中的 Poppler/Java 等会提供同名但不兼容的 DLL；只给打包子进程保留 Python 和系统路径。
        system_root = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        env["PATH"] = os.pathsep.join(map(str, (
            Path(sys.executable).parent, Path(sys.base_prefix), system_root / "System32", system_root,
        )))
    return env


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_sha256_file(paths: list[Path]) -> None:
    lines = [f"{_sha256(path)} *{path.name}" for path in paths]
    SHA_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _smoke_test_main(executable: Path) -> None:
    # 验证真实 Qt 导入与渲染后的退出码；错误弹窗仍保持进程存活，不能再以“运行了5秒”判定成功。
    with tempfile.TemporaryDirectory(prefix="tokenmeter-smoke-") as state_dir:
        env = _isolated_dll_environment()
        env.update(APPDATA=state_dir, LOCALAPPDATA=state_dir)
        subprocess.run([str(executable), "--smoke-test"], cwd=executable.parent,
                       check=True, timeout=60, env=env)


def _smoke_test_updater(executable: Path) -> None:
    subprocess.run([str(executable), "--help"], cwd=executable.parent, check=True)


def build_onedir(*, with_vpet: bool = False) -> None:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    INSTALLER_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    INSTALLER_PATH.unlink(missing_ok=True)
    SHA_FILE.unlink(missing_ok=True)
    LEGACY_SHA_FILE.unlink(missing_ok=True)
    _run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", str(MAIN_SPEC)],
         env=_isolated_dll_environment())
    _run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", str(UPDATER_SPEC)],
         env=_isolated_dll_environment())

    # Reuse the identity constants so build outputs and release asset names stay
    # aligned after repository/branding adjustments.
    main_exe = APP_DIST_DIR / MAIN_EXECUTABLE_NAME
    built_updater_exe = UPDATER_DIST_DIR / UPDATER_EXECUTABLE_NAME
    updater_exe = APP_DIST_DIR / UPDATER_EXECUTABLE_NAME
    if built_updater_exe.exists():
        shutil.copy2(built_updater_exe, updater_exe)
        updater_internal = UPDATER_DIST_DIR / "_internal"
        if updater_internal.exists():
            shutil.copytree(
                updater_internal, APP_DIST_DIR / "_internal", dirs_exist_ok=True
            )
    if not main_exe.exists() or not updater_exe.exists():
        raise FileNotFoundError("PyInstaller did not produce both executables")
    if with_vpet:
        # 保留旧命令参数，但桌宠始终产出独立附件，不再混入主安装目录。
        build_pet_pack()


def _pet_manifest(source: Path) -> dict:
    manifest = validate_pet_manifest(PET_MANIFEST)
    resource_manifest = json.loads((source / PET_RESOURCES_MANIFEST).read_text(encoding="utf-8"))
    resources = source / "resources"
    files = [path for path in resources.rglob("*") if path.is_file()]
    resource_hash = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(resources).as_posix()):
        # 路径参与摘要，避免同样内容被移动到另一动画槽位后仍误判为可复用资源。
        resource_hash.update(path.relative_to(resources).as_posix().encode("utf-8"))
        resource_hash.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                resource_hash.update(chunk)
    expected = {
        "revision": resource_manifest.get("revision"),
        "files": resource_manifest.get("resource_files"),
        "bytes": resource_manifest.get("resource_bytes"),
        "sha256": resource_hash.hexdigest(),
    }
    actual = {
        "revision": expected["revision"],
        "files": len(files),
        "bytes": sum(path.stat().st_size for path in files),
        "sha256": expected["sha256"],
    }
    if (not isinstance(expected["revision"], str)
            or len(expected["revision"]) != 40
            or any(character not in "0123456789abcdef" for character in expected["revision"])
            or type(expected["files"]) is not int or type(expected["bytes"]) is not int
            or actual != expected):
        raise ValueError("Pet resources manifest does not match the packaged resources")
    return {**manifest, "resources": expected}


def _write_pet_archive(source: Path, destination: Path, manifest: dict, *, include_resources: bool) -> None:
    temporary = destination.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as pack:
            for path in sorted(source.rglob("*")):
                relative = path.relative_to(source)
                if (path.is_file() and path.name != PACK_MANIFEST
                        and (include_resources or relative.parts[0] != "resources")):
                    pack.write(path, relative.as_posix())
            pack.writestr(PACK_MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def package_pet_payload(source: Path) -> Path:
    validate_payload(source)
    manifest = _pet_manifest(source)
    if not (source / "coreclr.dll").is_file():
        raise ValueError("Release pet packs must include the .NET runtime")
    PET_PACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    _write_pet_archive(source, PET_PACK_PATH, manifest, include_resources=True)
    # 旧客户端继续下载完整包；新版客户端确认本地资源版本一致后只下载宿主小包。
    _write_pet_archive(source, PET_HOST_PACK_PATH, manifest, include_resources=False)
    manifest_path = PET_PACK_PATH.parent / PACK_MANIFEST
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    # 桌宠发布不依赖主安装器，清单也参与校验以支持小流量检查兼容版本。
    (PET_PACK_PATH.parent / "SHA256SUMS.txt").write_text(
        "".join(f"{_sha256(path)} *{path.name}\n"
                for path in (PET_PACK_PATH, PET_HOST_PACK_PATH, manifest_path)),
        encoding="utf-8",
    )
    return PET_PACK_PATH


def build_pet_pack() -> Path:
    _run([sys.executable, str(ROOT / "scripts" / "build_vpet.py")])
    return package_pet_payload(ROOT / "build" / "vpet")

def _inno_compiler() -> str | None:
    compiler = shutil.which("iscc") or shutil.which("ISCC.exe")
    if compiler:
        return compiler
    program_files = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    candidates = [Path(program_files) / "Inno Setup 6" / "ISCC.exe"]
    if local_appdata:
        candidates.append(Path(local_appdata) / "Programs" / "Inno Setup 6" / "ISCC.exe")
    return next((str(path) for path in candidates if path.exists()), None)


def build_installer(*, required: bool) -> bool:
    compiler = _inno_compiler()
    if compiler:
        _run([compiler, f"/DMyAppVersion={APP_VERSION}", str(INSTALLER_SCRIPT)])
        if not INSTALLER_PATH.exists():
            raise FileNotFoundError("Inno Setup did not produce the expected installer")
        return True
    if required:
        raise FileNotFoundError("Inno Setup compiler not found")
    print("Inno Setup compiler not found; onedir build completed, installer skipped.")
    return False


def smoke_test() -> None:
    _smoke_test_updater(APP_DIST_DIR / UPDATER_EXECUTABLE_NAME)
    _smoke_test_main(APP_DIST_DIR / MAIN_EXECUTABLE_NAME)


def write_release_checksums(*, required: bool) -> bool:
    if not INSTALLER_PATH.exists():
        if required:
            raise FileNotFoundError("Installer missing; cannot generate release checksums")
        print("Installer missing; SHA256SUMS.txt generation skipped.")
        return False
    paths = [INSTALLER_PATH]
    _write_sha256_file(paths)
    return True


def build_release(*, skip_smoke_test: bool, with_vpet: bool = False) -> None:
    build_onedir(with_vpet=with_vpet)
    installer_built = build_installer(required=False)
    if not skip_smoke_test:
        smoke_test()
    if installer_built:
        write_release_checksums(required=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build TokenMeter release assets")
    parser.add_argument("--skip-smoke-test", action="store_true")
    parser.add_argument("--with-vpet", action="store_true", help="Also build a separate optional VPet ZIP (never bundled in the installer)")
    parser.add_argument(
        "--stage",
        choices=("all", "onedir", "pet", "installer", "smoke", "checksums"),
        default="all",
    )
    parser.add_argument("--require-installer", action="store_true")
    args = parser.parse_args(argv)
    if args.stage == "all":
        build_release(skip_smoke_test=args.skip_smoke_test, with_vpet=args.with_vpet)
    elif args.stage == "onedir":
        build_onedir(with_vpet=args.with_vpet)
    elif args.stage == "installer":
        build_installer(required=args.require_installer)
    elif args.stage == "pet":
        build_pet_pack()
    elif args.stage == "smoke":
        smoke_test()
    else:
        write_release_checksums(required=args.require_installer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
