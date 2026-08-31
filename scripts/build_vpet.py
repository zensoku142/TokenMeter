"""Build the optional vendored VPet host and a pinned default resource pack."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDORED_SOURCE = ROOT / "third_party" / "VPet"
# 保留旧缓存位置以复用已下载的动画；核心源码只从仓库内编译。
SOURCE = ROOT / "build" / "vpet-upstream"
OUTPUT = ROOT / "build" / "vpet"
UPSTREAM = "https://github.com/LorisYounger/VPet.git"
REVISION = "b6f7b00363529bafe3e7fc14bf51e17640941691"
CORE_MOD = Path("VPet-Simulator.Windows/mod/0000_core")
# 保留鼠标互动与自主移动/待机的完整动画，移除投喂、睡眠、工作、升级及养成状态资源。
ANIMATION_DIRS = (
    "Default",
    "MOVE",
    "Raise",
    "Say",
    "SideHide_Left_Main",
    "SideHide_Left_Rise",
    "SideHide_Right_Main",
    "SideHide_Right_Rise",
    "StartUP",
    "State",
    "Touch_Body",
    "Touch_Head",
    "IDEL",
)


def run(*args: str, cwd: Path = ROOT, env=None) -> None:
    subprocess.run(list(args), cwd=cwd, env=env, check=True)


def source_revision() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SOURCE, text=True).strip()


def ensure_source() -> None:
    if not SOURCE.exists():
        SOURCE.parent.mkdir(parents=True, exist_ok=True)
        run(
            "git",
            "clone",
            "--depth",
            "1",
            "--filter=blob:none",
            "--sparse",
            "--no-checkout",
            UPSTREAM,
            str(SOURCE),
        )
        run("git", "fetch", "--depth", "1", "origin", REVISION, cwd=SOURCE)
        run("git", "checkout", "--detach", REVISION, cwd=SOURCE)
    if source_revision() != REVISION:
        raise RuntimeError(f"Expected upstream {REVISION}; refusing to alter an existing checkout")
    needed = [str(CORE_MOD / "pet/vup" / name) for name in ANIMATION_DIRS]
    # 已下载的完整调研源码可直接使用，不改变其稀疏检出配置。
    if not all((SOURCE / path).exists() for path in needed):
        run("git", "sparse-checkout", "set", *needed, cwd=SOURCE)


def stage_resources() -> dict:
    target = OUTPUT / "resources"
    if target.exists():
        # 只清理固定的构建资源目录，避免裁剪清单变化时把旧资源误打进安装包。
        resolved = target.resolve()
        if resolved != OUTPUT / "resources" or target.is_symlink():
            raise RuntimeError("Refusing to clean an unexpected resource directory")
        shutil.rmtree(resolved)
    pet = target / "pet"
    pet.mkdir(parents=True)
    for name in ANIMATION_DIRS:
        shutil.copytree(SOURCE / CORE_MOD / "pet/vup" / name, pet / "vup" / name)
    lines = (SOURCE / CORE_MOD / "pet/vup.lps").read_text(encoding="utf-8-sig").splitlines()
    # 保留移动和触摸配置，删除工作/学习/玩耍的收益配置，防止入口被意外恢复。
    lines = [line for line in lines if not line.startswith("work:")]
    (pet / "vup.lps").write_text("\n".join(lines), encoding="utf-8")
    shutil.copy2(VENDORED_SOURCE / "LICENSE", OUTPUT / "VPet-LICENSE.txt")
    shutil.copy2(VENDORED_SOURCE / "README.md", OUTPUT / "VPet-README.md")
    shutil.copy2(ROOT / "pet_host/THIRD_PARTY_NOTICES.md", OUTPUT / "THIRD_PARTY_NOTICES.md")
    files = list(target.rglob("*"))
    report = {
        "upstream": UPSTREAM,
        "revision": REVISION,
        "animation_directories": ANIMATION_DIRS,
        "resource_bytes": sum(p.stat().st_size for p in files if p.is_file()),
        "resource_files": sum(p.is_file() for p in files),
        "growth_enabled": False,
    }
    (OUTPUT / "resources-manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def write_dependency_notices() -> None:
    # 依赖许可证跟随宿主发布，不能只附 VPet 自身的代码和动画声明。
    assets = json.loads((ROOT / "pet_host/obj/project.assets.json").read_text(encoding="utf-8"))
    names = {name for name, value in assets["libraries"].items() if value["type"] == "package"}
    for framework in assets.get("project", {}).get("frameworks", {}).values():
        for package in framework.get("downloadDependencies", []):
            names.add(package["name"] + "/" + package["version"].strip("[]").split(",")[0].strip())
    target = OUTPUT / "licenses"
    target.mkdir(exist_ok=True)
    dependencies = []
    for name in sorted(names):
        package = ROOT / "build/nuget-packages" / name.lower()
        nuspec = next(package.glob("*.nuspec"), None)
        if nuspec is None:
            continue
        metadata = ET.parse(nuspec).getroot()
        license_node = next(
            (node for node in metadata.iter() if node.tag.rsplit("}", 1)[-1] == "license"), None
        )
        license_value = license_node.text if license_node is not None else "See package metadata"
        folder = target / name.replace("/", "-")
        folder.mkdir(exist_ok=True)
        shutil.copy2(nuspec, folder / nuspec.name)
        for file in package.rglob("*"):
            if file.is_file() and (
                file.name.lower().startswith(
                    ("license", "third-party", "thirdpartynotices", "notice")
                )
            ):
                relative = file.relative_to(package)
                (folder / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, folder / relative)
        dependencies.append({"package": name, "license": license_value})
    (OUTPUT / "THIRD_PARTY_DEPENDENCIES.json").write_text(
        json.dumps(dependencies, indent=2), encoding="utf-8"
    )


def main() -> None:
    global OUTPUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT, help="Build subdirectory for a side-by-side trial")
    parser.add_argument(
        "--framework-dependent",
        action="store_true",
        help="Use installed .NET 8 Desktop Runtime for local trials",
    )
    parser.add_argument("--resources-only", action="store_true")
    args = parser.parse_args()
    OUTPUT = args.output.resolve()
    # 旁路构建可避免覆盖正在运行的宿主，但所有清理仍必须限制在项目 build 的直接子目录。
    if OUTPUT.parent != (ROOT / "build").resolve() or SOURCE.resolve() == OUTPUT:
        raise ValueError("Output must be a separate direct subdirectory of this project's build directory")
    ensure_source()
    if not args.resources_only:
        env = dict(
            os.environ,
            DOTNET_CLI_HOME=str(ROOT / "build/dotnet-home"),
            NUGET_PACKAGES=str(ROOT / "build/nuget-packages"),
            DOTNET_CLI_TELEMETRY_OPTOUT="1",
            DOTNET_GENERATE_ASPNET_CERTIFICATE="false",
        )
        run(
            "dotnet",
            "publish",
            "pet_host/TokenMeter.Pet.csproj",
            "-c",
            "Release",
            "-r",
            "win-x64",
            "--self-contained",
            "false" if args.framework_dependent else "true",
            "-o",
            str(OUTPUT),
            "-v",
            "minimal",
            env=env,
        )
    report = stage_resources()
    write_dependency_notices()
    report["total_bytes"] = sum(p.stat().st_size for p in OUTPUT.rglob("*") if p.is_file())
    if (OUTPUT / "TokenMeter.Pet.exe").is_file():
        # 源码运行使用最后一次成功构建；旁路构建不应让 main.py 继续启动旧宿主。
        active = ROOT / "build" / "vpet-active.json"
        temporary = active.with_suffix(".json.tmp")
        temporary.write_text(json.dumps({"directory": OUTPUT.name}), encoding="utf-8")
        temporary.replace(active)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
