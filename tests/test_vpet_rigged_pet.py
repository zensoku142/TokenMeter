import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from scripts import build_vpet_rigged_pet as rigged


def _write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _layer(path: Path, size: tuple[int, int], color: tuple[int, int, int, int]) -> None:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((0, 0, size[0] - 1, size[1] - 1), fill=color)
    image.save(path)


def _manifest(
    tmp_path: Path,
    layers: list[dict[str, object]],
    *,
    width: int = 40,
    height: int = 40,
    anchor_x: int = 20,
    baseline_y: int = 32,
) -> Path:
    return _write_json(
        tmp_path / "layer-manifest.json",
        {
            "schema_version": 1,
            "canvas": {"width": width, "height": height},
            "neutral": {"anchor_x": anchor_x, "baseline_y": baseline_y, "scale": 1},
            "layers": layers,
        },
    )


def _catalog(
    tmp_path: Path,
    frames: list[dict[str, object]],
    entries: list[dict[str, object]] | None = None,
) -> Path:
    return _write_json(
        tmp_path / "motion-catalog.json",
        {
            "schema_version": 1,
            "tracks": {"motion": {"frames": frames}},
            "logical_entries": entries
            or [
                {
                    "id": "default",
                    "track": "motion",
                    "output": "Default/Nomal/1",
                    "file_prefix": "frame",
                }
            ],
        },
    )


def _alpha_bbox(path: Path) -> tuple[int, int, int, int]:
    with Image.open(path) as image:
        bounds = image.getchannel("A").getbbox()
    assert bounds is not None
    return bounds


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_rotation_uses_declared_pivot_as_the_fixed_joint(tmp_path):
    _layer(tmp_path / "arm.png", (3, 9), (240, 40, 30, 255))
    manifest = _manifest(
        tmp_path,
        [
            {
                "id": "arm",
                "path": "arm.png",
                "z": 0,
                "placement": {"x": -1.5, "y": -9},
                "pivot": {"x": 1.5, "y": 9},
            }
        ],
    )
    catalog = _catalog(
        tmp_path,
        [
            {"duration": 125, "parts": {}},
            {"duration": 125, "parts": {"arm": {"rotation": 90}}},
        ],
    )

    outputs = rigged.render_catalog(manifest, catalog, tmp_path / "rendered")
    upright = _alpha_bbox(outputs[0])
    rotated = _alpha_bbox(outputs[1])

    assert upright[3] == pytest.approx(32, abs=1)
    assert rotated[0] == pytest.approx(20, abs=1)
    assert upright[3] - upright[1] > upright[2] - upright[0]
    assert rotated[2] - rotated[0] > rotated[3] - rotated[1]


def test_root_rotation_moves_the_complete_puppet_about_the_foot_anchor(tmp_path):
    _layer(tmp_path / "body.png", (4, 12), (240, 40, 30, 255))
    _layer(tmp_path / "badge.png", (3, 3), (20, 70, 220, 255))
    manifest = _manifest(
        tmp_path,
        [
            {
                "id": "body",
                "path": "body.png",
                "z": 0,
                "placement": {"x": -2, "y": -12},
                "pivot": {"x": 2, "y": 12},
            },
            {
                "id": "badge",
                "path": "badge.png",
                "z": 1,
                "placement": {"x": 5, "y": -22},
                "pivot": {"x": 0, "y": 0},
            },
        ],
        width=70,
        height=70,
        anchor_x=30,
        baseline_y=50,
    )
    catalog = _catalog(
        tmp_path,
        [
            {"duration": 125, "parts": {}},
            {"duration": 125, "parts": {}, "root": {"rotation": 90}},
        ],
    )

    outputs = rigged.render_catalog(manifest, catalog, tmp_path / "rendered")
    upright = _alpha_bbox(outputs[0])
    rotated = _alpha_bbox(outputs[1])

    assert upright[3] == pytest.approx(50, abs=1)
    assert rotated[0] == pytest.approx(30, abs=1)
    assert rotated[2] - rotated[0] > upright[2] - upright[0]


def test_layers_keep_manifest_z_order_allow_motion_override_and_transparent_corners(tmp_path):
    _layer(tmp_path / "body.png", (8, 8), (20, 70, 220, 255))
    _layer(tmp_path / "badge.png", (4, 4), (240, 40, 30, 255))
    _layer(tmp_path / "spark.png", (2, 2), (30, 210, 90, 255))
    manifest = _manifest(
        tmp_path,
        [
            {
                "id": "badge",
                "path": "badge.png",
                "z": 2,
                "placement": {"x": -2, "y": -6},
                "pivot": {"x": 0, "y": 0},
            },
            {
                "id": "body",
                "path": "body.png",
                "z": 1,
                "placement": {"x": -4, "y": -8},
                "pivot": {"x": 0, "y": 0},
            },
            {
                "id": "spark",
                "path": "spark.png",
                "z": 4,
                "visible": False,
                "placement": {"x": -1, "y": -5},
                "pivot": {"x": 0, "y": 0},
            },
        ],
    )
    catalog = _catalog(
        tmp_path,
        [
            {"duration": 125, "parts": {}},
            {"duration": 125, "parts": {"body": {"z": 3}}},
            {"duration": 125, "parts": {"spark": {"visible": True}}},
        ],
    )

    outputs = rigged.render_catalog(manifest, catalog, tmp_path / "rendered")
    with Image.open(outputs[0]) as image:
        assert image.getpixel((20, 28))[:3] == (240, 40, 30)
        assert image.getpixel((17, 25))[:3] == (20, 70, 220)
        assert [image.getpixel(point)[3] for point in ((0, 0), (39, 0), (0, 39), (39, 39))] == [
            0,
            0,
            0,
            0,
        ]
    with Image.open(outputs[1]) as image:
        assert image.getpixel((20, 28))[:3] == (20, 70, 220)
    with Image.open(outputs[2]) as image:
        assert image.getpixel((20, 28))[:3] == (30, 210, 90)


def test_frame_names_track_duration_and_reuse_exact_pixels(tmp_path):
    _layer(tmp_path / "body.png", (6, 6), (30, 180, 90, 255))
    manifest = _manifest(
        tmp_path,
        [
            {
                "id": "body",
                "path": "body.png",
                "z": 0,
                "placement": {"x": -3, "y": -6},
                "pivot": {"x": 3, "y": 6},
            }
        ],
    )
    catalog = _catalog(
        tmp_path,
        [
            {"duration": 125, "parts": {}},
            {
                "duration": 250,
                "parts": {
                    "body": {
                        "translation": {"x": 1, "y": 0},
                        "rotation": 5,
                        "scale": 1.05,
                    }
                },
            },
        ],
        [
            {
                "id": "default",
                "track": "motion",
                "output": "Default/Nomal/1",
                "file_prefix": "idle",
            },
            {
                "id": "logical-reuse",
                "track": "motion",
                "output": "IDEL/reuse/Nomal/B",
                "file_prefix": "reuse",
            },
        ],
    )

    outputs = rigged.render_catalog(manifest, catalog, tmp_path / "rendered")
    assert {path.name for path in outputs} == {
        "idle_000_125.png",
        "idle_001_250.png",
        "reuse_000_125.png",
        "reuse_001_250.png",
    }
    by_name = {path.name: path for path in outputs}
    assert _sha256(by_name["idle_000_125.png"]) == _sha256(by_name["reuse_000_125.png"])
    assert _sha256(by_name["idle_001_250.png"]) == _sha256(by_name["reuse_001_250.png"])


def test_every_frame_keeps_fixed_canvas_and_shared_foot_baseline(tmp_path):
    _layer(tmp_path / "body.png", (8, 10), (50, 90, 210, 255))
    _layer(tmp_path / "arm.png", (2, 7), (240, 150, 20, 255))
    manifest = _manifest(
        tmp_path,
        [
            {
                "id": "body",
                "path": "body.png",
                "z": 0,
                "placement": {"x": -4, "y": -10},
                "pivot": {"x": 4, "y": 10},
            },
            {
                "id": "arm",
                "path": "arm.png",
                "z": 1,
                "placement": {"x": 2, "y": -9},
                "pivot": {"x": 1, "y": 6},
            },
        ],
    )
    catalog = _catalog(
        tmp_path,
        [
            {"duration": 125, "parts": {}},
            {"duration": 125, "parts": {"arm": {"rotation": -35, "scale": 1.2}}},
        ],
    )

    outputs = rigged.render_catalog(manifest, catalog, tmp_path / "rendered")
    bounds = []
    for output in outputs:
        with Image.open(output) as image:
            assert image.mode == "RGBA"
            assert image.size == (40, 40)
            bounds.append(image.getchannel("A").getbbox())
    assert bounds[0] is not None and bounds[1] is not None
    assert bounds[0][3] == bounds[1][3] == 32


def test_nontransparent_pixels_outside_canvas_fail_without_partial_output(tmp_path):
    _layer(tmp_path / "body.png", (8, 8), (30, 160, 80, 255))
    manifest = _manifest(
        tmp_path,
        [
            {
                "id": "body",
                "path": "body.png",
                "z": 0,
                "placement": {"x": 6, "y": -4},
                "pivot": {"x": 0, "y": 0},
            }
        ],
        width=20,
        height=20,
        anchor_x=16,
        baseline_y=10,
    )
    catalog = _catalog(tmp_path, [{"duration": 125, "parts": {}}])
    output = tmp_path / "rendered"

    with pytest.raises(
        rigged.RigError,
        match=r"track motion frame 0: layer body exceeds the canvas",
    ):
        rigged.render_catalog(manifest, catalog, output)
    assert not output.exists()


def test_repeated_runs_produce_identical_png_sha256(tmp_path):
    _layer(tmp_path / "body.png", (7, 9), (110, 50, 210, 255))
    manifest = _manifest(
        tmp_path,
        [
            {
                "id": "body",
                "path": "body.png",
                "z": 0,
                "placement": {"x": -3.5, "y": -9},
                "pivot": {"x": 3.5, "y": 9},
            }
        ],
    )
    catalog = _catalog(
        tmp_path,
        [
            {"duration": 125, "parts": {"body": {"rotation": -12}}},
            {"duration": 375, "parts": {"body": {"rotation": 12}}},
        ],
    )

    first = rigged.render_catalog(manifest, catalog, tmp_path / "first")
    second = rigged.render_catalog(manifest, catalog, tmp_path / "second")
    first_hashes = [_sha256(path) for path in first]
    second_hashes = [_sha256(path) for path in second]
    assert [path.relative_to(tmp_path / "first") for path in first] == [
        path.relative_to(tmp_path / "second") for path in second
    ]
    assert first_hashes == second_hashes
