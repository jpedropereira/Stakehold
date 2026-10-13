import os
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from data_store.market_data.repository import (
    get_earliest_dates,
    get_latest_close_dates,
    save_raw_market_data,
)
from shared.db.connection import apply_schema

pytestmark = pytest.mark.integration

SCHEMA_PATH = Path(__file__).parents[2] / "src" / "data_store" / "db" / "schema.sql"


def _connect():
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    apply_schema(conn, SCHEMA_PATH)
    return conn


def make_df(rows):
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


@pytest.fixture(autouse=True)
def clean_table():
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM raw_market_data WHERE ticker = %s", ("TEST",))
    conn.commit()
    conn.close()
    yield
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM raw_market_data WHERE ticker = %s", ("TEST",))
    conn.commit()
    conn.close()


def test_save_raw_market_data_inserts_new_rows():
    conn = _connect()
    df = make_df([["TEST", date(2026, 1, 1), 10.0, 11.0, 9.0, 10.5, 10.5, 1000, 0.25, 0.0]])

    count = save_raw_market_data(conn, "yfinance", "equity", df)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT close, asset_type, dividends, stock_splits FROM raw_market_data "
            "WHERE ticker = %s AND price_date = %s AND source = %s",
            ("TEST", date(2026, 1, 1), "yfinance"),
        )
        close, asset_type, dividends, stock_splits = cur.fetchone()

    assert count == 1
    assert float(close) == 10.5
    assert asset_type == "equity"
    assert float(dividends) == 0.25
    assert float(stock_splits) == 0.0
    conn.close()


def test_save_raw_market_data_upserts_on_rerun_with_restated_values():
    # Covers both the "late settlement adjustment" (schema.md's daily_ohlcv rationale) and
    # "late-corrected special dividend" (its old dividend_events rationale) cases -- now a
    # single upsert since both live in one row.
    conn = _connect()
    original = make_df([["TEST", date(2026, 1, 1), 10.0, 11.0, 9.0, 10.5, 10.5, 1000, 1.35, 0.0]])
    restated = make_df([["TEST", date(2026, 1, 1), 10.0, 11.0, 9.0, 12.0, 12.0, 5000, 1.42, 4.0]])

    save_raw_market_data(conn, "yfinance", "equity", original)
    save_raw_market_data(conn, "yfinance", "equity", restated)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT close, volume, dividends, stock_splits FROM raw_market_data "
            "WHERE ticker = %s AND price_date = %s AND source = %s",
            ("TEST", date(2026, 1, 1), "yfinance"),
        )
        close, volume, dividends, stock_splits = cur.fetchone()
        cur.execute(
            "SELECT COUNT(*) FROM raw_market_data WHERE ticker = %s AND price_date = %s",
            ("TEST", date(2026, 1, 1)),
        )
        (total,) = cur.fetchone()

    assert float(close) == 12.0
    assert volume == 5000
    assert float(dividends) == 1.42
    assert float(stock_splits) == 4.0
    assert total == 1  # overwritten in place, not duplicated
    conn.close()


def test_save_raw_market_data_keeps_distinct_sources_independent():
    conn = _connect()
    yfinance_df = make_df([["TEST", date(2026, 1, 1), 10.0, 11.0, 9.0, 10.5, 10.5, 1000, 0.0, 0.0]])
    other_df = make_df([["TEST", date(2026, 1, 1), 20.0, 21.0, 19.0, 20.5, 20.5, 2000, 0.0, 0.0]])

    save_raw_market_data(conn, "yfinance", "equity", yfinance_df)
    save_raw_market_data(conn, "other_source", "equity", other_df)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM raw_market_data WHERE ticker = %s AND price_date = %s",
            ("TEST", date(2026, 1, 1)),
        )
        (total,) = cur.fetchone()

    assert total == 2
    conn.close()


def test_save_raw_market_data_stores_non_equity_asset_types():
    conn = _connect()
    df = make_df(
        [["TEST", date(2026, 1, 1), 50000.0, 51000.0, 49000.0, 50500.0, 50500.0, 1000, 0.0, 0.0]]
    )

    save_raw_market_data(conn, "yfinance", "crypto", df)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT asset_type FROM raw_market_data "
            "WHERE ticker = %s AND price_date = %s AND source = %s",
            ("TEST", date(2026, 1, 1), "yfinance"),
        )
        (asset_type,) = cur.fetchone()

    assert asset_type == "crypto"
    conn.close()


@pytest.mark.parametrize(
    "column,bad_value",
    [
        ("open", 1e14),
        ("high", 1e14),
        ("low", 1e14),
        ("close", 1e14),
        ("adj_close", 1e14),
        ("volume", 2**63),
        ("dividends", 1e14),
        ("stock_splits", 1e12),
    ],
)
def test_save_raw_market_data_drops_a_row_postgres_would_reject(column, bad_value):
    # The unit-test equivalent of this (test_repository.py) only checks the Python-side
    # comparison against _NUMERIC_BOUNDS with a fake cursor -- it can't catch the bounds being
    # wrong relative to the real column types. This drives a value right at each bound through
    # save_raw_market_data against a live NUMERIC(18,4)/(18,6)/BIGINT schema, so a mismatch
    # would surface here as an assertion failure instead of a NumericValueOutOfRange in
    # production.
    conn = _connect()
    columns = [
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
    ]
    good = ["TEST", date(2026, 1, 1), 100.0, 101.0, 99.0, 100.5, 100.5, 1000, 0.0, 0.0]
    bad = ["TEST", date(2026, 1, 2), 100.0, 101.0, 99.0, 100.5, 100.5, 1000, 0.0, 0.0]
    bad[columns.index(column)] = bad_value
    df = make_df([good, bad])

    count = save_raw_market_data(conn, "yfinance", "equity", df)

    assert count == 1
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM raw_market_data WHERE ticker = %s",
            ("TEST",),
        )
        (total,) = cur.fetchone()
    assert total == 1
    conn.close()


def test_get_earliest_dates_returns_min_date_per_ticker_and_source():
    conn = _connect()
    save_raw_market_data(
        conn,
        "yfinance",
        "equity",
        make_df(
            [
                ["TEST", date(2020, 1, 1), 10.0, 11.0, 9.0, 10.5, 10.5, 1000, 0.0, 0.0],
                ["TEST", date(2021, 1, 1), 10.0, 11.0, 9.0, 10.5, 10.5, 1000, 0.0, 0.0],
            ]
        ),
    )
    save_raw_market_data(
        conn,
        "other_source",
        "equity",
        make_df([["TEST", date(2015, 1, 1), 10.0, 11.0, 9.0, 10.5, 10.5, 1000, 0.0, 0.0]]),
    )

    yfinance_result = get_earliest_dates(conn, "yfinance", ["TEST", "NEVER_INSERTED"])
    other_result = get_earliest_dates(conn, "other_source", ["TEST"])

    assert yfinance_result == {"TEST": date(2020, 1, 1)}
    assert "NEVER_INSERTED" not in yfinance_result
    assert other_result == {"TEST": date(2015, 1, 1)}
    conn.close()


def test_get_latest_close_dates_skips_rows_without_a_close():
    # The 2026-09-03 rows stored with no close must not count as fetched, or the daily ingest's
    # catch-up would start after them and never repair them.
    conn = _connect()
    save_raw_market_data(
        conn,
        "yfinance",
        "equity",
        make_df(
            [
                ["TEST", date(2026, 9, 2), 10.0, 11.0, 9.0, 10.5, 10.5, 1000, 0.0, 0.0],
                ["TEST", date(2026, 9, 3), 10.0, 11.0, 9.0, None, None, 400, 0.0, 0.0],
            ]
        ),
    )
    save_raw_market_data(
        conn,
        "other_source",
        "equity",
        make_df([["TEST", date(2026, 9, 10), 10.0, 11.0, 9.0, 10.5, 10.5, 1000, 0.0, 0.0]]),
    )

    yfinance_result = get_latest_close_dates(conn, "yfinance", ["TEST", "NEVER_INSERTED"])
    other_result = get_latest_close_dates(conn, "other_source", ["TEST"])

    assert yfinance_result == {"TEST": date(2026, 9, 2)}
    assert other_result == {"TEST": date(2026, 9, 10)}
    conn.close()
