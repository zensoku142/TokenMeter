"""Read-only Gemini CLI OAuth quota via Google's Code Assist client endpoints."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from api.providers.base import (
    FetchError,
    Provider,
    ProviderQuota,
    QuotaWindow,
    build_session,
)

_ENDPOINT = "https://cloudcode-pa.googleapis.com/v1internal:"
_MAX_CREDENTIAL_BYTES = 64 * 1024


def _read_json(path: Path) -> dict[str, Any]:
    # 凭据文件有界读取，避免误选大型会话日志；内容和路径均不进入错误消息。
    with path.open("rb") as handle:
        raw = handle.read(_MAX_CREDENTIAL_BYTES + 1)
    if len(raw) > _MAX_CREDENTIAL_BYTES:
        raise ValueError("INVALID_CREDENTIALS")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("INVALID_CREDENTIALS")
    return result


class GeminiProvider(Provider):
    id = "gemini"
    name = "Gemini CLI"
    supports_subscription_quota = True
    official_api_hosts = {"cloudcode-pa.googleapis.com"}
    dashboard_url = "https://console.cloud.google.com/gemini-admin"
    support_description = (
        "实验性 Gemini CLI / Code Assist OAuth 额度；只读本机登录，过期需在 CLI 重新登录。"
        "个人版已迁往 Antigravity，API Key / Vertex AI 不适用。"
    )
    credential_fields = {
        "ACCESS_TOKEN": {
            "label": "OAuth Access Token（可选）", "secret": True, "optional": True,
            "hint": "留空读取本机 Gemini CLI 登录；不支持 Gemini API Key",
        },
        "CREDENTIALS_FILE": {
            "label": "CLI 凭据文件（可选）", "secret": False, "optional": True,
            "hint": "默认 ~/.gemini/oauth_creds.json；仅读取，不修改或刷新登录令牌",
        },
        "PROJECT_ID": {
            "label": "Google Cloud Project ID（可选）", "secret": False, "optional": True,
            "hint": "留空从 Code Assist 查询项目；未返回项目时请填写 CLI 使用的项目 ID",
        },
    }

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(config)
        self._session = build_session()

    def close(self) -> None:
        self._session.close()

    def _credentials_path(self) -> Path:
        value = str(self.config_get("GEMINI_CREDENTIALS_FILE", "")).strip()
        return Path(value).expanduser() if value else Path.home() / ".gemini" / "oauth_creds.json"

    def _credentials(self) -> tuple[str, float | None]:
        manual = str(self.config_get("GEMINI_ACCESS_TOKEN", "")).strip()
        if manual:
            return manual, None
        path = self._credentials_path()
        try:
            settings = _read_json(path.with_name("settings.json"))
        except FileNotFoundError:
            settings = {}
        security = settings.get("security", {})
        auth = security.get("auth", {}) if isinstance(security, dict) else {}
        selected = auth.get("selectedType") if isinstance(auth, dict) else None
        # CLI 切换到 API Key/Vertex 后可能仍留旧 OAuth 文件，不能误显示旧账号额度。
        if selected in ("api-key", "gemini-api-key", "vertex-ai"):
            raise ValueError("UNSUPPORTED_AUTH")
        credentials = _read_json(path)
        token = credentials.get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise ValueError("NOT_CONFIGURED")
        expiry = credentials.get("expiry_date")
        if expiry is not None:
            if isinstance(expiry, bool) or not isinstance(expiry, (int, float)):
                raise ValueError("INVALID_CREDENTIALS")
            expiry = float(expiry)
            if not math.isfinite(expiry) or expiry <= 0:
                raise ValueError("INVALID_CREDENTIALS")
        return token.strip(), expiry

    def is_configured(self) -> bool:
        try:
            return bool(self._credentials()[0])
        except (OSError, ValueError, TypeError, OverflowError):
            return False

    def snapshot_identity(self) -> str:
        try:
            token, _ = self._credentials()
            # 项目也决定额度边界；换项目或换登录文件时不能复用旧账号快照。
            scope = [token, str(self._credentials_path().resolve()),
                     str(self.config_get("GEMINI_PROJECT_ID", "")).strip()]
            return hashlib.sha256(json.dumps(scope).encode()).hexdigest()
        except (OSError, ValueError, TypeError, OverflowError):
            return ""

    @staticmethod
    def _error(code: str) -> FetchError:
        messages = {
            "NOT_CONFIGURED": "未找到 Gemini CLI OAuth 登录，请先在 Gemini CLI 登录或填写 Access Token",
            "AUTH_EXPIRED": "Gemini 登录已过期，请在 Gemini CLI 重新登录或更新 Access Token",
            "INVALID_CREDENTIALS": "Gemini CLI 凭据文件格式无效，请重新登录或检查所选文件",
            "FILE_ERROR": "无法读取 Gemini CLI 凭据文件",
            "UNSUPPORTED_AUTH": "Gemini API Key / Vertex AI 不提供此 CLI 订阅额度，请使用 Google OAuth 登录",
            "PERMISSION_DENIED": "此 Google 登录无权读取 Code Assist 额度，请检查账号订阅与项目权限",
            "CONSUMER_TIER_DEPRECATED": "此 Gemini CLI 个人账号已迁往 Antigravity，请使用 Antigravity 查询额度",
            "PROJECT_REQUIRED": "Code Assist 未返回项目，请填写 Gemini CLI 使用的 Google Cloud Project ID",
            "RATE_LIMITED": "Gemini 额度查询过于频繁，请稍后重试",
            "NETWORK_TIMEOUT": "连接 Gemini 额度服务超时",
            "NETWORK_ERROR": "无法连接 Gemini 额度服务",
            "SERVER_ERROR": "Gemini 额度服务暂时异常",
            "INVALID_RESPONSE": "Gemini 额度数据结构已变化，请查看官方控制台",
            "QUOTA_UNAVAILABLE": "此账号未返回可用的 Gemini CLI 额度，请检查订阅或查看官方控制台",
        }
        return FetchError(code, "Gemini CLI 额度", messages[code])

    def _request(
        self, method: str, token: str, body: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, FetchError | None]:
        try:
            response = self._session.post(
                _ENDPOINT + method,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                         "Content-Type": "application/json", "User-Agent": "TokenMeter"},
                json=body, timeout=(3, 10),
                # 额度查询固定在 Google；禁止重定向把 OAuth 令牌传到其他地址。
                allow_redirects=False,
            )
        except requests.Timeout:
            return None, self._error("NETWORK_TIMEOUT")
        except requests.RequestException:
            return None, self._error("NETWORK_ERROR")
        if response.status_code != 200:
            text = response.text.lower() if isinstance(response.text, str) else ""
            # Google 迁移信号与普通失效分开；不回显响应正文中的账号或令牌。
            if ("unsupported_client" in text or "ineligibletiererror" in text
                    or ("migrate" in text and "antigravity" in text and "gemini" in text)):
                return None, self._error("CONSUMER_TIER_DEPRECATED")
            code = {401: "AUTH_EXPIRED", 403: "PERMISSION_DENIED", 429: "RATE_LIMITED"}.get(
                response.status_code, "SERVER_ERROR" if response.status_code >= 500 else "INVALID_RESPONSE"
            )
            return None, self._error(code)
        try:
            data = response.json()
            if not isinstance(data, dict) or data.get("error") is not None:
                raise ValueError("INVALID_RESPONSE")
        except (ValueError, TypeError):
            return None, self._error("INVALID_RESPONSE")
        return data, None

    def fetch_quota(self) -> tuple[ProviderQuota | None, FetchError | None]:
        try:
            token, expiry = self._credentials()
            if expiry is not None and expiry <= datetime.now(timezone.utc).timestamp() * 1000:
                # 刷新 OAuth 会与 CLI 竞争写入凭据；交由原客户端续期，本程序只读。
                return None, self._error("AUTH_EXPIRED")
        except FileNotFoundError:
            return None, self._error("NOT_CONFIGURED")
        except OSError:
            return None, self._error("FILE_ERROR")
        except (ValueError, TypeError, OverflowError) as error:
            code = str(error)
            return None, self._error(code if code in {"NOT_CONFIGURED", "UNSUPPORTED_AUTH"} else "INVALID_CREDENTIALS")
        project = str(self.config_get("GEMINI_PROJECT_ID", "")).strip()
        plan = ""
        if not project:
            status, error = self._request("loadCodeAssist", token, {
                "metadata": {"ideType": "IDE_UNSPECIFIED", "platform": "PLATFORM_UNSPECIFIED",
                             "pluginType": "GEMINI"},
            })
            if error or status is None:
                return None, error
            value = status.get("cloudaicompanionProject")
            # 不同 CLI 服务版本返回字符串或带 id/projectId 的对象。
            if isinstance(value, dict):
                value = value.get("id") or value.get("projectId")
            if not isinstance(value, str) or not value.strip():
                return None, self._error("PROJECT_REQUIRED")
            project = value.strip()
            tier = status.get("paidTier") or status.get("currentTier")
            if isinstance(tier, dict):
                label = tier.get("name") or tier.get("id")
                plan = label.strip() if isinstance(label, str) else ""
        payload, error = self._request("retrieveUserQuota", token, {"project": project})
        if error or payload is None:
            return None, error
        try:
            quota = self._parse_quota(payload, plan=plan)
        except (ValueError, TypeError, OverflowError):
            return None, self._error("INVALID_RESPONSE")
        if not quota.windows:
            return None, self._error("QUOTA_UNAVAILABLE")
        return quota, None

    @staticmethod
    def _parse_quota(payload: Any, *, plan: str = "") -> ProviderQuota:
        if not isinstance(payload, dict):
            raise ValueError("INVALID_RESPONSE")
        buckets = payload.get("buckets")
        if buckets is None:
            return ProviderQuota(plan=plan)
        if not isinstance(buckets, list):
            raise ValueError("INVALID_RESPONSE")
        models: dict[str, QuotaWindow] = {}
        for bucket in buckets:
            if not isinstance(bucket, dict):
                raise ValueError("INVALID_RESPONSE")
            model = bucket.get("modelId")
            fraction = bucket.get("remainingFraction")
            # 缺值不代表额度耗尽；remainingAmount 没有总量，不能反推百分比。
            if model is None or fraction is None:
                continue
            if not isinstance(model, str) or not model.strip():
                raise ValueError("INVALID_RESPONSE")
            if isinstance(fraction, bool) or not isinstance(fraction, (int, float)):
                raise ValueError("INVALID_RESPONSE")
            fraction = float(fraction)
            if not math.isfinite(fraction) or not 0 <= fraction <= 1:
                raise ValueError("INVALID_RESPONSE")
            reset = bucket.get("resetTime")
            resets_at = None
            if reset not in (None, ""):
                if not isinstance(reset, str):
                    raise ValueError("INVALID_RESPONSE")
                resets_at = datetime.fromisoformat(reset.replace("Z", "+00:00"))
                if resets_at.tzinfo is None:
                    raise ValueError("INVALID_RESPONSE")
            used = (1 - fraction) * 100
            model = model.strip()
            # 同模型 input/output 等桶共享展示行，保留最紧额度及其对应重置时间。
            previous = models.get(model)
            replace = previous is None or used > previous.used_percent
            if previous is not None and used == previous.used_percent:
                # 并列限制须全部恢复；取较晚重置，任一未知则保持未知，避免随数组顺序提前报恢复。
                replace = resets_at is None or (
                    previous.resets_at is not None and resets_at > previous.resets_at
                )
            if replace:
                models[model] = QuotaWindow(
                    f"gemini-{model}", model, used, resets_at=resets_at,
                    detail="Gemini CLI / Code Assist",
                )
        return ProviderQuota(windows=tuple(models[name] for name in sorted(models)), plan=plan)


__all__ = ["GeminiProvider"]
