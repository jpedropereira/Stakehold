import os

import psycopg
import pytest
from data_store import cli

pytestmark = pytest.mark.integration


def _connect():
    return psycopg.connect(os.environ["DATABASE_URL"])


def test_db_init_creates_expected_tables():
    exit_code = cli.main(["db", "init"])

    assert exit_code == 0

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name IN ('etf_holdings', 'raw_market_data', 'market_returns')
                ORDER BY table_name
                """)
            tables = [row[0] for row in cur.fetchall()]

    assert tables == ["etf_holdings", "market_returns", "raw_market_data"]


def test_db_init_creates_risk_free_rates_with_its_key():
    assert cli.main(["db", "init"]) == 0

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name, data_type, numeric_precision, numeric_scale, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'risk_free_rates'
                ORDER BY ordinal_position
                """)
            columns = cur.fetchall()
            cur.execute("""
                SELECT a.attname
                FROM pg_index i
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                WHERE i.indrelid = 'risk_free_rates'::regclass AND i.indisprimary
                ORDER BY array_position(i.indkey, a.attnum)
                """)
            key = [row[0] for row in cur.fetchall()]

    assert columns == [
        ("series", "character varying", None, None, "NO"),
        ("rate_date", "date", None, None, "NO"),
        ("source", "character varying", None, None, "NO"),
        ("rate", "numeric", 8, 4, "NO"),
        ("ingested_at", "timestamp with time zone", None, None, "NO"),
    ]
    assert key == ["series", "rate_date", "source"]


def test_db_init_is_idempotent():
    cli.main(["db", "init"])

    exit_code = cli.main(["db", "init"])

    assert exit_code == 0


def test_holdings_weight_percent_accepts_a_fully_concentrated_position():
    # NUMERIC(6,4) capped at 99.9999, so a >=100% line raised a numeric overflow -- and since
    # holdings load via one COPY, that lost the entire fund snapshot, not just the bad row.
    cli.main(["db", "init"])

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO etf_holdings (fund_key, as_of_date, holding_ticker, weight_percent)
                VALUES ('weight_overflow_probe', DATE '2026-01-01', 'ONLY', 100.0)
                """)
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("DELETE FROM etf_holdings WHERE fund_key = 'weight_overflow_probe'")
        conn.commit()


_PRICE_COLUMNS = [
    ("market_returns", "split_adjusted_close"),
    ("raw_market_data", "adj_close"),
    ("raw_market_data", "close"),
    ("raw_market_data", "dividends"),
    ("raw_market_data", "high"),
    ("raw_market_data", "low"),
    ("raw_market_data", "open"),
]


def _price_column_types(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name, column_name, numeric_precision, numeric_scale
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND (table_name, column_name) IN (
                  ('market_returns', 'split_adjusted_close'), ('raw_market_data', 'open'),
                  ('raw_market_data', 'high'), ('raw_market_data', 'low'),
                  ('raw_market_data', 'close'), ('raw_market_data', 'adj_close'),
                  ('raw_market_data', 'dividends'))
            ORDER BY table_name, column_name
            """)
        return cur.fetchall()


def test_db_init_stores_prices_to_six_decimals():
    # Rescaling a pence price to pounds (IQ-1) needs two more decimals to stay exact.
    assert cli.main(["db", "init"]) == 0

    with _connect() as conn:
        types = _price_column_types(conn)

    assert types == [(table, column, 20, 6) for table, column in _PRICE_COLUMNS]


def test_db_init_widens_price_columns_created_at_four_decimals_and_keeps_their_values():
    cli.main(["db", "init"])
    with _connect() as conn:
        with conn.cursor() as cur:
            # A database created before IQ-1, with a row in it.
            for table, column in _PRICE_COLUMNS:
                cur.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE NUMERIC(18,4)")
            cur.execute("""
                INSERT INTO raw_market_data
                    (ticker, price_date, source, asset_type, open, high, low, close, adj_close)
                VALUES ('WIDEN_PROBE', DATE '2026-01-01', 'yfinance', 'equity',
                        1234.5678, 1234.5678, 1234.5678, 1234.5678, 1234.5678)
                """)
        conn.commit()

    try:
        assert cli.main(["db", "init"]) == 0
        assert cli.main(["db", "init"]) == 0  # and a rerun leaves the widened columns alone

        with _connect() as conn:
            types = _price_column_types(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT close FROM raw_market_data WHERE ticker = 'WIDEN_PROBE'")
                (close,) = cur.fetchone()
    finally:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM raw_market_data WHERE ticker = 'WIDEN_PROBE'")
            conn.commit()

    assert types == [(table, column, 20, 6) for table, column in _PRICE_COLUMNS]
    assert str(close) == "1234.567800"
