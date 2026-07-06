"""Xiaomi MiMo platform provider.

Uses the platform API at ``platform.xiaomimimo.com`` to fetch balance,
monthly usage summary, and per-day usage details — all authenticated via
browser cookie.
"""

from __future__ import annotations

from typing import Any

import requests

import config_manager
from api.providers.base import (
    FetchError,
    Provider,
    ProviderBalance,
    ProviderSummary,
    build_session,
    _decimal,
)

_MIMO_PLATFORM = "https://platform.xiaomimimo.com"


class MiMoProvider(Provider):
    id = "mimo"
    name = "小米 MiMo"
    default_currency = "CNY"
    default_base = _MIMO_PLATFORM
    official_api_hosts = {"platform.xiaomimimo.com"}
    # TokenScope2 的接口会同时返回余额 (balance)、月度费用和逐日用量，
    # 所以这里把 supports 开关打开，让 data.store 可以走通用路径，
    # 与 DeepSeek 一致聚合 today_cost_cny / today_tokens / daily_usage。
    supports_daily_usage = True
    supports_cost = True
    credential_fields = {
        "COOKIE": {
            "label": "Cookie",
            "secret": True,
            "multiline": True,
            "hint": "登录 platform.xiaomimimo.com 后复制浏览器 Cookie",
        },
        "API_PLATFORM_PH": {
            "label": "api-platform_ph",
            "secret": False,
            "hint": "浏览器请求 URL 中 ?api-platform_ph= 后面的值",
        },
        "API_KEY": {
            "label": "推理 API Key（用量查询不使用）",
            "secret": True,
            "optional": True,
            "hint": "仅保留旧配置，不会发送到控制台用量接口",
        },
        "BASE": {
            "label": "平台地址",
            "secret": False,
            "hint": "默认 https://platform.xiaomimimo.com",
        },
    }

    def __init__(self) -> None:
        self._session = build_session()

    # ------------------------------------------------------------------ helpers
    def is_configured(self) -> bool:
        return bool(
            str(config_manager.get("MIMO_COOKIE", "")).strip()
            or str(config_manager.get("MIMO_API_KEY", "")).strip()
        )

    def _base_url(self) -> str:
        custom = str(config_manager.get("MIMO_BASE", "")).strip()
        # 迁移早期版本默认指向 api.xiaomimimo.com；用量/余额端点只在 platform
        # platform.xiaomimimo.com 提供，因此把旧默认值替换为当前默认值。
        if custom in {"https://api.xiaomimimo.com", "api.xiaomimimo.com"}:
            custom = ""
        return custom or _MIMO_PLATFORM

    def _platform_headers(self) -> dict[str, str]:
        cookie = str(config_manager.get("MIMO_COOKIE", "")).strip()
        # 浏览器的 Cookie 头要求"name=value; name2=value2"，把粘贴时
        # 带入的换行/制表/多余空白规范化为单个空格，保证分号分隔。
        # 同时只保留每一项，避免空的空段。
        cookie_lines = [
            token.strip()
            for token in " ".join(cookie.splitlines()).split(";")
            if token.strip()
        ]
        cookie = "; ".join(cookie_lines)
        # 将 api-platform_ph 注入 cookie 作为登录态的一部分，平台会同时
        # 校验 URL 参数与 cookie 中的对应项。
        ph = str(config_manager.get("MIMO_API_PLATFORM_PH", "")).strip()
        if ph and "api-platform_ph" not in cookie:
            ph_decoded = ph.replace("%2F", "/").replace("%3D", "=")
            cookie = f'{cookie}; api-platform_ph="{ph_decoded}"'
        return {
            "accept": "*/*",
            "accept-language": "zh",
            "content-type": "application/json",
            "x-timezone": "Asia/Shanghai",
            "origin": self._base_url(),
            "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", '
            '"Not)A;Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "cookie": cookie,
            "referer": f"{self._base_url()}/console/usage",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/149.0.0.0 Safari/537.36"
            ),
        }

    def _url(self, path: str) -> str:
        """构造完整 URL，并在末尾附加 ``api-platform_ph``。

        ``ph`` 直接作为原始查询串附加，避免对用户从浏览器复制的百分
        比编码（如 ``%2F``）被二次编码。
        """
        base = self._base_url()
        url = f"{base}{path}"
        ph = str(config_manager.get("MIMO_API_PLATFORM_PH", "")).strip()
        if ph:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}api-platform_ph={ph}"
        return url

    def _get(self, path: str) -> Any:
        if not self.is_configured():
            raise RuntimeError("NOT_CONFIGURED")
        try:
            response = self._session.get(
                self._url(path),
                headers=self._platform_headers(),
                timeout=(5, 15),
            )
        except requests.Timeout as exc:
            raise RuntimeError("NETWORK_TIMEOUT") from exc
        except requests.RequestException as exc:
            raise RuntimeError("NETWORK_ERROR") from exc
        return self._check_response(response)

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        if not self.is_configured():
            raise RuntimeError("NOT_CONFIGURED")
        try:
            response = self._session.post(
                self._url(path),
                json=body,
                headers=self._platform_headers(),
                timeout=(5, 15),
            )
        except requests.Timeout as exc:
            raise RuntimeError("NETWORK_TIMEOUT") from exc
        except requests.RequestException as exc:
            raise RuntimeError("NETWORK_ERROR") from exc
        return self._check_response(response)

    @staticmethod
    def _check_response(response: requests.Response) -> Any:
        if response.status_code in (401, 403):
            raise RuntimeError("AUTH_EXPIRED")
        if response.status_code == 429:
            raise RuntimeError("RATE_LIMITED")
        if response.status_code >= 500:
            raise RuntimeError("SERVER_ERROR")
        if not response.ok:
            raise RuntimeError(f"HTTP_{response.status_code}")
        try:
            payload = response.json()
        except requests.JSONDecodeError as exc:
            raise RuntimeError("INVALID_RESPONSE") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("INVALID_RESPONSE")
        if payload.get("code") in (401, "401"):
            raise RuntimeError("AUTH_EXPIRED")
        if payload.get("code") not in (0, "0", None):
            raise RuntimeError("API_ERROR")
        data = payload.get("data")
        if not isinstance(data, (dict, list)):
            raise RuntimeError("INVALID_RESPONSE")
        return data

    @staticmethod
    def _error(source: str, exc: Exception) -> FetchError:
        code = str(exc)
        messages = {
            "NOT_CONFIGURED": "尚未配置 MiMo Cookie",
            "AUTH_EXPIRED": "MiMo 登录状态已失效，请重新复制 Cookie",
            "NETWORK_TIMEOUT": "连接 MiMo 超时",
            "NETWORK_ERROR": "无法连接 MiMo",
            "RATE_LIMITED": "MiMo 请求过于频繁，请稍后重试",
            "SERVER_ERROR": "MiMo 服务暂时异常",
            "INVALID_RESPONSE": "MiMo 返回结构已变化",
            "API_ERROR": "MiMo 返回业务错误",
        }
        return FetchError(code, source, messages.get(code, f"MiMo 请求失败（{code}）"))

    # -------------------------------------------------------------- fetches
    def fetch_balance(self) -> tuple[ProviderBalance | None, FetchError | None]:
        try:
            data = self._get("/api/v1/balance")
        except Exception as exc:
            return None, self._error("MiMo 余额", exc)
        balance_str = str(data.get("balance", "0") or "0")
        currency = str(data.get("currency", "CNY") or "CNY")
        balance = _decimal(balance_str)
        # 账户为按量付费模式时，平台不返回套餐剩余 token；
        # 以 amount 作为余额主字段，让 UI 的账户余额/费用部分正常展示。
        return ProviderBalance(
            currency=currency,
            amount=balance,
            token_estimate=0,
        ), None

    def fetch_summary(self) -> tuple[ProviderSummary | None, FetchError | None]:
        try:
            data = self._get("/api/v1/usage")
        except Exception as exc:
            return None, self._error("MiMo 用量", exc)
        cost_usage = data.get("costUsage") or {}
        token_usage = data.get("tokenUsage") or {}
        month_cost = _decimal(cost_usage.get("currentMonthCost"))
        month_tokens = int(str(token_usage.get("totalToken", 0) or 0))
        return ProviderSummary(
            month_cost=month_cost,
            month_tokens=month_tokens,
            remaining_tokens=0,
        ), None

    def fetch_payloads(
        self, months: list[tuple[int, int]]
    ) -> tuple[list[dict[str, Any]], list[FetchError]]:
        """抓取指定月份的每日用量，合并为标准 ``{days, total}`` 结构。

        每一行包含：date/model/consumedAmount/inputHitToken/inputMissToken/
        outputToken/totalToken。按日期聚合后交给 data.store 做统一展示。
        """
        payloads: list[dict[str, Any]] = []
        errors: list[FetchError] = []
        for month, year in sorted(set(months)):
            try:
                rows = self._post(
                    "/api/v1/usage/detail/list",
                    {"year": year, "month": month},
                )
            except RuntimeError as exc:
                errors.append(self._error("MiMo 用量明细", exc))
                continue
            except Exception as exc:
                errors.append(self._error("MiMo 用量明细", exc))
                continue
            if not isinstance(rows, list):
                errors.append(FetchError("INVALID_RESPONSE", "MiMo 用量明细", "返回格式错误"))
                continue
            by_date: dict[str, dict[str, Any]] = {}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                day_str = str(row.get("date", ""))
                if not day_str:
                    continue
                if day_str not in by_date:
                    by_date[day_str] = {"date": day_str, "data": []}
                model = str(row.get("model", "unknown"))
                consumed = str(row.get("consumedAmount", "0") or "0")
                input_hit = int(row.get("inputHitToken", 0) or 0)
                input_miss = int(row.get("inputMissToken", 0) or 0)
                output = int(row.get("outputToken", 0) or 0)
                by_date[day_str]["data"].append({
                    "model": model,
                    "usage": [
                        {"type": "PROMPT_CACHE_HIT_TOKEN", "amount": str(input_hit)},
                        {"type": "PROMPT_CACHE_MISS_TOKEN", "amount": str(input_miss)},
                        {"type": "RESPONSE_TOKEN", "amount": str(output)},
                        {"type": "cost_cny", "amount": consumed},
                    ],
                })
            days = sorted(by_date.values(), key=lambda d: d["date"])
            payloads.append({"days": days, "total": []})
        return payloads, errors


__all__ = ["MiMoProvider"]
