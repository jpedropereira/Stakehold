import os
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from data_store.fx.repository import (
    get_earliest_fx_dates,
    get_latest_fx_dates,
    save_fx_rates,
)
from shared.db.connection import apply_schema

pytestmark = pytest.mark.integration

SCHEMA_PATH = Path(__file__).parents[2] / "src" / "data_store" / "db" / "schema.sql"


def _connect():
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    apply_schema(conn, SCHEMA_PATH)
    return conn


def make_df(rows):
    return pd.DataFrame(rows, columns=["base_currency", "quote_currency", "rate_date", "rate"])


@pytest.fixture(autouse=True)
def clean_table():
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM fx_rates WHERE base_currency = %s", ("TST",))
    conn.commit()
    conn.close()
    yield
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM fx_rates WHERE base_currency = %s", ("TST",))
    conn.commit()
    conn.close()


def test_save_fx_rates_inserts_new_rows():
    conn = _connect()
    df = make_df([["TST", "USD", date(2026, 1, 1), 1.08]])

    count = save_fx_rates(conn, "yfinance", df)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT rate FROM fx_rates "
            "WHERE base_currency = %s AND quote_currency = %s AND rate_date = %s "
            "AND source = %s",
            ("TST", "USD", date(2026, 1, 1), "yfinance"),
        )
        (rate,) = cur.fetchone()

    assert count == 1
    assert float(rate) == 1.08
    conn.close()


def test_save_fx_rates_upserts_on_rerun_with_restated_values():
    conn = _connect()
    original = make_df([["TST", "USD", date(2026, 1, 1), 1.08]])
    restated = make_df([["TST", "USD", date(2026, 1, 1), 1.09]])

    save_fx_rates(conn, "yfinance", original)
    save_fx_rates(conn, "yfinance", restated)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT rate FROM fx_rates "
            "WHERE base_currency = %s AND quote_currency = %s AND rate_date = %s "
            "AND source = %s",
            ("TST", "USD", date(2026, 1, 1), "yfinance"),
        )
        (rate,) = cur.fetchone()
        cur.execute(
            "SELECT COUNT(*) FROM fx_rates "
            "WHERE base_currency = %s AND quote_currency = %s AND rate_date = %s",
            ("TST", "USD", date(2026, 1, 1)),
        )
        (total,) = cur.fetchone()

    assert float(rate) == 1.09
    assert total == 1  # overwritten in place, not duplicated
    conn.close()


def test_save_fx_rates_keeps_distinct_sources_independent():
    conn = _connect()
    yfinance_df = make_df([["TST", "USD", date(2026, 1, 1), 1.08]])
    other_df = make_df([["TST", "USD", date(2026, 1, 1), 1.20]])

    save_fx_rates(conn, "yfinance", yfinance_df)
    save_fx_rates(conn, "other_source", other_df)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM fx_rates "
            "WHERE base_currency = %s AND quote_currency = %s AND rate_date = %s",
            ("TST", "USD", date(2026, 1, 1)),
        )
        (total,) = cur.fetchone()

    assert total == 2
    conn.close()


def test_save_fx_rates_drops_a_row_postgres_would_reject():
    # The unit-test equivalent (test_repository.py) only checks the Python-side comparison
    # against _RATE_BOUND with a fake cursor -- this drives a value right at the bound through
    # save_fx_rates against a live NUMERIC(18,8) schema, so a mismatch would surface here as an
    # assertion failure instead of a NumericValueOutOfRange in production.
    conn = _connect()
    good = ["TST", "USD", date(2026, 1, 1), 1.08]
    bad = ["TST", "USD", date(2026, 1, 2), 1e11]
    df = make_df([good, bad])

    count = save_fx_rates(conn, "yfinance", df)

    assert count == 1
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM fx_rates WHERE base_currency = %s", ("TST",))
        (total,) = cur.fetchone()
    assert total == 1
    conn.close()


def test_get_earliest_fx_dates_returns_min_date_per_pair_and_source():
    conn = _connect()
    save_fx_rates(
        conn,
        "yfinance",
        make_df(
            [
                ["TST", "USD", date(2020, 1, 1), 1.08],
                ["TST", "USD", date(2021, 1, 1), 1.10],
            ]
        ),
    )
    save_fx_rates(
        conn,
        "other_source",
        make_df([["TST", "USD", date(2015, 1, 1), 1.05]]),
    )

    yfinance_result = get_earliest_fx_dates(conn, "yfinance", [("TST", "USD"), ("TST", "GBP")])
    other_result = get_earliest_fx_dates(conn, "other_source", [("TST", "USD")])

    assert yfinance_result == {("TST", "USD"): date(2020, 1, 1)}
    assert ("TST", "GBP") not in yfinance_result
    assert other_result == {("TST", "USD"): date(2015, 1, 1)}
    conn.close()


def test_get_latest_fx_dates_returns_max_date_per_pair_and_source():
    conn = _connect()
    save_fx_rates(
        conn,
        "yfinance",
        make_df(
            [
                ["TST", "USD", date(2026, 9, 2), 1.08],
                ["TST", "USD", date(2026, 9, 3), 1.10],
            ]
        ),
    )
    save_fx_rates(conn, "other_source", make_df([["TST", "USD", date(2026, 9, 20), 1.05]]))

    yfinance_result = get_latest_fx_dates(conn, "yfinance", [("TST", "USD"), ("TST", "GBP")])
    other_result = get_latest_fx_dates(conn, "other_source", [("TST", "USD")])

    assert yfinance_result == {("TST", "USD"): date(2026, 9, 3)}
    assert other_result == {("TST", "USD"): date(2026, 9, 20)}
    conn.close()
