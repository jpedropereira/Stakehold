from datetime import date

import pandas as pd
from data_store.dividends.repository import get_earliest_dividend_dates, save_dividends


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
        # None means "behave like Postgres": the upsert reports one affected row per staged
        # row, since every row either inserts or updates. Pass an explicit rowcount to assert
        # that the caller reads the database's count rather than the frame's length.
        self._forced_rowcount = rowcount
        self.rowcount = -1  # psycopg's value before any statement has run
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

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True


def make_df(rows):
    return pd.DataFrame(rows, columns=["ticker", "ex_date", "amount"])


def test_save_dividends_creates_temp_table_copies_and_upserts():
    conn = FakeConnection()
    df = make_df([["AAPL", date(2026, 1, 1), 0.25]])

    count = save_dividends(conn, "yfinance", "equity", df)

    assert count == 1
    create_sql = conn._cursor.executed[0][0]
    assert "CREATE TEMP TABLE" in create_sql
    assert "asset_type" in create_sql
    assert "COPY" in conn._cursor.copy_calls[0]
    upsert_sql = conn._cursor.executed[1][0]
    assert "INSERT INTO dividend_events" in upsert_sql
    assert "asset_type" in upsert_sql
    assert "ON CONFLICT" in upsert_sql
    assert "DO UPDATE" in upsert_sql
    assert conn.committed is True


def test_save_dividends_stamps_source_and_asset_type_on_each_row():
    conn = FakeConnection()
    df = make_df(
        [
            ["AAPL", date(2026, 1, 1), 0.25],
            ["MSFT", date(2026, 1, 1), 0.75],
        ]
    )

    save_dividends(conn, "yfinance", "equity", df)

    sources = [row[2] for row in conn._cursor.copy_rows]
    asset_types = [row[3] for row in conn._cursor.copy_rows]
    assert sources == ["yfinance", "yfinance"]
    assert asset_types == ["equity", "equity"]


def test_save_dividends_writes_amount_for_each_row():
    conn = FakeConnection()
    df = make_df([["AAPL", date(2026, 1, 1), 0.25]])

    save_dividends(conn, "yfinance", "equity", df)

    written_row = conn._cursor.copy_rows[0]
    assert written_row[4] == 0.25


def test_save_dividends_supports_other_asset_types():
    conn = FakeConnection()
    df = make_df([["CSU.TO", date(2026, 1, 1), 1.38]])

    save_dividends(conn, "yfinance", "equity", df)

    assert conn._cursor.copy_rows[0][3] == "equity"


def test_get_earliest_dividend_dates_returns_empty_for_no_tickers():
    conn = FakeConnection()

    result = get_earliest_dividend_dates(conn, "yfinance", [])

    assert result == {}
    assert conn._cursor.executed == []


def test_get_earliest_dividend_dates_returns_min_date_per_ticker():
    conn = FakeConnection(
        fetch_result=[("CSU.TO", date(2008, 3, 17)), ("BT-A.L", date(1993, 1, 22))]
    )

    result = get_earliest_dividend_dates(conn, "yfinance", ["CSU.TO", "BT-A.L", "IWDA.AS"])

    assert result == {"CSU.TO": date(2008, 3, 17), "BT-A.L": date(1993, 1, 22)}
    assert "IWDA.AS" not in result  # no rows for this ticker -- absent, not None


def test_get_earliest_dividend_dates_query_shape():
    conn = FakeConnection(fetch_result=[])

    get_earliest_dividend_dates(conn, "yfinance", ["AAPL", "MSFT"])

    sql, params = conn._cursor.executed[0]
    assert "MIN(ex_date)" in sql
    assert "GROUP BY" in sql
    assert "ANY(%s)" in sql
    assert params == ("yfinance", ["AAPL", "MSFT"])


def test_save_dividends_reports_what_the_database_wrote_not_the_frame_length():
    conn = FakeConnection(rowcount=2)
    df = make_df(
        [
            ["AAPL", date(2026, 1, 1), 0.24],
            ["AAPL", date(2026, 4, 1), 0.25],
            ["AAPL", date(2026, 7, 1), 0.26],
        ]
    )

    assert save_dividends(conn, "yfinance", "equity", df) == 2
