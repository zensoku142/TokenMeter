"""Data-directory validation and migration primitives."""

from __future__ import annotations

import os
from pathlib import Path

from data_directory import migrate_legacy_data


def normalize_data_dir(value: str | os.PathLike[str]) -> Path:
    raw_value = os.path.expandvars(os.path.expanduser(str(value).strip()))
    if not raw_value:
        raise ValueError("应用数据目录不能为空")
    if raw_value.startswith("\\\\"):
        raise ValueError("应用数据目录不支持网络共享路径")
    path = Path(raw_value)
    if not path.is_absolute():
        raise ValueError("应用数据目录必须使用绝对路径")
    return path.resolve(strict=False)


def validate_separate_dirs(source: Path, target: Path) -> None:
    source = source.resolve(strict=False)
    target = target.resolve(strict=False)
    if source == target:
        return
    if source in target.parents or target in source.parents:
        raise ValueError("新旧应用数据目录不能互相包含")


def migrate_data_dir(source: Path, target: Path) -> None:
    migrate_legacy_data(source, target)


__all__ = ["migrate_data_dir", "normalize_data_dir", "validate_separate_dirs"]
