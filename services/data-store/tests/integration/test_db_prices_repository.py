import os
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from data_store.db.connection import apply_schema
from data_store.prices.repository import get_earliest_price_dates, save_prices

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

    count = save_prices(conn, "yfinance", "equity", df)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT close, asset_type FROM daily_ohlcv "
            "WHERE ticker = %s AND price_date = %s AND source = %s",
            ("TEST", date(2026, 1, 1), "yfinance"),
        )
        close, asset_type = cur.fetchone()

    assert count == 1
    assert float(close) == 10.5
    assert asset_type == "equity"
    conn.close()


def test_save_prices_upserts_on_rerun_with_restated_values():
    conn = _connect()
    original = make_df([["TEST", date(2026, 1, 1), 10.0, 11.0, 9.0, 10.5, 10.5, 1000]])
    restated = make_df([["TEST", date(2026, 1, 1), 10.0, 11.0, 9.0, 12.0, 12.0, 5000]])

    save_prices(conn, "yfinance", "equity", original)
    save_prices(conn, "yfinance", "equity", restated)

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

    save_prices(conn, "yfinance", "equity", yfinance_df)
    save_prices(conn, "other_source", "equity", other_df)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM daily_ohlcv WHERE ticker = %s AND price_date = %s",
            ("TEST", date(2026, 1, 1)),
        )
        (total,) = cur.fetchone()

    assert total == 2
    conn.close()


def test_save_prices_stores_non_equity_asset_types():
    conn = _connect()
    df = make_df([["TEST", date(2026, 1, 1), 50000.0, 51000.0, 49000.0, 50500.0, 50500.0, 1000]])

    save_prices(conn, "yfinance", "crypto", df)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT asset_type FROM daily_ohlcv "
            "WHERE ticker = %s AND price_date = %s AND source = %s",
            ("TEST", date(2026, 1, 1), "yfinance"),
        )
        (asset_type,) = cur.fetchone()

    assert asset_type == "crypto"
    conn.close()


def test_get_earliest_price_dates_returns_min_date_per_ticker_and_source():
    conn = _connect()
    save_prices(
        conn,
        "yfinance",
        "equity",
        make_df(
            [
                ["TEST", date(2020, 1, 1), 10.0, 11.0, 9.0, 10.5, 10.5, 1000],
                ["TEST", date(2021, 1, 1), 10.0, 11.0, 9.0, 10.5, 10.5, 1000],
            ]
        ),
    )
    save_prices(
        conn,
        "other_source",
        "equity",
        make_df([["TEST", date(2015, 1, 1), 10.0, 11.0, 9.0, 10.5, 10.5, 1000]]),
    )

    yfinance_result = get_earliest_price_dates(conn, "yfinance", ["TEST", "NEVER_INSERTED"])
    other_result = get_earliest_price_dates(conn, "other_source", ["TEST"])

    assert yfinance_result == {"TEST": date(2020, 1, 1)}
    assert "NEVER_INSERTED" not in yfinance_result
    assert other_result == {"TEST": date(2015, 1, 1)}
    conn.close()
