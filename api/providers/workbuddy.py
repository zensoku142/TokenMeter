"""Offline WorkBuddy adapter; deliberately excluded from the provider registry."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, DecimalException
from typing import Any

import requests

from api import browser_cookie
from api.providers.api_balance import _amount
from api.providers.base import (
    FetchError,
    Provider,
    ProviderQuota,
    QuotaMetric,
    build_session,
)


class WorkBuddyProvider(Provider):
    id = "workbuddy"
    name = "WorkBuddy"
    supports_subscription_quota = True
    supports_cookie_acquisition = True
    dashboard_url = "https://www.workbuddy.cn/profile/plans-usage"
    support_description = "读取 WorkBuddy 剩余积分、每日消耗和模型使用明细；积分与调用次数按官网原始数据展示。"
    credential_fields = {
        "COOKIE": {
            "label": "Cookie",
            "secret": True,
            "hint": "使用下方隔离浏览器登录 WorkBuddy 后获取，也可手动粘贴官网 Cookie",
        },
        "USER_AGENT": {
            "label": "浏览器标识 (User-Agent)",
            "secret": False,
            "optional": True,
            "hint": "随 Cookie 自动采集；必须与登录 WorkBuddy 的浏览器一致",
        },
    }

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(config)
        self._session = build_session()

    def close(self) -> None:
        self._session.close()

    @staticmethod
    def acquired_cookie_values(cookie: str) -> dict[str, str]:
        return {"COOKIE": browser_cookie.normalize_cookie(cookie)}

    @staticmethod
    def acquired_credential_values(value: str) -> dict[str, str]:
        payload = json.loads(value)
        if not isinstance(payload, dict) or not all(isinstance(payload.get(key), str) for key in ("COOKIE", "USER_AGENT")):
            raise RuntimeError("INVALID_RESPONSE")
        return {"COOKIE": browser_cookie.normalize_cookie(payload["COOKIE"]), "USER_AGENT": payload["USER_AGENT"].strip()}

    @staticmethod
    def acquire_cookie_via_chrome(
        stop_event: threading.Event,
        use_edge: bool = False,
        user_data_dir: str | None = None,
    ) -> str:
        # 兼容原有单独采集 Cookie 的调用方；设置页使用包含浏览器标识的完整凭据采集。
        return WorkBuddyProvider.acquired_credential_values(WorkBuddyProvider.acquire_credentials_via_chrome(
            stop_event, use_edge, user_data_dir,
        ))["COOKIE"]

    @staticmethod
    def acquire_credentials_via_chrome(
        stop_event: threading.Event,
        use_edge: bool = False,
        user_data_dir: str | None = None,
    ) -> str:
        session = browser_cookie.open_chrome_session(
            stop_event,
            acquire_url=WorkBuddyProvider.dashboard_url,
            profile_name="workbuddy-chrome",
            use_edge=use_edge,
            user_data_dir=user_data_dir,
        )
        try:
            if not stop_event.wait(timeout=180):
                raise RuntimeError("WORKBUDDY_COOKIE_EMPTY")
            cookie = session.cookies(allowed_domains=("workbuddy.cn",), url="https://www.workbuddy.cn/billing/meter/get-user-resource-summary")
            user_agent = session.user_agent()
            provider = WorkBuddyProvider({"WORKBUDDY_COOKIE": cookie, "WORKBUDDY_USER_AGENT": user_agent})
            try:
                # 在登录窗口仍打开时校验完整会话；成功后才向设置页交付凭据。
                _, error = provider.fetch_quota()
                if error:
                    raise RuntimeError(error.code)
                return json.dumps({"COOKIE": cookie, "USER_AGENT": user_agent})
            finally:
                provider.close()
        finally:
            session.close()

    @staticmethod
    def describe_acquire_error(exc: Exception) -> str:
        return {
            "CHROME_NOT_FOUND": "未检测到 Chrome 或 Edge，请先安装浏览器，或手动粘贴 Cookie",
            "WORKBUDDY_COOKIE_EMPTY": "WorkBuddy 登录未完成或已失效，请在浏览器登录成功后再点击“完成采集”",
            "AUTH_EXPIRED": "WorkBuddy 登录未完成或已失效，请在浏览器登录成功后再点击“完成采集”",
            "NETWORK_TIMEOUT": "请求超时",
            "NETWORK_ERROR": "连接失败，请检查网络和平台地址",
        }.get(str(exc), "WorkBuddy 登录或积分读取失败，请重新登录后完成采集")

    @staticmethod
    def _parse_quota(data: Mapping[str, Any]) -> ProviderQuota:
        remaining = _amount(data, "TotalCredits", nonnegative=True)
        groups = data.get("CreditGroups", [])
        if not isinstance(groups, list):
            raise ValueError("Invalid credit groups")
        titles = {
            "plan_base": "套餐基础积分", "plan_bonus": "套餐赠送积分",
            "addon": "购买积分", "benefit": "平台奖励积分",
        }
        amounts: dict[str, tuple[Decimal, Decimal]] = {}
        for group in groups:
            if not isinstance(group, dict):
                raise ValueError("Invalid credit group")
            kind = group.get("Kind")
            if not isinstance(kind, str) or not kind:
                raise ValueError("Invalid credit kind")
            total = _amount(group, "CycleTotalCapacity", nonnegative=True)
            left = _amount(group, "CycleRemainCapacity", nonnegative=True)
            if left > total:
                raise ValueError("Invalid remaining credits")
            # 官网按 Kind 汇总多个资源包；稳定的分组 ID 避免接口排序变化串用历史额度。
            previous_total, previous_left = amounts.get(kind, (Decimal(0), Decimal(0)))
            amounts[kind] = previous_total + total, previous_left + left
        # 积分是实际数量，不用套餐百分比替代；总余额固定为主指标，零积分分组也保留。
        metrics = [QuotaMetric("剩余积分", _credit_text(remaining), value_kind="credits")]
        metrics.extend(QuotaMetric(
            titles.get(kind, "积分额度"), _credit_text(left),
            f"已用 {_credit_text(total - left)} / 总计 {_credit_text(total)} 积分", value_kind="credits",
        ) for kind, (total, left) in amounts.items())
        return ProviderQuota(
            metrics=tuple(metrics),
        )

    def fetch_quota(self) -> tuple[ProviderQuota | None, FetchError | None]:
        data, error = self._request_data("get-user-resource-summary", {})
        if error or data is None:
            return None, error
        try:
            return self._parse_quota(data), None
        except (KeyError, TypeError, ValueError, DecimalException):
            return None, FetchError("INVALID_RESPONSE", "WorkBuddy 积分", "WorkBuddy 返回了无效数据或接口结构已变化")

    def _request_data(self, endpoint: str, body: dict[str, Any]) -> tuple[dict[str, Any] | None, FetchError | None]:
        def error(code: str, message: str):
            return None, FetchError(code, "WorkBuddy 积分", message)

        cookie = browser_cookie.normalize_cookie(str(self.config_get("WORKBUDDY_COOKIE", "")))
        if not cookie:
            return error("NOT_CONFIGURED", "尚未连接 WorkBuddy，请在账户连接中登录")
        if any(ord(char) < 32 for char in cookie):
            return error("INVALID_CONFIG", "请检查 Cookie 格式")
        user_agent = str(self.config_get("WORKBUDDY_USER_AGENT", "")).strip()
        if not user_agent or any(ord(char) < 32 for char in user_agent):
            return error("INVALID_CONFIG", "请重新采集 WorkBuddy 登录凭据，补全浏览器标识")
        try:
            response = self._session.post(
                f"https://www.workbuddy.cn/billing/meter/{endpoint}",
                json=body,
                headers={"Accept": "application/json", "Cookie": cookie, "X-Client-Platform": "web", "User-Agent": user_agent},
                # 设置页需要及时反馈失败；单轮不反复重试登录态，后台刷新会按既有退避再次查询。
                timeout=(3, 10),
                # 登录 Cookie 只能发送给官方查询地址，不能跟随重定向泄露给其他站点。
                allow_redirects=False,
            )
        except requests.Timeout:
            return error("NETWORK_TIMEOUT", "请求超时")
        except requests.RequestException:
            return error("NETWORK_ERROR", "连接失败，请检查网络和平台地址")
        if response.status_code in (401, 403):
            return error("AUTH_EXPIRED", "WorkBuddy 登录已失效，请重新连接")
        if response.status_code == 429:
            return error("RATE_LIMITED", "请求过于频繁，请稍后重试")
        if response.status_code >= 500:
            return error("SERVER_ERROR", "服务暂时不可用")
        if not 200 <= response.status_code < 300:
            return error("HTTP_ERROR", "请求失败，请检查平台 API 地址")
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Invalid summary")
            if payload.get("code", 0) != 0:
                return error("API_ERROR", "WorkBuddy 积分接口返回错误，请检查登录状态")
            if not isinstance(payload.get("data"), dict):
                raise ValueError("Invalid summary")
            return payload["data"], None
        except (KeyError, TypeError, ValueError, DecimalException):
            return error("INVALID_RESPONSE", "WorkBuddy 返回了无效数据或接口结构已变化")

    def fetch_credit_usage(self) -> tuple[dict[str, Any] | None, FetchError | None]:
        today = datetime.now(timezone(timedelta(hours=8))).date()
        month_start = today.replace(day=1)
        start = min(month_start, today - timedelta(days=6))
        records: dict[str, dict[str, Any]] = {}
        deadline = time.monotonic() + 20
        try:
            for page in range(1, 21):
                if time.monotonic() >= deadline:
                    break
                data, error = self._request_data("get-user-request-usage", {
                    "startTime": f"{start.isoformat()} 00:00:00", "endTime": f"{today.isoformat()} 23:59:59",
                    "pageNum": page, "pageSize": 3000,
                    # 官网将 WorkBuddy 映射到 codebuddy 产品；该账号明细同时保留客户端标记。
                    "appTypes": ["codebuddy"],
                })
                if error or data is None:
                    return None, error
                total = data["total"]
                rows = data["data"]
                if type(total) is not int or total < 0 or not isinstance(rows, list):
                    raise ValueError("Invalid usage page")
                for row in rows:
                    if not isinstance(row, dict) or not isinstance(row.get("requestId"), str) or not row["requestId"]:
                        raise ValueError("Invalid request")
                    # 只保留计量字段，不缓存官网返回的 input / inputTrunc 提示词或会话内容。
                    records[row["requestId"]] = {key: row[key] for key in ("credit", "model", "client", "requestTime")}
                if len(records) >= total:
                    return self._aggregate_usage(list(records.values()), today, start), None
                if not rows:
                    break
            return None, FetchError("USAGE_INCOMPLETE", "WorkBuddy 使用明细", "WorkBuddy 使用明细未完整加载，请稍后重试")
        except (KeyError, TypeError, ValueError, DecimalException):
            return None, FetchError("INVALID_RESPONSE", "WorkBuddy 使用明细", "WorkBuddy 返回了无效数据或接口结构已变化")

    @staticmethod
    def _aggregate_usage(records: list[dict[str, Any]], today: date, start: date) -> dict[str, Any]:
        daily: dict[str, dict[str, Any]] = {}
        models: dict[str, dict[str, Any]] = {}
        month_start = today.replace(day=1).isoformat()
        for row in records:
            credit = _amount(row, "credit", nonnegative=True)
            stamp = datetime.fromisoformat(str(row["requestTime"]))
            if stamp.tzinfo is not None:
                stamp = stamp.astimezone(timezone(timedelta(hours=8)))
            day = stamp.date().isoformat()
            if day < start.isoformat() or day > today.isoformat():
                continue
            model = row["model"]
            if not isinstance(model, str) or not model:
                raise ValueError("Invalid model")
            bucket = daily.setdefault(day, {"date": day, "credits": Decimal(0), "requests": 0})
            bucket["credits"] += credit
            bucket["requests"] += 1
            if day >= month_start:
                item = models.setdefault(model, {"model": model, "credits": Decimal(0), "requests": 0, "clients": set()})
                item["credits"] += credit
                item["requests"] += 1
                item["clients"].add(str(row["client"]))
        month_rows = [item for day, item in daily.items() if day >= month_start]
        month_credits = sum((item["credits"] for item in month_rows), Decimal(0))
        today_credits = daily.get(today.isoformat(), {}).get("credits", Decimal(0))
        statistics = [
            {"title": "本月消耗积分", "value": _credit_text(month_credits)},
            {"title": "今日消耗积分", "value": _credit_text(today_credits)},
            {"title": "本月调用次数", "value": str(sum(item["requests"] for item in month_rows))},
            {"title": "使用模型数", "value": str(len(models))},
            {"title": "本月活跃天数", "value": str(len(month_rows))},
        ]
        return {
            "start_date": start.isoformat(), "end_date": today.isoformat(),
            "daily": [{**daily[day], "credits": str(daily[day]["credits"])} for day in sorted(daily)],
            "models": [{**item, "credits": str(item["credits"]), "clients": sorted(item["clients"])}
                       for item in sorted(models.values(), key=lambda item: (-item["credits"], item["model"]))],
            "statistics": statistics,
        }


def _credit_text(value: Decimal) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")
