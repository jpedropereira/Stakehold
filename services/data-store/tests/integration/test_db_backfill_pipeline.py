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
        cur.execute("DELETE FROM daily_ohlcv WHERE ticker = %s", ("TEST",))
    conn.commit()
    conn.close()
    yield
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM daily_ohlcv WHERE ticker = %s", ("TEST",))
    conn.commit()
    conn.close()


class FakePriceSource:
    """No real network — proves backfill's own gap logic, not yfinance connectivity."""

    name = "yfinance"

    def __init__(self):
        self.fetch_calls = []

    def fetch(self, tickers, start_date, end_date):
        self.fetch_calls.append((tickers[0], start_date, end_date))
        dates = pd.date_range(start_date, end_date, freq="D").date
        return pd.DataFrame(
            {
                "ticker": tickers[0],
                "price_date": dates,
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "adj_close": 10.5,
                "volume": 1000,
            }
        )


def _make_tracked_ticker():
    return TickerConfig(
        ticker="TEST",
        display_name="Test",
        asset_type="equity",
        price_source="yfinance",
        enabled=True,
    )


def test_backfill_is_idempotent_on_rerun(monkeypatch):
    conn = _connect()
    fake_source = FakePriceSource()
    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)
    tracked = [_make_tracked_ticker()]

    first_count = pipeline.backfill_historical_ohlcv(
        conn, [], tracked, date(2024, 1, 1), date(2024, 1, 10), delay_seconds=0
    )

    assert first_count == 10
    assert fake_source.fetch_calls == [("TEST", date(2024, 1, 1), date(2024, 1, 10))]

    second_count = pipeline.backfill_historical_ohlcv(
        conn, [], tracked, date(2024, 1, 1), date(2024, 1, 10), delay_seconds=0
    )

    # The whole earliest_date..end_date range is already covered by the first run -- the
    # second run must not re-fetch it, proving re-running after a partial/full completion is
    # cheap rather than re-downloading everything.
    assert second_count == 0
    assert len(fake_source.fetch_calls) == 1
    conn.close()


def test_backfill_requests_only_remaining_gap_after_partial_coverage(monkeypatch):
    conn = _connect()
    fake_source = FakePriceSource()
    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)
    tracked = [_make_tracked_ticker()]

    # Simulate an earlier run that only covered the most recent week.
    pipeline.backfill_historical_ohlcv(
        conn, [], tracked, date(2024, 1, 4), date(2024, 1, 10), delay_seconds=0
    )

    pipeline.backfill_historical_ohlcv(
        conn, [], tracked, date(2024, 1, 1), date(2024, 1, 10), delay_seconds=0
    )

    # Second run should only have requested the still-missing stretch before the existing
    # earliest stored date (2024-01-04, passed as the exclusive end -- fetch() stops the day
    # before it on its own), not the whole range again.
    assert fake_source.fetch_calls[1] == ("TEST", date(2024, 1, 1), date(2024, 1, 4))
    conn.close()
