"""Calibrate a 3x3 puppet layer sheet against its assembled reference cell.

Cells are fixed row-major: eight named parts followed by the assembled reference. Optional
search-region JSON maps a part id to normalized ``[left, top, right, bottom]`` bounds and
constrains the center of that part during template matching.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

LAYER_IDS = (
    "back_hair",
    "torso",
    "tail",
    "head",
    "anatomical_left_arm",
    "anatomical_right_arm",
    "anatomical_left_leg",
    "anatomical_right_leg",
)

# Back-to-front order for the neutral paper puppet. The calibrated PNGs already contain
# their fixed scale and placement; z remains useful when the rig later moves individual parts.
LAYER_Z = {
    "back_hair": 0,
    "tail": 1,
    "anatomical_left_leg": 2,
    "anatomical_right_leg": 3,
    "torso": 4,
    "anatomical_left_arm": 5,
    "anatomical_right_arm": 6,
    "head": 7,
}


class CalibrationError(ValueError):
    """Raised when the sheet or requested calibration is structurally invalid."""


class QualityGateError(CalibrationError):
    """Raised after QA artifacts are saved when the calibrated composite misses a gate."""

    def __init__(self, report_path: Path):
        super().__init__(f"calibration quality gates failed; see {report_path}")
        self.report_path = report_path


@dataclass(frozen=True)
class Match:
    scale: float
    x: int
    y: int
    score: float
    patch: Image.Image
    region: tuple[float, float, float, float]


def _png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()


def _write_png(path: Path, image: Image.Image) -> None:
    path.write_bytes(_png_bytes(image))


def _split_sheet(sheet: Image.Image) -> tuple[list[Image.Image], tuple[int, int]]:
    if sheet.width % 3 or sheet.height % 3:
        raise CalibrationError("sheet width and height must both be divisible by 3")
    cell_width = sheet.width // 3
    cell_height = sheet.height // 3
    cells = [
        sheet.crop(
            (
                column * cell_width,
                row * cell_height,
                (column + 1) * cell_width,
                (row + 1) * cell_height,
            )
        ).convert("RGB")
        for row in range(3)
        for column in range(3)
    ]
    return cells, (cell_width, cell_height)


def _infer_chroma_key(cells: list[Image.Image]) -> tuple[int, int, int]:
    samples: list[np.ndarray] = []
    for cell in cells:
        pixels = np.asarray(cell, dtype=np.uint8)
        margin = max(2, min(cell.size) // 24)
        samples.extend(
            (
                pixels[:margin, :margin],
                pixels[:margin, -margin:],
                pixels[-margin:, :margin],
                pixels[-margin:, -margin:],
            )
        )
    median = np.median(np.concatenate([item.reshape(-1, 3) for item in samples]), axis=0)
    return tuple(int(round(value)) for value in median)


def _remove_chroma(
    image: Image.Image,
    key: tuple[int, int, int],
    transparent_distance: float,
    opaque_distance: float,
) -> Image.Image:
    if transparent_distance < 0 or opaque_distance <= transparent_distance:
        raise CalibrationError("chroma distances must satisfy 0 <= transparent < opaque")
    rgb = np.asarray(image.convert("RGB"), dtype=np.float64)
    key_array = np.asarray(key, dtype=np.float64)
    key_unit = key_array / max(np.linalg.norm(key_array), 1.0)
    norms = np.linalg.norm(rgb, axis=2, keepdims=True)
    direction = rgb / np.maximum(norms, 1.0)
    distance = np.linalg.norm(direction - key_unit, axis=2) * 255.0
    alpha = np.clip(
        (distance - transparent_distance) / (opaque_distance - transparent_distance), 0.0, 1.0
    )

    # Generated sheets often contain darker magenta grid seams. They remain the same key hue
    # even when Euclidean distance from the corner median is large, so remove them explicitly.
    red, green, blue = np.moveaxis(rgb, -1, 0)
    key_like = (
        (np.minimum(red, blue) >= 100)
        & (np.minimum(red, blue) - green >= 60)
        & (np.abs(red - blue) <= 50)
    )
    alpha[key_like] = 0.0

    # Undo the key contribution on antialiased edge pixels before clearing hidden RGB.
    safe_alpha = np.maximum(alpha[..., None], 1.0 / 255.0)
    foreground = (rgb - (1.0 - alpha[..., None]) * key_array) / safe_alpha
    foreground = np.clip(np.rint(foreground), 0, 255).astype(np.uint8)
    alpha_u8 = np.clip(np.rint(alpha * 255.0), 0, 255).astype(np.uint8)
    foreground[alpha_u8 == 0] = 0
    return Image.fromarray(np.dstack((foreground, alpha_u8)), "RGBA")


def _crop_visible(image: Image.Image, label: str) -> Image.Image:
    bounds = image.getchannel("A").getbbox()
    if bounds is None:
        raise CalibrationError(f"{label} is fully transparent after chroma removal")
    return image.crop(bounds)


def _scaled_patch(image: Image.Image, scale: float) -> Image.Image:
    if scale <= 0:
        raise CalibrationError("scale must be greater than zero")
    padding = 3
    width = max(1, math.ceil(image.width * scale) + padding * 2)
    height = max(1, math.ceil(image.height * scale) + padding * 2)
    transformed = image.transform(
        (width, height),
        Image.Transform.AFFINE,
        (1.0 / scale, 0.0, -padding / scale, 0.0, 1.0 / scale, -padding / scale),
        resample=Image.Resampling.BICUBIC,
        fillcolor=(0, 0, 0, 0),
    )
    bounds = transformed.getchannel("A").getbbox()
    if bounds is None:
        raise CalibrationError("scaled layer became fully transparent")
    return transformed.crop(bounds)


def _fft_correlate_valid(image: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    image_height, image_width = image.shape
    kernel_height, kernel_width = kernel.shape
    shape = (image_height + kernel_height - 1, image_width + kernel_width - 1)
    product = np.fft.rfftn(image, s=shape, axes=(0, 1)) * np.fft.rfftn(
        kernel[::-1, ::-1], s=shape, axes=(0, 1)
    )
    convolution = np.fft.irfftn(product, s=shape, axes=(0, 1))
    return convolution[
        kernel_height - 1 : image_height,
        kernel_width - 1 : image_width,
    ]


def _fft_correlate_valid_channels(image: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    image_height, image_width, _ = image.shape
    kernel_height, kernel_width, _ = kernel.shape
    shape = (image_height + kernel_height - 1, image_width + kernel_width - 1)
    image_fft = np.fft.rfftn(image, s=shape, axes=(0, 1))
    kernel_fft = np.fft.rfftn(kernel[::-1, ::-1], s=shape, axes=(0, 1))
    convolution = np.fft.irfftn(np.sum(image_fft * kernel_fft, axis=2), s=shape, axes=(0, 1))
    return convolution[
        kernel_height - 1 : image_height,
        kernel_width - 1 : image_width,
    ]


def _placement_scores(reference: np.ndarray, patch: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    template = np.asarray(patch, dtype=np.float64) / 255.0
    template_alpha = template[..., 3]
    weight = float(template_alpha.sum())
    if weight <= 0:
        raise CalibrationError("template has no visible pixels")

    reference_alpha = reference[..., 3]
    reference_rgb = reference[..., :3] * reference_alpha[..., None]
    template_rgb = template[..., :3]
    overlap = _fft_correlate_valid(reference_alpha, template_alpha)
    coverage = overlap / weight
    target_squared = _fft_correlate_valid(
        np.sum(reference_rgb * reference_rgb, axis=2), template_alpha
    )
    cross = _fft_correlate_valid_channels(reference_rgb, template_rgb * template_alpha[..., None])
    template_squared = float(np.sum(template_alpha[..., None] * template_rgb * template_rgb))
    color_mse = np.maximum(target_squared + template_squared - 2.0 * cross, 0.0) / (3.0 * weight)
    template_mean = np.sum(template_alpha[..., None] * template_rgb, axis=(0, 1)) / weight
    template_centered = (template_rgb - template_mean) * template_alpha[..., None]
    centered_cross = _fft_correlate_valid_channels(reference_rgb, template_centered)
    target_sum = np.stack(
        [_fft_correlate_valid(reference_rgb[..., channel], template_alpha) for channel in range(3)],
        axis=2,
    )
    target_energy = np.maximum(
        target_squared - np.sum(target_sum * target_sum, axis=2) / weight, 0.0
    )
    template_energy = float(np.sum(template_alpha[..., None] * (template_rgb - template_mean) ** 2))
    denominator = np.sqrt(template_energy * target_energy)
    normalized_correlation = np.divide(
        centered_cross,
        denominator,
        out=np.zeros_like(centered_cross),
        where=denominator > 1e-12,
    )
    # MSE locates the right color family; centered correlation prevents a smaller crop from
    # winning merely because it fits inside a correctly colored part.
    return coverage - 1.5 * color_mse + 0.75 * normalized_correlation, overlap


def _scale_grid(minimum: float, maximum: float, step: float) -> tuple[float, ...]:
    if minimum <= 0 or maximum < minimum or step <= 0:
        raise CalibrationError("scale range must satisfy 0 < min <= max and step > 0")
    count = int(math.floor((maximum - minimum) / step + 1e-9))
    values = [minimum + index * step for index in range(count + 1)]
    if values[-1] < maximum - 1e-9:
        values.append(maximum)
    return tuple(sorted({round(value, 10) for value in values}))


def _best_match(
    layer: Image.Image,
    reference: Image.Image,
    scales: tuple[float, ...],
    region: tuple[float, float, float, float],
) -> Match:
    target = np.asarray(reference, dtype=np.float64) / 255.0
    target_bounds = reference.getchannel("A").getbbox()
    if target_bounds is None:
        raise CalibrationError("assembled reference is fully transparent")
    target_center = (
        (target_bounds[0] + target_bounds[2]) / 2,
        (target_bounds[1] + target_bounds[3]) / 2,
    )
    best: Match | None = None
    best_key: tuple[float, float, float, int, int] | None = None
    for scale in scales:
        patch = _scaled_patch(layer, scale)
        if patch.width > reference.width or patch.height > reference.height:
            continue
        scores, overlap = _placement_scores(target, patch)
        y_positions, x_positions = np.indices(scores.shape)
        center_x = x_positions + patch.width / 2
        center_y = y_positions + patch.height / 2
        allowed = (
            (center_x >= region[0] * reference.width)
            & (center_x <= region[2] * reference.width)
            & (center_y >= region[1] * reference.height)
            & (center_y <= region[3] * reference.height)
        )
        if region != (0.0, 0.0, 1.0, 1.0):
            region_left = max(0, math.floor(region[0] * reference.width))
            region_top = max(0, math.floor(region[1] * reference.height))
            region_right = min(reference.width, math.ceil(region[2] * reference.width))
            region_bottom = min(reference.height, math.ceil(region[3] * reference.height))
            region_alpha = float(
                target[region_top:region_bottom, region_left:region_right, 3].sum()
            )
            if region_alpha > 0:
                # Optional anatomical regions make the score symmetric: the candidate must
                # explain the reference support in that region, not merely fit inside it.
                scores += 1.5 * np.minimum(overlap / region_alpha, 1.0)
        scores = np.where(allowed, np.round(scores, 10), -np.inf)
        flat_index = int(np.argmax(scores))
        score = float(scores.flat[flat_index])
        if not math.isfinite(score):
            continue
        y, x = np.unravel_index(flat_index, scores.shape)
        distance = abs(x + patch.width / 2 - target_center[0]) + abs(
            y + patch.height / 2 - target_center[1]
        )
        key = (score, -abs(scale - 1.0), -distance, -int(y), -int(x))
        if best_key is None or key > best_key:
            best_key = key
            best = Match(scale, int(x), int(y), score, patch, region)
    if best is None:
        raise CalibrationError("no scale and placement fit inside the requested search region")
    return best


def _match_layer(
    layer: Image.Image,
    reference: Image.Image,
    minimum_scale: float,
    maximum_scale: float,
    scale_step: float,
    region: tuple[float, float, float, float],
) -> Match:
    coarse = _best_match(
        layer, reference, _scale_grid(minimum_scale, maximum_scale, scale_step), region
    )
    fine_step = scale_step / 5.0
    fine_minimum = max(minimum_scale, coarse.scale - scale_step)
    fine_maximum = min(maximum_scale, coarse.scale + scale_step)
    return _best_match(layer, reference, _scale_grid(fine_minimum, fine_maximum, fine_step), region)


def _aligned_layer(match: Match, canvas_size: tuple[int, int], layer_id: str) -> Image.Image:
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    if (
        match.x < 0
        or match.y < 0
        or match.x + match.patch.width > canvas.width
        or match.y + match.patch.height > canvas.height
    ):
        raise CalibrationError(f"{layer_id} placement would crop outside the canvas")
    canvas.alpha_composite(match.patch, (match.x, match.y))
    corners = (
        canvas.getpixel((0, 0))[3],
        canvas.getpixel((canvas.width - 1, 0))[3],
        canvas.getpixel((0, canvas.height - 1))[3],
        canvas.getpixel((canvas.width - 1, canvas.height - 1))[3],
    )
    if any(corners):
        raise CalibrationError(f"{layer_id} does not keep all four canvas corners transparent")
    return canvas


def _compose(layers: dict[str, Image.Image], canvas_size: tuple[int, int]) -> Image.Image:
    composite = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    for layer_id in sorted(LAYER_IDS, key=LAYER_Z.__getitem__):
        composite.alpha_composite(layers[layer_id])
    return composite


def _quality_metrics(reference: Image.Image, composite: Image.Image) -> dict[str, float]:
    reference_array = np.asarray(reference, dtype=np.float64) / 255.0
    composite_array = np.asarray(composite, dtype=np.float64) / 255.0
    reference_mask = reference_array[..., 3] > 1.0 / 255.0
    composite_mask = composite_array[..., 3] > 1.0 / 255.0
    intersection = reference_mask & composite_mask
    union = reference_mask | composite_mask
    reference_area = int(reference_mask.sum())
    composite_area = int(composite_mask.sum())
    intersection_area = int(intersection.sum())
    union_area = int(union.sum())
    if not reference_area or not composite_area or not union_area:
        raise CalibrationError("reference and composite must both contain visible pixels")

    reference_premultiplied = reference_array[..., :3] * reference_array[..., 3, None]
    composite_premultiplied = composite_array[..., :3] * composite_array[..., 3, None]
    color_error = np.abs(reference_premultiplied - composite_premultiplied).mean(axis=2)
    alpha_error = np.abs(reference_array[..., 3] - composite_array[..., 3])
    return {
        "silhouette_iou": intersection_area / union_area,
        "reference_coverage": intersection_area / reference_area,
        "composite_coverage": intersection_area / composite_area,
        "color_mae": float(color_error[union].mean()),
        "alpha_mae": float(alpha_error[union].mean()),
    }


def _difference_image(reference: Image.Image, composite: Image.Image) -> Image.Image:
    reference_array = np.asarray(reference, dtype=np.float64) / 255.0
    composite_array = np.asarray(composite, dtype=np.float64) / 255.0
    reference_premultiplied = reference_array[..., :3] * reference_array[..., 3, None]
    composite_premultiplied = composite_array[..., :3] * composite_array[..., 3, None]
    color = np.abs(reference_premultiplied - composite_premultiplied).max(axis=2)
    alpha = np.abs(reference_array[..., 3] - composite_array[..., 3])
    intensity = np.maximum(color, alpha)
    pixels = np.zeros((*intensity.shape, 4), dtype=np.uint8)
    pixels[..., 0] = np.rint(color * 255).astype(np.uint8)
    pixels[..., 1] = np.rint(alpha * 255).astype(np.uint8)
    pixels[..., 3] = np.rint(intensity * 255).astype(np.uint8)
    return Image.fromarray(pixels, "RGBA")


def _load_search_regions(path: Path | None) -> dict[str, tuple[float, float, float, float]]:
    if path is None:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"cannot read search regions: {exc}") from exc
    if not isinstance(raw, dict):
        raise CalibrationError("search regions must be an object")
    unknown = raw.keys() - set(LAYER_IDS)
    if unknown:
        raise CalibrationError(f"unknown search region parts: {', '.join(sorted(unknown))}")
    regions: dict[str, tuple[float, float, float, float]] = {}
    for layer_id, value in raw.items():
        if (
            not isinstance(value, list)
            or len(value) != 4
            or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value)
        ):
            raise CalibrationError(f"search region {layer_id} must be [left, top, right, bottom]")
        region = tuple(float(item) for item in value)
        if not (0 <= region[0] < region[2] <= 1 and 0 <= region[1] < region[3] <= 1):
            raise CalibrationError(f"search region {layer_id} must use normalized coordinates")
        regions[layer_id] = region
    return regions


def calibrate_sheet(
    sheet_path: Path,
    output_dir: Path,
    *,
    search_regions: dict[str, tuple[float, float, float, float]] | None = None,
    minimum_scale: float = 0.15,
    maximum_scale: float = 1.25,
    scale_step: float = 0.025,
    chroma_key: tuple[int, int, int] | None = None,
    transparent_distance: float = 30.0,
    opaque_distance: float = 100.0,
    minimum_silhouette_iou: float = 0.75,
    maximum_color_mae: float = 0.18,
) -> dict[str, Any]:
    """Calibrate one sheet into a new output directory and enforce final QA gates."""

    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise CalibrationError(f"output directory already exists: {output_dir}")
    if not 0 <= minimum_silhouette_iou <= 1 or not 0 <= maximum_color_mae <= 1:
        raise CalibrationError("quality thresholds must be between zero and one")
    try:
        with Image.open(sheet_path) as opened:
            cells, canvas_size = _split_sheet(opened.convert("RGB"))
    except OSError as exc:
        raise CalibrationError(f"cannot load sheet {sheet_path}: {exc}") from exc
    key = chroma_key or _infer_chroma_key(cells)
    processed = [_remove_chroma(cell, key, transparent_distance, opaque_distance) for cell in cells]
    reference = processed[8]
    if any(
        reference.getpixel(point)[3]
        for point in (
            (0, 0),
            (reference.width - 1, 0),
            (0, reference.height - 1),
            (reference.width - 1, reference.height - 1),
        )
    ):
        raise CalibrationError("assembled reference must keep all four canvas corners transparent")

    regions = search_regions or {}
    matches: dict[str, Match] = {}
    aligned: dict[str, Image.Image] = {}
    for index, layer_id in enumerate(LAYER_IDS):
        layer = _crop_visible(processed[index], layer_id)
        match = _match_layer(
            layer,
            reference,
            minimum_scale,
            maximum_scale,
            scale_step,
            regions.get(layer_id, (0.0, 0.0, 1.0, 1.0)),
        )
        matches[layer_id] = match
        aligned[layer_id] = _aligned_layer(match, canvas_size, layer_id)

    composite = _compose(aligned, canvas_size)
    metrics = _quality_metrics(reference, composite)
    failures: list[str] = []
    if metrics["silhouette_iou"] < minimum_silhouette_iou:
        failures.append("silhouette_iou")
    if metrics["color_mae"] > maximum_color_mae:
        failures.append("color_mae")
    quality_ok = not failures
    reference_bounds = reference.getchannel("A").getbbox()
    assert reference_bounds is not None
    anchor_x = (reference_bounds[0] + reference_bounds[2]) / 2
    baseline_y = reference_bounds[3]
    manifest = {
        "schema_version": 1,
        "canvas": {"width": canvas_size[0], "height": canvas_size[1]},
        "neutral": {"anchor_x": anchor_x, "baseline_y": baseline_y, "scale": 1},
        "layers": [],
    }
    for layer_id in sorted(LAYER_IDS, key=LAYER_Z.__getitem__):
        bounds = aligned[layer_id].getchannel("A").getbbox()
        assert bounds is not None
        manifest["layers"].append(
            {
                "id": layer_id,
                "path": f"layers/{layer_id}.png",
                "z": LAYER_Z[layer_id],
                "placement": {"x": -anchor_x, "y": -baseline_y},
                # Calibration cannot infer anatomy; bbox center is a deterministic editable pivot.
                "pivot": {
                    "x": (bounds[0] + bounds[2]) / 2,
                    "y": (bounds[1] + bounds[3]) / 2,
                },
            }
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "ok": quality_ok,
        "canvas": {"width": canvas_size[0], "height": canvas_size[1]},
        "chroma_key": {"r": key[0], "g": key[1], "b": key[2]},
        "thresholds": {
            "minimum_silhouette_iou": minimum_silhouette_iou,
            "maximum_color_mae": maximum_color_mae,
        },
        "metrics": metrics,
        "failed_gates": failures,
        "layers": {
            layer_id: {
                "scale": matches[layer_id].scale,
                "placement": {"x": matches[layer_id].x, "y": matches[layer_id].y},
                "score": matches[layer_id].score,
                "search_region": list(matches[layer_id].region),
            }
            for layer_id in LAYER_IDS
        },
    }

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_dir.name}-", dir=output_dir.parent
    ) as temporary:
        payload = Path(temporary) / "payload"
        layers_dir = payload / "layers"
        layers_dir.mkdir(parents=True)
        for layer_id in LAYER_IDS:
            _write_png(layers_dir / f"{layer_id}.png", aligned[layer_id])
        _write_png(payload / "assembled-reference.png", reference)
        _write_png(payload / "neutral-composite.png", composite)
        _write_png(payload / "neutral-diff.png", _difference_image(reference, composite))
        (payload / "layer-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (payload / "qa-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        payload.replace(output_dir)
    if not quality_ok:
        raise QualityGateError(output_dir / "qa-report.json")
    return report


def _parse_chroma_key(value: str) -> tuple[int, int, int]:
    raw = value.removeprefix("#")
    if len(raw) != 6:
        raise argparse.ArgumentTypeError("chroma key must be RRGGBB")
    try:
        return tuple(int(raw[index : index + 2], 16) for index in (0, 2, 4))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("chroma key must be RRGGBB") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--search-regions",
        type=Path,
        help="JSON mapping part ids to normalized [left, top, right, bottom] bounds",
    )
    parser.add_argument("--min-scale", type=float, default=0.15)
    parser.add_argument("--max-scale", type=float, default=1.25)
    parser.add_argument("--scale-step", type=float, default=0.025)
    parser.add_argument("--chroma-key", type=_parse_chroma_key)
    parser.add_argument("--transparent-distance", type=float, default=30.0)
    parser.add_argument("--opaque-distance", type=float, default=100.0)
    parser.add_argument("--min-silhouette-iou", type=float, default=0.75)
    parser.add_argument("--max-color-mae", type=float, default=0.18)
    args = parser.parse_args(argv)
    try:
        report = calibrate_sheet(
            args.sheet,
            args.output_dir,
            search_regions=_load_search_regions(args.search_regions),
            minimum_scale=args.min_scale,
            maximum_scale=args.max_scale,
            scale_step=args.scale_step,
            chroma_key=args.chroma_key,
            transparent_distance=args.transparent_distance,
            opaque_distance=args.opaque_distance,
            minimum_silhouette_iou=args.min_silhouette_iou,
            maximum_color_mae=args.max_color_mae,
        )
    except QualityGateError as exc:
        print(str(exc))
        return 1
    except CalibrationError as exc:
        print(f"calibration failed: {exc}")
        return 1
    print(json.dumps({"output_dir": str(args.output_dir.resolve()), "metrics": report["metrics"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
