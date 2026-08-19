import os
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from data_store import pipeline
from data_store.config_loader import TickerConfig
from data_store.db.connection import apply_schema

pytestmark = pytest.mark.integration

SCHEMA_PATH = Path(__file__).parents[2] / "data_store" / "db" / "schema.sql"


def _connect():
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    apply_schema(conn, SCHEMA_PATH)
    return conn


@pytest.fixture(autouse=True)
def clean_table():
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM dividend_events WHERE ticker = %s", ("TEST",))
    conn.commit()
    conn.close()
    yield
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM dividend_events WHERE ticker = %s", ("TEST",))
    conn.commit()
    conn.close()


class FakeDividendSource:
    """No real network — proves backfill's own gap logic, not yfinance connectivity. Unlike
    daily OHLCV rows (one per trading day), dividend events are sparse -- this fake returns
    exactly one event, dated at the requested window's start, so the resulting stored earliest
    ex_date lines up with whatever start_date each backfill call used, the same way the OHLCV
    fake's "one row per day" makes its earliest stored price_date line up with its start_date."""

    name = "yfinance"

    def __init__(self):
        self.fetch_calls = []

    def fetch(self, tickers, start_date, end_date):
        self.fetch_calls.append((tickers[0], start_date, end_date))
        return pd.DataFrame({"ticker": [tickers[0]], "ex_date": [start_date], "amount": [1.0]})


def _make_tracked_ticker():
    return TickerConfig(
        ticker="TEST",
        display_name="Test",
        asset_type="equity",
        price_source="yfinance",
        enabled=True,
    )


def test_backfill_dividends_is_idempotent_on_rerun(monkeypatch):
    conn = _connect()
    fake_source = FakeDividendSource()
    monkeypatch.setitem(pipeline.DIVIDEND_SOURCES, "yfinance", lambda: fake_source)
    tracked = [_make_tracked_ticker()]

    first_result = pipeline.backfill_dividend_events(
        conn, [], tracked, {}, date(2024, 1, 1), date(2024, 1, 10), delay_seconds=0
    )

    assert first_result["rows_saved"] == 1
    assert fake_source.fetch_calls == [("TEST", date(2024, 1, 1), date(2024, 1, 10))]

    second_result = pipeline.backfill_dividend_events(
        conn, [], tracked, {}, date(2024, 1, 1), date(2024, 1, 10), delay_seconds=0
    )

    # The whole earliest_date..end_date range is already covered by the first run -- the
    # second run must not re-fetch it, proving re-running after a partial/full completion is
    # cheap rather than re-downloading everything.
    assert second_result["rows_saved"] == 0
    assert len(fake_source.fetch_calls) == 1
    conn.close()


def test_backfill_dividends_requests_only_remaining_gap_after_partial_coverage(monkeypatch):
    conn = _connect()
    fake_source = FakeDividendSource()
    monkeypatch.setitem(pipeline.DIVIDEND_SOURCES, "yfinance", lambda: fake_source)
    tracked = [_make_tracked_ticker()]

    # Simulate an earlier run that only covered the most recent stretch.
    pipeline.backfill_dividend_events(
        conn, [], tracked, {}, date(2024, 1, 4), date(2024, 1, 10), delay_seconds=0
    )

    pipeline.backfill_dividend_events(
        conn, [], tracked, {}, date(2024, 1, 1), date(2024, 1, 10), delay_seconds=0
    )

    # Second run should only have requested the still-missing stretch before the existing
    # earliest stored ex_date (2024-01-04, passed as the exclusive end -- fetch() stops the
    # day before it on its own), not the whole range again.
    assert fake_source.fetch_calls[1] == ("TEST", date(2024, 1, 1), date(2024, 1, 4))
    conn.close()
