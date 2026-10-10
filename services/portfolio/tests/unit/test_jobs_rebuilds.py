"""jobs.run_rebuild_positions, run_rebuild_returns and run_rebuild_returns_all: the rebuild
jobs' run results, blocked/failed payloads and log lines.

Split out of test_jobs.py to keep it under the repo's 64KB limit. Its small helpers are
repeated here rather than shared: a sibling helper module can't be imported under the
workspace's --import-mode=importlib."""

import logging
import uuid

import pytest
from portfolio import jobs
from portfolio.errors import AccountNotFound
from portfolio.pipeline import RebuildOutcome, ReturnsRebuildOutcome
from portfolio.settings import SettingsError
from portfolio.ticker_mapping.base import TickerMappings

_FAKE_DATABASE_URL = "postgresql://portfolio:pw@localhost/app"  # pragma: allowlist secret
_FAKE_ADMIN_DATABASE_URL = "postgresql://admin:pw@localhost/app"  # pragma: allowlist secret


def _fake_settings(*, datastore_api_url=None, internal_api_secret=None):
    return type(
        "S",
        (),
        {
            "database_url": _FAKE_DATABASE_URL,
            "admin_database_url": _FAKE_ADMIN_DATABASE_URL,
            "datastore_api_url": datastore_api_url,
            "internal_api_secret": internal_api_secret,
        },
    )()


class _SimpleFakeConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _patch_new_job_common(monkeypatch):
    monkeypatch.setattr(jobs, "load_settings", _fake_settings)
    fake_conn = _SimpleFakeConnection()
    monkeypatch.setattr(jobs, "get_connection", lambda url: fake_conn)
    return fake_conn


def _patch_datastore_settings(
    monkeypatch, *, url="http://datastore:8000", secret="secret"  # pragma: allowlist secret
):
    monkeypatch.setattr(
        jobs,
        "load_settings",
        lambda: _fake_settings(datastore_api_url=url, internal_api_secret=secret),
    )


# --- run_rebuild_positions ---


def test_run_rebuild_positions_reports_ok_with_the_payload(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    outcome = RebuildOutcome(
        snapshots_saved=3,
        positions_rebuilt=1,
        positions_removed=0,
        blocked=[],
        anomalies=[],
        positions_with_anomalies=0,
        failed_pairs=[],
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_positions", lambda conn, **kw: outcome)

    result = jobs.run_rebuild_positions(user_id=uuid.uuid4())

    assert result == {
        "status": "ok",
        "rows_saved": 3,
        "positions_rebuilt": 1,
        "positions_removed": 0,
        "instruments_blocked": 0,
        "positions_with_anomalies": 0,
        "pairs_failed": 0,
        "blocked_instruments": [],
        "failed_pairs": [],
    }
    assert fake_conn.closed is True


def test_run_rebuild_positions_reports_nothing_to_do_with_no_accounts(monkeypatch):
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    outcome = RebuildOutcome(
        snapshots_saved=0,
        positions_rebuilt=0,
        positions_removed=0,
        blocked=[],
        anomalies=[],
        positions_with_anomalies=0,
        failed_pairs=[],
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_positions", lambda conn, **kw: outcome)

    result = jobs.run_rebuild_positions(user_id=uuid.uuid4())

    assert result["status"] == "nothing_to_do"


def test_run_rebuild_positions_reports_ok_when_only_pruning_occurred(monkeypatch):
    """positions_removed alone must still count as work done -- a run that only pruned stale
    snapshots is not the same as a run that touched nothing (regression test: units_total used
    to omit positions_removed, so this used to report `nothing_to_do`)."""
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    outcome = RebuildOutcome(
        snapshots_saved=0,
        positions_rebuilt=0,
        positions_removed=1,
        blocked=[],
        anomalies=[],
        positions_with_anomalies=0,
        failed_pairs=[],
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_positions", lambda conn, **kw: outcome)

    result = jobs.run_rebuild_positions(user_id=uuid.uuid4())

    assert result["status"] == "ok"


def test_run_rebuild_positions_reports_partial_with_blocked_instruments(monkeypatch):
    """An unmapped instrument must flip the run to partial -- never a quiet ok."""
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    outcome = RebuildOutcome(
        snapshots_saved=1,
        positions_rebuilt=1,
        positions_removed=0,
        blocked=[("ISIN", "IE00B4L5Y983", "acme")],
        anomalies=[],
        positions_with_anomalies=0,
        failed_pairs=[],
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_positions", lambda conn, **kw: outcome)

    result = jobs.run_rebuild_positions(user_id=uuid.uuid4())

    assert result["status"] == "partial"
    assert result["instruments_blocked"] == 1
    assert result["blocked_instruments"] == [
        {"kind": "ISIN", "identifier": "IE00B4L5Y983", "broker": "acme"}
    ]


def test_run_rebuild_positions_reports_partial_with_failed_pairs(monkeypatch):
    """A driver-level failure saving one pair must flip the run to partial too, and surface the
    pair -- the same treatment blocked instruments already get, not a silent drop."""
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    outcome = RebuildOutcome(
        snapshots_saved=0,
        positions_rebuilt=0,
        positions_removed=0,
        blocked=[],
        anomalies=[],
        positions_with_anomalies=0,
        failed_pairs=[("m", "IWDA.AS", "connection reset")],
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_positions", lambda conn, **kw: outcome)

    result = jobs.run_rebuild_positions(user_id=uuid.uuid4())

    assert result["status"] == "partial"
    assert result["pairs_failed"] == 1
    assert result["failed_pairs"] == [
        {"account_label": "m", "ticker": "IWDA.AS", "error": "connection reset"}
    ]


def test_run_rebuild_positions_propagates_domain_error_and_closes_connection(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)

    def boom(conn, **kw):
        raise AccountNotFound("nope")

    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_positions", boom)

    with pytest.raises(AccountNotFound):
        jobs.run_rebuild_positions(user_id=uuid.uuid4(), account_label="nope")

    assert fake_conn.closed is True


def test_run_rebuild_positions_propagates_a_failure_loading_the_mappings(monkeypatch):
    fake_conn = _patch_new_job_common(monkeypatch)

    def boom(conn):
        raise RuntimeError("database went away")

    monkeypatch.setattr(jobs.pipeline, "load_ticker_mappings", boom)

    with pytest.raises(RuntimeError):
        jobs.run_rebuild_positions(user_id=uuid.uuid4())

    assert fake_conn.closed is True


def test_run_rebuild_positions_passes_the_account_label_through(monkeypatch):
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    captured = {}

    def fake_rebuild(conn, **kw):
        captured.update(kw)
        return RebuildOutcome(
            snapshots_saved=0,
            positions_rebuilt=0,
            positions_removed=0,
            blocked=[],
            anomalies=[],
            positions_with_anomalies=0,
            failed_pairs=[],
        )

    monkeypatch.setattr(jobs.pipeline, "rebuild_positions", fake_rebuild)
    user_id = uuid.uuid4()

    jobs.run_rebuild_positions(user_id=user_id, account_label="m")

    assert captured["user_id"] == user_id
    assert captured["account_label"] == "m"


def test_run_rebuild_positions_reports_positions_with_anomalies_from_the_outcome(monkeypatch):
    """positions_with_anomalies is passed through verbatim from RebuildOutcome, not re-derived
    from len(anomalies) -- two anomaly messages for the same position must still report 1, so
    this deliberately sets a count that disagrees with the raw message count."""
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    outcome = RebuildOutcome(
        snapshots_saved=1,
        positions_rebuilt=1,
        positions_removed=0,
        blocked=[],
        anomalies=[
            "IWDA.AS in account 1: traded in both 'EUR' and 'PLN'",
            "IWDA.AS in account 1: quantity went negative",
        ],
        positions_with_anomalies=1,
        failed_pairs=[],
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_positions", lambda conn, **kw: outcome)

    result = jobs.run_rebuild_positions(user_id=uuid.uuid4())

    assert result["status"] == "ok"
    assert result["positions_with_anomalies"] == 1


def test_run_rebuild_positions_logs_start_before_completion(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    _patch_new_job_common(monkeypatch)
    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    outcome = RebuildOutcome(
        snapshots_saved=1,
        positions_rebuilt=1,
        positions_removed=0,
        blocked=[],
        anomalies=[],
        positions_with_anomalies=0,
        failed_pairs=[],
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_positions", lambda conn, **kw: outcome)

    jobs.run_rebuild_positions(user_id=uuid.uuid4())

    messages = [r.message for r in caplog.records]
    start_index = next(i for i, m in enumerate(messages) if "rebuilding positions" in m)
    done_index = next(i for i, m in enumerate(messages) if "done" in m)
    assert start_index < done_index


# --- run_rebuild_returns ---


def _patch_returns_job_common(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "load_settings",
        lambda: _fake_settings(
            datastore_api_url="http://datastore:8000",
            internal_api_secret="secret",  # pragma: allowlist secret
        ),
    )
    monkeypatch.setattr(
        jobs.pipeline, "load_ticker_mappings", lambda conn: TickerMappings(isin={}, symbol={})
    )
    fake_conn = _SimpleFakeConnection()
    monkeypatch.setattr(jobs, "get_connection", lambda url: fake_conn)
    return fake_conn


def test_run_rebuild_returns_reports_ok_with_the_payload(monkeypatch):
    fake_conn = _patch_returns_job_common(monkeypatch)
    outcome = ReturnsRebuildOutcome(
        snapshots_saved=5, tickers_rebuilt=1, blocked=[], failed_pairs=[]
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_returns", lambda conn, **kw: outcome)

    result = jobs.run_rebuild_returns(user_id=uuid.uuid4())

    assert result == {
        "status": "ok",
        "rows_saved": 5,
        "tickers_rebuilt": 1,
        "tickers_removed": 0,
        "tickers_blocked": 0,
        "pairs_failed": 0,
        "blocked_tickers": [],
        "failed_pairs": [],
    }
    assert fake_conn.closed is True


def test_run_rebuild_returns_reports_nothing_to_do_with_no_tickers(monkeypatch):
    _patch_returns_job_common(monkeypatch)
    outcome = ReturnsRebuildOutcome(
        snapshots_saved=0, tickers_rebuilt=0, blocked=[], failed_pairs=[]
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_returns", lambda conn, **kw: outcome)

    result = jobs.run_rebuild_returns(user_id=uuid.uuid4())

    assert result["status"] == "nothing_to_do"


def test_run_rebuild_returns_reports_ok_when_only_pruning_occurred(monkeypatch, caplog):
    """A run that only removed a gone ticker's rows did work -- the same rule
    run_rebuild_positions follows for positions_removed, never `nothing_to_do`."""
    _patch_returns_job_common(monkeypatch)
    outcome = ReturnsRebuildOutcome(
        snapshots_saved=0, tickers_rebuilt=0, blocked=[], failed_pairs=[], tickers_removed=1
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_returns", lambda conn, **kw: outcome)

    with caplog.at_level("INFO", logger="portfolio.jobs"):
        result = jobs.run_rebuild_returns(user_id=uuid.uuid4())

    assert result["status"] == "ok"
    assert result["tickers_removed"] == 1
    assert "0 ticker(s), 1 removed" in caplog.text


def test_run_rebuild_returns_reports_partial_with_blocked_tickers(monkeypatch):
    """A ticker data-store doesn't track must flip the run to partial -- never a quiet ok."""
    _patch_returns_job_common(monkeypatch)
    outcome = ReturnsRebuildOutcome(
        snapshots_saved=0, tickers_rebuilt=0, blocked=[("m", "IWDA.AS")], failed_pairs=[]
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_returns", lambda conn, **kw: outcome)

    result = jobs.run_rebuild_returns(user_id=uuid.uuid4())

    assert result["status"] == "partial"
    assert result["tickers_blocked"] == 1
    assert result["blocked_tickers"] == [{"account_label": "m", "ticker": "IWDA.AS"}]


def test_run_rebuild_returns_reports_partial_with_failed_pairs(monkeypatch):
    _patch_returns_job_common(monkeypatch)
    outcome = ReturnsRebuildOutcome(
        snapshots_saved=0,
        tickers_rebuilt=0,
        blocked=[],
        failed_pairs=[("m", "IWDA.AS", "connection reset")],
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_returns", lambda conn, **kw: outcome)

    result = jobs.run_rebuild_returns(user_id=uuid.uuid4())

    assert result["status"] == "partial"
    assert result["pairs_failed"] == 1
    assert result["failed_pairs"] == [
        {"account_label": "m", "ticker": "IWDA.AS", "error": "connection reset"}
    ]


def test_run_rebuild_returns_propagates_domain_error_and_closes_connection(monkeypatch):
    fake_conn = _patch_returns_job_common(monkeypatch)

    def boom(conn, **kw):
        raise AccountNotFound("nope")

    monkeypatch.setattr(jobs.pipeline, "rebuild_returns", boom)

    with pytest.raises(AccountNotFound):
        jobs.run_rebuild_returns(user_id=uuid.uuid4(), account_label="nope")

    assert fake_conn.closed is True


def test_run_rebuild_returns_requires_datastore_api_url(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "load_settings",
        lambda: _fake_settings(datastore_api_url=None, internal_api_secret="secret"),
    )

    with pytest.raises(SettingsError):
        jobs.run_rebuild_returns(user_id=uuid.uuid4())


def test_run_rebuild_returns_requires_internal_api_secret(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "load_settings",
        lambda: _fake_settings(datastore_api_url="http://datastore:8000", internal_api_secret=None),
    )

    with pytest.raises(SettingsError):
        jobs.run_rebuild_returns(user_id=uuid.uuid4())


def test_run_rebuild_returns_builds_the_http_client_from_settings(monkeypatch):
    _patch_returns_job_common(monkeypatch)
    captured = {}

    class FakeHttpDataStoreClient:
        def __init__(self, base_url, internal_api_secret):
            captured["base_url"] = base_url
            captured["internal_api_secret"] = internal_api_secret

    monkeypatch.setattr(jobs, "HttpDataStoreClient", FakeHttpDataStoreClient)
    monkeypatch.setattr(
        jobs.pipeline,
        "rebuild_returns",
        lambda conn, **kw: ReturnsRebuildOutcome(
            snapshots_saved=0, tickers_rebuilt=0, blocked=[], failed_pairs=[]
        ),
    )

    jobs.run_rebuild_returns(user_id=uuid.uuid4())

    assert captured == {
        "base_url": "http://datastore:8000",
        "internal_api_secret": "secret",  # pragma: allowlist secret
    }


def test_run_rebuild_returns_passes_the_account_label_through(monkeypatch):
    _patch_returns_job_common(monkeypatch)
    captured = {}

    def fake_rebuild(conn, **kw):
        captured.update(kw)
        return ReturnsRebuildOutcome(
            snapshots_saved=0, tickers_rebuilt=0, blocked=[], failed_pairs=[]
        )

    monkeypatch.setattr(jobs.pipeline, "rebuild_returns", fake_rebuild)
    user_id = uuid.uuid4()

    jobs.run_rebuild_returns(user_id=user_id, account_label="m")

    assert captured["user_id"] == user_id
    assert captured["account_label"] == "m"


def test_run_rebuild_returns_logs_start_before_completion(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    _patch_returns_job_common(monkeypatch)
    outcome = ReturnsRebuildOutcome(
        snapshots_saved=1, tickers_rebuilt=1, blocked=[], failed_pairs=[]
    )
    monkeypatch.setattr(jobs.pipeline, "rebuild_returns", lambda conn, **kw: outcome)

    jobs.run_rebuild_returns(user_id=uuid.uuid4())

    messages = [r.message for r in caplog.records]
    start_index = next(i for i, m in enumerate(messages) if "rebuilding returns" in m)
    done_index = next(i for i, m in enumerate(messages) if "done" in m)
    assert start_index < done_index


# --- run_rebuild_returns_all ---


def _patch_returns_all(monkeypatch, *, user_ids, per_user):
    """`per_user` maps a user id to the result its run_rebuild_returns returns, or to an
    exception it raises."""
    fake_conn = _patch_new_job_common(monkeypatch)
    _patch_datastore_settings(monkeypatch)
    monkeypatch.setattr(jobs.pipeline, "list_user_ids", lambda conn: list(user_ids))
    calls = []

    def fake_returns(**kw):
        calls.append(kw["user_id"])
        outcome = per_user[kw["user_id"]]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(jobs, "run_rebuild_returns", fake_returns)
    return fake_conn, calls


def _returns_result(status="ok", rows_saved=10, blocked=0, pairs_failed=0):
    return {
        "status": status,
        "rows_saved": rows_saved,
        "tickers_rebuilt": 2,
        "tickers_blocked": blocked,
        "pairs_failed": pairs_failed,
        "blocked_tickers": [],
        "failed_pairs": [],
    }


def test_run_rebuild_returns_all_rebuilds_every_user_and_sums_the_counts(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    ann, bea = uuid.uuid4(), uuid.uuid4()
    fake_conn, calls = _patch_returns_all(
        monkeypatch,
        user_ids=[ann, bea],
        per_user={ann: _returns_result(rows_saved=10), bea: _returns_result(rows_saved=5)},
    )

    result = jobs.run_rebuild_returns_all()

    assert calls == [ann, bea]
    assert result == {
        "status": "ok",
        "rows_saved": 15,
        "users_rebuilt": 2,
        "users_failed": 0,
        "tickers_blocked": 0,
        "pairs_failed": 0,
    }
    assert fake_conn.closed is True
    assert "rebuilding returns for 2 user(s)" in caplog.text
    assert "done, 15 return snapshot(s) saved for 2 user(s), 0 failed" in caplog.text


def test_run_rebuild_returns_all_keeps_going_when_one_user_fails(monkeypatch, caplog):
    ann, bea = uuid.uuid4(), uuid.uuid4()
    _, calls = _patch_returns_all(
        monkeypatch,
        user_ids=[ann, bea],
        per_user={ann: RuntimeError("data-store unreachable"), bea: _returns_result()},
    )

    result = jobs.run_rebuild_returns_all()

    assert calls == [ann, bea]
    assert result["status"] == "partial"
    assert result["users_rebuilt"] == 1
    assert result["users_failed"] == 1
    assert any(r.exc_info for r in caplog.records if r.levelno >= logging.ERROR)


def test_run_rebuild_returns_all_is_partial_when_a_user_had_blocked_tickers(monkeypatch):
    ann = uuid.uuid4()
    _patch_returns_all(
        monkeypatch,
        user_ids=[ann],
        per_user={ann: _returns_result(status="partial", blocked=1)},
    )

    result = jobs.run_rebuild_returns_all()

    assert result["status"] == "partial"
    assert result["tickers_blocked"] == 1
    assert result["users_failed"] == 0


def test_run_rebuild_returns_all_with_no_users_is_nothing_to_do(monkeypatch):
    _patch_returns_all(monkeypatch, user_ids=[], per_user={})

    assert jobs.run_rebuild_returns_all()["status"] == "nothing_to_do"


def test_run_rebuild_returns_all_requires_data_store(monkeypatch):
    _patch_new_job_common(monkeypatch)

    with pytest.raises(SettingsError, match="rebuild-returns"):
        jobs.run_rebuild_returns_all()
