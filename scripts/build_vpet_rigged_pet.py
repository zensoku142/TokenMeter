"""Render deterministic layered VPet frames from a neutral rig and motion catalog.

Layer coordinates are expressed relative to the rig anchor and foot baseline. Positive
rotation is clockwise in screen coordinates. Every logical entry referencing the same
track receives the exact same encoded frame bytes under its own output path.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from PIL import Image  # pyright: ignore[reportMissingImports]


class RigError(ValueError):
    """Raised when a rig, motion, or rendered frame violates the asset contract."""


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class NeutralPlacement:
    anchor_x: float
    baseline_y: float
    scale: float


@dataclass(frozen=True)
class Layer:
    id: str
    z: int
    visible: bool
    placement: Point
    pivot: Point
    image: Image.Image


@dataclass(frozen=True)
class Rig:
    width: int
    height: int
    neutral: NeutralPlacement
    layers: tuple[Layer, ...]


@dataclass(frozen=True)
class PartTransform:
    translation: Point = Point(0.0, 0.0)
    rotation: float = 0.0
    scale: float = 1.0
    z: int | None = None
    visible: bool | None = None


@dataclass(frozen=True)
class MotionFrame:
    duration: int
    parts: dict[str, PartTransform]
    root: PartTransform = PartTransform()


@dataclass(frozen=True)
class LogicalEntry:
    id: str
    track: str
    output_parts: tuple[str, ...]
    file_prefix: str


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RigError(f"Cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RigError(f"{path} must contain a JSON object")
    return value


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RigError(f"{label} must be an object")
    return value


def _expect_keys(
    value: dict[str, Any], *, required: set[str], optional: set[str] = set(), label: str
) -> None:
    missing = required - value.keys()
    unknown = value.keys() - required - optional
    if missing:
        raise RigError(f"{label} is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise RigError(f"{label} has unknown keys: {', '.join(sorted(unknown))}")


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RigError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise RigError(f"{label} must be a finite number")
    return result


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RigError(f"{label} must be a positive integer")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RigError(f"{label} must be a non-empty string")
    return value


def _point(value: Any, label: str) -> Point:
    data = _mapping(value, label)
    _expect_keys(data, required={"x", "y"}, label=label)
    return Point(_number(data["x"], f"{label}.x"), _number(data["y"], f"{label}.y"))


def _relative_parts(value: Any, label: str) -> tuple[str, ...]:
    raw = _text(value, label).replace("\\", "/")
    posix = PurePosixPath(raw)
    windows = PureWindowsPath(raw)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise RigError(f"{label} must be a relative path")
    if not posix.parts or any(part in {"", ".", ".."} for part in posix.parts):
        raise RigError(f"{label} contains an unsafe path segment")
    return tuple(posix.parts)


def _load_rig(path: Path) -> Rig:
    data = _read_json(path)
    _expect_keys(
        data,
        required={"schema_version", "canvas", "neutral", "layers"},
        label="layer manifest",
    )
    if data["schema_version"] != 1:
        raise RigError("layer manifest schema_version must be 1")

    canvas = _mapping(data["canvas"], "canvas")
    _expect_keys(canvas, required={"width", "height"}, label="canvas")
    width = _positive_int(canvas["width"], "canvas.width")
    height = _positive_int(canvas["height"], "canvas.height")

    neutral_data = _mapping(data["neutral"], "neutral")
    _expect_keys(neutral_data, required={"anchor_x", "baseline_y", "scale"}, label="neutral")
    neutral = NeutralPlacement(
        _number(neutral_data["anchor_x"], "neutral.anchor_x"),
        _number(neutral_data["baseline_y"], "neutral.baseline_y"),
        _number(neutral_data["scale"], "neutral.scale"),
    )
    if neutral.scale <= 0:
        raise RigError("neutral.scale must be greater than zero")
    if not 0 <= neutral.anchor_x <= width or not 0 <= neutral.baseline_y <= height:
        raise RigError("neutral anchor and baseline must be inside the canvas")

    layer_values = data["layers"]
    if not isinstance(layer_values, list) or not layer_values:
        raise RigError("layers must be a non-empty array")

    base = path.resolve().parent
    seen_ids: set[str] = set()
    seen_z: set[int] = set()
    layers: list[Layer] = []
    for index, raw_layer in enumerate(layer_values):
        label = f"layers[{index}]"
        layer_data = _mapping(raw_layer, label)
        _expect_keys(
            layer_data,
            required={"id", "path", "z", "placement", "pivot"},
            optional={"visible"},
            label=label,
        )
        layer_id = _text(layer_data["id"], f"{label}.id")
        if layer_id in seen_ids:
            raise RigError(f"duplicate layer id: {layer_id}")
        seen_ids.add(layer_id)

        z = layer_data["z"]
        if isinstance(z, bool) or not isinstance(z, int):
            raise RigError(f"{label}.z must be an integer")
        if z in seen_z:
            raise RigError(f"duplicate z-order: {z}")
        seen_z.add(z)
        visible = layer_data.get("visible", True)
        if not isinstance(visible, bool):
            raise RigError(f"{label}.visible must be a boolean")

        source_parts = _relative_parts(layer_data["path"], f"{label}.path")
        source = base.joinpath(*source_parts).resolve()
        if not source.is_relative_to(base) or source.suffix.lower() != ".png":
            raise RigError(f"{label}.path must reference a PNG beside the manifest")
        try:
            with Image.open(source) as opened:
                if opened.format != "PNG":
                    raise RigError(f"{source} is not a PNG")
                image = opened.convert("RGBA")
                image.load()
        except OSError as exc:
            raise RigError(f"Cannot load layer {source}: {exc}") from exc
        if image.getchannel("A").getbbox() is None:
            raise RigError(f"layer {layer_id} is fully transparent")

        pivot = _point(layer_data["pivot"], f"{label}.pivot")
        if not 0 <= pivot.x <= image.width or not 0 <= pivot.y <= image.height:
            raise RigError(f"{label}.pivot must be inside the layer bounds")
        layers.append(
            Layer(
                layer_id,
                z,
                visible,
                _point(layer_data["placement"], f"{label}.placement"),
                pivot,
                image,
            )
        )

    return Rig(width, height, neutral, tuple(sorted(layers, key=lambda item: item.z)))


def _part_transform(value: Any, label: str) -> PartTransform:
    data = _mapping(value, label)
    _expect_keys(
        data,
        required=set(),
        optional={"translation", "rotation", "scale", "z", "visible"},
        label=label,
    )
    translation = (
        _point(data["translation"], f"{label}.translation")
        if "translation" in data
        else Point(0.0, 0.0)
    )
    rotation = _number(data.get("rotation", 0), f"{label}.rotation")
    scale = _number(data.get("scale", 1), f"{label}.scale")
    if scale <= 0:
        raise RigError(f"{label}.scale must be greater than zero")
    z = data.get("z")
    if z is not None and (isinstance(z, bool) or not isinstance(z, int)):
        raise RigError(f"{label}.z must be an integer")
    visible = data.get("visible")
    if visible is not None and not isinstance(visible, bool):
        raise RigError(f"{label}.visible must be a boolean")
    return PartTransform(translation, rotation, scale, z, visible)


def _load_motion_catalog(
    path: Path, layer_ids: set[str]
) -> tuple[dict[str, tuple[MotionFrame, ...]], tuple[LogicalEntry, ...]]:
    data = _read_json(path)
    _expect_keys(
        data,
        required={"schema_version", "tracks", "logical_entries"},
        label="motion catalog",
    )
    if data["schema_version"] != 1:
        raise RigError("motion catalog schema_version must be 1")

    tracks_data = _mapping(data["tracks"], "tracks")
    if not tracks_data:
        raise RigError("tracks must not be empty")
    tracks: dict[str, tuple[MotionFrame, ...]] = {}
    for track_id, raw_track in tracks_data.items():
        _text(track_id, "track id")
        track_data = _mapping(raw_track, f"tracks.{track_id}")
        _expect_keys(track_data, required={"frames"}, label=f"tracks.{track_id}")
        raw_frames = track_data["frames"]
        if not isinstance(raw_frames, list) or not raw_frames:
            raise RigError(f"tracks.{track_id}.frames must be a non-empty array")
        frames: list[MotionFrame] = []
        for index, raw_frame in enumerate(raw_frames):
            label = f"tracks.{track_id}.frames[{index}]"
            frame_data = _mapping(raw_frame, label)
            _expect_keys(
                frame_data,
                required={"duration", "parts"},
                optional={"root"},
                label=label,
            )
            parts_data = _mapping(frame_data["parts"], f"{label}.parts")
            unknown_parts = parts_data.keys() - layer_ids
            if unknown_parts:
                raise RigError(
                    f"{label} references unknown parts: {', '.join(sorted(unknown_parts))}"
                )
            root = _part_transform(frame_data.get("root", {}), f"{label}.root")
            if root.z is not None or root.visible is not None:
                raise RigError(f"{label}.root only supports translation, rotation, and scale")
            frames.append(
                MotionFrame(
                    _positive_int(frame_data["duration"], f"{label}.duration"),
                    {
                        part_id: _part_transform(value, f"{label}.parts.{part_id}")
                        for part_id, value in parts_data.items()
                    },
                    root,
                )
            )
        tracks[track_id] = tuple(frames)

    raw_entries = data["logical_entries"]
    if not isinstance(raw_entries, list) or not raw_entries:
        raise RigError("logical_entries must be a non-empty array")
    entries: list[LogicalEntry] = []
    seen_ids: set[str] = set()
    for index, raw_entry in enumerate(raw_entries):
        label = f"logical_entries[{index}]"
        entry_data = _mapping(raw_entry, label)
        _expect_keys(
            entry_data,
            required={"id", "track", "output", "file_prefix"},
            label=label,
        )
        entry_id = _text(entry_data["id"], f"{label}.id")
        if entry_id in seen_ids:
            raise RigError(f"duplicate logical entry id: {entry_id}")
        seen_ids.add(entry_id)
        track = _text(entry_data["track"], f"{label}.track")
        if track not in tracks:
            raise RigError(f"{label}.track references unknown track: {track}")
        prefix = _text(entry_data["file_prefix"], f"{label}.file_prefix")
        if "/" in prefix or "\\" in prefix or prefix in {".", ".."}:
            raise RigError(f"{label}.file_prefix must be a file name prefix")
        entries.append(
            LogicalEntry(
                entry_id,
                track,
                _relative_parts(entry_data["output"], f"{label}.output"),
                prefix,
            )
        )

    output_keys: set[str] = set()
    for entry in entries:
        for index, frame in enumerate(tracks[entry.track]):
            name = f"{entry.file_prefix}_{index:03d}_{frame.duration}.png"
            key = "/".join((*entry.output_parts, name)).casefold()
            if key in output_keys:
                raise RigError(f"multiple logical entries write the same file: {key}")
            output_keys.add(key)
    return tracks, tuple(entries)


def _transformed_layer(
    rig: Rig, layer: Layer, transform: PartTransform, root: PartTransform
) -> tuple[Image.Image, tuple[int, int]]:
    # Compose directly into canvas coordinates so scale and rotation share one declared pivot;
    # expanding and then pasting a rotated bitmap would move the joint as its bounds change.
    global_scale = rig.neutral.scale
    scale = global_scale * transform.scale
    angle = math.radians(transform.rotation)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    a = scale * cosine
    b = -scale * sine
    d = scale * sine
    e = scale * cosine

    pivot_x = rig.neutral.anchor_x + global_scale * (
        layer.placement.x + transform.translation.x + layer.pivot.x
    )
    pivot_y = rig.neutral.baseline_y + global_scale * (
        layer.placement.y + transform.translation.y + layer.pivot.y
    )
    c = pivot_x - a * layer.pivot.x - b * layer.pivot.y
    f = pivot_y - d * layer.pivot.x - e * layer.pivot.y

    # Root motion rotates the assembled puppet around its foot anchor. This preserves every
    # local joint while allowing whole-body states such as lying down without redrawing layers.
    root_angle = math.radians(root.rotation)
    root_cosine = math.cos(root_angle)
    root_sine = math.sin(root_angle)
    root_a = root.scale * root_cosine
    root_b = -root.scale * root_sine
    root_d = root.scale * root_sine
    root_e = root.scale * root_cosine
    root_x = rig.neutral.anchor_x
    root_y = rig.neutral.baseline_y
    root_c = (
        root_x
        + global_scale * root.translation.x
        - root_a * root_x
        - root_b * root_y
    )
    root_f = (
        root_y
        + global_scale * root.translation.y
        - root_d * root_x
        - root_e * root_y
    )
    a, b, c, d, e, f = (
        root_a * a + root_b * d,
        root_a * b + root_b * e,
        root_a * c + root_b * f + root_c,
        root_d * a + root_e * d,
        root_d * b + root_e * e,
        root_d * c + root_e * f + root_f,
    )

    corners = (
        (0.0, 0.0),
        (float(layer.image.width), 0.0),
        (0.0, float(layer.image.height)),
        (float(layer.image.width), float(layer.image.height)),
    )
    transformed = tuple((a * x + b * y + c, d * x + e * y + f) for x, y in corners)
    padding = 3
    left = math.floor(min(x for x, _ in transformed)) - padding
    top = math.floor(min(y for _, y in transformed)) - padding
    right = math.ceil(max(x for x, _ in transformed)) + padding
    bottom = math.ceil(max(y for _, y in transformed)) + padding

    determinant = a * e - b * d
    # Pillow samples output back into the source, so invert the forward source-to-canvas matrix.
    inverse = (
        e / determinant,
        -b / determinant,
        (e * (left - c) - b * (top - f)) / determinant,
        -d / determinant,
        a / determinant,
        (-d * (left - c) + a * (top - f)) / determinant,
    )
    patch = layer.image.transform(
        (right - left, bottom - top),
        Image.Transform.AFFINE,
        inverse,
        resample=Image.Resampling.BICUBIC,
        fillcolor=(0, 0, 0, 0),
    )
    alpha_bounds = patch.getchannel("A").getbbox()
    if alpha_bounds is None:
        raise RigError(f"layer {layer.id} disappears after transformation")
    visible_left = left + alpha_bounds[0]
    visible_top = top + alpha_bounds[1]
    visible_right = left + alpha_bounds[2]
    visible_bottom = top + alpha_bounds[3]
    if (
        visible_left < 0
        or visible_top < 0
        or visible_right > rig.width
        or visible_bottom > rig.height
    ):
        # Check transformed alpha rather than the rectangular layer file: transparent source
        # padding may legitimately extend beyond the fixed canvas, visible pixels may not.
        raise RigError(
            f"layer {layer.id} exceeds the canvas: "
            f"({visible_left}, {visible_top}, {visible_right}, {visible_bottom})"
        )
    return patch.crop(alpha_bounds), (visible_left, visible_top)


def render_frame(rig: Rig, frame: MotionFrame) -> Image.Image:
    """Render one full-size RGBA frame without fitting, trimming, or baseline adjustment."""

    canvas = Image.new("RGBA", (rig.width, rig.height), (0, 0, 0, 0))
    # A raised hand sometimes has to pass in front of the face while its neutral sleeve stays
    # behind the torso. Per-frame z only changes that overlap; manifest order remains the tie-breaker.
    layer_states = [
        (index, layer, frame.parts.get(layer.id, PartTransform()))
        for index, layer in enumerate(rig.layers)
    ]
    ordered_layers = sorted(
        layer_states,
        key=lambda item: (item[2].z if item[2].z is not None else item[1].z, item[0]),
    )
    for _, layer, transform in ordered_layers:
        if transform.visible is False or (transform.visible is None and not layer.visible):
            continue
        patch, destination = _transformed_layer(rig, layer, transform, frame.root)
        canvas.alpha_composite(patch, destination)

    corners = (
        canvas.getpixel((0, 0))[3],
        canvas.getpixel((rig.width - 1, 0))[3],
        canvas.getpixel((0, rig.height - 1))[3],
        canvas.getpixel((rig.width - 1, rig.height - 1))[3],
    )
    if any(corners):
        raise RigError("rendered frame must keep all four canvas corners transparent")
    return canvas


def _png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    # Pillow emits no timestamp by default; fixed options make repeated runs byte-identical.
    image.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()


def render_catalog(layer_manifest: Path, motion_catalog: Path, output_dir: Path) -> list[Path]:
    """Validate and render a catalog into a new directory, returning written PNG paths."""

    rig = _load_rig(layer_manifest)
    tracks, entries = _load_motion_catalog(motion_catalog, {layer.id for layer in rig.layers})
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise RigError(f"output directory already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)

    entries_by_track: dict[str, list[LogicalEntry]] = {}
    for entry in entries:
        entries_by_track.setdefault(entry.track, []).append(entry)

    relative_outputs: list[Path] = []
    with tempfile.TemporaryDirectory(
        prefix=f".{output_dir.name}-", dir=output_dir.parent
    ) as temporary:
        payload = Path(temporary) / "payload"
        payload.mkdir()
        for track_id in sorted(entries_by_track):
            frames = tracks[track_id]
            # Encode a reused track once so every logical entry receives identical PNG bytes.
            encoded_items = []
            for frame_index, frame in enumerate(frames):
                try:
                    encoded_items.append((frame.duration, _png_bytes(render_frame(rig, frame))))
                except RigError as exc:
                    raise RigError(
                        f"track {track_id} frame {frame_index}: {exc}"
                    ) from exc
            encoded = tuple(encoded_items)
            for entry in sorted(entries_by_track[track_id], key=lambda item: item.id):
                directory = payload.joinpath(*entry.output_parts)
                directory.mkdir(parents=True, exist_ok=True)
                for index, (duration, content) in enumerate(encoded):
                    relative = Path(*entry.output_parts) / (
                        f"{entry.file_prefix}_{index:03d}_{duration}.png"
                    )
                    (payload / relative).write_bytes(content)
                    relative_outputs.append(relative)
        payload.replace(output_dir)
    return [output_dir / path for path in sorted(relative_outputs)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layer-manifest", type=Path, required=True)
    parser.add_argument("--motion-catalog", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        outputs = render_catalog(args.layer_manifest, args.motion_catalog, args.output_dir)
    except RigError as exc:
        parser.error(str(exc))
    print(json.dumps({"output_dir": str(args.output_dir.resolve()), "frames": len(outputs)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
