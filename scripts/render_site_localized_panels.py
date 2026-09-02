from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Website screenshots must use production widgets without reading the user's Qt state.
os.environ.setdefault("QT_QPA_PLATFORM", "windows" if os.name == "nt" else "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "2")
os.environ.setdefault("APPDATA", str(ROOT / ".test-appdata"))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from api.providers.base import QuotaMetric, QuotaWindow
from config import runtime as config_manager
from config.defaults import DEFAULT_CONFIG
from data.store import PerProviderData, TokenData
from ui.i18n import configure_language
from ui.qt_panel import ANNUAL_PANEL_HEIGHT, PANEL_HEIGHT, PANEL_MAX_WIDTH, MainPanel
from ui.qt_theme import DARK_THEME, LIGHT_THEME, configure_theme


OUTPUT_DIR = ROOT / "site" / "assets"
LOCALES = ("zh-tw", "en", "ja", "ko")


def codex_demo() -> TokenData:
    today = date(2026, 8, 31)
    daily_usage = []
    for offset in range(364, -1, -1):
        day = today - timedelta(days=offset)
        # A deterministic cadence keeps the annual activity view dense but repeatable.
        active = (day.toordinal() * 17 + day.month * 13) % 11
        tokens = 0 if active < 2 else (active + 2) * 7_800_000
        daily_usage.append({"date": day.isoformat(), "tokens": tokens, "cost_cny": 0})

    weekly_tokens = [32_000_000, 48_000_000, 41_000_000, 64_000_000, 52_000_000, 29_000_000, 43_000_000]
    weekly_usage = [
        {
            "date": (today - timedelta(days=6 - index)).isoformat(),
            "tokens": tokens,
            "cost_cny": 0,
        }
        for index, tokens in enumerate(weekly_tokens)
    ]
    reset = datetime(2026, 9, 4, 12, 26, tzinfo=timezone.utc)
    active_until = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)
    windows = [QuotaWindow("codex-weekly", "每周额度", 35, resets_at=reset)]
    statistics = [
        QuotaMetric("累计 Token 数", "11.4亿", raw_value=1_140_000_000, value_kind="tokens"),
        QuotaMetric("峰值 Token 数", "799.5万", raw_value=7_995_000, value_kind="tokens"),
        QuotaMetric("最长任务时长", "52分 35秒", raw_value=3_155, value_kind="seconds"),
        QuotaMetric("当前连续天数", "7 天", raw_value=7, value_kind="days"),
        QuotaMetric("最长连续天数", "27 天", raw_value=27, value_kind="days"),
    ]
    provider = PerProviderData(
        "codex",
        "Codex",
        quota_windows=windows,
        quota_statistics=statistics,
        account_plan="Pro",
        account_plan_active_until=active_until,
        quota_source="interface",
        weekly_activity_source="interface",
        activity_source="interface",
        statistics_source="interface",
        status="ok",
    )
    return TokenData(
        status="ok",
        last_success_at=datetime.now(),
        quota_windows=windows,
        quota_statistics=statistics,
        account_plan="Pro",
        account_plan_active_until=active_until,
        quota_source="interface",
        weekly_activity_source="interface",
        activity_source="interface",
        statistics_source="interface",
        daily_usage=daily_usage,
        weekly_usage=weekly_usage,
        per_provider=[provider],
    )


def deepseek_demo() -> TokenData:
    today = date(2026, 8, 31)
    costs = [Decimal("2.18"), Decimal("2.83"), Decimal("2.51"), Decimal("3.46"), Decimal("3.02"), Decimal("1.94"), Decimal("3.94")]
    daily_usage = [
        {
            "date": (today - timedelta(days=6 - index)).isoformat(),
            "tokens": 1_600_000 + index * 370_000,
            "cost_cny": cost,
        }
        for index, cost in enumerate(costs)
    ]
    minute_usage = []
    minute_cost_usage = []
    for index, minute in enumerate(range(0, 24 * 60, 60)):
        cache_hit = 3_400 + ((index * 7) % 9) * 620
        cache_miss = 2_100 + ((index * 5) % 7) * 540
        output = 1_200 + ((index * 3) % 8) * 410
        minute_usage.extend(
            (
                {"minute": minute, "token_type": "PROMPT_CACHE_HIT_TOKEN", "token_amount": cache_hit},
                {"minute": minute, "token_type": "PROMPT_CACHE_MISS_TOKEN", "token_amount": cache_miss},
                {"minute": minute, "token_type": "RESPONSE_TOKEN", "token_amount": output},
            )
        )
        minute_cost_usage.append(
            {"minute": minute, "cost_cny": Decimal(cache_hit + cache_miss + output) / Decimal("100000")}
        )

    provider = PerProviderData(
        "deepseek",
        "DeepSeek",
        balance_cny=128.64,
        monthly_cost_cny=50.69,
        monthly_usage_tokens=9_742_800,
        today_cost_cny=3.94,
        today_tokens=279_978,
        total_cost_cny=570.92,
        status="ok",
    )
    return TokenData(
        currency="CNY",
        status="ok",
        last_success_at=datetime.now(),
        balance_cny=128.64,
        balance_tokens=12_864_000,
        monthly_cost_cny=50.69,
        monthly_usage_tokens=9_742_800,
        today_cost_cny=3.94,
        today_tokens=279_978,
        total_cost_cny=570.92,
        daily_usage=daily_usage,
        minute_usage=minute_usage,
        minute_cost_usage=minute_cost_usage,
        minute_usage_status="estimated",
        minute_usage_date=today.isoformat(),
        minute_usage_days=[today.isoformat()],
        minute_usage_source="estimated",
        per_provider=[provider],
    )


def render_panel(app: QApplication, locale: str, theme: str, kind: str) -> Path:
    config_manager._config = {**DEFAULT_CONFIG, "ACTIVE_PROVIDER": "codex" if kind == "codex" else "deepseek"}
    configure_language(app, locale)
    configure_theme(
        app,
        theme,
        light_accent=LIGHT_THEME.accent,
        dark_accent=DARK_THEME.accent,
        light_panel_opacity=100,
        dark_panel_opacity=100,
    )

    panel = MainPanel()
    panel.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    data = codex_demo() if kind == "codex" else deepseek_demo()
    panel.update_data(data)
    if kind == "deepseek":
        panel._set_activity_view("minute")
    height = ANNUAL_PANEL_HEIGHT if kind == "codex" else PANEL_HEIGHT
    panel.setFixedSize(PANEL_MAX_WIDTH, height)
    panel.show()
    for _ in range(4):
        app.processEvents()

    stem = "panel-light" if kind == "codex" and theme == "light" else "panel-dark" if kind == "codex" else "panel-deepseek"
    output = OUTPUT_DIR / f"{stem}-{locale}.png"
    pixmap = panel.grab()
    if not pixmap.save(str(output), "PNG"):
        raise RuntimeError(f"Unable to save {output}")
    expected = (PANEL_MAX_WIDTH * 2, height * 2)
    if (pixmap.width(), pixmap.height()) != expected:
        raise RuntimeError(f"Unexpected render size for {output}: {pixmap.width()}x{pixmap.height()}, expected {expected[0]}x{expected[1]}")
    panel.close()
    panel.deleteLater()
    app.processEvents()
    return output


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    outputs = []
    for locale in LOCALES:
        outputs.extend(
            (
                render_panel(app, locale, "light", "codex"),
                render_panel(app, locale, "dark", "codex"),
                render_panel(app, locale, "dark", "deepseek"),
            )
        )
    print("\n".join(str(path.relative_to(ROOT)) for path in outputs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
