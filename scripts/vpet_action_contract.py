"""Export and validate the path-derived VPet animation action contract."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESOURCE_ROOT = ROOT / "build" / "vpet" / "resources" / "pet" / "vup"
DEFAULT_CONTRACT = ROOT / "pet_host" / "vpet_action_contract.json"
SCHEMA_VERSION = 1

# Keep this order aligned with GraphInfo.GraphType: path inference takes the first match.
GRAPH_TYPES = (
    "Common",
    "Raised_Dynamic",
    "Raised_Static",
    "Move",
    "Default",
    "Touch_Head",
    "Touch_Body",
    "Idel",
    "Sleep",
    "Say",
    "StateONE",
    "StateTWO",
    "StartUP",
    "Shutdown",
    "Work",
    "Switch_Up",
    "Switch_Down",
    "Switch_Thirsty",
    "Switch_Hunger",
    "SideHide_Left_Main",
    "SideHide_Left_Rise",
    "SideHide_Right_Main",
    "SideHide_Right_Rise",
)
MODE_TOKENS = (
    ("happy", "Happy"),
    ("nomal", "Nomal"),
    ("poorcondition", "PoorCondition"),
    ("ill", "Ill"),
)
ANIMAT_TOKENS = (
    ("a", "A_Start"),
    ("start", "A_Start"),
    ("b", "B_Loop"),
    ("loop", "B_Loop"),
    ("c", "C_End"),
    ("end", "C_End"),
    ("single", "Single"),
)
NUMBER_TOKEN = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")


class ContractError(ValueError):
    """Raised when a resource tree cannot produce a valid action contract."""


def _remove_first(parts: list[str], token: str) -> bool:
    try:
        parts.remove(token)
    except ValueError:
        return False
    return True


def parse_graph_info(relative_directory: str) -> dict[str, str]:
    """Mirror GraphInfo's path-based type, mood, stage, and name inference."""
    parts = [part for part in relative_directory.lower().replace("\\", "_").replace("/", "_").split("_") if part]

    mode = "Nomal"
    for token, value in MODE_TOKENS:
        if _remove_first(parts, token):
            mode = value
            break

    graph_type = "Common"
    for candidate in GRAPH_TYPES:
        tokens = candidate.lower().split("_")
        try:
            index = parts.index(tokens[0])
        except ValueError:
            continue
        if parts[index:index + len(tokens)] == tokens:
            del parts[index:index + len(tokens)]
            graph_type = candidate
            break

    animat_type = "Single"
    for token, value in ANIMAT_TOKENS:
        if _remove_first(parts, token):
            animat_type = value
            break

    while parts and (NUMBER_TOKEN.fullmatch(parts[-1]) or parts[-1].startswith("~")):
        parts.pop()
    name = parts[-1] if parts else graph_type.lower()
    return {
        "graph_type": graph_type,
        "name": name,
        "mode": mode,
        "animat_type": animat_type,
    }


def _frame_duration(path: Path) -> int:
    suffix = path.stem.rsplit("_", 1)[-1]
    try:
        duration = int(suffix)
    except ValueError as exc:
        raise ContractError(
            f"invalid frame duration suffix in {path.name!r}; expected a trailing integer"
        ) from exc
    if duration <= 0:
        raise ContractError(f"invalid non-positive frame duration in {path.name!r}")
    return duration


def _scan_segments(resource_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not resource_root.is_dir():
        return [], [f"resource root does not exist: {resource_root}"]

    directories: dict[Path, list[Path]] = {}
    for frame in resource_root.rglob("*.png"):
        if frame.is_file():
            directories.setdefault(frame.parent, []).append(frame)

    segments: list[dict[str, Any]] = []
    errors: list[str] = []
    for directory, frames in sorted(
        directories.items(), key=lambda item: item[0].relative_to(resource_root).as_posix().casefold()
    ):
        relative = directory.relative_to(resource_root).as_posix()
        ordered_frames = sorted(frames, key=lambda path: path.name.casefold())
        try:
            durations = [_frame_duration(frame) for frame in ordered_frames]
        except ContractError as exc:
            errors.append(f"{relative}: {exc}")
            continue
        metadata = parse_graph_info(relative)
        segments.append(
            {
                "relative_directory": relative,
                **metadata,
                # The first path component is the stable resource-level action family.
                "family": relative.split("/", 1)[0],
                "frame_count": len(ordered_frames),
                "duration_ms": sum(durations),
                "frame_durations_ms": durations,
            }
        )
    if not directories:
        errors.append(f"resource root contains no PNG animation frames: {resource_root}")
    return segments, errors


def _coerce_lps_value(value: str) -> str | int | float:
    if re.fullmatch(r"[+-]?\d+", value):
        return int(value)
    if NUMBER_TOKEN.fullmatch(value):
        return float(value)
    return value


def parse_move_configs(lps_path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not lps_path.is_file():
        return [], [f"VPet config does not exist: {lps_path}"]

    configs: list[dict[str, Any]] = []
    errors: list[str] = []
    for line_number, raw_line in enumerate(lps_path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw_line.strip()
        if not line.lower().startswith("move:|"):
            continue
        fields: dict[str, Any] = {}
        for item in line.split(":|")[1:]:
            item = item.removesuffix(":").removesuffix("|")
            if not item:
                continue
            if "#" not in item:
                errors.append(f"{lps_path}:{line_number}: invalid MOVE field {item!r}")
                continue
            key, value = item.split("#", 1)
            fields[key] = _coerce_lps_value(value)
        graph = fields.get("graph")
        if not isinstance(graph, str) or not graph:
            errors.append(f"{lps_path}:{line_number}: MOVE entry is missing graph")
            continue
        configs.append({"index": len(configs) + 1, "graph": graph.lower(), "fields": fields})
    if not configs:
        errors.append(f"VPet config contains no MOVE entries: {lps_path}")
    return configs, errors


def _missing_move_graph_errors(
    segments: list[dict[str, Any]], move_configs: list[dict[str, Any]]
) -> list[str]:
    available = {
        str(segment["name"]).casefold()
        for segment in segments
        if segment["graph_type"] == "Move"
    }
    return [
        f"MOVE config references missing graph {config['graph']!r}"
        for config in move_configs
        if str(config["graph"]).casefold() not in available
    ]


def build_contract(resource_root: Path, lps_path: Path) -> dict[str, Any]:
    """Build a deterministic contract from one complete VPet resource tree."""
    resource_root = Path(resource_root)
    lps_path = Path(lps_path)
    segments, errors = _scan_segments(resource_root)
    move_configs, move_errors = parse_move_configs(lps_path)
    errors.extend(move_errors)
    errors.extend(_missing_move_graph_errors(segments, move_configs))
    if errors:
        raise ContractError("\n".join(errors))
    return {
        "schema_version": SCHEMA_VERSION,
        "segment_count": len(segments),
        "frame_count": sum(int(segment["frame_count"]) for segment in segments),
        "move_config_count": len(move_configs),
        "segments": segments,
        "move_configs": move_configs,
    }


def write_contract(contract: dict[str, Any], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_contract(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ContractError("action contract root must be an object")
    return data


def validate_contract(
    contract: dict[str, Any], resource_root: Path, lps_path: Path
) -> list[str]:
    """Return every structural or timing difference from the authoritative contract."""
    errors: list[str] = []
    if contract.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"unsupported contract schema: {contract.get('schema_version')!r}; expected {SCHEMA_VERSION}"
        )

    expected_segments = contract.get("segments")
    if not isinstance(expected_segments, list):
        return [*errors, "contract segments must be a list"]
    expected_moves = contract.get("move_configs")
    if not isinstance(expected_moves, list):
        return [*errors, "contract move_configs must be a list"]

    if contract.get("segment_count") != len(expected_segments):
        errors.append("contract segment_count does not match its segments")
    expected_frame_count = sum(
        int(segment.get("frame_count", 0))
        for segment in expected_segments
        if isinstance(segment, dict)
    )
    if contract.get("frame_count") != expected_frame_count:
        errors.append("contract frame_count does not match its segments")
    if contract.get("move_config_count") != len(expected_moves):
        errors.append("contract move_config_count does not match its MOVE entries")

    actual_segments, scan_errors = _scan_segments(Path(resource_root))
    errors.extend(scan_errors)
    actual_moves, move_errors = parse_move_configs(Path(lps_path))
    errors.extend(move_errors)

    def indexed(segments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {
            str(segment["relative_directory"]): segment
            for segment in segments
            if isinstance(segment, dict) and "relative_directory" in segment
        }

    expected_by_directory = indexed(expected_segments)
    actual_by_directory = indexed(actual_segments)
    for relative in sorted(expected_by_directory.keys() - actual_by_directory.keys(), key=str.casefold):
        errors.append(f"missing segment directory: {relative}")
    for relative in sorted(actual_by_directory.keys() - expected_by_directory.keys(), key=str.casefold):
        errors.append(f"unexpected segment directory: {relative}")

    compared_fields = (
        "graph_type",
        "name",
        "mode",
        "animat_type",
        "family",
        "frame_count",
        "duration_ms",
        "frame_durations_ms",
    )
    for relative in sorted(expected_by_directory.keys() & actual_by_directory.keys(), key=str.casefold):
        expected = expected_by_directory[relative]
        actual = actual_by_directory[relative]
        for field in compared_fields:
            if actual.get(field) != expected.get(field):
                errors.append(
                    f"segment {relative} has wrong {field}: "
                    f"{actual.get(field)!r}; expected {expected.get(field)!r}"
                )

    if actual_moves != expected_moves:
        errors.append("MOVE configurations differ from the action contract")
    errors.extend(_missing_move_graph_errors(actual_segments, actual_moves))
    return errors


def _lps_for(resource_root: Path, configured: Path | None) -> Path:
    return configured if configured is not None else resource_root.parent / "vup.lps"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser("export", help="export a contract from an existing resource tree")
    export.add_argument("--resource-root", type=Path, default=DEFAULT_RESOURCE_ROOT)
    export.add_argument("--lps", type=Path)
    export.add_argument("--output", type=Path, default=DEFAULT_CONTRACT)

    validate = subparsers.add_parser("validate", help="validate a resource tree against a contract")
    validate.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    validate.add_argument("--resource-root", type=Path, default=DEFAULT_RESOURCE_ROOT)
    validate.add_argument("--lps", type=Path)

    args = parser.parse_args(argv)
    lps_path = _lps_for(args.resource_root, args.lps)
    try:
        if args.command == "export":
            contract = build_contract(args.resource_root, lps_path)
            write_contract(contract, args.output)
            print(
                f"exported {contract['segment_count']} segments, "
                f"{contract['frame_count']} frames, and "
                f"{contract['move_config_count']} MOVE entries to {args.output}"
            )
            return 0

        contract = load_contract(args.contract)
        errors = validate_contract(contract, args.resource_root, lps_path)
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        print(f"VPet action contract failed: {exc}")
        return 1
    if errors:
        print("VPet action contract validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        f"validated {contract['segment_count']} segments, "
        f"{contract['frame_count']} frames, and "
        f"{contract['move_config_count']} MOVE entries"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
