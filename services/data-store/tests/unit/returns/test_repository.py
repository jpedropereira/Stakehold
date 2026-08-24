from datetime import date

import numpy as np
import pandas as pd
import pytest
from data_store.returns.repository import (
    _COLUMNS,
    get_sources,
    iter_price_series,
    save_market_returns,
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
    def __init__(self, fetch_results=None, rowcount=None):
        # A queue: each fetchall() call pops the next result, so a caller that issues more than
        # one query (e.g. iter_price_series discovering tickers, then fetching each batch) gets
        # a different, controllable answer each time.
        self._fetch_results = list(fetch_results or [])
        self._forced_rowcount = rowcount
        self.rowcount = -1
        self.executed = []
        self.copy_calls = []
        self.copy_rows = []

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
        return self._fetch_results.pop(0) if self._fetch_results else []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, fetch_results=None, rowcount=None):
        self._cursor = FakeCursor(fetch_results=fetch_results, rowcount=rowcount)
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


def make_returns_df(rows):
    return pd.DataFrame(
        rows,
        columns=[
            "ticker",
            "price_date",
            "split_adjusted_close",
            "future_split_factor",
            "price_return",
            "dividend_return",
            "simple_return",
            "log_return",
        ],
    )


# --- get_sources -------------------------------------------------------------------------------


def test_get_sources_returns_distinct_sources_from_raw_market_data():
    conn = FakeConnection(fetch_results=[[("yfinance",)]])

    sources = get_sources(conn)

    assert sources == ["yfinance"]
    sql, _ = conn._cursor.executed[0]
    assert "DISTINCT source" in sql
    assert "raw_market_data" in sql


# --- iter_price_series ---------------------------------------------------------------------


def test_iter_price_series_discovers_tickers_when_none_given():
    conn = FakeConnection(
        fetch_results=[
            [("AAPL",), ("MSFT",)],  # ticker discovery query
            [("AAPL", date(2026, 1, 1), 100.0, 0.0, 0.0, 1000)],  # batch 1
        ]
    )

    batches = list(iter_price_series(conn, "yfinance", None, batch_size=10))

    assert len(batches) == 1
    discovery_sql, discovery_params = conn._cursor.executed[0]
    assert "DISTINCT ticker" in discovery_sql
    assert discovery_params == ("yfinance",)


def test_iter_price_series_uses_given_tickers_without_a_discovery_query():
    conn = FakeConnection(fetch_results=[[("AAPL", date(2026, 1, 1), 100.0, 0.0, 0.0, 1000)]])

    list(iter_price_series(conn, "yfinance", ["AAPL"], batch_size=10))

    assert len(conn._cursor.executed) == 1  # no discovery query issued
    sql, params = conn._cursor.executed[0]
    assert "raw_market_data" in sql
    assert "ANY(%s)" in sql
    assert params == ("yfinance", ["AAPL"])


def test_iter_price_series_batches_tickers():
    conn = FakeConnection(fetch_results=[[], []])

    list(iter_price_series(conn, "yfinance", ["AAPL", "MSFT", "GOOG"], batch_size=2))

    assert len(conn._cursor.executed) == 2
    first_params = conn._cursor.executed[0][1]
    second_params = conn._cursor.executed[1][1]
    assert first_params == ("yfinance", ["AAPL", "MSFT"])
    assert second_params == ("yfinance", ["GOOG"])


def test_iter_price_series_yields_a_dataframe_with_expected_columns():
    conn = FakeConnection(fetch_results=[[("AAPL", date(2026, 1, 1), 100.0, 0.5, 0.0, 1000)]])

    (batch,) = list(iter_price_series(conn, "yfinance", ["AAPL"], batch_size=10))

    assert list(batch.columns) == [
        "ticker",
        "price_date",
        "close",
        "dividends",
        "stock_splits",
        "volume",
    ]
    assert batch.iloc[0]["ticker"] == "AAPL"


def test_iter_price_series_orders_by_ticker_and_price_date():
    conn = FakeConnection(fetch_results=[[]])

    list(iter_price_series(conn, "yfinance", ["AAPL"], batch_size=10))

    sql, _ = conn._cursor.executed[0]
    assert "ORDER BY ticker, price_date" in sql


def test_iter_price_series_returns_no_batches_when_there_are_no_tickers():
    conn = FakeConnection(fetch_results=[[]])  # empty discovery result

    batches = list(iter_price_series(conn, "yfinance", None, batch_size=10))

    assert batches == []


# --- save_market_returns -------------------------------------------------------------------


def test_save_market_returns_creates_temp_table_copies_and_upserts():
    conn = FakeConnection()
    df = make_returns_df([["AAPL", date(2026, 1, 1), 100.0, 1.0, None, None, None, None]])

    count = save_market_returns(conn, "yfinance", df)

    assert count == 1
    create_sql = conn._cursor.executed[0][0]
    assert "CREATE TEMP TABLE" in create_sql
    assert "COPY" in conn._cursor.copy_calls[0]
    upsert_sql = conn._cursor.executed[1][0]
    assert "INSERT INTO market_returns" in upsert_sql
    assert "ON CONFLICT" in upsert_sql
    assert "DO UPDATE" in upsert_sql
    assert "calculated_at = now()" in upsert_sql
    assert conn.committed is True


def test_save_market_returns_stamps_source_on_each_row():
    conn = FakeConnection()
    df = make_returns_df(
        [
            ["AAPL", date(2026, 1, 1), 100.0, 1.0, None, None, None, None],
            ["MSFT", date(2026, 1, 1), 200.0, 1.0, None, None, None, None],
        ]
    )

    save_market_returns(conn, "yfinance", df)

    sources = [row[2] for row in conn._cursor.copy_rows]
    assert sources == ["yfinance", "yfinance"]


def test_save_market_returns_writes_none_for_null_returns_on_a_first_date_row():
    conn = FakeConnection()
    df = make_returns_df([["AAPL", date(2026, 1, 1), 100.0, 1.0, np.nan, np.nan, np.nan, np.nan]])

    save_market_returns(conn, "yfinance", df)

    written_row = conn._cursor.copy_rows[0]
    assert written_row[-4:] == [None, None, None, None]


def test_save_market_returns_reports_what_the_database_wrote_not_the_frame_length():
    conn = FakeConnection(rowcount=2)
    df = make_returns_df(
        [
            ["AAPL", date(2026, 1, 1), 100.0, 1.0, None, None, None, None],
            ["AAPL", date(2026, 1, 2), 101.0, 1.0, 0.01, 0.0, 0.01, 0.00995],
            ["AAPL", date(2026, 1, 3), 102.0, 1.0, 0.0099, 0.0, 0.0099, 0.00985],
        ]
    )

    assert save_market_returns(conn, "yfinance", df) == 2


@pytest.mark.parametrize(
    "column,bad_value",
    [
        ("split_adjusted_close", 1e14),
        ("future_split_factor", 1e15),
        ("price_return", 1e10),
        ("dividend_return", 1e10),
        ("simple_return", 1e10),
        ("log_return", 1e10),
    ],
)
def test_save_market_returns_drops_a_row_whose_value_exceeds_its_column_range(
    caplog, column, bad_value
):
    caplog.set_level("WARNING")
    conn = FakeConnection()
    good = ["AAPL", date(2026, 1, 1), 100.0, 1.0, 0.01, 0.0, 0.01, 0.00995]
    bad = ["AAPL", date(2026, 1, 2), 100.0, 1.0, 0.01, 0.0, 0.01, 0.00995]
    bad[_COLUMNS.index(column)] = bad_value
    df = make_returns_df([good, bad])

    count = save_market_returns(conn, "yfinance", df)

    assert len(conn._cursor.copy_rows) == 1
    assert conn._cursor.copy_rows[0][1] == date(2026, 1, 1)
    assert count == 1
    assert any("2026-01-02" in r.message and column in r.message for r in caplog.records)


def test_save_market_returns_keeps_a_cumulative_reverse_split_factor():
    """Regression: BMPS.MI's four reverse splits multiply out to 5e-8, which NUMERIC(18,6)
    rounded to 0 -- silently breaking the documented `split_adjusted_close *
    future_split_factor` as-traded derivation for every row before its first split."""
    conn = FakeConnection()
    df = make_returns_df([["BMPS.MI", date(2010, 1, 4), 100.0, 5e-8, 0.01, 0.0, 0.01, 0.00995]])

    count = save_market_returns(conn, "yfinance", df)

    assert count == 1
    assert conn._cursor.copy_rows[0][_COLUMNS.index("future_split_factor") + 1] == 5e-8


@pytest.mark.parametrize("bad_value", [1e-16, 0.0])
def test_save_market_returns_drops_a_row_whose_split_factor_would_round_to_zero(caplog, bad_value):
    """A split factor below the column's scale, or already collapsed to zero, must be warned
    about and skipped rather than stored -- a stored 0 reads as a valid factor and silently
    zeroes any as-traded price derived from it."""
    caplog.set_level("WARNING")
    conn = FakeConnection()
    good = ["AAPL", date(2026, 1, 1), 100.0, 1.0, 0.01, 0.0, 0.01, 0.00995]
    bad = ["AAPL", date(2026, 1, 2), 100.0, bad_value, 0.01, 0.0, 0.01, 0.00995]
    df = make_returns_df([good, bad])

    count = save_market_returns(conn, "yfinance", df)

    assert count == 1
    assert len(conn._cursor.copy_rows) == 1
    assert conn._cursor.copy_rows[0][1] == date(2026, 1, 1)
    assert any(
        "2026-01-02" in r.message and "future_split_factor" in r.message for r in caplog.records
    )


def test_save_market_returns_keeps_a_zero_return_which_is_a_legitimate_flat_day():
    """The underflow guard must apply only to future_split_factor: a return of exactly 0 is a
    flat day, not a defect, and must survive."""
    conn = FakeConnection()
    df = make_returns_df([["AAPL", date(2026, 1, 2), 100.0, 1.0, 0.0, 0.0, 0.0, 0.0]])

    assert save_market_returns(conn, "yfinance", df) == 1
    assert len(conn._cursor.copy_rows) == 1


def test_save_market_returns_rolls_back_on_failure_so_the_connection_stays_usable():
    conn = RaisingCopyConnection()
    df = make_returns_df([["AAPL", date(2026, 1, 1), 100.0, 1.0, None, None, None, None]])

    with pytest.raises(RuntimeError):
        save_market_returns(conn, "yfinance", df)

    assert conn.rolled_back is True
    assert conn.committed is False
