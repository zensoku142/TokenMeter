"""HTTPS-only transport shared by credential-bearing platform requests."""

from urllib.parse import urlparse

import requests


def is_https_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return bool(
            parsed.scheme == "https" and parsed.hostname
            and parsed.username is None and parsed.password is None
            and (parsed.port is None or 0 < parsed.port <= 65535)
        )
    except ValueError:
        return False


class HttpsSession(requests.Session):
    def send(self, request, **kwargs):
        # 重定向也经由 send；仅检查初始配置无法阻止 HTTPS 被降级为明文请求。
        if not is_https_url(request.url or ""):
            raise requests.exceptions.InvalidURL("Platform requests require HTTPS")
        return super().send(request, **kwargs)
