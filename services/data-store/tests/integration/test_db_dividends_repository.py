import os
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from data_store.db.connection import apply_schema
from data_store.dividends.repository import get_earliest_dividend_dates, save_dividends

pytestmark = pytest.mark.integration

SCHEMA_PATH = Path(__file__).parents[2] / "data_store" / "db" / "schema.sql"


def _connect():
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    apply_schema(conn, SCHEMA_PATH)
    return conn


def make_df(rows):
    return pd.DataFrame(rows, columns=["ticker", "ex_date", "amount"])


@pytest.fixture(autouse=True)
def clean_table():
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM dividend_events WHERE ticker = %s", ("TEST",))
    conn.commit()
    conn.close()
    yield
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM dividend_events WHERE ticker = %s", ("TEST",))
    conn.commit()
    conn.close()


def test_save_dividends_inserts_new_rows():
    conn = _connect()
    df = make_df([["TEST", date(2026, 1, 1), 1.35]])

    count = save_dividends(conn, "yfinance", "equity", df)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT amount, asset_type FROM dividend_events "
            "WHERE ticker = %s AND ex_date = %s AND source = %s",
            ("TEST", date(2026, 1, 1), "yfinance"),
        )
        amount, asset_type = cur.fetchone()

    assert count == 1
    assert float(amount) == 1.35
    assert asset_type == "equity"
    conn.close()


def test_save_dividends_upserts_on_conflict_with_corrected_amount():
    # Proves the "late-corrected special dividend" case from docs/schema.md's Loading
    # strategy note: a rerun with a restated amount overwrites in place, not duplicates.
    conn = _connect()
    original = make_df([["TEST", date(2026, 1, 1), 1.35]])
    corrected = make_df([["TEST", date(2026, 1, 1), 1.42]])

    save_dividends(conn, "yfinance", "equity", original)
    save_dividends(conn, "yfinance", "equity", corrected)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT amount FROM dividend_events WHERE ticker = %s AND ex_date = %s AND source = %s",
            ("TEST", date(2026, 1, 1), "yfinance"),
        )
        (amount,) = cur.fetchone()
        cur.execute(
            "SELECT COUNT(*) FROM dividend_events WHERE ticker = %s AND ex_date = %s",
            ("TEST", date(2026, 1, 1)),
        )
        (total,) = cur.fetchone()

    assert float(amount) == 1.42
    assert total == 1  # overwritten in place, not duplicated
    conn.close()


def test_save_dividends_keeps_distinct_sources_independent():
    conn = _connect()
    yfinance_df = make_df([["TEST", date(2026, 1, 1), 1.35]])
    other_df = make_df([["TEST", date(2026, 1, 1), 1.50]])

    save_dividends(conn, "yfinance", "equity", yfinance_df)
    save_dividends(conn, "other_source", "equity", other_df)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM dividend_events WHERE ticker = %s AND ex_date = %s",
            ("TEST", date(2026, 1, 1)),
        )
        (total,) = cur.fetchone()

    assert total == 2
    conn.close()


def test_save_dividends_stores_non_equity_asset_types():
    conn = _connect()
    df = make_df([["TEST", date(2026, 1, 1), 0.10]])

    save_dividends(conn, "yfinance", "etf", df)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT asset_type FROM dividend_events "
            "WHERE ticker = %s AND ex_date = %s AND source = %s",
            ("TEST", date(2026, 1, 1), "yfinance"),
        )
        (asset_type,) = cur.fetchone()

    assert asset_type == "etf"
    conn.close()


def test_get_earliest_dividend_dates_returns_min_date_per_ticker_and_source():
    conn = _connect()
    save_dividends(
        conn,
        "yfinance",
        "equity",
        make_df(
            [
                ["TEST", date(2020, 1, 1), 1.0],
                ["TEST", date(2021, 1, 1), 1.1],
            ]
        ),
    )
    save_dividends(conn, "other_source", "equity", make_df([["TEST", date(2015, 1, 1), 0.9]]))

    yfinance_result = get_earliest_dividend_dates(conn, "yfinance", ["TEST", "NEVER_INSERTED"])
    other_result = get_earliest_dividend_dates(conn, "other_source", ["TEST"])

    assert yfinance_result == {"TEST": date(2020, 1, 1)}
    assert "NEVER_INSERTED" not in yfinance_result
    assert other_result == {"TEST": date(2015, 1, 1)}
    conn.close()
