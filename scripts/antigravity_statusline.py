"""Export only official Antigravity CLI quota fields for TokenMeter."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# 支持直接作为 CLI 命令运行及模块导入；复用已有原子写入，不维护第二套落盘逻辑。
if __package__:
    from .claude_statusline import write_snapshot
else:
    from claude_statusline import write_snapshot


def make_snapshot(payload: Any, account_scope: str = "") -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("product") != "antigravity":
        raise ValueError("Invalid Antigravity payload")
    observed = datetime.now(timezone.utc)
    quotas = payload.get("quota")
    if quotas is None:
        quotas = {}
    if not isinstance(quotas, dict) or len(quotas) > 64:
        raise ValueError("Invalid quota map")
    clean = {}
    for identifier, quota in quotas.items():
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 160 or not identifier.isprintable():
            continue
        if not isinstance(quota, dict):
            continue
        remaining = quota.get("remaining_fraction")
        if isinstance(remaining, bool) or not isinstance(remaining, (int, float)) or not 0 <= remaining <= 1 or not math.isfinite(remaining):
            continue
        reset = quota.get("reset_time")
        try:
            if reset is not None:
                parsed = datetime.fromisoformat(reset.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("Reset timezone missing")
            else:
                seconds = quota.get("reset_in_seconds")
                parsed = None
                if seconds is not None:
                    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
                        raise ValueError("Invalid relative reset")
                    parsed = observed + timedelta(seconds=seconds)
        except (AttributeError, TypeError, ValueError, OverflowError):
            continue
        clean[identifier] = {
            "remaining_fraction": remaining,
            "reset_time": parsed.isoformat() if parsed is not None else None,
        }
    email = payload.get("email")
    identity = account_scope.strip() or (email.strip().casefold() if isinstance(email, str) else "")
    # 输入含邮箱、工作目录、会话路径等信息；仅保存不可逆账号指纹和额度白名单。
    scope = hashlib.sha256(f"antigravity:{identity}".encode()).hexdigest() if identity else ""
    plan = payload.get("plan_tier")
    return {
        "schema_version": 1,
        "product": "antigravity",
        "observed_at": observed.isoformat(),
        "account_scope": scope,
        "plan": " ".join(plan.split())[:128] if isinstance(plan, str) else "",
        "quota": clean,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path.home() / ".gemini" / "antigravity-cli" / "tokenmeter-usage.json")
    parser.add_argument("--account-scope", default="", help="Optional account alias when the CLI omits email")
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("Input too large")
        snapshot = make_snapshot(json.loads(raw), args.account_scope)
        # 启动阶段也写空额度，及时清除上一会话的读数；不执行 CLI 命令或模型推理。
        write_snapshot(args.output.expanduser(), snapshot)
        entries = list(snapshot["quota"].items())
        segments = [f"{key}: {value['remaining_fraction'] * 100:g}%" for key, value in entries[:3]]
        if len(entries) > 3:
            segments.append(f"+{len(entries) - 3} more")
        print("Antigravity | " + (" | ".join(segments) if segments else "quota pending"))
        return 0
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        print("Antigravity | quota unavailable")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
