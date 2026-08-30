"""A split restates the provider's whole history; ours has to follow.

The daily ingest only re-fetches a rolling window. When a ticker splits, the provider rewrites
every historical price and dividend into the new scale, so any stored row outside that window
keeps the old one -- leaving a single ticker's series on two scales at once, with a step change
at the window boundary that is indistinguishable from a real several-hundred-percent move.

These tests use a fake source rather than the network, so what they prove is the refresh logic
and what actually lands in Postgres, not provider connectivity.
"""

import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from data_store import pipeline
from data_store.config_loader import TickerConfig
from shared.db.connection import apply_schema

pytestmark = pytest.mark.integration

SCHEMA_PATH = Path(__file__).parents[2] / "src" / "data_store" / "db" / "schema.sql"


def _connect():
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    apply_schema(conn, SCHEMA_PATH)
    return conn


@pytest.fixture(autouse=True)
def clean_table():
    def wipe():
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM raw_market_data WHERE ticker = %s", ("TEST",))
        conn.commit()
        conn.close()

    wipe()
    yield
    wipe()


class SplittingMarketDataSource:
    """Mimics what the provider actually does around a split.

    The important behaviour, and the reason this bug exists: once a split has happened the
    provider restates *every* date it is asked for into the new scale -- not just the dates
    after the split. Verified against stored data: AAPL's 2020-08-28 close reads 124.8075
    against the 499.23 it actually traded at that day.
    """

    name = "yfinance"

    def __init__(self, *, base_close=100.0):
        self.base_close = base_close
        self.split_date = None
        self.ratio = 1.0
        self.fetch_calls = []

    def apply_split(self, split_date, ratio):
        self.split_date = split_date
        self.ratio = ratio

    def fetch(self, tickers, start_date, end_date):
        self.fetch_calls.append((tickers[0], start_date, end_date))
        dates = pd.date_range(start_date, end_date, freq="D").date
        close = self.base_close / self.ratio
        return pd.DataFrame(
            {
                "ticker": tickers[0],
                "price_date": dates,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "adj_close": close,
                "volume": 1000,
                "dividends": 0.0,
                "stock_splits": [self.ratio if d == self.split_date else 0.0 for d in dates],
            }
        )


def _tracked():
    return [
        TickerConfig(
            ticker="TEST",
            display_name="Test",
            asset_type="equity",
            price_source="yfinance",
            enabled=True,
        )
    ]


def _stored_closes(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT close FROM raw_market_data WHERE ticker = 'TEST'")
        return {row[0] for row in cur.fetchall()}


def _row_count(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM raw_market_data WHERE ticker = 'TEST'")
        return cur.fetchone()[0]


def test_a_split_restates_history_the_daily_window_does_not_cover(monkeypatch):
    conn = _connect()
    source = SplittingMarketDataSource()
    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: source)
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    # History on the pre-split scale, well outside any daily ingest window.
    pipeline.backfill_market_data(
        conn, [], _tracked(), {}, date(2024, 1, 1), date(2024, 1, 10), delay_seconds=0
    )
    assert _stored_closes(conn) == {Decimal("100.0000")}

    # The ticker splits 4-for-1. The provider now reports every date at a quarter of what it
    # used to; the daily ingest sees only a three-day window that happens to contain the split.
    source.apply_split(date(2024, 1, 12), 4.0)
    result = pipeline.ingest_market_data(
        conn, [], _tracked(), {}, date(2024, 1, 11), date(2024, 1, 13)
    )

    # The whole series is on one scale. Without the refresh the 1-10 January rows would still
    # read 100 while the new ones read 25 -- a 4x cliff at the window boundary.
    assert _stored_closes(conn) == {Decimal("25.0000")}
    assert result["splits_refreshed"] == 1
    assert result["splits_refresh_failed"] == 0
    conn.close()


def test_the_refresh_rewrites_rows_rather_than_duplicating_them(monkeypatch):
    conn = _connect()
    source = SplittingMarketDataSource()
    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: source)
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_market_data(
        conn, [], _tracked(), {}, date(2024, 1, 1), date(2024, 1, 10), delay_seconds=0
    )
    source.apply_split(date(2024, 1, 12), 4.0)
    pipeline.ingest_market_data(conn, [], _tracked(), {}, date(2024, 1, 11), date(2024, 1, 13))

    # 1-13 January inclusive, once each: the refresh goes through the same upsert as everything
    # else, so re-fetching an already-stored date updates it in place.
    assert _row_count(conn) == 13
    conn.close()


def test_an_ordinary_day_does_not_trigger_a_refresh(monkeypatch):
    conn = _connect()
    source = SplittingMarketDataSource()
    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: source)
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_market_data(
        conn, [], _tracked(), {}, date(2024, 1, 1), date(2024, 1, 10), delay_seconds=0
    )
    before = len(source.fetch_calls)

    result = pipeline.ingest_market_data(
        conn, [], _tracked(), {}, date(2024, 1, 11), date(2024, 1, 13)
    )

    # The common path stays one fetch: a whole-history request on every ordinary day would be
    # a large, pointless load on a rate-limited provider.
    assert len(source.fetch_calls) == before + 1
    assert result["splits_refreshed"] == 0
    conn.close()


def test_force_refresh_repairs_a_history_left_on_two_scales(monkeypatch):
    """The recovery path once the split day has aged out of the ingest window.

    At that point nothing re-triggers the automatic refresh, so this is the only way back to a
    consistent series -- plain backfill would skip the ticker outright for already having rows.
    """
    conn = _connect()
    source = SplittingMarketDataSource()
    monkeypatch.setitem(pipeline.MARKET_DATA_SOURCES, "yfinance", lambda: source)
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    pipeline.backfill_market_data(
        conn, [], _tracked(), {}, date(2024, 1, 1), date(2024, 1, 10), delay_seconds=0
    )
    # Corrupt the series the way a missed split would: newer rows on the post-split scale,
    # older ones left behind on the pre-split scale.
    source.apply_split(date(2024, 1, 12), 4.0)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO raw_market_data "
            "(ticker, price_date, source, asset_type, close, stock_splits) "
            "VALUES ('TEST', '2024-01-12', 'yfinance', 'equity', 25.0, 4.0)"
        )
    conn.commit()
    assert _stored_closes(conn) == {Decimal("100.0000"), Decimal("25.0000")}

    # A plain backfill cannot fix this -- the ticker already has rows, so it is skipped.
    pipeline.backfill_market_data(
        conn, [], _tracked(), {}, None, date(2024, 1, 13), delay_seconds=0
    )
    assert _stored_closes(conn) == {Decimal("100.0000"), Decimal("25.0000")}

    pipeline.backfill_market_data(
        conn,
        [],
        _tracked(),
        {},
        date(2024, 1, 1),
        date(2024, 1, 13),
        delay_seconds=0,
        force_refresh=True,
    )

    assert _stored_closes(conn) == {Decimal("25.0000")}
    conn.close()
