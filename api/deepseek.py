"""DeepSeek platform internal API adapter."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from api.http import HttpsSession
from config import runtime as config_manager


@dataclass(frozen=True)
class APIError(Exception):
    code: str
    endpoint: str
    message: str
    status_code: int | None = None

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class DeepSeekWebProfile:
    chrome_major: str = "147"
    app_version: str = "20240425.0"
    accept_language: str = "zh-CN,zh;q=0.9"


# 这些字段仅用于兼容 DeepSeek 网页私有接口，不允许覆盖请求域名或本地路径。
WEB_PROFILE = DeepSeekWebProfile()


def _build_session() -> requests.Session:
    session = HttpsSession()
    retry = Retry(
        total=3,
        connect=2,
        read=2,
        backoff_factor=0.5,
        # 429 表示平台已要求降速；立即重试只会延长限流，应交给下一轮定时刷新。
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        # 否则 urllib3 遇到带 Retry-After 的 429 仍会隐式重试。
        respect_retry_after_header=False,
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


_SESSION = _build_session()
build_session = _build_session


def _config_get(config: Mapping[str, Any] | None, key: str, default: Any = None) -> Any:
    return config.get(key, default) if config is not None else config_manager.get(key, default)


def normalize_authorization(value: Any) -> str:
    """Accept either a raw platform token or a copied Authorization header value."""
    authorization = str(value or "").strip()
    if authorization.startswith("{"):
        try:
            stored = json.loads(authorization)
        except (TypeError, ValueError):
            stored = None
        if isinstance(stored, dict):
            authorization = str(stored.get("value") or stored.get("token") or "").strip()
    if authorization.lower().startswith("authorization:"):
        authorization = authorization.split(":", 1)[1].strip()
    if authorization and not authorization.lower().startswith("bearer "):
        authorization = f"Bearer {authorization}"
    return authorization


def platform_token_subject(value: Any) -> str:
    """Read a stable account subject from the JWT without exposing or trusting other claims."""
    authorization = normalize_authorization(value)
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    parts = token.split(".")
    if len(parts) != 3:
        return ""
    try:
        payload = json.loads(
            base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)).decode("utf-8")
        )
    except (ValueError, UnicodeDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    for key in ("sub", "user_id", "userId", "uid"):
        subject = str(payload.get(key) or "").strip()
        if subject:
            return subject
    return ""


def _headers(config: Mapping[str, Any] | None = None) -> dict[str, str]:
    base = _config_get(config, "DEEPSEEK_BASE", "https://platform.deepseek.com")
    # 该私有接口会通过浏览器标识做风控；缺少这些兼容头时会返回 HTML 429，而非正常 API 限流。
    # 版本集中保留在适配器中，平台策略变化时只需更新这里。
    return {
        "accept": "*/*",
        "accept-language": WEB_PROFILE.accept_language,
        "authorization": normalize_authorization(_config_get(config, "DEEPSEEK_AUTH", "")),
        "sec-ch-ua": (
            f'"Google Chrome";v="{WEB_PROFILE.chrome_major}", '
            f'"Not.A/Brand";v="8", "Chromium";v="{WEB_PROFILE.chrome_major}"'
        ),
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "x-app-version": WEB_PROFILE.app_version,
        "x-client-platform": "web",
        "cookie": _config_get(config, "DEEPSEEK_COOKIE", ""),
        "referer": f"{base}/usage",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{WEB_PROFILE.chrome_major}.0.0.0 Safari/537.36"
        ),
    }


def _error_code(status_code: int) -> str:
    if status_code in (401, 403):
        return "AUTH_EXPIRED"
    if status_code == 429:
        return "RATE_LIMITED"
    if status_code >= 500:
        return "SERVER_ERROR"
    return "UNKNOWN_ERROR"


def _get(
    path: str,
    *,
    params: dict[str, int] | None = None,
    config: Mapping[str, Any] | None = None,
    session: requests.Session | None = None,
) -> Any:
    auth = str(_config_get(config, "DEEPSEEK_AUTH", "")).strip()
    cookie = str(_config_get(config, "DEEPSEEK_COOKIE", "")).strip()
    if not auth and not cookie:
        # 在发起网络请求前区分“未配置”和“凭据失效”，让面板能给出准确设置引导。
        raise APIError(
            "NOT_CONFIGURED", path.rsplit("/", 1)[-1],
            "尚未配置 Token/Cookie，请先打开设置"
        )
    base = str(_config_get(config, "DEEPSEEK_BASE", "https://platform.deepseek.com")).rstrip("/")
    endpoint = path.rsplit("/", 1)[-1]
    try:
        response = (session or _SESSION).get(
            f"{base}{path}", headers=_headers(config), params=params, timeout=(5, 15)
        )
    except requests.Timeout as exc:
        raise APIError("NETWORK_TIMEOUT", endpoint, "连接 DeepSeek 超时") from exc
    except requests.RequestException as exc:
        raise APIError("NETWORK_ERROR", endpoint, "无法连接 DeepSeek") from exc

    if not response.ok:
        content_type = response.headers.get("Content-Type", "").lower()
        # 平台风控也使用 429，但返回 HTML 且没有 Retry-After，不能误报为普通限流。
        blocked_by_platform = response.status_code == 429 and "json" not in content_type
        code = "PLATFORM_BLOCKED" if blocked_by_platform else _error_code(response.status_code)
        messages = {
            "AUTH_EXPIRED": "凭证已失效，请重新填写 Token/Cookie",
            "RATE_LIMITED": "请求过于频繁，请稍后重试",
            "PLATFORM_BLOCKED": "平台风控拒绝请求，请稍后重试或更新兼容配置",
            "SERVER_ERROR": "DeepSeek 服务暂时异常",
            "UNKNOWN_ERROR": f"DeepSeek 请求失败（HTTP {response.status_code}）",
        }
        raise APIError(code, endpoint, messages[code], response.status_code)

    content_type = response.headers.get("Content-Type", "").lower()
    if content_type and "json" not in content_type:
        raise APIError("INVALID_RESPONSE", endpoint, "DeepSeek 返回了非 JSON 数据")
    try:
        payload = response.json()
    except requests.JSONDecodeError as exc:
        raise APIError("INVALID_RESPONSE", endpoint, "DeepSeek 返回的数据无法解析") from exc
    if not isinstance(payload, dict):
        raise APIError("INVALID_RESPONSE", endpoint, "DeepSeek 返回结构已变化")
    try:
        outer_code = payload.get("code", 0)
        if outer_code not in (0, "0", None):
            message = payload.get("msg") or payload.get("message")
            error_code = (
                "AUTH_EXPIRED"
                if str(outer_code) in {"401", "403", "40003"}
                else "UNKNOWN_ERROR"
            )
            raise APIError(
                error_code,
                endpoint,
                (
                    "DeepSeek 平台 Token 无效或已失效，请重新获取"
                    if error_code == "AUTH_EXPIRED"
                    else str(message or f"DeepSeek 请求失败（业务码 {outer_code}）")
                ),
            )
        data = payload["data"]
        biz_code = data.get("biz_code", 0)
        if biz_code not in (0, "0", None):
            message = data.get("biz_msg") or payload.get("msg") or payload.get("message")
            error_code = (
                "AUTH_EXPIRED" if str(biz_code) in {"401", "403", "40003"} else "UNKNOWN_ERROR"
            )
            raise APIError(
                error_code,
                endpoint,
                (
                    "DeepSeek 平台 Token 无效或已失效，请重新获取"
                    if error_code == "AUTH_EXPIRED"
                    else str(message or f"DeepSeek 请求失败（业务码 {biz_code}）")
                ),
            )
        return data["biz_data"]
    except APIError:
        raise
    except (KeyError, TypeError) as exc:
        message = payload.get("msg") or payload.get("message")
        raise APIError(
            "INVALID_RESPONSE", endpoint, str(message or "DeepSeek 返回结构已变化")
        ) from exc


def _month_range(month: int, year: int) -> tuple[int, int, int]:
    """Return the platform's fixed-offset, left-closed month range."""
    local_offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    offset_seconds = int(local_offset.total_seconds())
    # 私有接口只接受 15 分钟粒度的固定偏移；使用设备当前偏移与应用既有本地日界保持一致。
    offset_seconds = max(-43200, min(50400, round(offset_seconds / 900) * 900))
    fixed_timezone = timezone(timedelta(seconds=offset_seconds))
    start = datetime(year, month, 1, tzinfo=fixed_timezone)
    next_month = (
        datetime(year + 1, 1, 1, tzinfo=fixed_timezone)
        if month == 12
        else datetime(year, month + 1, 1, tzinfo=fixed_timezone)
    )
    return int(start.timestamp()), int(next_month.timestamp()), offset_seconds


def _bucket_date(value: Any, offset_seconds: int) -> str | None:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    fixed_timezone = timezone(timedelta(seconds=offset_seconds))
    return datetime.fromtimestamp(timestamp, fixed_timezone).date().isoformat()


def _normalize_amount(result: dict[str, Any], offset_seconds: int) -> dict[str, Any]:
    series = result.get("series")
    if not isinstance(series, list):
        raise APIError("INVALID_RESPONSE", "amount", "Token 用量格式异常")
    days: dict[str, list[dict[str, Any]]] = {}
    for item in series:
        if not isinstance(item, dict) or not isinstance(item.get("buckets"), list):
            continue
        model = str(item.get("model", "unknown")).strip() or "unknown"
        for bucket in item["buckets"]:
            if not isinstance(bucket, dict) or not isinstance(bucket.get("usage"), dict):
                continue
            usage_date = _bucket_date(bucket.get("time"), offset_seconds)
            if usage_date is None:
                continue
            usage = [
                {"type": token_type, "amount": bucket["usage"][token_type]}
                for token_type in (
                    "PROMPT_CACHE_HIT_TOKEN",
                    "PROMPT_CACHE_MISS_TOKEN",
                    "RESPONSE_TOKEN",
                )
                if token_type in bucket["usage"]
            ]
            days.setdefault(usage_date, []).append({"model": model, "usage": usage})
    return {"days": [{"date": day, "data": data} for day, data in sorted(days.items())]}


def _normalize_cost(result: dict[str, Any], offset_seconds: int) -> dict[str, Any]:
    currencies = result.get("data")
    if not isinstance(currencies, list):
        raise APIError("INVALID_RESPONSE", "cost", "费用用量格式异常")
    cny = next(
        (item for item in currencies if isinstance(item, dict) and item.get("currency") == "CNY"),
        None,
    )
    if cny is None:
        return {"days": []}
    series = cny.get("series")
    if not isinstance(series, list):
        raise APIError("INVALID_RESPONSE", "cost", "费用用量格式异常")
    days: dict[str, list[dict[str, Any]]] = {}
    for item in series:
        if not isinstance(item, dict) or not isinstance(item.get("buckets"), list):
            continue
        model = str(item.get("model", "unknown")).strip() or "unknown"
        for bucket in item["buckets"]:
            if not isinstance(bucket, dict) or "cost" not in bucket:
                continue
            usage_date = _bucket_date(bucket.get("time"), offset_seconds)
            if usage_date is None:
                continue
            days.setdefault(usage_date, []).append(
                {"model": model, "usage": [{"type": "cost_cny", "amount": bucket["cost"]}]}
            )
    return {"days": [{"date": day, "data": data} for day, data in sorted(days.items())]}


def get_user_summary(
    config: Mapping[str, Any] | None = None,
    *,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    result = _get("/api/v0/users/get_user_summary", config=config, session=session)
    if not isinstance(result, dict):
        raise APIError("INVALID_RESPONSE", "get_user_summary", "账户摘要格式异常")
    return result


def get_usage_amount(
    month: int,
    year: int,
    config: Mapping[str, Any] | None = None,
    *,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    start, end, offset = _month_range(month, year)
    try:
        result = _get(
            "/api/v0/usage/by_api_key/amount",
            params={"start": start, "end": end, "tz": offset},
            config=config,
            session=session,
        )
        if not isinstance(result, dict):
            raise APIError("INVALID_RESPONSE", "amount", "Token 用量格式异常")
        return _normalize_amount(result, offset)
    except APIError as exc:
        if exc.code not in {"INVALID_RESPONSE", "UNKNOWN_ERROR"}:
            raise
        # 部分旧版或代理平台尚未提供新端点，保留旧接口作为兼容回退。
        result = _get(
            "/api/v0/usage/amount",
            params={"month": month, "year": year},
            config=config,
            session=session,
        )
        if not isinstance(result, dict):
            raise APIError("INVALID_RESPONSE", "amount", "Token 用量格式异常")
        return result


def get_usage_cost(
    month: int,
    year: int,
    config: Mapping[str, Any] | None = None,
    *,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    start, end, offset = _month_range(month, year)
    try:
        result = _get(
            "/api/v0/usage/by_api_key/cost",
            params={"start": start, "end": end, "tz": offset},
            config=config,
            session=session,
        )
        if not isinstance(result, dict):
            raise APIError("INVALID_RESPONSE", "cost", "费用用量格式异常")
        return _normalize_cost(result, offset)
    except APIError as exc:
        if exc.code not in {"INVALID_RESPONSE", "UNKNOWN_ERROR"}:
            raise
        result = _get(
            "/api/v0/usage/cost",
            params={"month": month, "year": year},
            config=config,
            session=session,
        )
        if isinstance(result, list):
            result = result[0] if result else {}
        if not isinstance(result, dict):
            raise APIError("INVALID_RESPONSE", "cost", "费用用量格式异常")
        return result
