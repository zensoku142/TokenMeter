import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw

from scripts import build_vpet_rigged_pet as rigged
from scripts import calibrate_vpet_puppet_sheet as calibration

KEY = (255, 0, 255, 255)
CANVAS = (96, 96)
SCALE = 0.5
POSITIONS = {
    "back_hair": (4, 4),
    "torso": (36, 4),
    "tail": (68, 4),
    "head": (4, 36),
    "anatomical_left_arm": (36, 36),
    "anatomical_right_arm": (68, 36),
    "anatomical_left_leg": (20, 70),
    "anatomical_right_leg": (56, 70),
}
COLORS = (
    (30, 80, 180, 255),
    (240, 240, 225, 255),
    (20, 120, 150, 255),
    (245, 185, 150, 255),
    (180, 60, 30, 255),
    (35, 170, 70, 255),
    (250, 190, 35, 255),
    (125, 55, 180, 255),
)


def _sprite(index: int) -> Image.Image:
    image = Image.new("RGBA", (44, 36), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    color = COLORS[index]
    draw.polygon(((2, 2), (41, 3), (40, 33), (3, 34)), fill=color)
    accent = tuple(255 - channel for channel in color[:3]) + (255,)
    draw.rectangle((8 + index % 4, 9, 15 + index % 4, 24), fill=accent)
    draw.rectangle((29, 10 + index % 6, 34, 14 + index % 6), fill=accent)
    return calibration._crop_visible(image, f"sprite-{index}")


def _on_key(image: Image.Image) -> Image.Image:
    cell = Image.new("RGBA", CANVAS, KEY)
    cell.alpha_composite(image, ((CANVAS[0] - image.width) // 2, (CANVAS[1] - image.height) // 2))
    return cell.convert("RGB")


def _fixture_sheet(
    path: Path, *, unmatched_reference_mark: bool = False
) -> dict[str, tuple[float, ...]]:
    sprites = {layer_id: _sprite(index) for index, layer_id in enumerate(calibration.LAYER_IDS)}
    aligned: dict[str, Image.Image] = {}
    regions: dict[str, tuple[float, ...]] = {}
    for layer_id, sprite in sprites.items():
        patch = calibration._scaled_patch(sprite, SCALE)
        x, y = POSITIONS[layer_id]
        layer = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
        layer.alpha_composite(patch, (x, y))
        aligned[layer_id] = layer
        regions[layer_id] = (
            max(0.0, (x - 6) / CANVAS[0]),
            max(0.0, (y - 6) / CANVAS[1]),
            min(1.0, (x + patch.width + 6) / CANVAS[0]),
            min(1.0, (y + patch.height + 6) / CANVAS[1]),
        )

    reference = calibration._compose(aligned, CANVAS)
    if unmatched_reference_mark:
        ImageDraw.Draw(reference).rectangle((80, 8, 87, 15), fill=(10, 10, 10, 255))
    cells = [_on_key(sprites[layer_id]) for layer_id in calibration.LAYER_IDS]
    cells.append(_on_key(reference))
    sheet = Image.new("RGB", (CANVAS[0] * 3, CANVAS[1] * 3))
    for index, cell in enumerate(cells):
        sheet.paste(cell, ((index % 3) * CANVAS[0], (index // 3) * CANVAS[1]))
    sheet.save(path)
    return regions


def _hashes(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _calibrate(sheet: Path, output: Path, regions: dict[str, tuple[float, ...]]) -> dict:
    return calibration.calibrate_sheet(
        sheet,
        output,
        search_regions=regions,
        minimum_scale=0.4,
        maximum_scale=0.6,
        scale_step=0.1,
        chroma_key=(255, 0, 255),
        transparent_distance=10,
        opaque_distance=80,
        minimum_silhouette_iou=0.80,
        maximum_color_mae=0.10,
    )


def test_calibration_recovers_layers_and_is_byte_deterministic(tmp_path):
    sheet = tmp_path / "sheet.png"
    regions = _fixture_sheet(sheet)

    first_report = _calibrate(sheet, tmp_path / "first", regions)
    second_report = _calibrate(sheet, tmp_path / "second", regions)

    assert first_report["ok"] and second_report["ok"]
    assert first_report["metrics"]["silhouette_iou"] >= 0.80
    assert first_report["metrics"]["color_mae"] <= 0.10
    assert _hashes(tmp_path / "first") == _hashes(tmp_path / "second")
    for layer_id in calibration.LAYER_IDS:
        match = first_report["layers"][layer_id]
        assert abs(match["scale"] - SCALE) <= 0.06
        layer_path = tmp_path / "first" / "layers" / f"{layer_id}.png"
        with Image.open(layer_path) as image:
            assert image.mode == "RGBA"
            assert image.size == CANVAS
            assert [image.getpixel(point)[3] for point in ((0, 0), (95, 0), (0, 95), (95, 95))] == [
                0,
                0,
                0,
                0,
            ]


def test_calibrated_manifest_round_trips_through_rig_renderer(tmp_path):
    sheet = tmp_path / "sheet.png"
    regions = _fixture_sheet(sheet)
    output = tmp_path / "calibrated"
    _calibrate(sheet, output, regions)
    motion = output / "motion-catalog.json"
    motion.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tracks": {"neutral": {"frames": [{"duration": 125, "parts": {}}]}},
                "logical_entries": [
                    {
                        "id": "neutral",
                        "track": "neutral",
                        "output": "Default/Nomal/1",
                        "file_prefix": "neutral",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    rendered = rigged.render_catalog(output / "layer-manifest.json", motion, tmp_path / "rendered")[
        0
    ]
    with Image.open(rendered) as actual, Image.open(output / "neutral-composite.png") as expected:
        assert actual.size == expected.size == CANVAS
        difference = ImageChops.difference(actual.convert("RGBA"), expected.convert("RGBA"))
        assert max(channel[1] for channel in difference.getextrema()) <= 1


def test_failed_quality_gate_returns_nonzero_and_keeps_qa(tmp_path):
    sheet = tmp_path / "bad-sheet.png"
    regions = _fixture_sheet(sheet, unmatched_reference_mark=True)
    regions_path = tmp_path / "regions.json"
    regions_path.write_text(json.dumps(regions), encoding="utf-8")
    output = tmp_path / "failed"

    exit_code = calibration.main(
        [
            "--sheet",
            str(sheet),
            "--output-dir",
            str(output),
            "--search-regions",
            str(regions_path),
            "--min-scale",
            "0.4",
            "--max-scale",
            "0.6",
            "--scale-step",
            "0.1",
            "--chroma-key",
            "FF00FF",
            "--transparent-distance",
            "10",
            "--opaque-distance",
            "80",
            "--min-silhouette-iou",
            "0.99",
            "--max-color-mae",
            "0.001",
        ]
    )

    assert exit_code == 1
    report = json.loads((output / "qa-report.json").read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert (output / "neutral-diff.png").is_file()
    assert (output / "neutral-composite.png").is_file()


def test_aligned_layer_rejects_an_opaque_canvas_corner():
    patch = Image.new("RGBA", (4, 4), (20, 40, 80, 255))
    match = calibration.Match(1.0, 0, 0, 1.0, patch, (0.0, 0.0, 1.0, 1.0))

    with pytest.raises(calibration.CalibrationError, match="corners transparent"):
        calibration._aligned_layer(match, (20, 20), "corner-part")
