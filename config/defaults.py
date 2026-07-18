"""Default configuration values and field metadata."""

from __future__ import annotations

from typing import Any

SECRET_KEYS = (
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_AUTH",
    "DEEPSEEK_COOKIE",
    "MIMO_COOKIE",
    "MIMO_API_PLATFORM_PH",
    "MIMO_API_KEY",
)
OFFICIAL_HOSTS = {
    "platform.deepseek.com",
    "api.deepseek.com",
    "platform.xiaomimimo.com",
}
DEFAULT_CONFIG: dict[str, Any] = {
    "DEEPSEEK_API_KEY": "",
    "DEEPSEEK_AUTH": "",
    "DEEPSEEK_COOKIE": "",
    "DEEPSEEK_BASE": "https://platform.deepseek.com",
    "DEEPSEEK_PEAK_PRICING_ENABLED": False,
    "DEEPSEEK_PEAK_PERIOD_1_START": "09:00",
    "DEEPSEEK_PEAK_PERIOD_1_END": "12:00",
    "DEEPSEEK_PEAK_PERIOD_2_START": "14:00",
    "DEEPSEEK_PEAK_PERIOD_2_END": "18:00",
    "MIMO_COOKIE": "",
    "MIMO_API_PLATFORM_PH": "",
    "MIMO_API_KEY": "",
    "MIMO_BASE": "https://platform.xiaomimimo.com",
    "REFRESH_INTERVAL": 60_000,
    "WIDGET_COMPACT_SIZE": 88,
    "WIDGET_EXPANDED_SIZE": (820, 564),
    "BG_COLOR": "#071427",
    "ACCENT_COLOR": "#2f6fe4",
    "TEXT_COLOR": "#edf4ff",
    "ACTIVE_PROVIDER": "deepseek",
    "EDGE_HIDE_ENABLED": True,
    "PANEL_AUTO_COLLAPSE_ON_DEACTIVATE": True,
    "UI_THEME": "dark",
    "UPDATE_AUTO_CHECK_ENABLED": True,
    "UPDATE_CHANNEL": "stable",
    "UPDATE_SKIPPED_VERSION": "",
    "MINUTE_USAGE_CHART_TYPE": "bar",
    "MINUTE_USAGE_INTERVAL_MINUTES": 5,
    "MINUTE_USAGE_RETENTION_DAYS": 3,
}
FIELD_META: dict[str, dict[str, Any]] = {
    **{key: {"kind": "text", "secret": key in SECRET_KEYS} for key in DEFAULT_CONFIG},
    "REFRESH_INTERVAL": {"kind": "int", "min": 5_000},
    "DEEPSEEK_PEAK_PRICING_ENABLED": {"kind": "bool"},
    "DEEPSEEK_PEAK_PERIOD_1_START": {"kind": "time"},
    "DEEPSEEK_PEAK_PERIOD_1_END": {"kind": "time"},
    "DEEPSEEK_PEAK_PERIOD_2_START": {"kind": "time"},
    "DEEPSEEK_PEAK_PERIOD_2_END": {"kind": "time"},
    "WIDGET_COMPACT_SIZE": {"kind": "int", "min": 88, "max": 124},
    "WIDGET_EXPANDED_SIZE": {"kind": "tuple_int"},
    "BG_COLOR": {"kind": "color"},
    "ACCENT_COLOR": {"kind": "color"},
    "TEXT_COLOR": {"kind": "color"},
    "EDGE_HIDE_ENABLED": {"kind": "bool"},
    "PANEL_AUTO_COLLAPSE_ON_DEACTIVATE": {"kind": "bool"},
    "UI_THEME": {"kind": "choice", "choices": ("system", "light", "dark")},
    "UPDATE_AUTO_CHECK_ENABLED": {"kind": "bool"},
    "MINUTE_USAGE_CHART_TYPE": {"kind": "choice", "choices": ("bar", "line")},
    "MINUTE_USAGE_INTERVAL_MINUTES": {"kind": "int", "min": 1, "max": 60},
    "MINUTE_USAGE_RETENTION_DAYS": {"kind": "int", "min": 1, "max": 365},
}

__all__ = ["DEFAULT_CONFIG", "FIELD_META", "OFFICIAL_HOSTS", "SECRET_KEYS"]
