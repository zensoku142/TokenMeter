"""Read and write non-sensitive runtime state files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_json(path: Path, values: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_dict(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def load_widget_position(path: Path) -> tuple[int, int] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return int(value["x"]), int(value["y"])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def save_widget_position(path: Path, x: int, y: int) -> None:
    path.write_text(
        json.dumps({"x": int(x), "y": int(y)}, ensure_ascii=False),
        encoding="utf-8",
    )


def merge_dict(path: Path, values: dict[str, Any]) -> None:
    current = load_dict(path)
    current.update(values)
    write_json(path, current)


def clear(path: Path) -> None:
    path.unlink(missing_ok=True)


__all__ = [
    "clear",
    "load_dict",
    "load_widget_position",
    "merge_dict",
    "save_widget_position",
    "write_json",
]
