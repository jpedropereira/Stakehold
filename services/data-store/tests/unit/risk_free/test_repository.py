from datetime import date
from decimal import Decimal

import pandas as pd
from data_store.risk_free.repository import (
    get_first_risk_free_dates,
    get_latest_risk_free_dates,
    get_risk_free_rate_series,
    save_risk_free_rates,
)


class FakeCopy:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def write_row(self, row):
        self.rows.append(row)


class FakeCursor:
    def __init__(self, fetch_result=None, rowcount=None):
        self._forced_rowcount = rowcount
        self.rowcount = -1
        self.executed = []
        self.copy_calls = []
        self.copy_rows = []
        self.fetch_result = fetch_result or []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "INSERT INTO" in sql:
            self.rowcount = (
                self._forced_rowcount if self._forced_rowcount is not None else len(self.copy_rows)
            )

    def copy(self, sql):
        self.copy_calls.append(sql)
        return FakeCopy(self.copy_rows)

    def fetchall(self):
        return self.fetch_result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, fetch_result=None, rowcount=None):
        self._cursor = FakeCursor(fetch_result=fetch_result, rowcount=rowcount)
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def make_df(rows):
    return pd.DataFrame(rows, columns=["series", "rate_date", "rate"])


def test_save_upserts_through_a_staging_table_and_commits():
    conn = FakeConnection()

    count = save_risk_free_rates(conn, "ecb", make_df([["estr", date(2019, 10, 1), -0.549]]))

    assert count == 1
    assert "CREATE TEMP TABLE" in conn._cursor.executed[0][0]
    assert "COPY" in conn._cursor.copy_calls[0]
    upsert_sql = conn._cursor.executed[1][0]
    assert "INSERT INTO risk_free_rates" in upsert_sql
    assert "ON CONFLICT (series, rate_date, source) DO UPDATE" in upsert_sql
    assert conn.committed is True


def test_save_stamps_the_source_and_keeps_negative_rates():
    conn = FakeConnection()
    df = make_df([["estr", date(2019, 10, 1), -0.549], ["eonia", date(2019, 9, 30), -0.451]])

    save_risk_free_rates(conn, "ecb", df)

    assert conn._cursor.copy_rows == [
        ["estr", date(2019, 10, 1), "ecb", -0.549],
        ["eonia", date(2019, 9, 30), "ecb", -0.451],
    ]


def test_save_drops_a_rate_postgres_would_reject_and_warns(caplog):
    conn = FakeConnection()
    df = make_df([["estr", date(2026, 1, 1), 12345.0], ["estr", date(2026, 1, 2), 1.93]])

    save_risk_free_rates(conn, "ecb", df)

    assert [row[1] for row in conn._cursor.copy_rows] == [date(2026, 1, 2)]
    assert "estr 2026-01-01" in caplog.text
    assert "out of range" in caplog.text


def test_save_reports_what_the_database_wrote_not_the_frame_length():
    conn = FakeConnection(rowcount=1)
    df = make_df([["estr", date(2026, 1, 1), 1.93], ["estr", date(2026, 1, 2), 1.93]])

    assert save_risk_free_rates(conn, "ecb", df) == 1


def test_get_latest_dates_maps_each_stored_series_to_its_latest_date():
    conn = FakeConnection(fetch_result=[("estr", date(2026, 10, 16))])

    result = get_latest_risk_free_dates(conn, "ecb", ["estr", "eonia"])

    assert result == {"estr": date(2026, 10, 16)}
    sql, params = conn._cursor.executed[0]
    assert "MAX(rate_date)" in sql
    assert params == ("ecb", ["estr", "eonia"])


def test_get_latest_dates_with_no_series_skips_the_query():
    conn = FakeConnection()

    assert get_latest_risk_free_dates(conn, "ecb", []) == {}
    assert conn._cursor.executed == []


def test_get_first_dates_maps_each_stored_series_to_its_first_date():
    conn = FakeConnection(fetch_result=[("eonia", date(1999, 1, 4))])

    result = get_first_risk_free_dates(conn, "ecb", ["estr", "eonia"])

    assert result == {"eonia": date(1999, 1, 4)}
    sql, params = conn._cursor.executed[0]
    assert "MIN(rate_date)" in sql
    assert params == ("ecb", ["estr", "eonia"])


def test_get_first_dates_with_no_series_skips_the_query():
    conn = FakeConnection()

    assert get_first_risk_free_dates(conn, "ecb", []) == {}
    assert conn._cursor.executed == []


def test_get_rate_series_reads_one_series_over_an_inclusive_range_in_date_order():
    rows = [(date(2019, 10, 1), Decimal("-0.5490"))]
    conn = FakeConnection(fetch_result=rows)

    result = get_risk_free_rate_series(conn, "ecb", "estr", date(2019, 10, 1), date(2019, 10, 4))

    assert result == rows
    sql, params = conn._cursor.executed[0]
    assert "BETWEEN" in sql
    assert "ORDER BY rate_date" in sql
    assert params == ("estr", "ecb", date(2019, 10, 1), date(2019, 10, 4))
