import os
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from data_store.macro.calculator import compute_indicator_rates
from data_store.macro.repository import (
    _RAW_COLUMNS,
    get_raw_indicators,
    save_macro_indicator_rates,
    save_raw_macro_indicators,
)
from shared.db.connection import apply_schema

pytestmark = pytest.mark.integration

SCHEMA_PATH = Path(__file__).parents[2] / "src" / "data_store" / "db" / "schema.sql"


def _connect():
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    apply_schema(conn, SCHEMA_PATH)
    return conn


def make_raw_df(rows):
    return pd.DataFrame(rows, columns=_RAW_COLUMNS)


@pytest.fixture(autouse=True)
def clean_tables():
    def _delete():
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM raw_macro_indicators WHERE indicator_code = %s", ("TEST",))
            cur.execute("DELETE FROM macro_indicator_rates WHERE region = %s", ("TEST",))
        conn.commit()
        conn.close()

    _delete()
    yield
    _delete()


def test_save_raw_macro_indicators_inserts_new_rows():
    conn = _connect()
    df = make_raw_df(
        [["fred", "TEST", "TEST", date(2024, 1, 1), "inflation", "monthly", 300.1, "Index"]]
    )

    count = save_raw_macro_indicators(conn, df)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT value, unit FROM raw_macro_indicators "
            "WHERE source = %s AND indicator_code = %s AND region = %s AND observation_date = %s",
            ("fred", "TEST", "TEST", date(2024, 1, 1)),
        )
        value, unit = cur.fetchone()

    assert count == 1
    assert float(value) == 300.1
    assert unit == "Index"
    conn.close()


def test_save_raw_macro_indicators_upserts_on_rerun_with_a_restated_value():
    conn = _connect()
    original = make_raw_df(
        [["fred", "TEST", "TEST", date(2024, 1, 1), "inflation", "monthly", 300.1, "Index"]]
    )
    restated = make_raw_df(
        [["fred", "TEST", "TEST", date(2024, 1, 1), "inflation", "monthly", 300.5, "Index"]]
    )

    save_raw_macro_indicators(conn, original)
    save_raw_macro_indicators(conn, restated)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT value FROM raw_macro_indicators "
            "WHERE source = %s AND indicator_code = %s AND region = %s AND observation_date = %s",
            ("fred", "TEST", "TEST", date(2024, 1, 1)),
        )
        (value,) = cur.fetchone()
        cur.execute(
            "SELECT COUNT(*) FROM raw_macro_indicators WHERE indicator_code = %s", ("TEST",)
        )
        (total,) = cur.fetchone()

    assert float(value) == 300.5
    assert total == 1  # overwritten in place, not duplicated
    conn.close()


def test_save_raw_macro_indicators_keeps_distinct_sources_independent():
    conn = _connect()
    fred_row = make_raw_df(
        [["fred", "TEST", "TEST", date(2024, 1, 1), "inflation", "monthly", 300.1, "Index"]]
    )
    eurostat_row = make_raw_df(
        [["eurostat", "TEST", "TEST", date(2024, 1, 1), "inflation", "monthly", 2.8, "Rate"]]
    )

    save_raw_macro_indicators(conn, fred_row)
    save_raw_macro_indicators(conn, eurostat_row)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM raw_macro_indicators WHERE indicator_code = %s", ("TEST",)
        )
        (total,) = cur.fetchone()

    assert total == 2
    conn.close()


def test_get_raw_indicators_returns_every_stored_row():
    conn = _connect()
    save_raw_macro_indicators(
        conn,
        make_raw_df(
            [
                ["fred", "TEST", "TEST", date(2024, 1, 1), "inflation", "monthly", 300.1, "Index"],
                ["fred", "TEST", "TEST", date(2024, 2, 1), "inflation", "monthly", 300.5, "Index"],
            ]
        ),
    )

    result = get_raw_indicators(conn)
    test_rows = result[result["indicator_code"] == "TEST"]

    assert len(test_rows) == 2
    conn.close()


def test_calculate_macro_indicator_rates_round_trip_against_a_live_database():
    # Full pipeline: land raw rows, read them back, compute rates, save them, read the result --
    # exercises the real NUMERIC(18,8)/COPY/ON CONFLICT path the unit tests' fake cursor can't.
    conn = _connect()
    rows = [
        ["eurostat", "TEST", "TEST", date(2024, 1, 1), "inflation", "monthly", 2.8, "Rate"],
        ["eurostat", "TEST", "TEST", date(2024, 2, 1), "inflation", "monthly", 2.6, "Rate"],
    ]
    save_raw_macro_indicators(conn, make_raw_df(rows))

    raw = get_raw_indicators(conn)
    rates = compute_indicator_rates(raw[raw["indicator_code"] == "TEST"])
    count = save_macro_indicator_rates(conn, rates)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT rate_value, calculation_method FROM macro_indicator_rates "
            "WHERE region = %s AND observation_date = %s",
            ("TEST", date(2024, 1, 1)),
        )
        rate_value, calculation_method = cur.fetchone()

    assert count == 2
    assert float(rate_value) == 2.8
    assert calculation_method == "source_reported"
    conn.close()


def test_save_macro_indicator_rates_upserts_and_bumps_calculated_at():
    conn = _connect()
    first = pd.DataFrame(
        [["eurostat", "TEST", "inflation", date(2024, 1, 1), "monthly", 2.8, "source_reported"]],
        columns=[
            "source",
            "region",
            "indicator_type",
            "observation_date",
            "frequency",
            "rate_value",
            "calculation_method",
        ],
    )
    second = first.copy()
    second["rate_value"] = 3.1

    save_macro_indicator_rates(conn, first)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT calculated_at FROM macro_indicator_rates "
            "WHERE region = %s AND observation_date = %s",
            ("TEST", date(2024, 1, 1)),
        )
        (first_calculated_at,) = cur.fetchone()

    save_macro_indicator_rates(conn, second)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT rate_value, calculated_at FROM macro_indicator_rates "
            "WHERE region = %s AND observation_date = %s",
            ("TEST", date(2024, 1, 1)),
        )
        rate_value, second_calculated_at = cur.fetchone()

    assert float(rate_value) == 3.1
    assert second_calculated_at >= first_calculated_at
    conn.close()
