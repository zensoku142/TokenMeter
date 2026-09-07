"""Antigravity CLI quota from the documented statusline payload, without network requests."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from api.providers.base import FetchError, Provider, ProviderQuota, QuotaMetric, QuotaWindow


class AntigravityProvider(Provider):
    id = "antigravity"
    name = "Antigravity"
    supports_subscription_quota = True
    support_description = "读取 Antigravity CLI 官方状态栏额度快照；需配置 antigravity_statusline.py，超过 15 分钟未更新则过期。"
    dashboard_url = "https://antigravity.google/docs/cli/commands/usage/"
    credential_fields = {
        "STATUSLINE_FILE": {
            "label": "状态栏快照文件（可选）", "secret": False, "optional": True,
            "hint": "默认 ~/.gemini/antigravity-cli/tokenmeter-usage.json；仅显示 CLI 实际提供的模型额度",
        },
    }

    def _snapshot_path(self) -> Path:
        value = str(self.config_get("ANTIGRAVITY_STATUSLINE_FILE", "")).strip()
        return Path(value).expanduser() if value else Path.home() / ".gemini" / "antigravity-cli" / "tokenmeter-usage.json"

    def is_configured(self) -> bool:
        try:
            return self._snapshot_path().is_file()
        except OSError:
            return False

    def _read_snapshot(self) -> tuple[dict[str, Any], datetime]:
        with self._snapshot_path().open("rb") as handle:
            raw = handle.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise ValueError("Snapshot too large")
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict) or type(payload.get("schema_version")) is not int
            or payload["schema_version"] != 1 or payload.get("product") != self.id
            or not isinstance(payload.get("quota"), dict) or len(payload["quota"]) > 64
        ):
            raise ValueError("Invalid snapshot")
        observed = datetime.fromisoformat(str(payload.get("observed_at", "")).replace("Z", "+00:00"))
        if observed.tzinfo is None or observed > datetime.now(timezone.utc) + timedelta(minutes=1):
            raise ValueError("Invalid observation time")
        return payload, observed.astimezone(timezone.utc)

    def snapshot_identity(self) -> str:
        try:
            payload, _ = self._read_snapshot()
            scope = payload.get("account_scope")
            if not isinstance(scope, str) or not scope.strip():
                return ""
            # 文件位置和账号共同隔离缓存；没有账号字段时只显示本轮读数，不复用跨会话缓存。
            identity = [str(self._snapshot_path().resolve()), scope.strip()]
            return hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            return ""

    @staticmethod
    def _error(code: str) -> FetchError:
        messages = {
            "NOT_CONFIGURED": "未找到 Antigravity 快照，请先配置 antigravity_statusline.py",
            "FILE_ERROR": "无法读取 Antigravity 状态栏快照文件",
            "INVALID_RESPONSE": "Antigravity 状态栏快照格式或额度字段无效",
            "NO_DATA": "Antigravity 尚未提供额度，请在 CLI 中使用 /usage 后刷新",
            "STALE_DATA": "Antigravity 状态栏超过 15 分钟未更新，请在 CLI 中使用 /usage 后刷新",
        }
        return FetchError(code, "Antigravity 额度", messages[code])

    def fetch_quota(self) -> tuple[ProviderQuota | None, FetchError | None]:
        try:
            payload, observed = self._read_snapshot()
            if datetime.now(timezone.utc) - observed > timedelta(minutes=15):
                return None, self._error("STALE_DATA")
            windows = []
            for identifier, value in payload["quota"].items():
                if not isinstance(identifier, str) or not 1 <= len(identifier) <= 160 or not identifier.isprintable():
                    continue
                if not isinstance(value, dict):
                    continue
                remaining = value.get("remaining_fraction")
                if isinstance(remaining, bool) or not isinstance(remaining, (int, float)) or not 0 <= remaining <= 1 or not math.isfinite(remaining):
                    continue
                reset = value.get("reset_time")
                try:
                    resets_at = datetime.fromisoformat(reset.replace("Z", "+00:00")) if reset is not None else None
                    if resets_at is not None and resets_at.tzinfo is None:
                        raise ValueError("Reset timezone missing")
                except (AttributeError, TypeError, ValueError, OverflowError):
                    continue
                # 模型/窗口名称来自官方桶 ID，不猜测每日/每周时长或把份额转换成 Token 总量。
                windows.append(QuotaWindow(identifier, identifier, (1 - remaining) * 100, resets_at))
            if not windows:
                return None, self._error("NO_DATA")
            plan = payload.get("plan")
            return ProviderQuota(
                windows=tuple(windows), source="local_snapshot",
                plan=" ".join(plan.split())[:128] if isinstance(plan, str) else "",
                metrics=(QuotaMetric("快照时间", observed.strftime("%H:%M"), observed.strftime("%Y-%m-%d UTC")),),
            ), None
        except FileNotFoundError:
            return None, self._error("NOT_CONFIGURED")
        except OSError:
            return None, self._error("FILE_ERROR")
        except (ValueError, TypeError, OverflowError, RecursionError):
            return None, self._error("INVALID_RESPONSE")
