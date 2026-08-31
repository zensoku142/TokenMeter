"""NayutoAI relay provider using portal Bearer authentication and request usage."""

from __future__ import annotations

import threading
import time
import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from api import browser_cookie
from api.providers.base import (
    ExactMinuteUsage,
    FetchError,
    Provider,
    ProviderBalance,
    ProviderSummary,
    _decimal,
    build_session,
)
from config import runtime as config_manager
from data import history

_NAYUTO_BASE = "https://nayutoai.xyz"
_NAYUTO_LOGIN_URL = f"{_NAYUTO_BASE}/console/dashboard"
_PAGE_SIZE = 50
_MAX_PAGES = 200
_REQUEST_TIMEOUT_SECONDS = 20
_SYNC_BUDGET_SECONDS = 20


class _NayutoAPIError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


def _shanghai_timezone():
    try:
        return ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8), "Asia/Shanghai")


def _fetch_error(source: str, exc: Exception) -> FetchError:
    if isinstance(exc, _NayutoAPIError):
        return FetchError(exc.code, source, exc.message)
    if isinstance(exc, (KeyError, TypeError, ValueError)):
        return FetchError("INVALID_RESPONSE", source, "NayutoAI 返回结构已变化")
    config_manager.logger().exception(
        "Nayuto request failed: source=%s error=%s", source, type(exc).__name__
    )
    return FetchError("UNKNOWN_ERROR", source, "读取 NayutoAI 数据时发生未知错误")


def _parse_created_at(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("created_at is missing")
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("created_at must include UTC offset")
    return parsed.astimezone(_shanghai_timezone())


def _token_value(record: Mapping[str, Any], field: str) -> int:
    if field not in record:
        raise ValueError(f"{field} is missing")
    value = _decimal(record.get(field))
    if not value.is_finite() or value < 0 or value != value.to_integral_value():
        raise ValueError(f"{field} is invalid")
    return int(value)


def _record_key(record: Mapping[str, Any]) -> tuple[Any, ...]:
    request_id = str(record.get("request_id") or "").strip()
    if request_id:
        return ("request_id", request_id)
    record_id = str(record.get("id") or "").strip()
    if record_id:
        return ("id", record_id)
    return (
        "fallback",
        str(record.get("created_at") or ""),
        str(record.get("api_key_id") or ""),
        str(record.get("model") or ""),
        str(record.get("input_tokens") or ""),
        str(record.get("cache_read_tokens") or ""),
        str(record.get("output_tokens") or ""),
        str(record.get("actual_cost") or ""),
        str(record.get("duration_ms") or ""),
    )


class NayutoProvider(Provider):
    id = "nayuto"
    name = "NayutoAI"
    default_currency = "USD"
    default_base = _NAYUTO_BASE
    official_api_hosts = {"nayutoai.xyz"}
    supports_daily_usage = True
    supports_cost = True
    supports_exact_minute_usage = True
    supports_model_usage = True
    supports_browser_credential_acquisition = True
    credential_acquisition_label = "Bearer"
    credential_acquisition_automatic = False
    credential_fields = {
        "AUTH": {
            "label": "Bearer Token",
            "secret": True,
            "hint": "建议使用下方隔离浏览器登录后自动获取",
            "browser_acquisition": True,
        },
        "BASE": {
            "label": "平台地址",
            "secret": False,
            "hint": f"默认 {_NAYUTO_BASE}",
        },
    }

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(config)
        self._session = build_session()
        self._stats_cache: dict[str, Any] | None = None
        self._exact_minute_usage: ExactMinuteUsage | None = None

    def close(self) -> None:
        self._session.close()

    def reset_refresh_cache(self) -> None:
        self._stats_cache = None
        self._exact_minute_usage = None

    def is_configured(self) -> bool:
        return bool(str(self.config_get("NAYUTO_AUTH", "")).strip())

    def _base_url(self) -> str:
        return str(self.config_get("NAYUTO_BASE", self.default_base)).strip().rstrip("/")

    def _authorization(self) -> str:
        value = str(self.config_get("NAYUTO_AUTH", "")).strip()
        if not value:
            raise _NayutoAPIError("NOT_CONFIGURED", "尚未连接 NayutoAI")
        return value if value.lower().startswith("bearer ") else f"Bearer {value}"

    def _request_json(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            response = self._session.get(
                f"{self._base_url()}{path}",
                params=params,
                headers={"Accept": "application/json", "Authorization": self._authorization()},
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.Timeout as exc:
            raise _NayutoAPIError("NETWORK_TIMEOUT", "NayutoAI 请求超时") from exc
        except requests.RequestException as exc:
            raise _NayutoAPIError("NETWORK_ERROR", "无法连接 NayutoAI") from exc
        if response.status_code in {401, 403}:
            raise _NayutoAPIError("AUTH_EXPIRED", "NayutoAI 登录凭据已失效，请重新连接")
        if response.status_code == 429:
            raise _NayutoAPIError("RATE_LIMITED", "NayutoAI 请求过于频繁，请稍后重试")
        if response.status_code >= 500:
            raise _NayutoAPIError("SERVER_ERROR", "NayutoAI 暂时不可用")
        if response.status_code >= 400:
            raise _NayutoAPIError("HTTP_ERROR", "NayutoAI 请求失败")
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise _NayutoAPIError("INVALID_RESPONSE", "NayutoAI 返回了无效数据") from exc
        if not isinstance(payload, dict):
            raise _NayutoAPIError("INVALID_RESPONSE", "NayutoAI 返回结构已变化")
        return payload

    def fetch_balance(self) -> tuple[ProviderBalance | None, FetchError | None]:
        try:
            payload = self._request_json("/portal/auth/me")
            if str(payload.get("status") or "").strip().lower() != "active":
                return None, FetchError("ACCOUNT_INACTIVE", "账户余额", "NayutoAI 账户当前不可用")
            if "balance" not in payload:
                raise ValueError("balance is missing")
            balance = _decimal(payload["balance"])
            if not balance.is_finite():
                raise ValueError("balance is invalid")
            return ProviderBalance("USD", balance), None
        except Exception as exc:
            return None, _fetch_error("账户余额", exc)

    def _stats(self) -> dict[str, Any]:
        if self._stats_cache is None:
            payload = self._request_json("/portal/user/dashboard/stats")
            for field in ("today_cost", "today_tokens", "total_cost", "total_tokens"):
                if field not in payload:
                    raise ValueError(f"{field} is missing")
            for field in ("today_cost", "total_cost"):
                amount = _decimal(payload[field])
                if not amount.is_finite() or amount < 0:
                    raise ValueError(f"{field} is invalid")
            _token_value(payload, "today_tokens")
            _token_value(payload, "total_tokens")
            self._stats_cache = payload
        return self._stats_cache

    def fetch_summary(self) -> tuple[ProviderSummary | None, FetchError | None]:
        try:
            stats = self._stats()
            # The endpoint exposes today/lifetime totals, not a calendar-month total.
            # Request details remain the authoritative source for the panel's month card.
            return ProviderSummary(
                today_cost=_decimal(stats["today_cost"]),
                today_tokens=_token_value(stats, "today_tokens"),
                total_cost=_decimal(stats["total_cost"]),
            ), None
        except Exception as exc:
            return None, _fetch_error("账户摘要", exc)

    @staticmethod
    def acquired_credential_values(value: str) -> dict[str, str]:
        normalized = str(value or "").strip()
        return {"AUTH": normalized} if normalized else {}

    @staticmethod
    def describe_acquire_error(exc: Exception) -> str:
        code = str(exc) if isinstance(exc, RuntimeError) else "ACQUIRE_UNEXPECTED"
        messages = {
            "CHROME_NOT_FOUND": "未检测到 Chrome 或 Edge，请先安装浏览器，或手动粘贴 Bearer",
            "USER_DATA_DIR_FAILED": "无法创建浏览器用户数据目录",
            "NO_FREE_CDP_PORT": "无法分配浏览器调试端口",
            "CHROME_LAUNCH_FAILED": "浏览器启动失败，请检查权限或安全软件",
            "BROWSER_NOT_READY": "浏览器调试接口未就绪，请稍后重试",
            "NAYUTO_AUTH_EMPTY": "未捕获到 NayutoAI Bearer，请确认已登录后再点击完成采集",
            "NAYUTO_AUTH_INVALID": "捕获到的 NayutoAI 登录凭据验证失败，请重新登录",
            "NAYUTO_ACCOUNT_INACTIVE": "NayutoAI 账户当前不可用",
            "ACQUIRE_UNEXPECTED": "采集 NayutoAI 登录凭据时出现未预期错误",
        }
        return messages.get(code, f"采集失败：{code}")

    @staticmethod
    def acquire_credentials_via_chrome(
        stop_event: threading.Event,
        use_edge: bool = False,
        user_data_dir: str | None = None,
    ) -> str:
        session = browser_cookie.open_chrome_session(
            stop_event,
            acquire_url=_NAYUTO_LOGIN_URL,
            profile_name="nayuto-chrome",
            use_edge=use_edge,
            user_data_dir=user_data_dir
            or str(config_manager.CONFIG_DIR / "nayuto-chrome"),
        )
        try:
            if not stop_event.wait(timeout=180):
                raise RuntimeError("NAYUTO_AUTH_EMPTY")
            headers = session.capture_request_headers(
                url_prefix=f"{_NAYUTO_BASE}/portal/",
                timeout_seconds=5.0,
            )
            authorization = next(
                (
                    str(value).strip()
                    for key, value in headers.items()
                    if str(key).lower() == "authorization"
                ),
                "",
            )
            if not authorization.lower().startswith("bearer "):
                raise RuntimeError("NAYUTO_AUTH_EMPTY")
            result = session.fetch_json(
                url=f"{_NAYUTO_BASE}/portal/auth/me",
                headers={"Authorization": authorization},
                allowed_domains=("nayutoai.xyz",),
            )
            if result.status_code in {401, 403}:
                raise RuntimeError("NAYUTO_AUTH_INVALID")
            if result.status_code != 200 or not isinstance(result.payload, dict):
                raise RuntimeError("NAYUTO_AUTH_INVALID")
            if str(result.payload.get("status") or "").strip().lower() != "active":
                raise RuntimeError("NAYUTO_ACCOUNT_INACTIVE")
            if "balance" not in result.payload:
                raise RuntimeError("NAYUTO_AUTH_INVALID")
            return authorization
        finally:
            session.close()

    def _fetch_usage_records(
        self, earliest_date: date
    ) -> tuple[Iterable[dict[str, Any]], int]:
        if self.history_scope:
            return self._sync_usage_records(earliest_date)
        records: list[dict[str, Any]] = []
        raw_count = 0
        total: int | None = None
        for page in range(1, _MAX_PAGES + 1):
            payload = self._request_json(
                "/portal/user/usage",
                params={"page": page, "page_size": _PAGE_SIZE},
            )
            items = payload.get("items")
            if not isinstance(items, list):
                raise _NayutoAPIError("INVALID_RESPONSE", "NayutoAI 用量列表结构已变化")
            if total is None:
                try:
                    total = int(payload["total"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise _NayutoAPIError(
                        "INVALID_RESPONSE", "NayutoAI 用量分页结构已变化"
                    ) from exc
                if total < 0:
                    raise _NayutoAPIError("INVALID_RESPONSE", "NayutoAI 用量分页结构已变化")
            if not items:
                return records, total
            raw_count += len(items)
            records.extend(item for item in items if isinstance(item, dict))

            page_dates: list[date] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    page_dates.append(_parse_created_at(item.get("created_at")).date())
                except (TypeError, ValueError):
                    continue
            if raw_count >= total:
                return records, total
            if page_dates and min(page_dates) < earliest_date:
                return records, total
            if len(items) < _PAGE_SIZE:
                return records, total
        raise _NayutoAPIError(
            "INVALID_RESPONSE", f"NayutoAI 用量分页超过安全上限（{_MAX_PAGES} 页）"
        )

    def _sync_usage_records(self, earliest_date: date) -> tuple[Iterable[dict[str, Any]], int]:
        scope = self.history_scope
        assert scope is not None
        state = history.load_nayuto_sync(scope)
        deadline = time.monotonic() + _SYNC_BUDGET_SECONDS
        first = self._request_json("/portal/user/usage", params={"page": 1, "page_size": _PAGE_SIZE})
        total = int(first.get("total", -1))
        items = first.get("items")
        if total < 0 or not isinstance(items, list):
            raise _NayutoAPIError("INVALID_RESPONSE", "NayutoAI 用量分页结构已变化")

        def key(item):
            return hashlib.sha256(json.dumps(_record_key(item)).encode()).hexdigest()

        first_key = key(items[0]) if items and isinstance(items[0], dict) else ""
        resumed = int(state.get("page", 1)) > 1
        if resumed:
            # page 接口没有游标；按新增条数修正偏移，并回读一页防止边界漂移。
            overlap = 1 if _MAX_PAGES > 2 else 0
            page = max(1, int(state["page"]) + max(0, total - int(state.get("total", total))) // _PAGE_SIZE - overlap)
            if state.get("anchor_seen"):
                state["anchor_seen"] = int(state["anchor_seen"]) + page - int(state["page"])
        else:
            page = 1
            covered = str(state.get("covered", ""))
            state.update(
                target=earliest_date.isoformat(), pending_head=first_key,
                # 每日重新校对请求范围，补上头部重叠窗口之外的计费修订。
                anchor=state.get("head", "") if (
                    covered and covered <= earliest_date.isoformat()
                    and state.get("full_day") == date.today().isoformat()
                ) else "",
                anchor_seen=0,
            )
        state["target"] = min(str(state["target"]), earliest_date.isoformat())
        if state.get("covered") and earliest_date.isoformat() < state["covered"]:
            state["anchor"] = ""
        original_total = total
        page_budget = _MAX_PAGES - (1 if page != 1 else 0)
        for attempt in range(page_budget):
            # 续传回读一页后至少向前处理一页，避免慢网络下永远停在同一重叠页。
            if attempt >= (2 if resumed and page_budget > 1 else 1) and time.monotonic() >= deadline:
                break
            payload = first if page == 1 else self._request_json(
                "/portal/user/usage", params={"page": page, "page_size": _PAGE_SIZE}
            )
            items = payload.get("items")
            if not isinstance(items, list):
                raise _NayutoAPIError("INVALID_RESPONSE", "NayutoAI 用量列表结构已变化")
            total = int(payload.get("total", total))
            rows = []
            page_days = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                record_key = key(item)
                if record_key == state.get("anchor"):
                    state["anchor_seen"] = page
                try:
                    day = _parse_created_at(item.get("created_at")).date().isoformat()
                except (TypeError, ValueError):
                    continue
                page_days.append(day)
                # 只缓存计费归一化所需字段，不能把接口的额外账户信息写入本地明细。
                record = {name: item[name] for name in (
                    "request_id", "id", "created_at", "api_key_id", "model", "input_tokens",
                    "cache_read_tokens", "output_tokens", "actual_cost", "duration_ms",
                ) if name in item}
                rows.append((record_key, day, record))
            reached_end = not items or len(items) < _PAGE_SIZE or page * _PAGE_SIZE >= total
            reached_target = bool(page_days and min(page_days) < state["target"])
            reached_anchor = bool(state.get("anchor_seen") and page > state["anchor_seen"])
            complete = reached_end or reached_target or reached_anchor
            state.update(page=page + 1, total=total)
            if complete:
                state.update(page=1, head=state["pending_head"],
                             covered=min(state.get("covered") or state["target"], state["target"]))
                if not state.get("anchor"):
                    state["full_day"] = date.today().isoformat()
            history.save_nayuto_page(scope, rows, state)
            if complete:
                if (resumed and first_key != state["pending_head"]) or total != original_total:
                    # 续传期间新增的头部记录另起一轮追平，不能把有缺口的快照当成完整账单。
                    break
                history.prune_nayuto_records(scope, earliest_date)
                return history.nayuto_records(scope, earliest_date), total
            page += 1
        raise _NayutoAPIError("SYNC_INCOMPLETE", "NayutoAI 历史正在分批同步，已保存进度，下次刷新继续")

    @staticmethod
    def _normalize_records(
        records: Iterable[dict[str, Any]],
        requested_months: set[tuple[int, int]],
    ) -> tuple[list[dict[str, Any]], ExactMinuteUsage, int]:
        seen: set[tuple[Any, ...]] = set()
        daily: dict[
            tuple[int, int], dict[date, dict[str, dict[str, Any]]]
        ] = {}
        minute_tokens: dict[tuple[date, int, str], int] = {}
        minute_costs: dict[tuple[date, int], Decimal] = {}
        minute_models: dict[tuple[date, int, str], dict[str, Any]] = {}
        usage_dates: set[date] = set()
        dirty_rows = 0

        for record in records:
            key = _record_key(record)
            if key in seen:
                continue
            try:
                observed_at = _parse_created_at(record.get("created_at"))
                usage_date = observed_at.date()
                month_key = (usage_date.month, usage_date.year)
                if month_key not in requested_months:
                    continue
                input_miss = _token_value(record, "input_tokens")
                input_hit = _token_value(record, "cache_read_tokens")
                output = _token_value(record, "output_tokens")
                if "actual_cost" not in record:
                    raise ValueError("actual_cost is missing")
                actual_cost = _decimal(record.get("actual_cost"))
                if not actual_cost.is_finite() or actual_cost < 0:
                    raise ValueError("actual_cost is invalid")
            except (TypeError, ValueError):
                dirty_rows += 1
                continue
            seen.add(key)

            # status is deliberately not filtered: the provider's billed actual_cost
            # remains authoritative until failed/cancelled billing semantics are proven.
            model = str(record.get("model") or "unknown").strip() or "unknown"
            model_data = daily.setdefault(month_key, {}).setdefault(
                usage_date, {}
            ).setdefault(
                model,
                {
                    "PROMPT_CACHE_HIT_TOKEN": 0,
                    "PROMPT_CACHE_MISS_TOKEN": 0,
                    "RESPONSE_TOKEN": 0,
                    "cost": Decimal("0"),
                },
            )
            model_data["PROMPT_CACHE_HIT_TOKEN"] += input_hit
            model_data["PROMPT_CACHE_MISS_TOKEN"] += input_miss
            model_data["RESPONSE_TOKEN"] += output
            model_data["cost"] += actual_cost

            minute = observed_at.hour * 60 + observed_at.minute
            for token_type, amount in (
                ("PROMPT_CACHE_HIT_TOKEN", input_hit),
                ("PROMPT_CACHE_MISS_TOKEN", input_miss),
                ("RESPONSE_TOKEN", output),
            ):
                row_key = (usage_date, minute, token_type)
                minute_tokens[row_key] = minute_tokens.get(row_key, 0) + amount
            cost_key = (usage_date, minute)
            minute_costs[cost_key] = minute_costs.get(cost_key, Decimal("0")) + actual_cost
            minute_model = minute_models.setdefault(
                (usage_date, minute, model),
                {
                    "cache_hit_tokens": 0,
                    "cache_miss_tokens": 0,
                    "output_tokens": 0,
                    "cost_cny": Decimal("0"),
                },
            )
            minute_model["cache_hit_tokens"] += input_hit
            minute_model["cache_miss_tokens"] += input_miss
            minute_model["output_tokens"] += output
            minute_model["cost_cny"] += actual_cost
            usage_dates.add(usage_date)

        payloads: list[dict[str, Any]] = []
        for month, year in sorted(requested_months, key=lambda item: (item[1], item[0])):
            days: list[dict[str, Any]] = []
            for usage_date, models in sorted(
                daily.get((month, year), {}).items(), key=lambda item: item[0]
            ):
                items: list[dict[str, Any]] = []
                for model, values in sorted(models.items()):
                    items.append(
                        {
                            "model": model,
                            "usage": [
                                {"type": "PROMPT_CACHE_HIT_TOKEN", "amount": values["PROMPT_CACHE_HIT_TOKEN"]},
                                {"type": "PROMPT_CACHE_MISS_TOKEN", "amount": values["PROMPT_CACHE_MISS_TOKEN"]},
                                {"type": "RESPONSE_TOKEN", "amount": values["RESPONSE_TOKEN"]},
                                {"type": "cost_cny", "amount": str(values["cost"])},
                            ],
                        }
                    )
                days.append({"date": usage_date.isoformat(), "data": items})
            payloads.append({"days": days, "total": [], "_month": (month, year)})

        token_rows = tuple(
            {
                "usage_date": usage_date,
                "minute": minute,
                "token_type": token_type,
                "token_amount": amount,
            }
            for (usage_date, minute, token_type), amount in sorted(minute_tokens.items())
        )
        cost_rows = tuple(
            {
                "usage_date": usage_date,
                "minute": minute,
                "cost_cny": amount,
            }
            for (usage_date, minute), amount in sorted(minute_costs.items())
        )
        model_rows = tuple(
            {
                "usage_date": usage_date,
                "minute": minute,
                "model": model,
                "cache_hit_tokens": values["cache_hit_tokens"],
                "cache_miss_tokens": values["cache_miss_tokens"],
                "output_tokens": values["output_tokens"],
                "cost_cny": values["cost_cny"],
            }
            for (usage_date, minute, model), values in sorted(minute_models.items())
        )
        return (
            payloads,
            ExactMinuteUsage(
                usage_dates=tuple(sorted(usage_dates)),
                token_rows=token_rows,
                cost_rows=cost_rows,
                complete_months=tuple(sorted(requested_months)),
                model_rows=model_rows,
            ),
            dirty_rows,
        )

    def fetch_payloads(
        self, months: list[tuple[int, int]]
    ) -> tuple[list[dict[str, Any]], list[FetchError]]:
        requested = set(dict.fromkeys(months))
        if not requested:
            self._exact_minute_usage = ExactMinuteUsage()
            return [], []
        try:
            earliest = min(date(year, month, 1) for month, year in requested)
            records, _total = self._fetch_usage_records(earliest)
            payloads, exact_usage, dirty_rows = self._normalize_records(
                records, requested
            )
            current = datetime.now(_shanghai_timezone()).date()
            for payload in payloads:
                month, year = payload["_month"]
                payload["_complete"] = (year, month) < (current.year, current.month)
            self._exact_minute_usage = exact_usage
            errors = (
                [
                    FetchError(
                        "INVALID_RESPONSE",
                        "用量明细",
                        f"已跳过 {dirty_rows} 条字段异常的 NayutoAI 用量记录",
                    )
                ]
                if dirty_rows
                else []
            )
            return payloads, errors
        except Exception as exc:
            self._exact_minute_usage = None
            return [], [_fetch_error("用量明细", exc)]

    def exact_minute_usage(self) -> ExactMinuteUsage | None:
        return self._exact_minute_usage


__all__ = ["NayutoProvider"]
