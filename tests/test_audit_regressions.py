"""Regression cases for account boundaries and bounded history/log reads."""

import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest
import requests
from requests.adapters import BaseAdapter

from api.http import HttpsSession
from api.providers.base import FetchError, Provider, ProviderQuota, ProviderSummary, QuotaMetric, QuotaWindow
from api.providers.codex import CodexProvider
from config import credentials
from config.store import is_official_base_url, validate_config
from data import history
from data.store import TokenData, _load_minute_history


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "DB_PATH", tmp_path / "usage.db")
    monkeypatch.setattr(TokenData, "_provider_snapshots", {})
    return tmp_path


class QuotaProvider(Provider):
    id = "codex"
    name = "Synthetic"
    supports_subscription_quota = True

    def __init__(self, account, *, failed=False, metadata=True):
        super().__init__({})
        self.account = account
        self.failed = failed
        self.metadata = metadata

    def snapshot_identity(self):
        return self.account

    def is_configured(self):
        return True

    def fetch_quota(self):
        if self.failed:
            return None, FetchError("NETWORK_ERROR", "synthetic", "offline")
        return ProviderQuota(
            windows=(QuotaWindow("weekly", "weekly", 25),),
            statistics=(QuotaMetric("tokens", "1234"),) if self.metadata else (),
            activity=((date.today().isoformat(), 1234),) if self.metadata else (),
            activity_source="interface", statistics_source="interface",
        ), None


def test_account_change_does_not_reuse_memory_or_persist_old_statistics(database):
    TokenData._fetch_with_provider(QuotaProvider("A"))
    offline_b = TokenData._fetch_with_provider(QuotaProvider("B", failed=True))
    assert offline_b.quota_windows == []
    online_b = TokenData._fetch_with_provider(QuotaProvider("B", metadata=False))
    assert online_b.quota_statistics == []
    assert history.load_provider_quota_snapshot("codex", "B")[0]["statistics"] == []
    assert history.load_provider_quota_snapshot("codex", "A")[0]["statistics"]


def test_identity_change_during_request_cannot_publish_snapshot(database):
    provider = QuotaProvider("A")
    original = provider.fetch_quota

    def changed():
        value = original()
        provider.account = "B"
        return value

    provider.fetch_quota = changed
    result = TokenData._fetch_with_provider(provider)
    assert result.errors[0].code == "ACCOUNT_CHANGED"
    assert not TokenData._provider_snapshots
    assert history.load_provider_quota_snapshot("codex", "B") is None


def test_api_history_adopts_legacy_once_and_keeps_accounts_separate(database):
    with history._connect() as connection:
        connection.execute("INSERT INTO daily_usage VALUES (?, ?, ?, ?, ?, ?, ?)",
                           ("2026-08-01", "model", "cost_cny", 0, "1.25", "now", "mimo"))
    first = history.scoped_provider("mimo", "A")
    second = history.scoped_provider("mimo", "B")
    assert history.total_cost(first) == Decimal("1.25")
    assert history.total_cost(second) == 0
    assert history.scoped_provider("mimo", "A") == first
    assert history.total_cost(first) == Decimal("1.25")


@pytest.mark.parametrize("url", ["http://platform.deepseek.com", "https://u:p@platform.deepseek.com", "https://example.com:bad"])
def test_unsafe_base_is_rejected(url):
    with pytest.raises(ValueError, match="HTTPS"):
        validate_config({"DEEPSEEK_BASE": url})
    assert not is_official_base_url(url)


def test_official_base_is_scoped_to_platform_and_port():
    assert is_official_base_url("https://platform.deepseek.com", "deepseek")
    assert not is_official_base_url("https://platform.deepseek.com", "mimo")
    assert not is_official_base_url("https://platform.deepseek.com:8443", "deepseek")


def test_https_redirect_cannot_downgrade_to_plaintext():
    class Redirect(BaseAdapter):
        def __init__(self):
            self.calls = []

        def send(self, request, **kwargs):
            self.calls.append(request.url)
            response = requests.Response()
            response.status_code = 302
            response.url = request.url
            response.request = request
            response.headers["Location"] = "http://example.test/plaintext"
            response._content = b""
            return response

        def close(self):
            pass

    adapter = Redirect()
    with HttpsSession() as session:
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        with pytest.raises(requests.exceptions.InvalidURL):
            session.get("https://example.test/start", headers={"Authorization": "Bearer synthetic"})
    assert adapter.calls == ["https://example.test/start"]


def test_clear_credential_removes_all_legacy_sources(monkeypatch):
    targets = {f"{prefix}/MIMO_COOKIE": "synthetic" for prefix in ("TokenMeter", "TokenSpider", "TokenScope")}
    backend = Mock()
    backend.CredDeleteW.side_effect = lambda name, *_: bool(targets.pop(name, None))
    monkeypatch.setattr(credentials, "_advapi32", backend)
    monkeypatch.setattr(credentials.os, "name", "nt")
    monkeypatch.delenv("TOKENMETER_E2E_DISABLE_CREDENTIALS", raising=False)
    monkeypatch.setattr(credentials, "read_credential_target", lambda name: targets.get(name, ""))
    credentials.write_credential("MIMO_COOKIE", "")
    assert credentials.read_credential("MIMO_COOKIE") == ""
    assert not targets


def test_partial_utf8_session_line_is_retried_after_append(tmp_path):
    record = {"timestamp": "2026-08-31T00:00:00Z", "type": "event_msg", "payload": {
        "type": "token_count", "text": "中文", "info": {"total_token_usage": {"total_tokens": 1234}}}}
    raw = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    split = raw.index("中".encode()) + 1
    path = tmp_path / "session.jsonl"
    path.write_bytes(raw[:split])
    partial = CodexProvider._scan_session_file(path, None)
    assert partial.offset == 0
    with path.open("ab") as stream:
        stream.write(raw[split:])
    complete = CodexProvider._scan_session_file(path, partial)
    assert sum(complete.daily.values()) == 1234
    assert CodexProvider._scan_session_file(path, complete) is complete


def test_replaced_session_file_cannot_reuse_an_old_offset(tmp_path):
    event = {"timestamp": "2026-08-31T00:00:00Z", "type": "event_msg", "payload": {
        "type": "token_count", "info": {"total_token_usage": {"total_tokens": 4}}}}
    path = tmp_path / "session.jsonl"
    path.write_text(json.dumps(event, separators=(",", ":")) + "\n", encoding="utf-8")
    previous = CodexProvider._scan_session_file(path, None)
    replacement = tmp_path / "replacement.jsonl"
    event["payload"]["info"]["total_token_usage"]["total_tokens"] = 12345
    replacement.write_text(json.dumps(event, separators=(",", ":")) + "\n", encoding="utf-8")
    replacement.replace(path)
    scanned = CodexProvider._scan_session_file(path, previous)
    assert sum(scanned.daily.values()) == 12345


def test_large_irrelevant_session_line_does_not_hide_following_event(tmp_path):
    event = b'{"timestamp":"2026-08-31T00:00:00Z","type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"total_tokens":4}}}}\n'
    path = tmp_path / "large.jsonl"
    path.write_bytes(b"x" * (2 * 1024 * 1024) + b"\n" + event)
    value = CodexProvider._scan_session_file(path, None)
    assert sum(value.daily.values()) == 4


def test_official_today_skips_local_log_scan(monkeypatch):
    monkeypatch.setattr(CodexProvider, "_activity_cache", {})
    provider = CodexProvider({})
    provider._profile_activity = Mock(return_value=(((date.today().isoformat(), 123),), ()))
    provider._local_activity = Mock(side_effect=AssertionError("unnecessary scan"))
    try:
        value = provider._activity_snapshot("synthetic", {})
        assert value[0] == ((date.today().isoformat(), 123),)
    finally:
        provider.close()


def test_lazy_history_reads_today_only_and_preserves_old_dates(database):
    today = date.today()
    yesterday = today - timedelta(days=1)
    with history._connect() as connection:
        for day in (yesterday, today):
            connection.execute("INSERT INTO minute_usage VALUES (?, ?, ?, ?, ?, ?)",
                               ("mimo", day.isoformat(), 0, "RESPONSE_TOKEN", 7, "now"))
    rows, _, _, days, cached, _, _ = _load_minute_history("mimo", today, include_history=False)
    assert rows[0]["token_amount"] == 7
    assert set(cached) == {today.isoformat()}
    assert days == [yesterday.isoformat(), today.isoformat()]
    assert history.minute_history_for_day("mimo", yesterday)[0][0]["token_amount"] == 7


def test_total_cost_refreshes_after_history_changes(database, monkeypatch):
    class API(Provider):
        id = "deepseek"
        supports_cost = True

        def is_configured(self):
            return True

        def fetch_summary(self):
            return ProviderSummary(month_cost=Decimal("1")), None

    monkeypatch.setattr(history, "total_cost", Mock(side_effect=[Decimal("1"), Decimal("2")]))
    assert TokenData._fetch_with_provider(API({})).total_cost_cny == 1
    assert TokenData._fetch_with_provider(API({})).total_cost_cny == 2


def test_download_stops_at_size_limit_and_cleans_partial_file(tmp_path, monkeypatch):
    from updater.client import GitHubReleaseClient, ReleaseAsset, UpdateError
    client = GitHubReleaseClient()
    response = Mock()
    response.iter_content.return_value = [b"1234", b"5"]
    url = "https://github.com/zensoku142/TokenMeter/releases/download/v1.0/test.exe"
    monkeypatch.setattr(client, "_open_download_stream", lambda _: (response, url))
    path = tmp_path / "test.exe"
    try:
        with pytest.raises(UpdateError, match="大小限制"):
            client._download_asset(ReleaseAsset("test.exe", url, 4), path,
                                   expected_sha="0" * 64, bytes_before=0, bytes_total=4,
                                   progress=None, cancel_requested=None)
    finally:
        client._session.close()
    assert not path.exists()
    assert not path.with_suffix(".exe.part").exists()
    response.close.assert_called_once()


def test_release_metadata_has_a_bounded_read(monkeypatch):
    from updater.client import GitHubReleaseClient, UpdateError
    client = GitHubReleaseClient()
    response = Mock(status_code=200)
    response.iter_content.return_value = [b"1234", b"5"]
    client._session.get = Mock(return_value=response)
    monkeypatch.setattr("updater.client.MAX_METADATA_BYTES", 4)
    try:
        with pytest.raises(UpdateError, match="大小限制"):
            client._request_json("https://api.github.com/synthetic")
    finally:
        client._session.close()
    response.close.assert_called_once()
