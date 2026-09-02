from datetime import date

import pandas as pd
import pytest
from data_store.macro.repository import (
    _RATE_COLUMNS,
    _RAW_COLUMNS,
    get_raw_indicators,
    save_macro_indicator_rates,
    save_raw_macro_indicators,
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


def make_raw_df(rows):
    return pd.DataFrame(rows, columns=_RAW_COLUMNS)


def make_rate_df(rows):
    return pd.DataFrame(rows, columns=_RATE_COLUMNS)


def test_save_raw_macro_indicators_creates_temp_table_copies_and_upserts():
    conn = FakeConnection()
    df = make_raw_df(
        [["fred", "CPIAUCSL", "US", date(2024, 1, 1), "inflation", "monthly", 300.1, "Index"]]
    )

    count = save_raw_macro_indicators(conn, df)

    assert count == 1
    create_sql = conn._cursor.executed[0][0]
    assert "CREATE TEMP TABLE" in create_sql
    assert "COPY" in conn._cursor.copy_calls[0]
    upsert_sql = conn._cursor.executed[1][0]
    assert "INSERT INTO raw_macro_indicators" in upsert_sql
    assert "ON CONFLICT" in upsert_sql
    assert "DO UPDATE" in upsert_sql
    assert conn.committed is True


def test_save_raw_macro_indicators_writes_every_row():
    conn = FakeConnection()
    df = make_raw_df(
        [
            ["fred", "CPIAUCSL", "US", date(2024, 1, 1), "inflation", "monthly", 300.1, "Index"],
            [
                "eurostat",
                "prc_hicp_manr",
                "EA",
                date(2024, 1, 1),
                "inflation",
                "monthly",
                2.8,
                "Rate",
            ],
        ]
    )

    save_raw_macro_indicators(conn, df)

    sources = [row[0] for row in conn._cursor.copy_rows]
    assert sources == ["fred", "eurostat"]


def test_save_raw_macro_indicators_drops_a_row_whose_value_is_out_of_range(caplog):
    caplog.set_level("WARNING")
    conn = FakeConnection()
    good = ["fred", "CPIAUCSL", "US", date(2024, 1, 1), "inflation", "monthly", 300.1, "Index"]
    bad = ["fred", "CPIAUCSL", "US", date(2024, 2, 1), "inflation", "monthly", 1e15, "Index"]
    df = make_raw_df([good, bad])

    count = save_raw_macro_indicators(conn, df)

    assert count == 1
    assert len(conn._cursor.copy_rows) == 1
    assert any("value" in r.message for r in caplog.records)


def test_save_raw_macro_indicators_rolls_back_on_failure():
    class RaisingCopyCursor(FakeCursor):
        def copy(self, sql):
            raise RuntimeError("numeric field overflow")

    class RaisingCopyConnection(FakeConnection):
        def __init__(self):
            super().__init__()
            self._cursor = RaisingCopyCursor()

    conn = RaisingCopyConnection()
    df = make_raw_df(
        [["fred", "CPIAUCSL", "US", date(2024, 1, 1), "inflation", "monthly", 300.1, "Index"]]
    )

    with pytest.raises(RuntimeError):
        save_raw_macro_indicators(conn, df)

    assert conn.rolled_back is True
    assert conn.committed is False


def test_get_raw_indicators_returns_every_row():
    conn = FakeConnection(
        fetch_result=[
            ("fred", "CPIAUCSL", "US", date(2024, 1, 1), "inflation", "monthly", 300.1, "Index"),
        ]
    )

    result = get_raw_indicators(conn)

    assert list(result.columns) == _RAW_COLUMNS
    assert len(result) == 1
    assert result.iloc[0]["source"] == "fred"


def test_save_macro_indicator_rates_creates_temp_table_copies_and_upserts():
    conn = FakeConnection()
    df = make_rate_df(
        [["eurostat", "EA", "inflation", date(2024, 1, 1), "monthly", 2.8, "source_reported"]]
    )

    count = save_macro_indicator_rates(conn, df)

    assert count == 1
    create_sql = conn._cursor.executed[0][0]
    assert "CREATE TEMP TABLE" in create_sql
    upsert_sql = conn._cursor.executed[1][0]
    assert "INSERT INTO macro_indicator_rates" in upsert_sql
    assert "ON CONFLICT" in upsert_sql
    assert "DO UPDATE" in upsert_sql
    assert "calculated_at = now()" in upsert_sql
    assert conn.committed is True


def test_save_macro_indicator_rates_drops_a_row_whose_value_is_out_of_range(caplog):
    caplog.set_level("WARNING")
    conn = FakeConnection()
    good = ["eurostat", "EA", "inflation", date(2024, 1, 1), "monthly", 2.8, "source_reported"]
    bad = ["fred", "US", "inflation", date(2024, 2, 1), "monthly", 1e11, "yoy_pct_change"]
    df = make_rate_df([good, bad])

    count = save_macro_indicator_rates(conn, df)

    assert count == 1
    assert len(conn._cursor.copy_rows) == 1
    assert any("rate_value" in r.message for r in caplog.records)


def test_save_macro_indicator_rates_reports_what_the_database_wrote_not_the_frame_length():
    conn = FakeConnection(rowcount=1)
    df = make_rate_df(
        [
            ["eurostat", "EA", "inflation", date(2024, 1, 1), "monthly", 2.8, "source_reported"],
            ["eurostat", "EA", "inflation", date(2024, 2, 1), "monthly", 2.6, "source_reported"],
        ]
    )

    assert save_macro_indicator_rates(conn, df) == 1
