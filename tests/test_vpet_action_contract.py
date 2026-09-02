import json
import shutil
from pathlib import Path

import pytest

from scripts import vpet_action_contract as contract

ROOT = Path(__file__).resolve().parents[1]


def _frames(root: Path, relative: str, durations: list[int]) -> None:
    directory = root / relative
    directory.mkdir(parents=True, exist_ok=True)
    for index, duration in enumerate(durations):
        (directory / f"frame_{index:03d}_{duration}.png").write_bytes(b"png")


def _move_lps(path: Path, graph: str = "walk.left", count: int = 16) -> None:
    path.write_text(
        "\n".join(
            f"move:|graph#{graph}:|TriggerLeft#200:|TriggerType#16:|"
            f"CheckLeft#100:|CheckType#16:|SpeedX#-14:|Distance#{index + 1}:|ModeType#14:|"
            for index in range(count)
        ),
        encoding="utf-8",
    )


def _resource_fixture(tmp_path: Path) -> tuple[Path, Path]:
    resource_root = tmp_path / "pet" / "vup"
    _frames(resource_root, "Default/Happy/1", [125, 250])
    _frames(resource_root, "MOVE/walk.left/A_Nomal", [125])
    lps_path = resource_root.parent / "vup.lps"
    _move_lps(lps_path)
    return resource_root, lps_path


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        (
            "IDEL/aside/Happy/B_2",
            {"graph_type": "Idel", "name": "aside", "mode": "Happy", "animat_type": "B_Loop"},
        ),
        (
            "Raise/Raised_Static/C_Ill",
            {"graph_type": "Raised_Static", "name": "raise", "mode": "Ill", "animat_type": "C_End"},
        ),
        (
            "State/StateTWO/Happy/B_2",
            {"graph_type": "StateTWO", "name": "state", "mode": "Happy", "animat_type": "B_Loop"},
        ),
        (
            "MOVE/walk.left.faster/A_Happy",
            {"graph_type": "Move", "name": "walk.left.faster", "mode": "Happy", "animat_type": "A_Start"},
        ),
        (
            "IDEL/happy_like520",
            {"graph_type": "Idel", "name": "like520", "mode": "Happy", "animat_type": "Single"},
        ),
    ],
)
def test_parse_graph_info_matches_vpet_path_rules(relative, expected):
    assert contract.parse_graph_info(relative) == expected


def test_build_and_validate_contract_records_segments_timings_and_moves(tmp_path):
    resource_root, lps_path = _resource_fixture(tmp_path)

    result = contract.build_contract(resource_root, lps_path)

    assert result["schema_version"] == 1
    assert result["segment_count"] == 2
    assert result["frame_count"] == 3
    assert result["move_config_count"] == 16
    default = result["segments"][0]
    assert default == {
        "relative_directory": "Default/Happy/1",
        "graph_type": "Default",
        "name": "default",
        "mode": "Happy",
        "animat_type": "Single",
        "family": "Default",
        "frame_count": 2,
        "duration_ms": 375,
        "frame_durations_ms": [125, 250],
    }
    assert [item["graph"] for item in result["move_configs"]] == ["walk.left"] * 16
    assert contract.validate_contract(result, resource_root, lps_path) == []


def test_build_contract_rejects_bad_duration_suffix(tmp_path):
    resource_root, lps_path = _resource_fixture(tmp_path)
    bad = resource_root / "Default/Happy/1/frame_000_125.png"
    bad.rename(bad.with_name("frame_bad.png"))

    with pytest.raises(contract.ContractError, match="invalid frame duration suffix"):
        contract.build_contract(resource_root, lps_path)


def test_validation_detects_missing_directory(tmp_path):
    resource_root, lps_path = _resource_fixture(tmp_path)
    expected = contract.build_contract(resource_root, lps_path)
    shutil.rmtree(resource_root / "Default")

    errors = contract.validate_contract(expected, resource_root, lps_path)

    assert "missing segment directory: Default/Happy/1" in errors


def test_validation_detects_wrong_stage_directory(tmp_path):
    resource_root, lps_path = _resource_fixture(tmp_path)
    _frames(resource_root, "IDEL/aside/Happy/A", [125])
    expected = contract.build_contract(resource_root, lps_path)
    source = resource_root / "IDEL/aside/Happy/A"
    source.rename(source.with_name("D"))

    errors = contract.validate_contract(expected, resource_root, lps_path)

    assert "missing segment directory: IDEL/aside/Happy/A" in errors
    assert "unexpected segment directory: IDEL/aside/Happy/D" in errors


def test_validation_detects_changed_frame_timing(tmp_path):
    resource_root, lps_path = _resource_fixture(tmp_path)
    expected = contract.build_contract(resource_root, lps_path)
    frame = resource_root / "Default/Happy/1/frame_000_125.png"
    frame.rename(frame.with_name("frame_000_500.png"))

    errors = contract.validate_contract(expected, resource_root, lps_path)

    assert any("wrong duration_ms" in error for error in errors)
    assert any("wrong frame_durations_ms" in error for error in errors)


def test_validation_detects_move_graph_missing(tmp_path):
    resource_root, lps_path = _resource_fixture(tmp_path)
    expected = contract.build_contract(resource_root, lps_path)
    shutil.rmtree(resource_root / "MOVE")

    errors = contract.validate_contract(expected, resource_root, lps_path)

    assert any("MOVE config references missing graph 'walk.left'" in error for error in errors)


def test_committed_contract_is_the_complete_current_action_baseline():
    saved = json.loads(
        (ROOT / "pet_host" / "vpet_action_contract.json").read_text(encoding="utf-8")
    )

    assert saved["schema_version"] == 1
    assert saved["segment_count"] == len(saved["segments"]) == 318
    assert saved["frame_count"] == sum(item["frame_count"] for item in saved["segments"]) == 2488
    assert saved["move_config_count"] == len(saved["move_configs"]) == 16
    assert {item["graph"] for item in saved["move_configs"]} == {
        "climb.left",
        "climb.right",
        "climb.top.left",
        "climb.top.right",
        "fall.left",
        "fall.right",
        "walk.left",
        "walk.right",
        "walk.left.faster",
        "walk.left.slow",
        "walk.right.faster",
        "walk.right.slow",
        "crawl.left",
        "crawl.right",
    }
