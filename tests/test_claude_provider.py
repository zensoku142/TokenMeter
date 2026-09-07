import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from api.providers.claude import ClaudeProvider
from scripts.claude_statusline import make_snapshot, write_snapshot


class ClaudeProviderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "usage.json"
        self.provider = ClaudeProvider({"CLAUDE_STATUSLINE_FILE": str(self.path)})
        self.reset = int((datetime.now(timezone.utc) + timedelta(hours=3)).timestamp())

    def save(self, payload):
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def snapshot(self):
        return make_snapshot(
            {"rate_limits": {"five_hour": {"used_percentage": 25.5, "resets_at": self.reset}}}
        )

    def test_reads_official_windows_without_token_estimates(self):
        payload = self.snapshot()
        payload["rate_limits"]["seven_day"] = {
            "used_percentage": 70,
            "resets_at": self.reset + 86400,
        }
        self.save(payload)
        quota, error = self.provider.fetch_quota()
        self.assertIsNone(error)
        self.assertEqual([window.id for window in quota.windows], ["five_hour", "seven_day"])
        self.assertEqual([window.used_percent for window in quota.windows], [25.5, 70])
        self.assertEqual([window.window_minutes for window in quota.windows], [300, 10080])
        self.assertEqual(
            quota.windows[0].resets_at, datetime.fromtimestamp(self.reset, timezone.utc)
        )
        self.assertEqual(quota.statistics, ())
        self.assertEqual(self.provider.fetch_balance(), (None, None))

    def test_missing_window_is_not_fabricated(self):
        self.save(self.snapshot())
        quota, error = self.provider.fetch_quota()
        self.assertIsNone(error)
        self.assertEqual(len(quota.windows), 1)
        self.save(make_snapshot({"context_window": {"used_percentage": 80}}))
        quota, error = self.provider.fetch_quota()
        self.assertIsNone(quota)
        self.assertEqual(error.code, "NO_DATA")

    def test_old_snapshot_is_not_reported_as_fresh_quota(self):
        payload = self.snapshot()
        payload["observed_at"] = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
        self.save(payload)
        quota, error = self.provider.fetch_quota()
        self.assertIsNone(quota)
        self.assertEqual(error.code, "STALE_DATA")

    def test_missing_file_is_unconfigured_and_read_failure_has_error(self):
        self.assertFalse(self.provider.is_configured())
        self.assertEqual(self.provider.fetch_quota()[1].code, "NOT_CONFIGURED")
        self.save(self.snapshot())
        self.assertTrue(self.provider.is_configured())
        with patch.object(Path, "open", side_effect=PermissionError("private message")):
            self.assertEqual(self.provider.fetch_quota()[1].code, "FILE_ERROR")

    def test_malformed_snapshots_are_not_reported_as_zero(self):
        for raw in ("{", "[]", '{"schema_version":true}', "x" * (16 * 1024 + 1)):
            with self.subTest(raw=raw[:50]):
                self.path.write_text(raw, encoding="utf-8")
                quota, error = self.provider.fetch_quota()
                self.assertIsNone(quota)
                self.assertEqual(error.code, "INVALID_RESPONSE")

    def test_rejects_invalid_percentages_and_reset_times(self):
        for field, values in (
            ("used_percentage", (None, True, "25", -1, 101, float("nan"), float("inf"))),
            ("resets_at", (None, False, "tomorrow", 0, -1, float("nan"), float("inf"), 10**100)),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    payload = self.snapshot()
                    payload["rate_limits"]["five_hour"][field] = value
                    self.save(payload)
                    quota, error = self.provider.fetch_quota()
                    self.assertIsNone(quota)
                    self.assertEqual(error.code, "INVALID_RESPONSE")

    def test_rejects_invalid_observation_time_and_schema(self):
        for field, values in (
            (
                "observed_at",
                (
                    True,
                    "yesterday",
                    "2026-09-05T12:00:00",
                    (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                ),
            ),
            ("schema_version", (None, True, "1", 2)),
            ("rate_limits", (None, [], True)),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    payload = self.snapshot()
                    payload[field] = value
                    self.save(payload)
                    self.assertEqual(self.provider.fetch_quota()[1].code, "INVALID_RESPONSE")

    def test_account_scope_is_required_for_persistent_identity(self):
        payload = self.snapshot()
        self.save(payload)
        self.assertEqual(self.provider.snapshot_identity(), "")
        payload["account_scope"] = "personal-account"
        self.save(payload)
        first = self.provider.snapshot_identity()
        self.assertEqual(len(first), 64)
        self.assertNotIn("personal-account", first)
        payload["account_scope"] = "work-account"
        self.save(payload)
        self.assertNotEqual(first, self.provider.snapshot_identity())

    def test_custom_paths_keep_same_scope_isolated(self):
        payload = self.snapshot()
        payload["account_scope"] = "personal-account"
        self.save(payload)
        alternate = self.path.with_name("alternate.json")
        alternate.write_text(json.dumps(payload), encoding="utf-8")
        second = ClaudeProvider({"CLAUDE_STATUSLINE_FILE": str(alternate)})
        self.assertNotEqual(self.provider.snapshot_identity(), second.snapshot_identity())

    def test_helper_saves_only_whitelisted_fields(self):
        payload = {
            "transcript_path": "secret-session.jsonl",
            "prompt": "private text",
            "api_key": "secret-key",
            "account_scope": "untrusted-auto-scope",
            "rate_limits": {
                "five_hour": {
                    "used_percentage": 20,
                    "resets_at": self.reset,
                    "secret": "nested-secret",
                }
            },
        }
        snapshot = make_snapshot(payload)
        write_snapshot(self.path, snapshot)
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(set(saved), {"schema_version", "observed_at", "rate_limits"})
        self.assertEqual(set(saved["rate_limits"]["five_hour"]), {"used_percentage", "resets_at"})
        self.assertNotIn("secret", self.path.read_text(encoding="utf-8"))

    def test_helper_replaces_previous_windows_when_limits_are_absent(self):
        write_snapshot(self.path, self.snapshot())
        write_snapshot(self.path, make_snapshot({}))
        self.assertEqual(self.provider.fetch_quota()[1].code, "NO_DATA")
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_failed_atomic_replace_preserves_previous_file_and_cleans_temporary(self):
        write_snapshot(self.path, self.snapshot())
        previous = self.path.read_bytes()
        with (
            patch("scripts.claude_statusline.os.replace", side_effect=PermissionError),
            self.assertRaises(PermissionError),
        ):
            write_snapshot(self.path, make_snapshot({}))
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_helper_command_round_trip_and_no_secret_output(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "claude_statusline.py"
        payload = {"rate_limits": self.snapshot()["rate_limits"], "prompt": "secret-prompt"}
        result = subprocess.run(
            [sys.executable, str(script), "--output", str(self.path)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "Claude | 5h 74.5% left")
        self.assertNotIn("secret", result.stdout + result.stderr + self.path.read_text())
        self.assertIsNone(self.provider.fetch_quota()[1])

    def test_helper_rejects_invalid_input_without_exposing_it(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "claude_statusline.py"
        result = subprocess.run(
            [sys.executable, str(script), "--output", str(self.path)],
            input="not-json-secret",
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.path.exists())
        self.assertNotIn("secret", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()


@pytest.fixture
def oauth_provider(tmp_path, monkeypatch):
    # 默认登录发现必须始终隔离到临时目录，不接触开发者本机账号。
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    session = Mock()
    session.get.return_value.status_code = 200
    session.get.return_value.json.return_value = {
        "five_hour": {"utilization": 25.5, "resets_at": "2026-09-06T12:00:00Z"},
        "seven_day": {"utilization": 70, "resets_at": None},
        "seven_day_sonnet": None,
    }
    monkeypatch.setattr("api.providers.claude.build_session", lambda: session)
    provider = ClaudeProvider({"CLAUDE_ACCESS_TOKEN": "synthetic-secret"})
    yield provider
    provider.close()


def save_oauth_credentials(provider, tmp_path, **values):
    path = tmp_path / ".claude" / ".credentials.json"
    path.parent.mkdir(exist_ok=True)
    credentials = {
        "claudeAiOauth": {
            "accessToken": "synthetic-local-secret", "refreshToken": "never-use-refresh",
            "expiresAt": (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp() * 1000,
            "scopes": ["user:profile", "user:inference"], "subscriptionType": "max", **values,
        },
    }
    path.write_text(json.dumps(credentials), encoding="utf-8")
    provider._config = {}
    return path


def test_manual_oauth_skips_file_reads_and_uses_fixed_transport(oauth_provider, monkeypatch):
    monkeypatch.setattr(Path, "open", Mock(side_effect=AssertionError("no file reads")))
    quota, error = oauth_provider.fetch_quota()
    assert error is None
    assert quota.source == "interface"
    assert [window.used_percent for window in quota.windows] == [25.5, 70]
    assert quota.windows[0].resets_at == datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    assert quota.windows[1].resets_at is None
    args, kwargs = oauth_provider._session.get.call_args
    assert args == ("https://api.anthropic.com/api/oauth/usage",)
    assert kwargs["headers"]["Authorization"] == "Bearer synthetic-secret"
    assert kwargs["headers"]["anthropic-beta"] == "oauth-2025-04-20"
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == (3, 10)


def test_default_reads_local_oauth_without_writing_and_reloads_account(oauth_provider, tmp_path):
    path = save_oauth_credentials(oauth_provider, tmp_path)
    original = path.read_bytes()
    assert oauth_provider.is_configured()
    first = oauth_provider.snapshot_identity()
    quota, error = oauth_provider.fetch_quota()
    assert error is None
    assert quota.plan == "max"
    assert quota.source == "interface"
    assert path.read_bytes() == original
    save_oauth_credentials(oauth_provider, tmp_path, accessToken="another-account-secret")
    assert oauth_provider.snapshot_identity() != first
    assert "secret" not in first


def test_custom_config_dir_is_respected(oauth_provider, tmp_path, monkeypatch):
    path = save_oauth_credentials(oauth_provider, tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(path.parent))
    assert oauth_provider._credentials_path() == path
    assert oauth_provider.fetch_quota()[1] is None


def test_explicit_snapshot_wins_over_configured_oauth(oauth_provider, tmp_path):
    path = tmp_path / "usage.json"
    snapshot = make_snapshot({"rate_limits": {"five_hour": {
        "used_percentage": 80, "resets_at": (datetime.now(timezone.utc) + timedelta(hours=2)).timestamp(),
    }}})
    snapshot["account_scope"] = "snapshot-account"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    first = oauth_provider.snapshot_identity()
    oauth_provider._config["CLAUDE_STATUSLINE_FILE"] = str(path)
    quota, error = oauth_provider.fetch_quota()
    assert error is None
    assert quota.windows[0].used_percent == 80
    assert quota.source == "local_snapshot"
    assert oauth_provider.snapshot_identity() != first
    oauth_provider._session.get.assert_not_called()


def test_default_missing_oauth_uses_existing_default_snapshot(oauth_provider, tmp_path):
    path = tmp_path / ".claude" / "tokenmeter-usage.json"
    path.parent.mkdir()
    snapshot = make_snapshot({"rate_limits": {"five_hour": {
        "used_percentage": 55, "resets_at": (datetime.now(timezone.utc) + timedelta(hours=2)).timestamp(),
    }}})
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    oauth_provider._config = {}
    assert oauth_provider.is_configured()
    quota, error = oauth_provider.fetch_quota()
    assert error is None
    assert quota.source == "local_snapshot"
    oauth_provider._session.get.assert_not_called()


def test_missing_explicit_credentials_does_not_fallback(oauth_provider, tmp_path):
    oauth_provider._config = {"CLAUDE_CREDENTIALS_FILE": str(tmp_path / "missing.json")}
    assert not oauth_provider.is_configured()
    assert oauth_provider.snapshot_identity() == ""
    with patch.object(oauth_provider, "_fetch_snapshot", side_effect=AssertionError("no fallback")):
        assert oauth_provider.fetch_quota()[1].code == "NOT_CONFIGURED"


@pytest.mark.parametrize("raw", ["{", "[]", "{}", '{"mcpOAuth":{}}', "x" * (64 * 1024 + 1)],
                         ids=["json", "list", "empty", "mcp-only", "oversize"])
def test_malformed_local_credentials_fail_without_snapshot_fallback(oauth_provider, tmp_path, raw):
    path = save_oauth_credentials(oauth_provider, tmp_path)
    path.write_text(raw, encoding="utf-8")
    with patch.object(oauth_provider, "_fetch_snapshot", side_effect=AssertionError("no fallback")):
        error = oauth_provider.fetch_quota()[1]
    assert error.code == "INVALID_CREDENTIALS"
    assert not oauth_provider.is_configured()
    assert oauth_provider.snapshot_identity() == ""
    oauth_provider._session.get.assert_not_called()


@pytest.mark.parametrize("values,code", [
    ({"expiresAt": 1}, "AUTH_EXPIRED"), ({"scopes": ["user:inference"]}, "SCOPE_REQUIRED"),
    ({"scopes": []}, "SCOPE_REQUIRED"), ({"scopes": "user:profile"}, "INVALID_CREDENTIALS"),
    ({"expiresAt": True}, "INVALID_CREDENTIALS"), ({"expiresAt": "123"}, "INVALID_CREDENTIALS"),
    ({"expiresAt": float("nan")}, "INVALID_CREDENTIALS"), ({"expiresAt": 10**400}, "INVALID_CREDENTIALS"),
])
def test_expired_or_unusable_login_never_refreshes(oauth_provider, tmp_path, values, code):
    path = save_oauth_credentials(oauth_provider, tmp_path, **values)
    original = path.read_bytes()
    assert oauth_provider.fetch_quota()[1].code == code
    assert path.read_bytes() == original
    oauth_provider._session.get.assert_not_called()


def test_identity_scopes_file_paths_and_manual_account(oauth_provider, tmp_path):
    first = oauth_provider.snapshot_identity()
    oauth_provider._config["CLAUDE_ACCESS_TOKEN"] = "second-secret"
    assert oauth_provider.snapshot_identity() != first
    first = oauth_provider.snapshot_identity()
    oauth_provider._config["CLAUDE_CREDENTIALS_FILE"] = str(tmp_path / "alternate.json")
    assert oauth_provider.snapshot_identity() != first


def test_model_specific_windows_and_new_scoped_weekly_contract(oauth_provider):
    payload = oauth_provider._session.get.return_value.json.return_value
    payload.update({
        "seven_day_sonnet": {"utilization": 50}, "seven_day_opus": {"utilization": 80},
        "seven_day_routines": {"utilization": 10}, "seven_day_cowork": {"utilization": 10},
        "limits": [
            {"kind": "weekly_scoped", "percent": 42, "scope": {"model": {"display_name": "Fable"}}},
            {"kind": "weekly_scoped", "percent": 30, "scope": {"model": {"display_name": "All models"}}},
            {"kind": "weekly_scoped", "percent": 90, "is_active": False,
             "scope": {"model": {"display_name": "Inactive"}}},
        ],
    })
    quota, error = oauth_provider.fetch_quota()
    assert error is None
    assert [window.used_percent for window in quota.windows] == [25.5, 70, 50, 80, 10, 42]
    assert quota.windows[-1].title == "Fable 每周额度"


@pytest.mark.parametrize("payload", [{}, {"five_hour": None}, {"five_hour": {"utilization": None}},
                                    {"five_hour": {"resets_at": "2026-09-06T12:00:00Z"}}])
def test_oauth_missing_usage_does_not_fabricate_zero(oauth_provider, payload):
    oauth_provider._session.get.return_value.json.return_value = payload
    assert oauth_provider.fetch_quota()[1].code == "QUOTA_UNAVAILABLE"


@pytest.mark.parametrize("field,value", [("utilization", True), ("utilization", "25"), ("utilization", -1),
    ("utilization", 101), ("utilization", float("nan")), ("utilization", float("inf")),
    ("utilization", 10**400), ("resets_at", 123), ("resets_at", "invalid"),
    ("resets_at", "2026-09-06T12:00:00")])
def test_oauth_invalid_window_fails_safely(oauth_provider, field, value):
    oauth_provider._session.get.return_value.json.return_value = {"five_hour": {"utilization": 25, field: value}}
    assert oauth_provider.fetch_quota()[1].code == "INVALID_RESPONSE"


@pytest.mark.parametrize("payload", [[], None, {"five_hour": []}, {"limits": {}}, {"limits": [None]},
                                    {"error": {"message": "synthetic-secret"}}])
def test_oauth_invalid_payload_fails_safely(oauth_provider, payload):
    oauth_provider._session.get.return_value.json.return_value = payload
    assert oauth_provider.fetch_quota()[1].code == "INVALID_RESPONSE"


@pytest.mark.parametrize("status,code", [(401, "AUTH_EXPIRED"), (403, "PERMISSION_DENIED"),
    (429, "RATE_LIMITED"), (302, "INVALID_RESPONSE"), (503, "SERVER_ERROR")])
def test_oauth_http_error_never_echoes_secret_or_uses_snapshot(oauth_provider, status, code, caplog):
    oauth_provider._session.get.return_value.status_code = status
    oauth_provider._session.get.return_value.text = "synthetic-secret"
    with patch.object(oauth_provider, "_fetch_snapshot", side_effect=AssertionError("no fallback")):
        error = oauth_provider.fetch_quota()[1]
    assert error.code == code
    assert "synthetic-secret" not in repr(error) + caplog.text


@pytest.mark.parametrize("failure,code", [(requests.Timeout("synthetic-secret"), "NETWORK_TIMEOUT"),
                                         (requests.ConnectionError("synthetic-secret"), "NETWORK_ERROR")])
def test_oauth_transport_error_is_safe(oauth_provider, failure, code, caplog):
    oauth_provider._session.get.side_effect = failure
    error = oauth_provider.fetch_quota()[1]
    assert error.code == code
    assert "synthetic-secret" not in repr(error) + caplog.text


def test_oauth_json_error_is_safe(oauth_provider):
    oauth_provider._session.get.return_value.json.side_effect = ValueError("synthetic-secret")
    assert oauth_provider.fetch_quota()[1].code == "INVALID_RESPONSE"


def test_oauth_close_releases_session(oauth_provider):
    oauth_provider.close()
    oauth_provider._session.close.assert_called_once_with()
