"""Check real WPF animation cache reuse, pixel fidelity, timing and idle release."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "win32" or not shutil.which("dotnet"),
                                reason="Animation cache checks require Windows and the .NET SDK")


def test_animation_cache_reuses_frames_preserves_pixels_and_releases_idle_images(tmp_path):
    output = tmp_path / "probe"
    result = subprocess.run(["dotnet", "build", str(ROOT / "tests/animation_cache/AnimationCacheProbe.csproj"),
                             "-c", "Release", "-o", str(output), "--artifacts-path", str(output / "artifacts"),
                             "--ignore-failed-sources", "-p:NuGetAudit=false", "-m:1"],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    source = tmp_path / "frames"
    source.mkdir()
    frames = [Image.new("RGBA", (16, 16), color) for color in ("red", "lime", "blue")]
    for index, (frame, duration) in enumerate(zip(frames, (40, 80, 120))):
        frame.save(source / f"frame_{index:03}_{duration}.png")
    frames[0].save(source / "animation.png", save_all=True, append_images=frames[1:],
                   duration=[40, 80, 120], loop=0, disposal=0, blend=0)
    # 第二次启动复用磁盘缓存，覆盖只读取 APNG 元数据的路径。
    for _ in range(2):
        result = subprocess.run([str(output / "AnimationCacheProbe.exe"), str(source), str(tmp_path / "cache")],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout) == {"png": True, "apng": True}
