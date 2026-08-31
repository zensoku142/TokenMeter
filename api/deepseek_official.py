"""DeepSeek public API client for stable account capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from api.http import HttpsSession
from config import runtime as config_manager
from api.deepseek import APIError


def _session() -> requests.Session:
    session = HttpsSession()
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=(429, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    return session


_SESSION = _session()
build_session = _session
BASE_URL = "https://api.deepseek.com"


def _get(
    path: str,
    config: Mapping[str, Any] | None = None,
    *,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    api_key = str(
        config.get("DEEPSEEK_API_KEY", "") if config is not None
        else config_manager.get("DEEPSEEK_API_KEY", "")
    ).strip()
    if not api_key:
        raise APIError("NOT_CONFIGURED", path, "未配置官方 API Key")
    try:
        response = (session or _SESSION).get(
            f"{BASE_URL}{path}",
            headers={"accept": "application/json", "authorization": f"Bearer {api_key}"},
            timeout=(5, 15),
        )
    except requests.Timeout as exc:
        raise APIError("NETWORK_TIMEOUT", path, "官方 API 连接超时") from exc
    except requests.RequestException as exc:
        raise APIError("NETWORK_ERROR", path, "无法连接官方 API") from exc
    if response.status_code in (401, 403):
        raise APIError("AUTH_EXPIRED", path, "官方 API Key 已失效", response.status_code)
    if response.status_code == 429:
        raise APIError("RATE_LIMITED", path, "官方 API 请求过于频繁", 429)
    if not response.ok:
        raise APIError("SERVER_ERROR", path, f"官方 API 请求失败（HTTP {response.status_code}）", response.status_code)
    try:
        payload = response.json()
    except requests.JSONDecodeError as exc:
        raise APIError("INVALID_RESPONSE", path, "官方 API 返回的数据无法解析") from exc
    if not isinstance(payload, dict):
        raise APIError("INVALID_RESPONSE", path, "官方 API 返回结构异常")
    return payload


def get_balance(
    config: Mapping[str, Any] | None = None,
    *,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    return _get("/user/balance", config, session=session)


def get_models(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return _get("/models", config)
