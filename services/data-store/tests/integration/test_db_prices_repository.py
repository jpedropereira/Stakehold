import os
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from data_store.db.connection import apply_schema
from data_store.prices.repository import save_prices

pytestmark = pytest.mark.integration

SCHEMA_PATH = Path(__file__).parents[2] / "data_store" / "db" / "schema.sql"


def _connect():
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    apply_schema(conn, SCHEMA_PATH)
    return conn


def make_df(rows):
    return pd.DataFrame(
        rows,
        columns=["ticker", "price_date", "open", "high", "low", "close", "adj_close", "volume"],
    )


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


def test_save_prices_inserts_new_rows():
    conn = _connect()
    df = make_df([["TEST", date(2026, 1, 1), 10.0, 11.0, 9.0, 10.5, 10.5, 1000]])

    count = save_prices(conn, "yfinance", df)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT close FROM daily_ohlcv WHERE ticker = %s AND price_date = %s AND source = %s",
            ("TEST", date(2026, 1, 1), "yfinance"),
        )
        (close,) = cur.fetchone()

    assert count == 1
    assert float(close) == 10.5
    conn.close()


def test_save_prices_upserts_on_rerun_with_restated_values():
    conn = _connect()
    original = make_df([["TEST", date(2026, 1, 1), 10.0, 11.0, 9.0, 10.5, 10.5, 1000]])
    restated = make_df([["TEST", date(2026, 1, 1), 10.0, 11.0, 9.0, 12.0, 12.0, 5000]])

    save_prices(conn, "yfinance", original)
    save_prices(conn, "yfinance", restated)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT close, volume FROM daily_ohlcv "
            "WHERE ticker = %s AND price_date = %s AND source = %s",
            ("TEST", date(2026, 1, 1), "yfinance"),
        )
        close, volume = cur.fetchone()
        cur.execute(
            "SELECT COUNT(*) FROM daily_ohlcv WHERE ticker = %s AND price_date = %s",
            ("TEST", date(2026, 1, 1)),
        )
        (total,) = cur.fetchone()

    assert float(close) == 12.0
    assert volume == 5000
    assert total == 1  # overwritten in place, not duplicated
    conn.close()


def test_save_prices_keeps_distinct_sources_independent():
    conn = _connect()
    yfinance_df = make_df([["TEST", date(2026, 1, 1), 10.0, 11.0, 9.0, 10.5, 10.5, 1000]])
    other_df = make_df([["TEST", date(2026, 1, 1), 20.0, 21.0, 19.0, 20.5, 20.5, 2000]])

    save_prices(conn, "yfinance", yfinance_df)
    save_prices(conn, "other_source", other_df)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM daily_ohlcv WHERE ticker = %s AND price_date = %s",
            ("TEST", date(2026, 1, 1)),
        )
        (total,) = cur.fetchone()

    assert total == 2
    conn.close()
