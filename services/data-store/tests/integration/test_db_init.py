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
                WHERE table_schema = 'public' AND table_name IN ('etf_holdings', 'daily_ohlcv')
                ORDER BY table_name
                """)
            tables = [row[0] for row in cur.fetchall()]

    assert tables == ["daily_ohlcv", "etf_holdings"]


def test_db_init_is_idempotent():
    cli.main(["db", "init"])

    exit_code = cli.main(["db", "init"])

    assert exit_code == 0
