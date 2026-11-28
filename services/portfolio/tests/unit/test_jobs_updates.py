"""jobs.run_update_transaction: the edit runs on its own connection, then the account's positions
and returns are rebuilt, isolated so a rebuild failure is reported rather than turning a saved
edit into an error. An edit that changed nothing rebuilds nothing. Kept out of test_jobs.py,
which is over the 64KB limit, so its small helpers are repeated here rather than shared."""

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from portfolio import jobs
from portfolio.errors import TransactionNotFound
from portfolio.settings import SettingsError


class _FakeConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _settings(datastore_api_url=None, internal_api_secret=None):
    return type(
        "S",
        (),
        {
            "database_url": "postgresql://fake/fake",  # pragma: allowlist secret
            "admin_database_url": "postgresql://fake/fake",  # pragma: allowlist secret
            "datastore_api_url": datastore_api_url,
            "internal_api_secret": internal_api_secret,
        },
    )()


@pytest.fixture
def fake_conn(monkeypatch):
    monkeypatch.setattr(jobs, "load_settings", _settings)
    conn = _FakeConnection()
    monkeypatch.setattr(jobs, "get_connection", lambda url: conn)
    monkeypatch.setattr(jobs, "user_connection", lambda settings, user_id: conn)
    monkeypatch.setattr(jobs, "cross_user_connection", lambda settings: conn)
    return conn


@pytest.fixture
def rebuild_calls(monkeypatch):
    calls = []

    def fake_rebuild_positions(**kw):
        calls.append(kw)
        return {"status": "ok", "rows_saved": 0}

    monkeypatch.setattr(jobs, "run_rebuild_positions", fake_rebuild_positions)
    return calls


_KWARGS = dict(
    account_label="m",
    broker_transaction_ref="ref-1",
    transaction_type="DEPOSIT",
    trade_datetime=datetime(2026, 1, 1, tzinfo=UTC),
    native_amount=Decimal("150"),
    native_currency="EUR",
)


def _update(user_id=None, **overrides):
    return jobs.run_update_transaction(user_id=user_id or uuid.uuid4(), **{**_KWARGS, **overrides})


def test_run_update_transaction_reports_the_edit_and_the_rebuilds(
    monkeypatch, fake_conn, rebuild_calls
):
    captured = {}

    def fake_update(conn, **kw):
        captured.update(kw)
        return 1

    monkeypatch.setattr(jobs.pipeline, "update_transaction", fake_update)
    user_id = uuid.uuid4()

    result = _update(user_id)

    assert result == {
        "status": "ok",
        "rows_saved": 1,
        "transactions_updated": 1,
        "positions_rebuild": "ok",
        "returns_rebuild": "skipped",
    }
    assert captured["user_id"] == user_id
    assert captured["broker_transaction_ref"] == "ref-1"
    assert captured["native_amount"] == Decimal("150")
    assert captured["datastore_client"] is None
    assert [(c["user_id"], c["account_label"]) for c in rebuild_calls] == [(user_id, "m")]
    assert fake_conn.closed is True


def test_run_update_transaction_rebuilds_nothing_when_nothing_changed(
    monkeypatch, fake_conn, rebuild_calls
):
    monkeypatch.setattr(jobs.pipeline, "update_transaction", lambda conn, **kw: 0)

    result = _update()

    assert result == {
        "status": "nothing_to_do",
        "rows_saved": 0,
        "transactions_updated": 0,
        "positions_rebuild": "skipped",
        "returns_rebuild": "skipped",
    }
    assert rebuild_calls == []


def test_run_update_transaction_propagates_a_missing_transaction_without_rebuilding(
    monkeypatch, fake_conn, rebuild_calls
):
    def boom(conn, **kw):
        raise TransactionNotFound("no such transaction")

    monkeypatch.setattr(jobs.pipeline, "update_transaction", boom)

    with pytest.raises(TransactionNotFound):
        _update()

    assert rebuild_calls == []
    assert fake_conn.closed is True


def test_run_update_transaction_reports_a_failed_rebuild_instead_of_raising(
    monkeypatch, fake_conn, caplog
):
    monkeypatch.setattr(jobs.pipeline, "update_transaction", lambda conn, **kw: 1)

    def broken_rebuild(**kw):
        raise RuntimeError("connection dropped")

    monkeypatch.setattr(jobs, "run_rebuild_positions", broken_rebuild)

    with caplog.at_level(logging.ERROR, logger="portfolio.jobs"):
        result = _update()

    assert result["status"] == "ok"
    assert result["positions_rebuild"] == "failed"
    assert result["returns_rebuild"] == "skipped"
    assert "the edit is saved" in caplog.text
    assert "portfolio rebuild-positions" in caplog.text


def test_run_update_transaction_logs_what_it_edits_and_the_outcome(
    monkeypatch, fake_conn, rebuild_calls, caplog
):
    monkeypatch.setattr(jobs.pipeline, "update_transaction", lambda conn, **kw: 1)

    with caplog.at_level(logging.INFO, logger="portfolio.jobs"):
        _update()

    assert "edit-transaction: editing 'ref-1' in account 'm'" in caplog.text
    assert "edit-transaction: done, 1 transaction(s) updated." in caplog.text


def test_run_update_transaction_says_when_nothing_changed(
    monkeypatch, fake_conn, rebuild_calls, caplog
):
    monkeypatch.setattr(jobs.pipeline, "update_transaction", lambda conn, **kw: 0)

    with caplog.at_level(logging.INFO, logger="portfolio.jobs"):
        _update()

    assert "edit-transaction: done, nothing changed." in caplog.text


def test_run_update_transaction_builds_a_data_store_client_for_a_ticker(monkeypatch, fake_conn):
    monkeypatch.setattr(
        jobs,
        "load_settings",
        lambda: _settings("http://datastore:8000", "secret"),  # pragma: allowlist secret
    )
    passed = {}

    class FakeHttpDataStoreClient:
        def __init__(self, base_url, internal_api_secret):
            pass

    def fake_update(conn, **kw):
        passed.update(kw)
        return 0

    monkeypatch.setattr(jobs, "HttpDataStoreClient", FakeHttpDataStoreClient)
    monkeypatch.setattr(jobs.pipeline, "update_transaction", fake_update)

    _update(instrument_identifier="IWDA.AS", instrument_identifier_kind="TICKER")

    assert isinstance(passed["datastore_client"], FakeHttpDataStoreClient)


def test_run_update_transaction_requires_data_store_settings_for_a_ticker(monkeypatch, fake_conn):
    monkeypatch.setattr(jobs.pipeline, "update_transaction", lambda conn, **kw: 1)

    with pytest.raises(SettingsError):
        _update(instrument_identifier="IWDA.AS", instrument_identifier_kind="TICKER")
