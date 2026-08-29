from datetime import date

import numpy as np
import pandas as pd
import pytest
from data_store.fx.repository import get_earliest_fx_dates, save_fx_rates


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


class RaisingCopyCursor(FakeCursor):
    def copy(self, sql):
        raise RuntimeError("numeric field overflow")


class RaisingCopyConnection(FakeConnection):
    def __init__(self):
        super().__init__()
        self._cursor = RaisingCopyCursor()


def make_df(rows):
    return pd.DataFrame(rows, columns=["base_currency", "quote_currency", "rate_date", "rate"])


def test_save_fx_rates_creates_temp_table_copies_and_upserts():
    conn = FakeConnection()
    df = make_df([["EUR", "USD", date(2026, 1, 1), 1.08]])

    count = save_fx_rates(conn, "yfinance", df)

    assert count == 1
    create_sql = conn._cursor.executed[0][0]
    assert "CREATE TEMP TABLE" in create_sql
    assert "base_currency" in create_sql
    assert "quote_currency" in create_sql
    assert "COPY" in conn._cursor.copy_calls[0]
    upsert_sql = conn._cursor.executed[1][0]
    assert "INSERT INTO fx_rates" in upsert_sql
    assert "ON CONFLICT" in upsert_sql
    assert "DO UPDATE" in upsert_sql
    assert conn.committed is True


def test_save_fx_rates_stamps_source_on_each_row():
    conn = FakeConnection()
    df = make_df(
        [
            ["EUR", "USD", date(2026, 1, 1), 1.08],
            ["EUR", "GBP", date(2026, 1, 1), 0.85],
        ]
    )

    save_fx_rates(conn, "yfinance", df)

    sources = [row[3] for row in conn._cursor.copy_rows]
    assert sources == ["yfinance", "yfinance"]


def test_save_fx_rates_writes_none_for_missing_rate():
    conn = FakeConnection()
    df = make_df([["EUR", "USD", date(2026, 1, 1), np.nan]])

    save_fx_rates(conn, "yfinance", df)

    assert conn._cursor.copy_rows[0][-1] is None


def test_save_fx_rates_reports_what_the_database_wrote_not_the_frame_length():
    conn = FakeConnection(rowcount=2)
    df = make_df(
        [
            ["EUR", "USD", date(2026, 1, 1), 1.08],
            ["EUR", "USD", date(2026, 1, 2), 1.09],
            ["EUR", "USD", date(2026, 1, 3), 1.10],
        ]
    )

    assert save_fx_rates(conn, "yfinance", df) == 2


def test_save_fx_rates_drops_a_row_whose_rate_exceeds_its_column_range(caplog):
    caplog.set_level("WARNING")
    conn = FakeConnection()
    good = ["EUR", "USD", date(2026, 1, 1), 1.08]
    bad = ["EUR", "USD", date(2026, 1, 2), 1e11]
    df = make_df([good, bad])

    count = save_fx_rates(conn, "yfinance", df)

    assert len(conn._cursor.copy_rows) == 1
    assert conn._cursor.copy_rows[0][2] == date(2026, 1, 1)
    assert count == 1
    assert any(
        "2026-01-02" in r.message and "rate" in r.message for r in caplog.records
    ), caplog.text


def test_save_fx_rates_keeps_a_value_just_inside_its_column_range():
    conn = FakeConnection()
    df = make_df([["EUR", "USD", date(2026, 1, 1), 9999999999.99]])

    count = save_fx_rates(conn, "yfinance", df)

    assert len(conn._cursor.copy_rows) == 1
    assert count == 1


def test_save_fx_rates_rolls_back_on_failure_so_the_connection_stays_usable():
    conn = RaisingCopyConnection()
    df = make_df([["EUR", "USD", date(2026, 1, 1), 1.08]])

    with pytest.raises(RuntimeError):
        save_fx_rates(conn, "yfinance", df)

    assert conn.rolled_back is True
    assert conn.committed is False


def test_get_earliest_fx_dates_returns_empty_for_no_pairs():
    conn = FakeConnection()

    result = get_earliest_fx_dates(conn, "yfinance", [])

    assert result == {}
    assert conn._cursor.executed == []


def test_get_earliest_fx_dates_returns_min_date_per_pair():
    conn = FakeConnection(
        fetch_result=[("EUR", "USD", date(2020, 1, 1)), ("EUR", "GBP", date(2015, 6, 15))]
    )

    result = get_earliest_fx_dates(
        conn, "yfinance", [("EUR", "USD"), ("EUR", "GBP"), ("EUR", "PLN")]
    )

    assert result == {("EUR", "USD"): date(2020, 1, 1), ("EUR", "GBP"): date(2015, 6, 15)}
    assert ("EUR", "PLN") not in result


def test_get_earliest_fx_dates_query_shape():
    conn = FakeConnection(fetch_result=[])

    get_earliest_fx_dates(conn, "yfinance", [("EUR", "USD"), ("EUR", "GBP")])

    sql, params = conn._cursor.executed[0]
    assert "MIN(rate_date)" in sql
    assert "GROUP BY" in sql
    assert params[0] == "yfinance"
