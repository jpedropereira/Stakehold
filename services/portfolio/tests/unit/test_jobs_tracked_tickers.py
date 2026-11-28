"""The tracked-ticker jobs (TK-7: lookup, add, list and change, passed through to data-store) and
the refresh-ready-tickers sweep (TK-10).

Split out of test_jobs.py to keep it under the repo's 64KB limit. Its small helpers are
repeated here rather than shared: a sibling helper module can't be imported under the
workspace's --import-mode=importlib."""

import logging
import uuid
from datetime import datetime

import pytest
from portfolio import jobs
from portfolio.settings import SettingsError


def _fake_settings(*, datastore_api_url=None, internal_api_secret=None):
    return type(
        "S",
        (),
        {
            "database_url": "postgresql://portfolio:pw@localhost/app",  # pragma: allowlist secret
            "admin_database_url": None,
            "datastore_api_url": datastore_api_url,
            "internal_api_secret": internal_api_secret,
        },
    )()


class _SimpleFakeConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _patch_datastore_settings(
    monkeypatch, *, url="http://datastore:8000", secret="secret"  # pragma: allowlist secret
):
    monkeypatch.setattr(
        jobs,
        "load_settings",
        lambda: _fake_settings(datastore_api_url=url, internal_api_secret=secret),
    )


# --- tracked tickers: passed through to data-store (TK-7) ----------------------------------------


@pytest.fixture
def tracked_client(monkeypatch):
    """A data-store client built from settings, recording each tracked-ticker call."""
    from portfolio.datastore_client.base import TickerLookup, TrackedTicker

    _patch_datastore_settings(monkeypatch)
    row = TrackedTicker(
        ticker="ASML.AS",
        display_name="ASML Holding N.V.",
        asset_type="equity",
        currency="EUR",
        exchange="Amsterdam",
        origin="request",
        status="pending",
        enabled=True,
        attempts=0,
        last_error=None,
        requested_by="00000000-0000-0000-0000-00000000000a",
        requested_at="2026-10-10T12:00:00Z",
        ready_at=None,
    )
    calls = []

    class FakeHttpDataStoreClient:
        def __init__(self, base_url, internal_api_secret):
            pass

        def lookup_ticker(self, ticker):
            calls.append(("lookup", ticker))
            return TickerLookup("ASML.AS", "available", None, "ASML", "EUR", "Amsterdam", "equity")

        def add_tracked_ticker(self, ticker, *, requested_by, enforce_limit):
            calls.append(("add", ticker, requested_by, enforce_limit))
            return row, True

        def list_tracked_tickers(self, *, requested_by=None, statuses=None, tickers=None):
            calls.append(("list", requested_by, statuses, tickers))
            return [row]

        def update_tracked_ticker(self, ticker, *, enabled, retry):
            calls.append(("update", ticker, enabled, retry))
            return row

    monkeypatch.setattr(jobs, "HttpDataStoreClient", FakeHttpDataStoreClient)
    return calls


def test_get_ticker_lookup(tracked_client):
    result = jobs.get_ticker_lookup(ticker="asml.as")

    assert tracked_client == [("lookup", "asml.as")]
    assert result["outcome"] == "available"


def test_run_add_tracked_ticker(tracked_client):
    requester = uuid.UUID("00000000-0000-0000-0000-00000000000a")

    result = jobs.run_add_tracked_ticker(
        ticker="asml.as", requested_by=requester, enforce_limit=False
    )

    assert tracked_client == [("add", "asml.as", requester, False)]
    assert (result["ticker"], result["created"]) == ("ASML.AS", True)


def test_get_tracked_tickers(tracked_client):
    result = jobs.get_tracked_tickers(requested_by=None, statuses=["failed"], tickers=None)

    assert tracked_client == [("list", None, ["failed"], None)]
    assert [r["ticker"] for r in result] == ["ASML.AS"]


def test_run_update_tracked_ticker(tracked_client):
    result = jobs.run_update_tracked_ticker(ticker="ASML.AS", enabled=None, retry=True)

    assert tracked_client == [("update", "ASML.AS", None, True)]
    assert result["ticker"] == "ASML.AS"


def test_tracked_ticker_jobs_require_data_store_settings(monkeypatch):
    _patch_datastore_settings(monkeypatch, url=None)

    with pytest.raises(SettingsError):
        jobs.get_ticker_lookup(ticker="X")


# --- refresh-ready-tickers (TK-10) ---------------------------------------------------------------


@pytest.fixture
def ready_sweep(monkeypatch):
    """data-store reporting some ready tickers; portfolio's accounts holding some of them; and
    run_rebuild_returns recorded rather than run."""
    from datetime import timezone

    from portfolio.datastore_client.base import TrackedTicker

    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    _patch_datastore_settings(monkeypatch)

    def ready(ticker, ready_at):
        return TrackedTicker(
            ticker=ticker,
            display_name=ticker,
            asset_type="equity",
            currency="EUR",
            exchange=None,
            origin="request",
            status="ready",
            enabled=True,
            attempts=0,
            last_error=None,
            requested_by=None,
            requested_at="2026-10-01T00:00:00Z",
            ready_at=ready_at,
        )

    state = {
        "tracked": [
            ready("NEW.AS", "2026-10-10T11:55:00Z"),  # 5 minutes ago
            ready("OLD.AS", "2026-10-09T12:00:00Z"),  # yesterday
            ready("NEVER", None),
        ],
        "holding": {("NEW.AS",): [(uuid.UUID(int=1), "main"), (uuid.UUID(int=2), "isa")]},
        "rebuilt": [],
        "list_calls": [],
        "failing": set(),
    }

    class FakeHttpDataStoreClient:
        def __init__(self, base_url, internal_api_secret):
            pass

        def list_tracked_tickers(self, *, requested_by=None, statuses=None, tickers=None):
            state["list_calls"].append(statuses)
            return state["tracked"]

    def fake_holding(conn, *, tickers):
        return state["holding"].get(tuple(sorted(tickers)), [])

    def fake_rebuild(*, user_id, account_label=None, settings=None):
        if (user_id, account_label) in state["failing"]:
            raise RuntimeError("boom")
        state["rebuilt"].append((user_id, account_label))
        return {"status": "ok", "rows_saved": 3, "tickers_blocked": 0, "pairs_failed": 0}

    monkeypatch.setattr(jobs, "HttpDataStoreClient", FakeHttpDataStoreClient)
    monkeypatch.setattr(jobs, "get_connection", lambda url: _SimpleFakeConnection())
    monkeypatch.setattr(jobs, "user_connection", lambda settings, user_id: _SimpleFakeConnection())
    monkeypatch.setattr(jobs, "cross_user_connection", lambda settings: _SimpleFakeConnection())
    monkeypatch.setattr(jobs.pipeline, "list_accounts_holding", fake_holding)
    monkeypatch.setattr(jobs, "run_rebuild_returns", fake_rebuild)
    state["now"] = now
    return state


def test_refresh_ready_tickers_rebuilds_accounts_holding_a_newly_ready_ticker(ready_sweep, caplog):
    caplog.set_level(logging.INFO)

    result = jobs.run_refresh_ready_tickers(window_minutes=10, now=ready_sweep["now"])

    assert ready_sweep["list_calls"] == [["ready"]]
    assert ready_sweep["rebuilt"] == [(uuid.UUID(int=1), "main"), (uuid.UUID(int=2), "isa")]
    assert result["status"] == "ok"
    assert result["rows_saved"] == 6
    assert result["accounts_rebuilt"] == 2
    assert "NEW.AS" in caplog.text


def test_refresh_ready_tickers_with_nothing_newly_ready_is_nothing_to_do(ready_sweep, caplog):
    caplog.set_level(logging.INFO)
    ready_sweep["tracked"] = ready_sweep["tracked"][1:]

    result = jobs.run_refresh_ready_tickers(window_minutes=10, now=ready_sweep["now"])

    assert result["status"] == "nothing_to_do"
    assert ready_sweep["rebuilt"] == []
    assert "no tickers became ready" in caplog.text


def test_refresh_ready_tickers_isolates_a_failing_account(ready_sweep):
    ready_sweep["failing"] = {(uuid.UUID(int=1), "main")}

    result = jobs.run_refresh_ready_tickers(window_minutes=10, now=ready_sweep["now"])

    assert ready_sweep["rebuilt"] == [(uuid.UUID(int=2), "isa")]
    assert result["status"] == "partial"
    assert result["accounts_failed"] == 1


def test_refresh_ready_tickers_with_no_holders_says_so(ready_sweep, caplog):
    caplog.set_level(logging.INFO)
    ready_sweep["holding"] = {}

    result = jobs.run_refresh_ready_tickers(window_minutes=10, now=ready_sweep["now"])

    assert result["status"] == "nothing_to_do"
    assert "no account holds" in caplog.text
