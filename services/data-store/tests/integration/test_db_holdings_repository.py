import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from data_store.holdings.repository import (
    get_latest_as_of_dates,
    get_latest_snapshot,
    get_latest_tickers,
    save_holdings,
)
from shared.db.connection import apply_schema

pytestmark = pytest.mark.integration

SCHEMA_PATH = Path(__file__).parents[2] / "src" / "data_store" / "db" / "schema.sql"

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


TEST_FUNDS = ("test_fund", "test_fund_b")


def _delete_test_funds():
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM etf_holdings WHERE fund_key = ANY(%s)", (list(TEST_FUNDS),))
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def clean_table():
    _delete_test_funds()
    yield
    _delete_test_funds()


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


AAPL = ["AAPL", "APPLE INC", "Tech", "Equity", 100.0, 5.0, 100.0, 10.0, 10.0, "US", "NASDAQ", "USD"]
SHEL = [
    "SHEL",
    "SHELL PLC",
    "Energy",
    "Equity",
    50.0,
    2.5,
    50.0,
    5.0,
    10.0,
    "United Kingdom",
    "London Stock Exchange",
    "GBP",
]
CASH = [
    None,
    "USD CASH",
    "Cash and/or Derivatives",
    "Cash",
    1.0,
    0.1,
    1.0,
    1.0,
    1.0,
    "US",
    "-",
    "USD",
]


def test_get_latest_snapshot_returns_every_line_of_the_newest_snapshot():
    conn = _connect()
    save_holdings(conn, "test_fund", date(2026, 1, 1), make_df([AAPL]))
    save_holdings(conn, "test_fund", date(2026, 1, 2), make_df([CASH, AAPL, SHEL]))

    as_of_date, rows = get_latest_snapshot(conn, "test_fund")

    assert as_of_date == date(2026, 1, 2)
    # Heaviest first; the cash line is kept, null ticker and all. Weights keep the column's
    # four places.
    assert rows == [
        ("AAPL", "APPLE INC", "Tech", "Equity", "US", "NASDAQ", "USD", Decimal("5.0000")),
        (
            "SHEL",
            "SHELL PLC",
            "Energy",
            "Equity",
            "United Kingdom",
            "London Stock Exchange",
            "GBP",
            Decimal("2.5000"),
        ),
        (
            None,
            "USD CASH",
            "Cash and/or Derivatives",
            "Cash",
            "US",
            "-",
            "USD",
            Decimal("0.1000"),
        ),
    ]
    conn.close()


def test_get_latest_snapshot_keeps_funds_apart():
    conn = _connect()
    save_holdings(conn, "test_fund", date(2026, 1, 1), make_df([AAPL]))
    save_holdings(conn, "test_fund_b", date(2026, 1, 5), make_df([SHEL]))

    as_of_date, rows = get_latest_snapshot(conn, "test_fund")

    assert as_of_date == date(2026, 1, 1)
    assert [row[0] for row in rows] == ["AAPL"]
    conn.close()


def test_get_latest_snapshot_is_none_for_a_fund_with_no_snapshot():
    conn = _connect()

    assert get_latest_snapshot(conn, "test_fund") is None
    conn.close()


def test_get_latest_as_of_dates_gives_each_funds_newest_snapshot():
    conn = _connect()
    save_holdings(conn, "test_fund", date(2026, 1, 1), make_df([AAPL]))
    save_holdings(conn, "test_fund", date(2026, 1, 8), make_df([AAPL]))
    save_holdings(conn, "test_fund_b", date(2026, 1, 5), make_df([SHEL]))

    latest = get_latest_as_of_dates(conn)

    # Other funds may be in the test database; only these two are this test's.
    assert {key: latest[key] for key in TEST_FUNDS} == {
        "test_fund": date(2026, 1, 8),
        "test_fund_b": date(2026, 1, 5),
    }
    conn.close()
