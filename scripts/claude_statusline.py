"""Save only official Claude Code rate limits; usable as a standalone statusLine command."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MAX_INPUT_BYTES = 1024 * 1024


def make_snapshot(payload: Any, account_scope: str = "") -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Invalid statusline JSON")
    limits = payload.get("rate_limits")
    if limits is not None and not isinstance(limits, dict):
        raise ValueError("Invalid rate limits")
    clean_limits = {}
    for identifier in ("five_hour", "seven_day"):
        if not limits or identifier not in limits:
            continue
        window = limits[identifier]
        if not isinstance(window, dict):
            raise ValueError("Invalid quota window")
        used, reset = window.get("used_percentage"), window.get("resets_at")
        if type(used) not in (int, float) or not math.isfinite(used) or not 0 <= used <= 100:
            raise ValueError("Invalid percentage")
        if type(reset) not in (int, float) or not math.isfinite(reset) or reset <= 0:
            raise ValueError("Invalid reset time")
        datetime.fromtimestamp(reset, timezone.utc)
        # 只白名单保存数字；原始输入还可能包含目录、对话路径和未来新增敏感字段。
        clean_limits[identifier] = {"used_percentage": used, "resets_at": reset}
    snapshot = {
        "schema_version": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "rate_limits": clean_limits,
    }
    if account_scope.strip():
        snapshot["account_scope"] = account_scope.strip()
    return snapshot


def write_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        # 临时文件与目标位于同一目录；先完整关闭再替换，避免 Windows 读取到半份 JSON。
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(snapshot, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path.home() / ".claude" / "tokenmeter-usage.json")
    parser.add_argument(
        "--account-scope",
        default="",
        help="Optional non-secret account alias; change it when switching accounts",
    )
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
        if len(raw) > _MAX_INPUT_BYTES:
            raise ValueError("Statusline input too large")
        snapshot = make_snapshot(json.loads(raw), args.account_scope)
        # 首次响应前也写空窗口，清除上次会话的额度；不把旧账号快照伪装成当前数据。
        write_snapshot(args.output.expanduser(), snapshot)
        labels = {"five_hour": "5h", "seven_day": "7d"}
        segments = [
            f"{labels[key]} {100 - window['used_percentage']:g}% left"
            for key, window in snapshot["rate_limits"].items()
        ]
        print("Claude | " + (" | ".join(segments) if segments else "quota pending"))
        return 0
    except (OSError, ValueError, TypeError, OverflowError):
        # 错误输出不回显输入、文件内容或命令行参数，防止状态栏泄露敏感数据。
        print("Claude | quota unavailable")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
