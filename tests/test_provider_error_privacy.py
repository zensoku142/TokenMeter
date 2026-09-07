"""Unexpected transport exceptions must not leak request credentials into UI or logs."""

from datetime import date
from unittest.mock import Mock

import pytest

from api.providers.base import Provider
from data import history
from data.store import TokenData


class SyntheticProvider(Provider):
    id = "synthetic"
    name = "Synthetic"

    def is_configured(self):
        return True


@pytest.mark.parametrize("method", ["fetch_quota", "fetch_balance", "fetch_summary", "fetch_payloads"])
@pytest.mark.parametrize("connection_test", [False, True])
def test_unexpected_provider_errors_keep_context_without_raw_credentials(method, connection_test, tmp_path, monkeypatch, caplog):
    provider = SyntheticProvider({})
    setattr(provider, method, Mock(side_effect=RuntimeError("Bearer SENSITIVE_REQUEST_SECRET")))
    monkeypatch.setattr(history, "DB_PATH", tmp_path / "usage.db")
    monkeypatch.setattr(TokenData, "_provider_snapshots", {})
    monkeypatch.setattr(TokenData, "_last_snapshot", None)
    result = (
        TokenData._test_connection_with_provider(provider) if connection_test
        else TokenData._fetch_with_provider(provider, date(2026, 9, 7))
    )
    assert any(error.code == "UNKNOWN_ERROR" for error in result.errors)
    assert "SENSITIVE_REQUEST_SECRET" not in repr(result.errors)
    assert "SENSITIVE_REQUEST_SECRET" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_connection_worker_does_not_leak_initialization_exception(monkeypatch, caplog):
    from ui.qt_settings import ConnectionWorker

    monkeypatch.setattr(TokenData, "test_connection", Mock(side_effect=RuntimeError("SENSITIVE_REQUEST_SECRET")))
    worker = ConnectionWorker({})
    results = []
    worker.finished_with_data.connect(results.append)
    worker.run()
    assert len(results) == 1
    assert "SENSITIVE_REQUEST_SECRET" not in repr(results[0].errors) + caplog.text
    assert "RuntimeError" in caplog.text


def test_connection_cancellation_skips_remaining_provider_queries():
    import threading

    stop = threading.Event()
    provider = SyntheticProvider({})
    provider.fetch_quota = Mock(side_effect=lambda: (stop.set(), None))
    provider.fetch_balance = Mock()
    provider.fetch_summary = Mock()
    provider.fetch_payloads = Mock()
    result = TokenData._test_connection_with_provider(provider, should_stop=stop.is_set)
    provider.fetch_balance.assert_not_called()
    provider.fetch_summary.assert_not_called()
    provider.fetch_payloads.assert_not_called()
    assert any(error.code == "CANCELLED" for error in result.errors)
