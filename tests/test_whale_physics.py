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
    def __init__(self, executable, directory, character="whale", *, fixed_character=True):
        directory.mkdir(exist_ok=True)
        layout = directory / "layout.json"
        if not layout.exists():
            layout.write_text(json.dumps({
                "character": character, "allowMove": False, "cloudMode": "off", "quotaPinned": False,
            }), encoding="utf-8")
        self.log = (directory / "probe.log").open("w", encoding="utf-8")
        self.process = subprocess.Popen([str(executable), str(directory), character if fixed_character else "default"], stdin=subprocess.PIPE,
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
    (whale / "apng").mkdir()
    # 用现有真实待机素材验证按需动作，不要求本地构建全部 106 段 WebM。
    shutil.copy2(ROOT / "pet_host/whale/idle.png", whale / "apng/__test_idle__.png")
    # 白龙娘的 APNG 由现有构建脚本生成；未构建素材时只跳过依赖它的真实渲染用例。
    built = Path(os.environ.get("TOKENMETER_TEST_PET_RESOURCES", ROOT / "build/vpet-bailong/resources")) / "pet"
    if (built / "bailong/animations/idle.png").is_file():
        shutil.copy2(built / "bailong.lps", resources / "bailong.lps")
        shutil.copytree(built / "bailong", resources / "bailong", ignore=shutil.ignore_patterns("webm"))
    return output / "WhalePhysicsProbe.exe"


@pytest.fixture(scope="module")
def probe_with_vpet(probe):
    # 仅需要萝莉斯的用例复制其大量帧，避免拖慢原有鲸鱼娘物理测试。
    built = Path(os.environ.get("TOKENMETER_TEST_PET_RESOURCES", ROOT / "build/vpet-bailong/resources")) / "pet"
    if (built / "vup.lps").is_file() and (built / "vup").is_dir():
        resources = probe.parent / "resources/pet"
        shutil.copy2(built / "vup.lps", resources / "vup.lps")
        shutil.copytree(built / "vup", resources / "vup")
    return probe


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


def test_all_models_remember_their_own_sizes_across_restart_and_switch(probe_with_vpet, tmp_path):
    probe = probe_with_vpet
    if not all((probe.parent / "resources/pet" / f"{name}.lps").is_file() for name in ("vup", "bailong")):
        pytest.skip("Build the white dragon and VPet resources first")
    state = tmp_path / "state"
    state.mkdir()
    # 旧版只有通用 size；升级后和切回角色时都应保留小于 160 的值。
    (state / "layout.json").write_text(json.dumps({"character": "bailong", "size": 120,
                                                   "allowMove": False, "cloudMode": "off"}), encoding="utf-8")
    pet = Whale(probe, state, "bailong", fixed_character=False)
    try:
        assert pet.initial["size"] == 120
        assert pet.command("resize", delta=20)["size"] == 140
        pet.command("switch", character="whale")
        assert until(lambda: (value := pet.command())["character"] == "whale" and value, timeout=60)["size"] == 160
        assert pet.command("resize", delta=80)["size"] == 240
        pet.command("switch", character="vpet")
        assert until(lambda: (value := pet.command())["character"] == "vpet" and value, timeout=60)["size"] == 240
        assert pet.command("resize", delta=-40)["size"] == 200
        pet.command("switch", character="bailong")
        assert until(lambda: (value := pet.command())["character"] == "bailong" and value, timeout=60)["size"] == 140
        pet.command("switch", character="whale")
        assert until(lambda: (value := pet.command())["character"] == "whale" and value, timeout=60)["size"] == 240
        pet.command("switch", character="vpet")
        assert until(lambda: (value := pet.command())["character"] == "vpet" and value, timeout=60)["size"] == 200
    finally:
        pet.close()
    assert json.loads((state / "layout.json").read_text(encoding="utf-8"))["modelSizes"] == {
        "vpet": 200, "whale": 240, "bailong": 140,
    }
    pet = Whale(probe, state, "vpet", fixed_character=False)
    try:
        assert pet.initial["size"] == 200
        pet.command("switch", character="bailong")
        assert until(lambda: (value := pet.command())["character"] == "bailong" and value, timeout=60)["size"] == 140
        pet.command("switch", character="whale")
        assert until(lambda: (value := pet.command())["character"] == "whale" and value, timeout=60)["size"] == 240
    finally:
        pet.close()


def test_fixed_bailong_instance_applies_its_own_size_limit(probe, tmp_path):
    if not (probe.parent / "resources/pet/bailong.lps").is_file():
        pytest.skip("Build the white dragon APNG resources first")
    state = tmp_path / "state"
    state.mkdir()
    (state / "layout.json").write_text(json.dumps({"character": "whale", "size": 100,
                                                   "allowMove": False, "cloudMode": "off"}), encoding="utf-8")
    pet = Whale(probe, state, "bailong")
    try:
        assert pet.initial["size"] == 100
    finally:
        pet.close()
    pet = Whale(probe, state, "whale")
    try:
        assert pet.initial["size"] == 160
    finally:
        pet.close()
    pet = Whale(probe, state, "bailong")
    try:
        assert pet.initial["size"] == 100
    finally:
        pet.close()


def test_bailong_deferred_action_can_be_cancelled_and_played(probe, tmp_path):
    if not (probe.parent / "resources/pet/bailong.lps").is_file():
        pytest.skip("Build the white dragon APNG resources first")
    pet = Whale(probe, tmp_path / "state", "bailong")
    try:
        assert pet.initial["requiredReady"] and pet.initial["deferred"] >= 35
        assert pet.initial["deferredUnready"] > 0
        pet.command("block_preparations")
        assert not pet.command("action_ready", id="work05")["ready"]
        assert pet.command("play", id="work05")["manualPending"]
        assert not pet.command("hide")["manualPending"]
        pet.command("release_preparations")
        until(lambda: pet.command("action_ready", id="work05")["ready"], timeout=30)
        assert pet.command("show")["display"] != "bailong.action.work05"
        pet.command("play", id="work05")
        assert until(lambda: (value := pet.command())["display"] == "bailong.action.work05" and value,
                     timeout=10)["manualPending"]
    finally:
        pet.close()


@pytest.mark.parametrize("character", ["vpet", "whale"])
def test_other_models_start_with_required_animations_before_optional_ones(probe_with_vpet, tmp_path, character):
    probe = probe_with_vpet
    if character == "vpet" and not (probe.parent / "resources/pet/vup.lps").is_file():
        pytest.skip("Build the VPet resources first")
    pet = Whale(probe, tmp_path / "state", character)
    try:
        assert pet.initial["requiredReady"]
        assert pet.initial["deferred"] > 0
    finally:
        pet.close()


def test_vpet_deferred_autonomous_action_is_cancelled_when_hidden(probe_with_vpet, tmp_path):
    probe = probe_with_vpet
    if not (probe.parent / "resources/pet/vup.lps").is_file():
        pytest.skip("Build the VPet resources first")
    pet = Whale(probe, tmp_path / "state", "vpet")
    try:
        pet.command("block_preparations")
        action = pet.command("play_deferred")
        assert action["pending"]
        pet.command("graph_event")
        time.sleep(0.15)
        assert pet.command()["autonomousActive"]
        pet.command("hide")
        pet.command("release_preparations")
        until(lambda: pet.command("deferred_ready", index=action["index"])["ready"], timeout=30)
        assert pet.command("show")["display"] != action["name"]
        pet.command("block_preparations")
        action = pet.command("play_deferred")
        assert action["pending"]
        pet.command("graph_event")
        time.sleep(0.15)
        assert pet.command()["autonomousActive"]
        pet.command("release_preparations")
        until(lambda: pet.command()["display"] == action["name"], timeout=30)
        until(lambda: not pet.command()["autonomousActive"], timeout=30)
    finally:
        pet.close()


def test_whale_can_play_an_action_loaded_after_startup(probe, tmp_path):
    pet = Whale(probe, tmp_path / "state", "whale")
    try:
        pet.command("play_whale", name="__test_idle__")
        until(lambda: pet.command()["display"] == "__test_idle__", timeout=30)
    finally:
        pet.close()


def test_vpet_deferred_state_sequence_plays_in_order(probe_with_vpet, tmp_path):
    probe = probe_with_vpet
    if not (probe.parent / "resources/pet/vup.lps").is_file():
        pytest.skip("Build the VPet resources first")
    pet = Whale(probe, tmp_path / "state", "vpet")
    try:
        assert pet.command("state_start")["autonomousActive"]
        result = until(lambda: (value := pet.command("state_result"))["expected"] and
                       not value["active"] and value, timeout=60)
        assert len(result["expected"]) == 7
        assert result["played"] == result["expected"]
        assert result["display"] == "Default"
    finally:
        pet.close()


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
