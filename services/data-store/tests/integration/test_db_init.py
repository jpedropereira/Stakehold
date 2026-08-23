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
