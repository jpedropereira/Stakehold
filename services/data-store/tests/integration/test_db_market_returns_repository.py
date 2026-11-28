import os
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from data_store.market_data.repository import save_raw_market_data
from data_store.returns.repository import (
    get_sources,
    get_tickers,
    iter_price_series,
    save_market_returns,
)
from shared.db.connection import apply_schema

pytestmark = pytest.mark.integration

SCHEMA_PATH = Path(__file__).parents[2] / "src" / "data_store" / "db" / "schema.sql"


def _connect():
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    apply_schema(conn, SCHEMA_PATH)
    return conn


def make_price_row(ticker, price_date, close, dividends=0.0, stock_splits=0.0):
    return [ticker, price_date, close, close, close, close, close, 1000, dividends, stock_splits]


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


def make_returns_df(rows):
    return pd.DataFrame(
        rows,
        columns=[
            "ticker",
            "price_date",
            "split_adjusted_close",
            "future_split_factor",
            "price_return",
            "dividend_return",
            "simple_return",
            "log_return",
        ],
    )


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


def test_get_sources_returns_distinct_sources_present_in_raw_market_data():
    conn = _connect()
    save_raw_market_data(
        conn, "yfinance", "equity", make_prices_df([make_price_row("TEST", date(2026, 1, 1), 10.0)])
    )

    sources = get_sources(conn)

    assert "yfinance" in sources
    conn.close()


def test_get_tickers_returns_distinct_tickers_for_a_source():
    conn = _connect()
    save_raw_market_data(
        conn,
        "yfinance",
        "equity",
        make_prices_df(
            [
                make_price_row("TEST_A", date(2026, 1, 1), 10.0),
                make_price_row("TEST_B", date(2026, 1, 1), 20.0),
            ]
        ),
    )

    tickers = get_tickers(conn, "yfinance")

    assert "TEST_A" in tickers
    assert "TEST_B" in tickers
    conn.close()


def test_iter_price_series_returns_a_ticker_full_ordered_history():
    conn = _connect()
    save_raw_market_data(
        conn,
        "yfinance",
        "equity",
        make_prices_df(
            [
                make_price_row("TEST", date(2026, 1, 2), 11.0),
                make_price_row("TEST", date(2026, 1, 1), 10.0),
            ]
        ),
    )

    batches = list(iter_price_series(conn, "yfinance", ["TEST"], batch_size=10))

    assert len(batches) == 1
    dates = list(batches[0]["price_date"])
    assert dates == [date(2026, 1, 1), date(2026, 1, 2)]
    conn.close()


def test_iter_price_series_splits_many_tickers_into_batches():
    conn = _connect()
    save_raw_market_data(
        conn,
        "yfinance",
        "equity",
        make_prices_df(
            [
                make_price_row("TEST_A", date(2026, 1, 1), 10.0),
                make_price_row("TEST_B", date(2026, 1, 1), 20.0),
                make_price_row("TEST_C", date(2026, 1, 1), 30.0),
            ]
        ),
    )

    batches = list(
        iter_price_series(conn, "yfinance", ["TEST_A", "TEST_B", "TEST_C"], batch_size=2)
    )

    assert len(batches) == 2
    assert sum(len(b) for b in batches) == 3
    conn.close()


def test_save_market_returns_inserts_new_rows():
    conn = _connect()
    df = make_returns_df([["TEST", date(2026, 1, 1), 10.0, 1.0, 0.01, 0.0, 0.01, 0.00995]])

    count = save_market_returns(conn, "yfinance", df)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT split_adjusted_close, price_return "
            "FROM market_returns WHERE ticker = %s AND price_date = %s AND source = %s",
            ("TEST", date(2026, 1, 1), "yfinance"),
        )
        split_adjusted_close, price_return = cur.fetchone()

    assert count == 1
    assert float(split_adjusted_close) == 10.0
    assert float(price_return) == pytest.approx(0.01)
    conn.close()


def test_save_market_returns_upserts_on_rerun():
    conn = _connect()
    original = make_returns_df([["TEST", date(2026, 1, 1), 10.0, 1.0, 0.01, 0.0, 0.01, 0.00995]])
    recomputed = make_returns_df([["TEST", date(2026, 1, 1), 12.0, 1.0, 0.05, 0.0, 0.05, 0.0488]])

    save_market_returns(conn, "yfinance", original)
    save_market_returns(conn, "yfinance", recomputed)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT split_adjusted_close FROM market_returns "
            "WHERE ticker = %s AND price_date = %s AND source = %s",
            ("TEST", date(2026, 1, 1), "yfinance"),
        )
        (split_adjusted_close,) = cur.fetchone()
        cur.execute(
            "SELECT COUNT(*) FROM market_returns WHERE ticker = %s AND price_date = %s",
            ("TEST", date(2026, 1, 1)),
        )
        (total,) = cur.fetchone()

    assert float(split_adjusted_close) == 12.0
    assert total == 1  # overwritten in place, not duplicated
    conn.close()


def test_save_market_returns_writes_null_for_a_series_anchor_row():
    conn = _connect()
    df = make_returns_df([["TEST", date(2026, 1, 1), 10.0, 1.0, None, None, None, None]])

    save_market_returns(conn, "yfinance", df)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT price_return, dividend_return, simple_return, log_return "
            "FROM market_returns WHERE ticker = %s AND price_date = %s AND source = %s",
            ("TEST", date(2026, 1, 1), "yfinance"),
        )
        row = cur.fetchone()

    assert row == (None, None, None, None)
    conn.close()


@pytest.mark.parametrize(
    "column,bad_value",
    [
        ("split_adjusted_close", 1e14),
        ("future_split_factor", 1e15),
        ("price_return", 1e10),
        ("dividend_return", 1e10),
        ("simple_return", 1e10),
        ("log_return", 1e10),
    ],
)
def test_save_market_returns_drops_a_row_postgres_would_reject(column, bad_value):
    # Unit tests check the bound against a fake cursor; this drives an out-of-range value
    # through save_market_returns against the real NUMERIC(18,4)/(30,15)/(18,8) schema, so a
    # mismatched bound surfaces here rather than as a NumericValueOutOfRange in production.
    conn = _connect()
    columns = [
        "ticker",
        "price_date",
        "split_adjusted_close",
        "future_split_factor",
        "price_return",
        "dividend_return",
        "simple_return",
        "log_return",
    ]
    good = ["TEST", date(2026, 1, 1), 10.0, 1.0, 0.01, 0.0, 0.01, 0.00995]
    bad = ["TEST", date(2026, 1, 2), 10.0, 1.0, 0.01, 0.0, 0.01, 0.00995]
    bad[columns.index(column)] = bad_value
    df = make_returns_df([good, bad])

    count = save_market_returns(conn, "yfinance", df)

    assert count == 1
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM market_returns WHERE ticker = %s", ("TEST",))
        (total,) = cur.fetchone()
    assert total == 1
    conn.close()


def test_save_market_returns_stores_a_cumulative_reverse_split_factor_without_underflow():
    # The regression the column widening exists for, and one a fake cursor cannot catch: only
    # real Postgres applies the column's scale. BMPS.MI's four reverse splits (0.01 x 0.05 x
    # 0.01 x 0.01) multiply out to 5e-8, which the original NUMERIC(18,6) rounded to 0 --
    # zeroing `split_adjusted_close * future_split_factor` for every row before its first split.
    conn = _connect()
    factor = 0.01 * 0.05 * 0.01 * 0.01
    df = make_returns_df([["TEST", date(2010, 1, 4), 10.0, factor, 0.01, 0.0, 0.01, 0.00995]])

    count = save_market_returns(conn, "yfinance", df)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT future_split_factor, split_adjusted_close * future_split_factor "
            "FROM market_returns WHERE ticker = %s AND price_date = %s",
            ("TEST", date(2010, 1, 4)),
        )
        stored_factor, as_traded = cur.fetchone()

    assert count == 1
    assert float(stored_factor) == pytest.approx(5e-8, rel=1e-9)
    assert float(as_traded) == pytest.approx(5e-7, rel=1e-9)
    conn.close()


def test_save_market_returns_drops_a_split_factor_that_would_round_to_zero():
    # Below the column's scale the value would store as 0, which reads as a real factor rather
    # than a missing one. Dropping the row keeps that ambiguity out of the table.
    conn = _connect()
    good = ["TEST", date(2026, 1, 1), 10.0, 1.0, 0.01, 0.0, 0.01, 0.00995]
    bad = ["TEST", date(2026, 1, 2), 10.0, 1e-16, 0.01, 0.0, 0.01, 0.00995]
    df = make_returns_df([good, bad])

    count = save_market_returns(conn, "yfinance", df)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM market_returns WHERE ticker = %s AND future_split_factor = 0",
            ("TEST",),
        )
        (zeroed,) = cur.fetchone()

    assert count == 1
    assert zeroed == 0
    conn.close()


def test_save_market_returns_keeps_six_decimals_of_a_split_adjusted_close():
    conn = _connect()
    df = make_returns_df([["TEST", date(2026, 1, 1), 0.023456, 1.0, None, None, None, None]])

    save_market_returns(conn, "yfinance", df)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT split_adjusted_close FROM market_returns "
            "WHERE ticker = %s AND price_date = %s AND source = %s",
            ("TEST", date(2026, 1, 1), "yfinance"),
        )
        (split_adjusted_close,) = cur.fetchone()

    assert str(split_adjusted_close) == "0.023456"
    conn.close()
