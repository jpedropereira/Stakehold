import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from data_store.risk_free.repository import (
    get_first_risk_free_dates,
    get_latest_risk_free_dates,
    get_risk_free_rate_series,
    save_risk_free_rates,
)
from shared.db.connection import apply_schema

pytestmark = pytest.mark.integration

SCHEMA_PATH = Path(__file__).parents[2] / "src" / "data_store" / "db" / "schema.sql"

# Every row these tests write carries this source, so cleanup touches nothing else.
SOURCE = "test"


def _connect():
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    apply_schema(conn, SCHEMA_PATH)
    return conn


def make_df(rows):
    return pd.DataFrame(rows, columns=["series", "rate_date", "rate"])


def _stored(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT series, rate_date, rate FROM risk_free_rates WHERE source = %s "
            "ORDER BY series, rate_date",
            (SOURCE,),
        )
        return [(series, rate_date, float(rate)) for series, rate_date, rate in cur.fetchall()]


@pytest.fixture(autouse=True)
def clean_table():
    def clean():
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM risk_free_rates WHERE source = %s", (SOURCE,))
        conn.commit()
        conn.close()

    clean()
    yield
    clean()


def test_save_stores_both_series_side_by_side_on_the_same_date():
    conn = _connect()
    df = make_df(
        [
            ["estr", date(2019, 10, 1), -0.549],
            ["eonia", date(2019, 10, 1), -0.464],
            ["eonia", date(2019, 9, 30), -0.451],
        ]
    )

    assert save_risk_free_rates(conn, SOURCE, df) == 3
    assert _stored(conn) == [
        ("eonia", date(2019, 9, 30), -0.451),
        ("eonia", date(2019, 10, 1), -0.464),
        ("estr", date(2019, 10, 1), -0.549),
    ]
    conn.close()


def test_save_rerun_with_a_restated_value_updates_in_place():
    conn = _connect()
    save_risk_free_rates(conn, SOURCE, make_df([["estr", date(2026, 10, 16), 2.441]]))

    rerun = save_risk_free_rates(conn, SOURCE, make_df([["estr", date(2026, 10, 16), 2.44]]))

    assert rerun == 1
    assert _stored(conn) == [("estr", date(2026, 10, 16), 2.44)]
    conn.close()


def test_save_drops_a_rate_postgres_would_reject_and_keeps_the_rest():
    conn = _connect()
    df = make_df([["estr", date(2026, 1, 1), 12345.0], ["estr", date(2026, 1, 2), 1.93]])

    assert save_risk_free_rates(conn, SOURCE, df) == 1
    assert _stored(conn) == [("estr", date(2026, 1, 2), 1.93)]
    conn.close()


def test_get_latest_dates_returns_the_max_per_series_and_omits_unstored_ones():
    conn = _connect()
    save_risk_free_rates(
        conn,
        SOURCE,
        make_df(
            [
                ["eonia", date(2021, 12, 30), -0.497],
                ["eonia", date(2021, 12, 31), -0.505],
            ]
        ),
    )

    assert get_latest_risk_free_dates(conn, SOURCE, ["estr", "eonia"]) == {
        "eonia": date(2021, 12, 31)
    }
    conn.close()


def test_get_first_dates_returns_the_min_per_series_and_omits_unstored_ones():
    conn = _connect()
    save_risk_free_rates(
        conn,
        SOURCE,
        make_df([["eonia", date(1999, 1, 5), 3.22], ["eonia", date(1999, 1, 4), 3.20]]),
    )

    assert get_first_risk_free_dates(conn, SOURCE, ["estr", "eonia"]) == {"eonia": date(1999, 1, 4)}
    conn.close()


def test_get_rate_series_is_one_series_in_date_order_within_inclusive_bounds():
    conn = _connect()
    save_risk_free_rates(
        conn,
        SOURCE,
        make_df(
            [
                ["estr", date(2019, 10, 3), -0.551],
                ["estr", date(2019, 10, 1), -0.549],
                ["estr", date(2019, 10, 2), -0.550],
                ["estr", date(2019, 10, 4), -0.552],
                ["eonia", date(2019, 10, 2), -0.465],
            ]
        ),
    )

    rows = get_risk_free_rate_series(conn, SOURCE, "estr", date(2019, 10, 1), date(2019, 10, 3))

    # Decimal at the column's scale, not a float: the splice subtracts the spread exactly.
    assert rows == [
        (date(2019, 10, 1), Decimal("-0.5490")),
        (date(2019, 10, 2), Decimal("-0.5500")),
        (date(2019, 10, 3), Decimal("-0.5510")),
    ]
    conn.close()
