import os
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from data_store import pipeline
from data_store.config_loader import FundConfig
from data_store.db.connection import apply_schema

pytestmark = pytest.mark.integration

SCHEMA_PATH = Path(__file__).parents[2] / "data_store" / "db" / "schema.sql"

FUND_KEY = "guardrail_test_fund"


def _connect():
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    apply_schema(conn, SCHEMA_PATH)
    return conn


def _seed_holding(conn, *, holding_ticker, exchange, asset_class="Equity"):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO etf_holdings (fund_key, as_of_date, holding_ticker, asset_class, exchange)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (FUND_KEY, date(2026, 1, 1), holding_ticker, asset_class, exchange),
        )
    conn.commit()


_TEST_TICKERS = (
    "0700",
    "0700.HK",
    "HOLX",
    "-",
    "1",
    "0001.HK",
    "1.HK",
    "VOLV B",
    "VOLV-B.ST",
    "VOLV B.ST",
    "BT.A",
    "BT-A.L",
    "BT.A.L",
    "BRKB",
    "BRK-B",
)


@pytest.fixture(autouse=True)
def clean_tables():
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM etf_holdings WHERE fund_key = %s", (FUND_KEY,))
        cur.execute("DELETE FROM daily_ohlcv WHERE ticker = ANY(%s)", (list(_TEST_TICKERS),))
    conn.commit()
    conn.close()
    yield
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM etf_holdings WHERE fund_key = %s", (FUND_KEY,))
        cur.execute("DELETE FROM daily_ohlcv WHERE ticker = ANY(%s)", (list(_TEST_TICKERS),))
    conn.commit()
    conn.close()


def make_fund():
    return FundConfig(
        key=FUND_KEY,
        display_name="Guardrail Test Fund",
        provider="ishares",
        url="https://example.com/fund",
        isin=None,
        price_source="yfinance",
        enabled=True,
    )


class FakePriceSource:
    """No real network -- proves the guardrail against what gets *requested*/stored, not
    yfinance connectivity."""

    name = "yfinance"

    def __init__(self):
        self.fetch_calls = []

    def fetch(self, tickers, start_date, end_date):
        self.fetch_calls.append(sorted(tickers))
        return pd.DataFrame(
            {
                "ticker": tickers,
                "price_date": [start_date] * len(tickers),
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "adj_close": 10.5,
                "volume": 1000,
            }
        )


def test_holding_on_exchange_needing_suffix_is_never_stored_bare(monkeypatch):
    # Regression guardrail for the bug that produced ~900k stale rows: a holding on an
    # exchange that maps to a real yfinance suffix must land in daily_ohlcv only under its
    # suffixed form -- the bare raw holding_ticker must never reappear, even transiently.
    conn = _connect()
    _seed_holding(conn, holding_ticker="0700", exchange="Hong Kong Exchanges And Clearing Ltd")
    fake_source = FakePriceSource()
    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)

    pipeline.ingest_daily_ohlcv(
        conn,
        [make_fund()],
        [],
        {"Hong Kong Exchanges And Clearing Ltd": ".HK"},
        date(2026, 1, 1),
        date(2026, 1, 10),
    )

    with conn.cursor() as cur:
        cur.execute("SELECT ticker FROM daily_ohlcv WHERE ticker IN ('0700', '0700.HK')")
        tickers = {row[0] for row in cur.fetchall()}

    assert tickers == {"0700.HK"}
    conn.close()


def test_holding_on_unmapped_exchange_is_never_stored_at_all(monkeypatch):
    # Regression guardrail: an exchange absent from yfinance_exchange_suffixes.yaml (like the
    # real "NO MARKET (E.G. UNLISTED)" case that produced stale HOLX/"-" rows) must never
    # result in a daily_ohlcv row for that ticker -- not bare, not under any form.
    conn = _connect()
    _seed_holding(conn, holding_ticker="HOLX", exchange="NO MARKET (E.G. UNLISTED)")
    fake_source = FakePriceSource()
    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)

    pipeline.ingest_daily_ohlcv(
        conn, [make_fund()], [], {"NASDAQ": None}, date(2026, 1, 1), date(2026, 1, 10)
    )

    with conn.cursor() as cur:
        cur.execute("SELECT ticker FROM daily_ohlcv WHERE ticker = 'HOLX'")
        rows = cur.fetchall()

    assert rows == []
    assert fake_source.fetch_calls == []
    conn.close()


def test_hong_kong_holding_is_never_stored_under_its_unpadded_form(monkeypatch):
    # Regression guardrail for the ~426-row cleanup this normalization fix required: a
    # short-digit Hong Kong ticker must land in daily_ohlcv only under its zero-padded form.
    conn = _connect()
    _seed_holding(conn, holding_ticker="1", exchange="Hong Kong Exchanges And Clearing Ltd")
    fake_source = FakePriceSource()
    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)

    pipeline.ingest_daily_ohlcv(
        conn,
        [make_fund()],
        [],
        {"Hong Kong Exchanges And Clearing Ltd": ".HK"},
        date(2026, 1, 1),
        date(2026, 1, 10),
    )

    with conn.cursor() as cur:
        cur.execute("SELECT ticker FROM daily_ohlcv WHERE ticker IN ('1', '1.HK', '0001.HK')")
        tickers = {row[0] for row in cur.fetchall()}

    assert tickers == {"0001.HK"}
    conn.close()


def test_nordic_class_share_holding_is_never_stored_with_a_raw_space(monkeypatch):
    conn = _connect()
    _seed_holding(conn, holding_ticker="VOLV B", exchange="Nasdaq Omx Nordic")
    fake_source = FakePriceSource()
    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)

    pipeline.ingest_daily_ohlcv(
        conn, [make_fund()], [], {"Nasdaq Omx Nordic": ".ST"}, date(2026, 1, 1), date(2026, 1, 10)
    )

    with conn.cursor() as cur:
        cur.execute(
            "SELECT ticker FROM daily_ohlcv WHERE ticker IN ('VOLV B', 'VOLV B.ST', 'VOLV-B.ST')"
        )
        tickers = {row[0] for row in cur.fetchall()}

    assert tickers == {"VOLV-B.ST"}
    conn.close()


def test_london_mid_string_dot_holding_is_never_stored_with_a_raw_dot(monkeypatch):
    # "BT.A" specifically -- the earlier trailing-dot-only rule missed this case (a mid-string
    # class-share dot, not a trailing full-stop) until it was broadened.
    conn = _connect()
    _seed_holding(conn, holding_ticker="BT.A", exchange="London Stock Exchange")
    fake_source = FakePriceSource()
    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)

    pipeline.ingest_daily_ohlcv(
        conn,
        [make_fund()],
        [],
        {"London Stock Exchange": ".L"},
        date(2026, 1, 1),
        date(2026, 1, 10),
    )

    with conn.cursor() as cur:
        cur.execute("SELECT ticker FROM daily_ohlcv WHERE ticker IN ('BT.A.L', 'BT-A.L')")
        tickers = {row[0] for row in cur.fetchall()}

    assert tickers == {"BT-A.L"}
    conn.close()


def test_ticker_override_is_never_stored_under_its_raw_mistagged_form(monkeypatch):
    # Regression guardrail for the override mechanism itself: BRKB's exchange resolves fine on
    # its own (NYSE, no suffix), so nothing about exchange resolution would ever catch this --
    # only the override does.
    conn = _connect()
    _seed_holding(conn, holding_ticker="BRKB", exchange="New York Stock Exchange Inc.")
    fake_source = FakePriceSource()
    monkeypatch.setitem(pipeline.PRICE_SOURCES, "yfinance", lambda: fake_source)

    pipeline.ingest_daily_ohlcv(
        conn,
        [make_fund()],
        [],
        {"New York Stock Exchange Inc.": None},
        date(2026, 1, 1),
        date(2026, 1, 10),
        ticker_overrides={"BRKB": "BRK-B"},
    )

    with conn.cursor() as cur:
        cur.execute("SELECT ticker FROM daily_ohlcv WHERE ticker IN ('BRKB', 'BRK-B')")
        tickers = {row[0] for row in cur.fetchall()}

    assert tickers == {"BRK-B"}
    conn.close()
