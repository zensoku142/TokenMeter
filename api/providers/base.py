"""Shared data types and HTTP policy for platform providers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


@dataclass(frozen=True)
class FetchError:
    code: str
    source: str
    message: str


@dataclass(frozen=True)
class ProviderBalance:
    currency: str = "CNY"
    amount: Decimal | None = None
    token_estimate: int = 0


@dataclass
class ModelUsage:
    model: str
    tokens: int = 0
    cost_cny: Decimal = Decimal("0")


@dataclass
class ProviderSummary:
    month_cost: Decimal | None = None
    month_tokens: int = 0
    remaining_tokens: int = 0


def _decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"无效数值: {value!r}") from None


def safe_int(value: Any) -> int:
    try:
        return int(_decimal(value))
    except ValueError:
        return 0


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=2,
        read=2,
        backoff_factor=0.5,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=False,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class Provider:
    id = "base"
    name = "Base"
    default_currency = "CNY"
    default_base = ""
    official_api_hosts: set[str] = set()
    supports_daily_usage = False
    supports_cost = False
    supports_estimated_minute_usage = False
    supports_cookie_acquisition = False
    credential_fields: dict[str, dict[str, Any]] = {}

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self._config = config

    def config_get(self, key: str, default: Any = None) -> Any:
        if self._config is not None:
            return self._config.get(key, default)
        import config_manager

        return config_manager.get(key, default)

    def is_configured(self) -> bool:
        for field, meta in self.credential_fields.items():
            if field == "BASE" or meta.get("optional"):
                continue
            if str(self.config_get(f"{self.id.upper()}_{field}", "")).strip():
                return True
        return False

    def reset_refresh_cache(self) -> None:
        """Reset data that is valid only within one refresh task."""

    def close(self) -> None:
        """Release provider-owned resources."""

    def fetch_balance(self) -> tuple[ProviderBalance | None, FetchError | None]:
        return None, None

    def fetch_summary(self) -> tuple[ProviderSummary | None, FetchError | None]:
        return None, None

    def fetch_payloads(
        self, months: list[tuple[int, int]]
    ) -> tuple[list[dict[str, Any]], list[FetchError]]:
        return [], []
