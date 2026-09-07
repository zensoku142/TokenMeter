"""Official Antigravity statusline fields are normalized without exposing CLI context."""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from api.providers.antigravity import AntigravityProvider
from api.providers.base import QuotaWindow
from data.store import TokenData
from scripts.antigravity_statusline import make_snapshot, write_snapshot


def payload(**changes):
    return {"product": "antigravity", "email": "user@example.test", "plan_tier": "Pro",
            "quota": {"gemini-weekly": {"remaining_fraction": 0.75, "reset_time": "2099-01-01T00:00:00Z"}},
            **changes}


@pytest.fixture
def provider(tmp_path):
    return AntigravityProvider({"ANTIGRAVITY_STATUSLINE_FILE": str(tmp_path / "quota.json")})


def save(provider, data=None):
    snapshot = make_snapshot(payload()) if data is None else data
    write_snapshot(provider._snapshot_path(), snapshot)
    return snapshot


def test_snapshot_export_preserves_quota_but_not_email_transcripts_or_tokens(provider):
    snapshot = make_snapshot(payload(cwd="PRIVATE-DIRECTORY", transcript_path="PRIVATE-TRANSCRIPT",
                                     access_token="PRIVATE-TOKEN", context_window={"used_percentage": 90}))
    serialized = json.dumps(snapshot)
    assert "user@example.test" not in serialized
    assert "PRIVATE" not in serialized
    assert "context_window" not in snapshot
    assert len(snapshot["account_scope"]) == 64
    save(provider, snapshot)
    quota, error = provider.fetch_quota()
    assert error is None and quota.source == "local_snapshot"
    assert quota.windows[0].used_percent == 25
    assert quota.windows[0].window_minutes is None
    assert quota.windows[0].resets_at == datetime(2099, 1, 1, tzinfo=timezone.utc)
    assert quota.plan == "Pro"


@pytest.mark.parametrize("remaining", [None, True, False, -0.1, 1.1, "0.5", [], {}, float("nan"), float("inf"), 10**400])
def test_invalid_fraction_cannot_become_full_quota(provider, remaining):
    values = payload(quota={"bad": {"remaining_fraction": remaining}, "good": {"remaining_fraction": 0}})
    exported = make_snapshot(values)
    assert list(exported["quota"]) == ["good"]
    # 同时验证直接改写快照的坏值，不能只依赖采集脚本校验。
    exported["quota"]["bad"] = values["quota"]["bad"]
    provider._snapshot_path().write_text(json.dumps(exported), encoding="utf-8")
    quota, error = provider.fetch_quota()
    assert error is None
    assert [(window.id, window.used_percent) for window in quota.windows] == [("good", 100)]


def test_optional_relative_reset_is_anchored_at_snapshot_time(provider):
    snapshot = make_snapshot(payload(quota={"model": {"remaining_fraction": 0.5, "reset_in_seconds": 60}}))
    save(provider, snapshot)
    quota, error = provider.fetch_quota()
    assert error is None
    observed = datetime.fromisoformat(snapshot["observed_at"])
    assert quota.windows[0].resets_at == observed + timedelta(seconds=60)


def test_absent_reset_stays_unknown_and_empty_payload_clears_previous_quota(provider):
    save(provider, make_snapshot(payload(quota={"model": {"remaining_fraction": 1}})))
    quota, error = provider.fetch_quota()
    assert error is None and quota.windows[0].resets_at is None
    save(provider, make_snapshot(payload(quota=None)))
    quota, error = provider.fetch_quota()
    assert quota is None and error.code == "NO_DATA"


@pytest.mark.parametrize("delta,code", [(timedelta(minutes=-16), "STALE_DATA"), (timedelta(minutes=2), "INVALID_RESPONSE")])
def test_snapshot_freshness_is_checked(provider, delta, code):
    snapshot = make_snapshot(payload())
    snapshot["observed_at"] = (datetime.now(timezone.utc) + delta).isoformat()
    save(provider, snapshot)
    assert provider.fetch_quota()[1].code == code


@pytest.mark.parametrize("data", [{}, [], {"product": "claude"}, payload(quota=[]), payload(quota="bad")])
def test_wrong_statusline_input_is_rejected(data):
    with pytest.raises(ValueError):
        make_snapshot(data)


def test_account_and_file_location_isolate_cache_and_missing_scope_disables_reuse(provider, tmp_path, monkeypatch):
    save(provider)
    first = provider.snapshot_identity()
    save(provider, make_snapshot(payload(email="other@example.test")))
    assert provider.snapshot_identity() != first
    save(provider, make_snapshot(payload(email="")))
    assert provider.snapshot_identity() == ""
    monkeypatch.setattr(TokenData, "_provider_snapshots", {"antigravity": TokenData(
        quota_windows=[QuotaWindow("old-account", "Old", 99)],
    )})
    assert TokenData.cached_snapshot("antigravity", "") is None
    assert TokenData._base_snapshot("antigravity", "").quota_windows == []
    explicit = make_snapshot(payload(email=""), "account-alias")
    save(provider, explicit)
    assert provider.snapshot_identity()
    other = AntigravityProvider({"ANTIGRAVITY_STATUSLINE_FILE": str(tmp_path / "other.json")})
    save(other, explicit)
    assert other.snapshot_identity() != provider.snapshot_identity()


def test_snapshot_timestamp_label_is_normalized_to_utc(provider):
    snapshot = make_snapshot(payload())
    now = datetime.now(timezone.utc).replace(microsecond=0)
    snapshot["observed_at"] = now.astimezone(timezone(timedelta(hours=8))).isoformat()
    save(provider, snapshot)
    quota, error = provider.fetch_quota()
    assert error is None
    assert quota.metrics[0].value == now.strftime("%H:%M")


@pytest.mark.parametrize("reset", ["invalid", "2026-01-01T00:00:00", True, {}, []])
def test_malformed_reset_does_not_drop_other_valid_buckets(provider, reset):
    data = payload(quota={"bad": {"remaining_fraction": 0.5, "reset_time": reset},
                          "good": {"remaining_fraction": 0.25}})
    snapshot = make_snapshot(data)
    assert list(snapshot["quota"]) == ["good"]
    snapshot["quota"]["bad"] = data["quota"]["bad"]
    save(provider, snapshot)
    quota, error = provider.fetch_quota()
    assert error is None and [window.id for window in quota.windows] == ["good"]


def test_missing_oversized_and_bad_files_return_sanitized_errors(provider):
    assert not provider.is_configured()
    assert provider.fetch_quota()[1].code == "NOT_CONFIGURED"
    for content in ("PRIVATE-TOKEN", "x" * (64 * 1024 + 1), "[" * 10000 + "]" * 10000):
        provider._snapshot_path().write_text(content, encoding="utf-8")
        quota, error = provider.fetch_quota()
        assert quota is None and error.code == "INVALID_RESPONSE"
        assert "PRIVATE" not in error.message


def test_script_runs_standalone_from_another_directory_without_network(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "antigravity_statusline.py"
    target = tmp_path / "quota.json"
    result = subprocess.run([sys.executable, str(script), "--output", str(target)],
                            input=json.dumps(payload()), text=True, capture_output=True, cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "75%" in result.stdout
    assert "user@example.test" not in result.stdout + result.stderr + target.read_text(encoding="utf-8")
