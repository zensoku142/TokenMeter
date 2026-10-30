"""Exercise real WPF pet throwing and independent instance movement (Windows + .NET SDK)."""

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "win32" or not shutil.which("dotnet"),
                                reason="Real WPF physics checks require Windows and the .NET SDK")


class Whale:
    def __init__(self, executable, directory, character="whale"):
        directory.mkdir()
        (directory / "layout.json").write_text(json.dumps({
            "character": character, "allowMove": False, "cloudMode": "off", "quotaPinned": False,
        }), encoding="utf-8")
        self.log = (directory / "probe.log").open("w", encoding="utf-8")
        self.process = subprocess.Popen([str(executable), str(directory), character], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=self.log, text=True, encoding="utf-8")
        self.messages = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.initial = self._reply(timeout=90)
        except BaseException:
            self.process.kill()
            self.process.wait(timeout=10)
            self.log.close()
            raise

    def _read(self):
        for line in self.process.stdout:
            self.messages.put(json.loads(line))
        self.messages.put(None)

    def _reply(self, timeout=30):
        try:
            result = self.messages.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError(f"Whale probe timed out; inspect {self.log.name}") from None
        assert result is not None, f"Whale probe exited; inspect {self.log.name}"
        return result

    def command(self, action="snapshot", **values):
        self.process.stdin.write(json.dumps({"action": action, **values}) + "\n")
        self.process.stdin.flush()
        return self._reply()

    def close(self):
        if self.process.poll() is None:
            self.command("close")
            self.process.wait(timeout=10)
        self.log.close()


@pytest.fixture(scope="module")
def probe(tmp_path_factory):
    output = tmp_path_factory.mktemp("whale-probe")
    result = subprocess.run(["dotnet", "build", str(ROOT / "tests/whale_physics/WhalePhysicsProbe.csproj"),
                             "-c", "Release", "-o", str(output), "--artifacts-path", str(output / "artifacts"),
                             "--ignore-failed-sources", "-p:NuGetAudit=false", "-m:1"],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    # 仅使用仓库内基础 APNG；抛掷检查不依赖 FFmpeg、下载缓存或用户的已安装桌宠。
    resources = output / "resources/pet"
    whale = resources / "whale"
    whale.mkdir(parents=True)
    shutil.copy2(ROOT / "pet_host/whale.lps", resources / "whale.lps")
    for source in (ROOT / "pet_host/whale").iterdir():
        if source.is_file() and source.suffix in {".png", ".json", ".lps"}:
            shutil.copy2(source, whale / source.name)
    # 白龙娘的 APNG 由现有构建脚本生成；未构建素材时只跳过依赖它的真实渲染用例。
    built = Path(os.environ.get("TOKENMETER_TEST_PET_RESOURCES", ROOT / "build/vpet-bailong/resources")) / "pet"
    if (built / "bailong/animations/idle.png").is_file():
        shutil.copy2(built / "bailong.lps", resources / "bailong.lps")
        shutil.copytree(built / "bailong", resources / "bailong", ignore=shutil.ignore_patterns("webm"))
    return output / "WhalePhysicsProbe.exe"


@pytest.fixture(scope="module")
def whales(probe, tmp_path_factory, request):
    characters = getattr(request, "param", ("whale", "whale"))
    if "bailong" in characters and not (probe.parent / "resources/pet/bailong.lps").is_file():
        pytest.skip("Build the white dragon APNG resources or set TOKENMETER_TEST_PET_RESOURCES")
    peers = []
    directory = tmp_path_factory.mktemp("whale-peers")
    try:
        for index, character in enumerate(characters):
            peers.append(Whale(probe, directory / str(index), character))
        yield peers
    finally:
        for peer in peers:
            try:
                peer.close()
            finally:
                if peer.process.poll() is None:
                    peer.process.kill()
                    peer.process.wait(timeout=10)


def arrange(peers, *, size=220):
    # 复用进程以减少 APNG 解码；先结束上一场景的运动，避免旧计时器影响新场景。
    for peer in peers:
        peer.command("hide")
    first = peers[0].initial
    dpi, work = first["dpi"], first["work"]
    x = (work["x"] + work["width"] / 2) / dpi - size * 1.5
    y = (work["y"] + work["height"] / 3) / dpi - size / 2
    placed = peers[0].command("place", x=x, y=y, size=size)
    spacing = (placed["bounds"]["width"] + 20) / dpi
    for index, peer in enumerate(peers[1:], 1):
        peer.command("place", x=x + index * spacing, y=y, size=size)
    return x, y, spacing


def until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.01)
    raise AssertionError("Expected pet movement or edge feedback did not occur")


@pytest.mark.parametrize("size", [160, 320])
@pytest.mark.parametrize("whales", [("whale", "whale"), ("bailong", "bailong"),
                                    ("whale", "bailong")], indirect=True)
def test_throw_passes_other_pet_without_pushing_or_extra_redraws(whales, size):
    x, y, _ = arrange(whales, size=size)
    target = whales[1].command()
    whales[0].command("measure")
    whales[0].command("launch", x=x, y=y, vx=2400, vy=0)
    until(lambda: whales[0].command()["bounds"]["x"] > target["bounds"]["x"] + target["bounds"]["width"])
    source, after = whales[0].command(), whales[1].command()
    assert source["throwing"] and source["vx"] == 2400
    assert source["impact"] == after["impact"] == 0
    assert not after["throwing"]
    assert (after["x"], after["y"]) == (target["x"], target["y"])
    assert source["fullCollections"] == 0
    assert source["moves"] <= source["physicsTicks"] + 1


@pytest.mark.parametrize("whales", [("whale", "whale"), ("bailong", "bailong"),
                                    ("whale", "bailong")], indirect=True)
def test_drag_over_other_pet_does_not_start_its_motion(whales):
    x, y, _ = arrange(whales)
    target = whales[1].command()
    whales[0].command("drag", x=x, y=y, dx=280)
    until(lambda: (current := whales[0].command())["bounds"]["x"] + current["bounds"]["width"] >
          target["bounds"]["x"] + target["bounds"]["width"] / 2)
    after = whales[1].command()
    assert not after["throwing"] and after["impact"] == 0
    assert (after["x"], after["y"]) == (target["x"], target["y"])


@pytest.mark.parametrize("whales", [("whale", "whale"), ("bailong", "bailong")], indirect=True)
@pytest.mark.parametrize("pause", [0, 100])
def test_release_launches_only_when_the_last_movement_is_recent(whales, pause):
    arrange(whales)
    for peer in whales[1:]:
        peer.command("hide")
    state = whales[0].command("gesture", pause=pause)
    assert state["throwing"] == (pause == 0)


def test_white_dragon_throw_does_not_require_installed_whale_resources(probe, tmp_path):
    if not (probe.parent / "resources/pet/bailong.lps").is_file():
        pytest.skip("Build the white dragon APNG resources first")
    standalone = tmp_path / "standalone"
    shutil.copytree(probe.parent, standalone, ignore=shutil.ignore_patterns("resources", "artifacts"))
    resources = standalone / "resources/pet"
    resources.mkdir(parents=True)
    shutil.copy2(probe.parent / "resources/pet/bailong.lps", resources / "bailong.lps")
    shutil.copytree(probe.parent / "resources/pet/bailong", resources / "bailong")
    peer = Whale(standalone / probe.name, tmp_path / "state", "bailong")
    try:
        arrange([peer], size=80)
        assert peer.command("gesture", pause=0)["throwing"]
    finally:
        peer.close()


@pytest.mark.parametrize("side", ["left", "right", "top", "bottom"])
def test_screen_edges_rebound_and_squash_along_the_contact_axis(whales, side):
    x, y, _ = arrange(whales)
    for peer in whales[1:]:
        peer.command("hide")
    whale = whales[0]
    state = whale.command()
    dpi, work, bounds = state["dpi"], state["work"], state["bounds"]
    vx = vy = 0
    if side == "left":
        x, vx = (work["x"] - bounds["x"] + state["x"]) / dpi, -1800
    elif side == "right":
        x, vx = (work["x"] + work["width"] - bounds["x"] + state["x"] - bounds["width"]) / dpi, 1800
    elif side == "top":
        y, vy = (work["y"] - bounds["y"] + state["y"]) / dpi, -1800
    else:
        y, vy = (work["y"] + work["height"] - bounds["y"] + state["y"] - bounds["height"]) / dpi, 1800
    state = whale.command("step", x=x, y=y, vx=vx, vy=vy, dt=0.016)
    assert state["impact"] > 0
    if side in {"left", "right"}:
        assert state["vx"] * vx < 0
        state = until(lambda: (current := whale.command())["scaleX"] < 1 and current)
        assert state["scaleX"] < 1 < state["scaleY"]
    else:
        assert state["vy"] * vy < 0
        state = until(lambda: (current := whale.command())["scaleY"] < 1 and current)
        assert state["scaleY"] < 1 < state["scaleX"]
