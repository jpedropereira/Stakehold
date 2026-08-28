import os
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from data_store.db.connection import apply_schema
from data_store.market_data.repository import save_raw_market_data
from data_store.pipeline import calculate_returns

pytestmark = pytest.mark.integration

SCHEMA_PATH = Path(__file__).parents[2] / "src" / "data_store" / "db" / "schema.sql"


def _connect():
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    apply_schema(conn, SCHEMA_PATH)
    return conn


def make_prices_df(rows):
    return pd.DataFrame(
        rows,
        columns=[
            "ticker",
            "price_date",
            "open",
            "high",
            "low",
            "close",
            "adj_close",
            "volume",
            "dividends",
            "stock_splits",
        ],
    )


def price_row(ticker, price_date, close, dividends=0.0, stock_splits=0.0):
    return [ticker, price_date, close, close, close, close, close, 1000, dividends, stock_splits]


@pytest.fixture(autouse=True)
def clean_tables():
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM market_returns WHERE ticker LIKE 'TEST%%'")
        cur.execute("DELETE FROM raw_market_data WHERE ticker LIKE 'TEST%%'")
    conn.commit()
    conn.close()
    yield
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM market_returns WHERE ticker LIKE 'TEST%%'")
        cur.execute("DELETE FROM raw_market_data WHERE ticker LIKE 'TEST%%'")
    conn.commit()
    conn.close()


def _returns_for(conn, ticker):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT price_date, split_adjusted_close, future_split_factor, price_return "
            "FROM market_returns WHERE ticker = %s ORDER BY price_date",
            (ticker,),
        )
        return cur.fetchall()


def test_calculate_returns_end_to_end_over_a_split(caplog):
    # A scaled-down AAPL-shaped scenario: two days before a 4:1 split, one on it. Values
    # verified by hand against the corrected (no-split-term) formula.
    conn = _connect()
    save_raw_market_data(
        conn,
        "yfinance",
        "equity",
        make_prices_df(
            [
                price_row("TEST", date(2026, 1, 1), 100.0),
                price_row("TEST", date(2026, 1, 2), 124.8075),
                price_row("TEST", date(2026, 1, 3), 129.04, stock_splits=4.0),
            ]
        ),
    )

    result = calculate_returns(conn, only_tickers={"TEST"})

    assert result["status"] == "ok"
    rows = _returns_for(conn, "TEST")
    assert len(rows) == 3

    day1, day2, day3 = rows
    assert day1[3] is None  # series anchor: no prior close

    assert float(day2[1]) == pytest.approx(124.8075)  # split_adjusted_close == raw close
    assert float(day2[2]) == pytest.approx(4.0)  # future_split_factor: one split still ahead
    assert float(day2[3]) == pytest.approx(124.8075 / 100.0 - 1)

    # The split day itself: no split-term double-adjustment (the +313% bug this whole
    # design exists to avoid) -- just close-over-close.
    assert float(day3[2]) == pytest.approx(1.0)  # no splits remain ahead of this date
    assert float(day3[3]) == pytest.approx(129.04 / 124.8075 - 1, abs=1e-4)
    conn.close()


def test_calculate_returns_is_a_full_rebuild_that_reflects_a_changed_raw_row():
    # Proves the full-rebuild claim: raw_market_data is restated (e.g. a late price
    # correction, or a split discovered after the fact) and a rerun must reflect it, not
    # keep serving the first run's numbers.
    conn = _connect()
    save_raw_market_data(
        conn,
        "yfinance",
        "equity",
        make_prices_df(
            [
                price_row("TEST", date(2026, 1, 1), 100.0),
                price_row("TEST", date(2026, 1, 2), 110.0),
            ]
        ),
    )
    calculate_returns(conn, only_tickers={"TEST"})
    first_return = dict(
        (row[0], float(row[3])) for row in _returns_for(conn, "TEST") if row[3] is not None
    )
    assert first_return[date(2026, 1, 2)] == pytest.approx(0.10)

    # Restate day 1's close, as an upsert would on a late correction.
    save_raw_market_data(
        conn,
        "yfinance",
        "equity",
        make_prices_df([price_row("TEST", date(2026, 1, 1), 100.0)]),
    )
    save_raw_market_data(
        conn,
        "yfinance",
        "equity",
        make_prices_df([price_row("TEST", date(2026, 1, 1), 105.0)]),
    )

    calculate_returns(conn, only_tickers={"TEST"})

    second_return = dict(
        (row[0], float(row[3])) for row in _returns_for(conn, "TEST") if row[3] is not None
    )
    assert second_return[date(2026, 1, 2)] == pytest.approx(110.0 / 105.0 - 1)
    conn.close()


def test_calculate_returns_with_only_tickers_ignores_other_stored_tickers():
    conn = _connect()
    save_raw_market_data(
        conn,
        "yfinance",
        "equity",
        make_prices_df(
            [
                price_row("TEST_A", date(2026, 1, 1), 10.0),
                price_row("TEST_B", date(2026, 1, 1), 20.0),
            ]
        ),
    )

    calculate_returns(conn, only_tickers={"TEST_A"})

    assert len(_returns_for(conn, "TEST_A")) == 1
    assert len(_returns_for(conn, "TEST_B")) == 0
    conn.close()
