from datetime import date

import numpy as np
import pandas as pd
import pytest
from data_store.market_data.repository import get_earliest_dates, save_raw_market_data


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
    return pd.DataFrame(
        rows,
        columns=[
            "ticker",
            "price_date",
            "open",
            "high",
            "low",
            "close",
            "adj_close",
            "volume",
            "dividends",
            "stock_splits",
        ],
    )


def test_save_raw_market_data_creates_temp_table_copies_and_upserts():
    conn = FakeConnection()
    df = make_df([["AAPL", date(2026, 1, 1), 100.0, 101.0, 99.0, 100.5, 100.5, 1000, 0.0, 0.0]])

    count = save_raw_market_data(conn, "yfinance", "equity", df)

    assert count == 1
    create_sql = conn._cursor.executed[0][0]
    assert "CREATE TEMP TABLE" in create_sql
    assert "asset_type" in create_sql
    assert "dividends" in create_sql
    assert "stock_splits" in create_sql
    assert "COPY" in conn._cursor.copy_calls[0]
    upsert_sql = conn._cursor.executed[1][0]
    assert "INSERT INTO raw_market_data" in upsert_sql
    assert "asset_type" in upsert_sql
    assert "dividends" in upsert_sql
    assert "stock_splits" in upsert_sql
    assert "ON CONFLICT" in upsert_sql
    assert "DO UPDATE" in upsert_sql
    assert conn.committed is True


def test_save_raw_market_data_stamps_source_and_asset_type_on_each_row():
    conn = FakeConnection()
    df = make_df(
        [
            ["AAPL", date(2026, 1, 1), 100.0, 101.0, 99.0, 100.5, 100.5, 1000, 0.0, 0.0],
            ["MSFT", date(2026, 1, 1), 200.0, 201.0, 199.0, 200.5, 200.5, 2000, 0.0, 0.0],
        ]
    )

    save_raw_market_data(conn, "yfinance", "equity", df)

    sources = [row[2] for row in conn._cursor.copy_rows]
    asset_types = [row[3] for row in conn._cursor.copy_rows]
    assert sources == ["yfinance", "yfinance"]
    assert asset_types == ["equity", "equity"]


def test_save_raw_market_data_writes_dividends_and_stock_splits():
    conn = FakeConnection()
    df = make_df([["CSU.TO", date(2026, 1, 1), 100.0, 101.0, 99.0, 100.5, 100.5, 1000, 1.35, 0.0]])

    save_raw_market_data(conn, "yfinance", "equity", df)

    written_row = conn._cursor.copy_rows[0]
    assert written_row[-2] == 1.35  # dividends
    assert written_row[-1] == 0.0  # stock_splits


def test_save_raw_market_data_writes_none_for_missing_values():
    conn = FakeConnection()
    df = make_df([["AAPL", date(2026, 1, 1), np.nan, 101.0, 99.0, 100.5, np.nan, 1000, 0.0, 0.0]])

    save_raw_market_data(conn, "yfinance", "equity", df)

    written_row = conn._cursor.copy_rows[0]
    assert written_row[4] is None  # open
    assert written_row[8] is None  # adj_close


def test_save_raw_market_data_casts_volume_to_int_for_bigint_column():
    conn = FakeConnection()
    # yfinance returns volume as a float (e.g. 37476700.0); COPY into a BIGINT column
    # rejects that literal text representation, so it must be cast to a real int.
    df = make_df(
        [["AAPL", date(2026, 1, 1), 100.0, 101.0, 99.0, 100.5, 100.5, 37476700.0, 0.0, 0.0]]
    )

    save_raw_market_data(conn, "yfinance", "equity", df)

    volume = conn._cursor.copy_rows[0][9]
    assert volume == 37476700
    assert isinstance(volume, int)


def test_save_raw_market_data_writes_none_for_missing_volume():
    conn = FakeConnection()
    df = make_df([["AAPL", date(2026, 1, 1), 100.0, 101.0, 99.0, 100.5, 100.5, np.nan, 0.0, 0.0]])

    save_raw_market_data(conn, "yfinance", "equity", df)

    assert conn._cursor.copy_rows[0][9] is None


def test_save_raw_market_data_supports_other_asset_types():
    conn = FakeConnection()
    df = make_df(
        [["BTC-USD", date(2026, 1, 1), 50000.0, 51000.0, 49000.0, 50500.0, 50500.0, 1000, 0.0, 0.0]]
    )

    save_raw_market_data(conn, "yfinance", "crypto", df)

    assert conn._cursor.copy_rows[0][3] == "crypto"


def test_save_raw_market_data_reports_what_the_database_wrote_not_the_frame_length():
    # The count is what gets logged as the run's outcome, so it should come from the upsert
    # itself rather than from the frame we handed it -- the frame is the request, rowcount is
    # the result.
    conn = FakeConnection(rowcount=2)
    df = make_df(
        [
            ["AAPL", date(2026, 1, 1), 100.0, 101.0, 99.0, 100.5, 100.5, 1000, 0.0, 0.0],
            ["AAPL", date(2026, 1, 2), 100.0, 101.0, 99.0, 100.5, 100.5, 1000, 0.0, 0.0],
            ["AAPL", date(2026, 1, 3), 100.0, 101.0, 99.0, 100.5, 100.5, 1000, 0.0, 0.0],
        ]
    )

    assert save_raw_market_data(conn, "yfinance", "equity", df) == 2


def test_save_raw_market_data_rolls_back_on_failure_so_the_connection_stays_usable():
    # A COPY failure (e.g. a value out of the column's range) leaves Postgres's transaction
    # aborted until something rolls it back -- without that, every later call on this same
    # connection fails too, even for tickers with perfectly good data.
    conn = RaisingCopyConnection()
    df = make_df([["AAPL", date(2026, 1, 1), 100.0, 101.0, 99.0, 100.5, 100.5, 1000, 0.0, 0.0]])

    with pytest.raises(RuntimeError):
        save_raw_market_data(conn, "yfinance", "equity", df)

    assert conn.rolled_back is True
    assert conn.committed is False


def test_get_earliest_dates_returns_empty_for_no_tickers():
    conn = FakeConnection()

    result = get_earliest_dates(conn, "yfinance", [])

    assert result == {}
    assert conn._cursor.executed == []


def test_get_earliest_dates_returns_min_date_per_ticker():
    conn = FakeConnection(fetch_result=[("AAPL", date(2020, 1, 1)), ("MSFT", date(2015, 6, 15))])

    result = get_earliest_dates(conn, "yfinance", ["AAPL", "MSFT", "NVCR"])

    assert result == {"AAPL": date(2020, 1, 1), "MSFT": date(2015, 6, 15)}
    assert "NVCR" not in result  # no rows for this ticker -- absent, not None


def test_get_earliest_dates_query_shape():
    conn = FakeConnection(fetch_result=[])

    get_earliest_dates(conn, "yfinance", ["AAPL", "MSFT"])

    sql, params = conn._cursor.executed[0]
    assert "MIN(price_date)" in sql
    assert "GROUP BY" in sql
    assert "ANY(%s)" in sql
    assert params == ("yfinance", ["AAPL", "MSFT"])
