"""jobs.run_delete_transaction: the delete runs on its own connection, then the account's
positions and returns are rebuilt, each isolated so a rebuild failure is reported rather than
turning a done delete into an error. jobs.run_delete_account: no rebuild follows, since the
account's derived rows go with it. Kept out of test_jobs.py, which is over the 64KB limit, so
its small helpers are repeated here rather than shared."""

import logging
import uuid

import pytest
from portfolio import jobs
from portfolio.errors import AccountNotFound, TransactionNotFound


class _FakeConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _settings():
    # No data-store configured, so the chained returns rebuild is reported as skipped.
    return type(
        "S",
        (),
        {
            "database_url": "postgresql://fake/fake",  # pragma: allowlist secret
            "admin_database_url": "postgresql://fake/fake",  # pragma: allowlist secret
            "datastore_api_url": None,
            "internal_api_secret": None,
        },
    )()


@pytest.fixture
def fake_conn(monkeypatch):
    monkeypatch.setattr(jobs, "load_settings", _settings)
    conn = _FakeConnection()
    monkeypatch.setattr(jobs, "get_connection", lambda url: conn)
    return conn


@pytest.fixture
def rebuild_calls(monkeypatch):
    calls = []

    def fake_rebuild_positions(**kw):
        calls.append(kw)
        return {"status": "ok", "rows_saved": 0}

    monkeypatch.setattr(jobs, "run_rebuild_positions", fake_rebuild_positions)
    return calls


def _delete(user_id=None):
    return jobs.run_delete_transaction(
        user_id=user_id or uuid.uuid4(), account_label="m", broker_transaction_ref="manual-1"
    )


def test_run_delete_transaction_reports_the_delete_and_the_rebuilds(
    monkeypatch, fake_conn, rebuild_calls
):
    captured = {}
    monkeypatch.setattr(jobs.pipeline, "delete_transaction", lambda conn, **kw: captured.update(kw))
    user_id = uuid.uuid4()

    result = _delete(user_id)

    assert result == {
        "status": "ok",
        "rows_saved": 0,
        "transactions_deleted": 1,
        "positions_rebuild": "ok",
        "returns_rebuild": "skipped",
    }
    assert captured == {
        "user_id": user_id,
        "account_label": "m",
        "broker_transaction_ref": "manual-1",
    }
    assert fake_conn.closed is True


def test_run_delete_transaction_rebuilds_the_account_it_deleted_from(
    monkeypatch, fake_conn, rebuild_calls
):
    monkeypatch.setattr(jobs.pipeline, "delete_transaction", lambda conn, **kw: None)
    user_id = uuid.uuid4()

    _delete(user_id)

    assert [(c["user_id"], c["account_label"]) for c in rebuild_calls] == [(user_id, "m")]


def test_run_delete_transaction_propagates_a_missing_transaction_without_rebuilding(
    monkeypatch, fake_conn, rebuild_calls
):
    def boom(conn, **kw):
        raise TransactionNotFound("no such transaction")

    monkeypatch.setattr(jobs.pipeline, "delete_transaction", boom)

    with pytest.raises(TransactionNotFound):
        _delete()

    assert rebuild_calls == []
    assert fake_conn.closed is True


def test_run_delete_transaction_reports_a_failed_rebuild_instead_of_raising(
    monkeypatch, fake_conn, caplog
):
    """The delete has committed by the time the rebuild runs, so raising would tell the caller
    a done delete failed. The log names the command to retry the rebuild."""
    monkeypatch.setattr(jobs.pipeline, "delete_transaction", lambda conn, **kw: None)

    def broken_rebuild(**kw):
        raise RuntimeError("connection dropped")

    monkeypatch.setattr(jobs, "run_rebuild_positions", broken_rebuild)

    with caplog.at_level(logging.ERROR, logger="portfolio.jobs"):
        result = _delete()

    assert result["status"] == "ok"
    assert result["positions_rebuild"] == "failed"
    assert result["returns_rebuild"] == "skipped"
    assert "the deletion is saved" in caplog.text
    assert "portfolio rebuild-positions" in caplog.text


def test_run_delete_transaction_logs_what_it_deletes_and_the_outcome(
    monkeypatch, fake_conn, rebuild_calls, caplog
):
    monkeypatch.setattr(jobs.pipeline, "delete_transaction", lambda conn, **kw: None)

    with caplog.at_level(logging.INFO, logger="portfolio.jobs"):
        _delete()

    assert "delete-transaction: deleting 'manual-1' from account 'm'" in caplog.text
    assert "delete-transaction: done, 1 transaction(s) deleted." in caplog.text


# --- run_delete_account ---


def _outcome(transactions=3, positions=2, returns=40):
    return jobs.pipeline.AccountDeleteOutcome(
        transactions_deleted=transactions,
        position_snapshots_deleted=positions,
        return_snapshots_deleted=returns,
    )


def test_run_delete_account_reports_what_went_with_the_account(
    monkeypatch, fake_conn, rebuild_calls
):
    captured = {}

    def fake_delete(conn, **kw):
        captured.update(kw)
        return _outcome(transactions=3)

    monkeypatch.setattr(jobs.pipeline, "delete_account", fake_delete)
    user_id = uuid.uuid4()

    result = jobs.run_delete_account(user_id=user_id, account_label="m")

    assert result == {
        "status": "ok",
        "rows_saved": 0,
        "accounts_deleted": 1,
        "transactions_deleted": 3,
    }
    assert captured == {"user_id": user_id, "account_label": "m"}
    assert fake_conn.closed is True
    assert rebuild_calls == []  # nothing is left to rebuild


def test_run_delete_account_propagates_an_unknown_account(monkeypatch, fake_conn):
    def boom(conn, **kw):
        raise AccountNotFound("no account 'm' for this user")

    monkeypatch.setattr(jobs.pipeline, "delete_account", boom)

    with pytest.raises(AccountNotFound):
        jobs.run_delete_account(user_id=uuid.uuid4(), account_label="m")

    assert fake_conn.closed is True


def test_run_delete_account_logs_what_it_deletes_and_the_outcome(monkeypatch, fake_conn, caplog):
    monkeypatch.setattr(jobs.pipeline, "delete_account", lambda conn, **kw: _outcome())

    with caplog.at_level(logging.INFO, logger="portfolio.jobs"):
        jobs.run_delete_account(user_id=uuid.uuid4(), account_label="m")

    assert "delete-account: deleting account 'm' and everything recorded in it" in caplog.text
    assert (
        "delete-account: done, account 'm' deleted with 3 transaction(s), "
        "2 position snapshot(s) and 40 return snapshot(s)."
    ) in caplog.text
