import os
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from data_store.db.connection import apply_schema
from data_store.holdings.repository import get_latest_tickers, save_holdings

pytestmark = pytest.mark.integration

SCHEMA_PATH = Path(__file__).parents[2] / "data_store" / "db" / "schema.sql"

RAW_DB_COLUMNS = [
    "holding_ticker",
    "holding_name",
    "sector",
    "asset_class",
    "market_value",
    "weight_percent",
    "notional_value",
    "shares",
    "price",
    "location",
    "exchange",
    "currency",
]


def _connect():
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    apply_schema(conn, SCHEMA_PATH)
    return conn


def make_df(rows):
    return pd.DataFrame(rows, columns=RAW_DB_COLUMNS)


@pytest.fixture(autouse=True)
def clean_table():
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM etf_holdings WHERE fund_key = %s", ("test_fund",))
    conn.commit()
    conn.close()
    yield
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM etf_holdings WHERE fund_key = %s", ("test_fund",))
    conn.commit()
    conn.close()


def test_save_holdings_is_idempotent_on_rerun():
    conn = _connect()
    df = make_df(
        [
            [
                "AAPL",
                "APPLE INC",
                "Tech",
                "Equity",
                100.0,
                5.0,
                100.0,
                10.0,
                10.0,
                "US",
                "NASDAQ",
                "USD",
            ]
        ]
    )

    save_holdings(conn, "test_fund", date(2026, 1, 1), df)
    count = save_holdings(conn, "test_fund", date(2026, 1, 1), df)

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM etf_holdings WHERE fund_key = %s", ("test_fund",))
        total = cur.fetchone()[0]

    assert count == 1
    assert total == 1
    conn.close()


def test_save_holdings_stores_null_ticker_for_cash_lines():
    conn = _connect()
    df = make_df([[None, "CASH", "Cash", "Cash", 1.0, 0.1, 1.0, 1.0, 1.0, "US", "-", "USD"]])

    save_holdings(conn, "test_fund", date(2026, 1, 1), df)

    with conn.cursor() as cur:
        cur.execute("SELECT holding_ticker FROM etf_holdings WHERE fund_key = %s", ("test_fund",))
        (ticker,) = cur.fetchone()

    assert ticker is None
    conn.close()


def test_get_latest_tickers_returns_most_recent_snapshot_only():
    conn = _connect()
    old_df = make_df(
        [
            [
                "OLD",
                "OLD CORP",
                "Tech",
                "Equity",
                100.0,
                5.0,
                100.0,
                10.0,
                10.0,
                "US",
                "NASDAQ",
                "USD",
            ]
        ]
    )
    new_df = make_df(
        [
            [
                "AAPL",
                "APPLE INC",
                "Tech",
                "Equity",
                100.0,
                5.0,
                100.0,
                10.0,
                10.0,
                "US",
                "NASDAQ",
                "USD",
            ],
            [None, "CASH", "Cash", "Cash", 1.0, 0.1, 1.0, 1.0, 1.0, "US", "-", "USD"],
            [
                "ESU6",
                "S&P500 EMINI SEP 26",
                "Cash and/or Derivatives",
                "Futures",
                0.0,
                0.0,
                1.0,
                1.0,
                1.0,
                "US",
                "Index And Options Market",
                "USD",
            ],
        ]
    )

    save_holdings(conn, "test_fund", date(2026, 1, 1), old_df)
    save_holdings(conn, "test_fund", date(2026, 1, 2), new_df)

    tickers = get_latest_tickers(conn, "test_fund")

    assert tickers == [("AAPL", "NASDAQ")]  # not ESU6 (futures) or the null-ticker cash line
    conn.close()
