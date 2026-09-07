"""MiniMax Token Plan quotas from the first-party CLI's read-only endpoint."""

from __future__ import annotations

from decimal import DecimalException

from api.providers.api_balance import _amount
from api.providers.base import FetchError, ProviderQuota, QuotaMetric, QuotaWindow
from api.providers.quota_api import _QuotaAPIProvider, _unix_time


class MiniMaxProvider(_QuotaAPIProvider):
    id = "minimax"
    name = "MiniMax Token Plan"
    default_base = "https://api.minimax.io/v1"
    official_api_hosts = {
        "api.minimax.io",
        "api.minimaxi.com",
        "www.minimax.io",
        "www.minimaxi.com",
    }
    dashboard_url = "https://platform.minimax.io/subscribe/token-plan"
    support_description = "读取 Token Plan 各服务的周期/周额度、重置与不限额状态；旧计数字段按官方 CLI 解释，不折算 Token 或现金。"
    credential_fields = {
        "API_KEY": {
            "label": "Token Plan API Key",
            "secret": True,
            "hint": "订阅的 sk-cp Key；按量付费 sk-api Key 不提供 Token Plan 额度",
        },
        "BASE": {
            "label": "API 地址",
            "secret": False,
            "hint": "国际 https://api.minimax.io/v1；国内 https://api.minimaxi.com/v1",
        },
    }

    def fetch_quota(self) -> tuple[ProviderQuota | None, FetchError | None]:
        source = "MiniMax Token Plan 额度"
        payload, error = self._request_data("/token_plan/remains", source)
        if error:
            return None, error
        try:
            if payload is None:
                raise ValueError("Missing payload")
            if "base_resp" in payload:
                status = payload["base_resp"]
                if not isinstance(status, dict) or type(status.get("status_code")) is not int:
                    raise ValueError("Invalid API status")
                if status["status_code"] != 0:
                    code = "AUTH_EXPIRED" if status["status_code"] in {1004, 2049} else "API_ERROR"
                    return None, self._error(code, source, "接口返回错误，请检查套餐密钥与站点")
            entries = payload["model_remains"]
            if not isinstance(entries, list):
                raise ValueError("Invalid services")
            windows, metrics = [], []
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict) or not isinstance(entry.get("model_name"), str):
                    raise ValueError("Invalid service")
                model = entry["model_name"].strip()
                if not model:
                    raise ValueError("Missing service")
                # 第一方 CLI: 两个状态均为3且总数均为0代表不在套餐中，不能显示成无限量。
                unavailable = all(
                    type(entry.get(f"current_{kind}_status")) is int
                    and entry[f"current_{kind}_status"] == 3
                    and _amount(entry, f"current_{kind}_total_count", nonnegative=True) == 0
                    for kind in ("interval", "weekly")
                )
                if unavailable:
                    metrics.append(QuotaMetric(model, "不在当前套餐中"))
                    continue
                for kind in ("interval", "weekly"):
                    prefix = f"current_{kind}_"
                    if not any(key.startswith(prefix) for key in entry):
                        continue
                    title = f"{model} · {'周额度' if kind == 'weekly' else '周期额度'}"
                    status = entry.get(f"{prefix}status")
                    if status is not None and (type(status) is not int or status not in {1, 2, 3}):
                        raise ValueError("Unknown quota status")
                    if status == 3:
                        metrics.append(QuotaMetric(title, "不限额", value_kind="unlimited"))
                        continue
                    remaining_key = f"{prefix}remaining_percent"
                    # 官方 CLI 将 null 与缺失百分比同样处理；仍需有效计数，不能把未知额度当零。
                    if entry.get(remaining_key) is not None:
                        remaining = _amount(entry, remaining_key, nonnegative=True)
                        if remaining > 100:
                            raise ValueError("Invalid remaining percentage")
                    else:
                        total = _amount(entry, f"{prefix}total_count", nonnegative=True)
                        count = _amount(entry, f"{prefix}usage_count", nonnegative=True)
                        if total <= 0 or count > total:
                            raise ValueError("Unknown entitlement")
                        # 最新官方 CLI 优先明确剩余百分比；缺少百分比时沿用旧版剩余计数语义。
                        remaining = count / total * 100
                    reset_key = "weekly_end_time" if kind == "weekly" else "end_time"
                    start_key = "weekly_start_time" if kind == "weekly" else "start_time"
                    reset = _unix_time(entry, reset_key, milliseconds=True)
                    start = _unix_time(entry, start_key, milliseconds=True)
                    minutes = None
                    if reset is not None and start is not None:
                        seconds = (reset - start).total_seconds()
                        if seconds <= 0 or seconds % 60 != 0:
                            raise ValueError("Invalid quota window")
                        minutes = int(seconds / 60)
                        if kind == "interval" and minutes == 300:
                            title = f"{model} · 5 小时额度"
                    detail = ""
                    # 可选加成为 null 表示未提供加成；保留已知周额度，不因展示字段丢失整个平台。
                    if kind == "weekly" and entry.get("weekly_boost_permille") is not None:
                        boost = _amount(entry, "weekly_boost_permille", nonnegative=True)
                        # 进度以实际套餐的100%为分母；加成显示值单独保留，不反转消耗比例。
                        detail = f"加成后剩余 {remaining * boost / 1000:g}%（基础额度剩余 {remaining:g}%）"
                    windows.append(
                        QuotaWindow(
                            f"service_{index}_{kind}",
                            title,
                            float(100 - remaining),
                            reset,
                            minutes,
                            detail,
                        )
                    )
            if not windows and not metrics:
                return None, self._error("QUOTA_UNAVAILABLE", source, "未返回可量化的套餐额度")
            return ProviderQuota(
                windows=tuple(windows), metrics=tuple(metrics), plan="MiniMax Token Plan"
            ), None
        except (KeyError, TypeError, ValueError, OverflowError, OSError, DecimalException):
            return None, self._invalid_response(source)
