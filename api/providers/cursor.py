"""Cursor personal subscription quota from the locally authenticated desktop client."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from collections.abc import Mapping
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, ClassVar

import requests

from api.http import HttpsSession
from api.providers.base import (
    FetchError,
    Provider,
    ProviderQuota,
    QuotaMetric,
    QuotaWindow,
    build_session,
)


class CursorProvider(Provider):
    id = "cursor"
    name = "Cursor"
    default_currency = "USD"
    supports_subscription_quota = True
    official_api_hosts = {"api2.cursor.sh"}
    credential_fields = {
        "GLOBAL_STORAGE": {
            "label": "Cursor globalStorage 目录（可选）",
            "secret": False,
            "optional": True,
            "directory": True,
            "hint": "实验性支持；默认读取 %APPDATA%\\Cursor\\User\\globalStorage\\state.vscdb",
        }
    }

    _API_BASE = "https://api2.cursor.sh/aiserver.v1.DashboardService"
    _ACCESS_TOKEN_KEY = "cursorAuth/accessToken"
    _activity_cache: ClassVar[
        dict[str, tuple[float, tuple[tuple[str, int], ...]]]
    ] = {}
    _activity_cache_lock: ClassVar[threading.Lock] = threading.Lock()
    _activity_cache_ttl_seconds = 60 * 60

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(config)
        self._session = build_session(retry_post=True)
        # 年度日用量是可选慢数据，不自动重试，避免弱网时拖住已成功的额度刷新。
        self._activity_session = HttpsSession()

    def close(self) -> None:
        self._session.close()
        self._activity_session.close()

    def _global_storage_dir(self) -> Path:
        configured = str(self.config_get("CURSOR_GLOBAL_STORAGE", "")).strip()
        if configured:
            return Path(configured).expanduser()
        appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return appdata / "Cursor" / "User" / "globalStorage"

    def _state_db_path(self) -> Path:
        return self._global_storage_dir() / "state.vscdb"

    def _access_token(self) -> str:
        path = self._state_db_path().resolve()
        uri = f"{path.as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as connection:
            connection.execute("PRAGMA query_only = ON")
            row = connection.execute(
                "SELECT value FROM ItemTable WHERE key = ?",
                (self._ACCESS_TOKEN_KEY,),
            ).fetchone()
        return str(row[0] if row else "").strip()

    def _credentials(self) -> str:
        access_token = self._access_token()
        if not access_token:
            raise ValueError("MISSING_ACCESS_TOKEN")
        return access_token

    def is_configured(self) -> bool:
        try:
            self._credentials()
            return True
        except (OSError, sqlite3.Error, ValueError):
            return False

    def snapshot_identity(self) -> str:
        try:
            access_token = self._credentials()
        except (OSError, sqlite3.Error, ValueError):
            return ""
        return hashlib.sha256(f"cursor:token:{access_token}".encode()).hexdigest()

    @staticmethod
    def _error(code: str, source: str, status_code: int | None = None) -> FetchError:
        messages = {
            "AUTH_EXPIRED": "Cursor 登录已失效，请在 Cursor 客户端重新登录",
            "RATE_LIMITED": "Cursor 用量查询过于频繁，请稍后重试",
            "NETWORK_TIMEOUT": "连接 Cursor 用量服务超时",
            "NETWORK_ERROR": "无法连接 Cursor 用量服务",
            "SERVER_ERROR": "Cursor 用量服务暂时异常",
            "INVALID_RESPONSE": "Cursor 用量数据结构已变化",
            "UNKNOWN_ERROR": "Cursor 用量查询发生未知错误",
        }
        message = messages[code]
        if code == "SERVER_ERROR" and status_code is not None:
            message = f"{message}（HTTP {status_code}）"
        return FetchError(code, source, message)

    def _post_rpc(
        self,
        method: str,
        access_token: str,
        source: str,
        body: Mapping[str, Any] | None = None,
        session: requests.Session | None = None,
    ) -> tuple[dict[str, Any] | None, FetchError | None]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Connect-Protocol-Version": "1",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            response = (session or self._session).post(
                f"{self._API_BASE}/{method}",
                headers=headers,
                json=dict(body or {}),
                timeout=(3, 10),
            )
        except requests.Timeout:
            return None, self._error("NETWORK_TIMEOUT", source)
        except requests.RequestException:
            return None, self._error("NETWORK_ERROR", source)
        if response.status_code in (401, 403):
            return None, self._error("AUTH_EXPIRED", source)
        if response.status_code == 429:
            return None, self._error("RATE_LIMITED", source)
        if response.status_code >= 500:
            return None, self._error("SERVER_ERROR", source, response.status_code)
        if not response.ok:
            return None, self._error("UNKNOWN_ERROR", source)
        try:
            payload = response.json()
        except (requests.JSONDecodeError, ValueError):
            return None, self._error("INVALID_RESPONSE", source)
        if not isinstance(payload, dict):
            return None, self._error("INVALID_RESPONSE", source)
        return payload, None

    @staticmethod
    def _amount(value: Any) -> Decimal | None:
        if value in (None, "") or isinstance(value, bool):
            return None
        try:
            result = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError("INVALID_AMOUNT") from None
        if not result.is_finite() or result < 0:
            raise ValueError("INVALID_AMOUNT")
        return result

    @classmethod
    def _money(cls, value: Any) -> str:
        amount = cls._amount(value)
        if amount is None:
            return "--"
        return f"${amount / Decimal(100):.2f}"

    @classmethod
    def _timestamp(cls, value: Any) -> datetime:
        amount = cls._amount(value)
        if amount is None:
            raise ValueError("INVALID_TIMESTAMP")
        if amount >= Decimal("100000000000"):
            amount /= 1000
        try:
            return datetime.fromtimestamp(float(amount), timezone.utc)
        except (OSError, OverflowError, ValueError):
            raise ValueError("INVALID_TIMESTAMP") from None

    @classmethod
    def _usage_percent(cls, plan_usage: Mapping[str, Any]) -> float:
        used = cls._amount(plan_usage.get("autoPercentUsed"))
        if used is None:
            used = cls._amount(plan_usage.get("totalPercentUsed"))
        if used is None:
            included = cls._amount(plan_usage.get("includedSpend"))
            limit = cls._amount(plan_usage.get("limit"))
            used = included / limit * 100 if included is not None and limit else Decimal(0)
        return float(max(Decimal(0), min(Decimal(100), used)))

    @classmethod
    def _money_or_percent(cls, spend: Any, percent: Any) -> str:
        if cls._amount(spend) is not None:
            return cls._money(spend)
        value = cls._amount(percent)
        if value is None:
            return "--"
        value = max(Decimal(0), min(Decimal(100), value))
        return f"{float(value):.0f}%"

    @classmethod
    def _ratio_text(cls, used: Any, limit: Any) -> str:
        if cls._amount(used) is None or cls._amount(limit) is None:
            return "--"
        return f"{cls._money(used)} / {cls._money(limit)}"

    @staticmethod
    def _billing_period(start: datetime, end: datetime) -> str:
        return f"{start.astimezone():%m-%d} — {end.astimezone():%m-%d}"

    @classmethod
    def _activity_cache_key(cls, access_token: str) -> str:
        return hashlib.sha256(f"cursor:activity:{access_token}".encode()).hexdigest()

    @classmethod
    def _daily_activity(cls, payload: Mapping[str, Any]) -> tuple[tuple[str, int], ...]:
        rows = payload.get("dailySpend")
        if not isinstance(rows, list):
            raise ValueError("INVALID_ACTIVITY")
        daily: dict[str, int] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("INVALID_ACTIVITY")
            day = cls._timestamp(row.get("day")).astimezone().date().isoformat()
            tokens = cls._amount(row.get("totalTokens"))
            if tokens is None or tokens != tokens.to_integral_value():
                raise ValueError("INVALID_ACTIVITY")
            daily[day] = daily.get(day, 0) + int(tokens)
        return tuple(sorted(daily.items()))

    def _activity_snapshot(
        self, access_token: str
    ) -> tuple[tuple[tuple[str, int], ...], str]:
        cache_key = self._activity_cache_key(access_token)
        now = time.monotonic()
        with self._activity_cache_lock:
            cached = self._activity_cache.get(cache_key)
        if cached and now - cached[0] < self._activity_cache_ttl_seconds:
            return cached[1], "cache"

        local_now = datetime.now().astimezone()
        period_end = local_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            days=1
        )
        period_start = period_end - timedelta(days=365)
        payload, error = self._post_rpc(
            "GetDailySpendByCategory",
            access_token,
            "Cursor Token 活动",
            {
                "periodStartMs": str(int(period_start.timestamp() * 1000)),
                "periodEndMs": str(int(period_end.timestamp() * 1000)),
                "groupBy": "SPEND_GROUP_BY_CATEGORY_USAGE_TYPE",
                "spendType": "SPEND_TYPE_ALL",
            },
            self._activity_session,
        )
        if error is not None or payload is None:
            return (cached[1], "cache") if cached else ((), "")
        try:
            activity = self._daily_activity(payload)
        except (TypeError, ValueError):
            return (cached[1], "cache") if cached else ((), "")
        with self._activity_cache_lock:
            self._activity_cache[cache_key] = (time.monotonic(), activity)
        return activity, "interface"

    def fetch_quota(self) -> tuple[ProviderQuota | None, FetchError | None]:
        source = "Cursor 订阅额度"
        try:
            access_token = self._credentials()
        except (OSError, sqlite3.Error, ValueError):
            return None, FetchError(
                "NOT_CONFIGURED", source, "未找到可用的 Cursor 客户端登录信息"
            )

        usage, error = self._post_rpc("GetCurrentPeriodUsage", access_token, source)
        if error is not None:
            return None, error
        plan, error = self._post_rpc("GetPlanInfo", access_token, source)
        if error is not None:
            return None, error

        try:
            if not isinstance(usage, dict) or not isinstance(plan, dict):
                raise ValueError("INVALID_RESPONSE")
            plan_usage = usage.get("planUsage")
            plan_info = plan.get("planInfo")
            spend_limit = usage.get("spendLimitUsage")
            if not isinstance(plan_usage, dict) or not isinstance(plan_info, dict):
                raise ValueError("INVALID_RESPONSE")
            if not isinstance(spend_limit, dict):
                spend_limit = {}

            cycle_start = self._timestamp(usage.get("billingCycleStart"))
            cycle_end = self._timestamp(usage.get("billingCycleEnd"))
            used_percent = self._usage_percent(plan_usage)
            plan_name = str(plan_info.get("planName") or "").strip()

            metrics = (
                QuotaMetric(
                    "套餐用量",
                    self._ratio_text(
                        plan_usage.get("includedSpend"), plan_usage.get("limit")
                    ),
                ),
                QuotaMetric(
                    "额外消费",
                    self._ratio_text(
                        spend_limit.get("individualUsed"),
                        spend_limit.get("individualLimit"),
                    ),
                ),
            )
            statistics = (
                QuotaMetric("套餐", plan_name or "--"),
                QuotaMetric("Bonus", self._money(plan_usage.get("bonusSpend"))),
                QuotaMetric(
                    "Auto",
                    self._money_or_percent(
                        plan_usage.get("autoSpend"), plan_usage.get("autoPercentUsed")
                    ),
                ),
                QuotaMetric(
                    "指定模型",
                    self._money_or_percent(
                        plan_usage.get("apiSpend"), plan_usage.get("apiPercentUsed")
                    ),
                ),
                QuotaMetric("账期", self._billing_period(cycle_start, cycle_end)),
            )
        except (TypeError, ValueError):
            return None, self._error("INVALID_RESPONSE", source)

        activity, activity_source = self._activity_snapshot(access_token)

        return ProviderQuota(
            windows=(
                QuotaWindow(
                    "cursor-monthly",
                    "每月额度",
                    used_percent,
                    resets_at=cycle_end,
                ),
            ),
            metrics=metrics,
            statistics=statistics,
            plan=plan_name,
            activity=activity,
            weekly_activity=activity,
            activity_source=activity_source,
            weekly_activity_source=activity_source,
            statistics_source="interface",
        ), None


__all__ = ["CursorProvider"]
