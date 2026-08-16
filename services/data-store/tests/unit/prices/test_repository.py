from datetime import date

import numpy as np
import pandas as pd
from data_store.prices.repository import save_prices


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
    def __init__(self):
        self.executed = []
        self.copy_calls = []
        self.copy_rows = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def copy(self, sql):
        self.copy_calls.append(sql)
        return FakeCopy(self.copy_rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self):
        self._cursor = FakeCursor()
        self.committed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True


def make_df(rows):
    return pd.DataFrame(
        rows,
        columns=["ticker", "price_date", "open", "high", "low", "close", "adj_close", "volume"],
    )


def test_save_prices_creates_temp_table_copies_and_upserts():
    conn = FakeConnection()
    df = make_df([["AAPL", date(2026, 1, 1), 100.0, 101.0, 99.0, 100.5, 100.5, 1000]])

    count = save_prices(conn, "yfinance", "equity", df)

    assert count == 1
    create_sql = conn._cursor.executed[0][0]
    assert "CREATE TEMP TABLE" in create_sql
    assert "asset_type" in create_sql
    assert "COPY" in conn._cursor.copy_calls[0]
    upsert_sql = conn._cursor.executed[1][0]
    assert "INSERT INTO daily_ohlcv" in upsert_sql
    assert "asset_type" in upsert_sql
    assert "ON CONFLICT" in upsert_sql
    assert "DO UPDATE" in upsert_sql
    assert conn.committed is True


def test_save_prices_stamps_source_and_asset_type_on_each_row():
    conn = FakeConnection()
    df = make_df(
        [
            ["AAPL", date(2026, 1, 1), 100.0, 101.0, 99.0, 100.5, 100.5, 1000],
            ["MSFT", date(2026, 1, 1), 200.0, 201.0, 199.0, 200.5, 200.5, 2000],
        ]
    )

    save_prices(conn, "yfinance", "equity", df)

    sources = [row[2] for row in conn._cursor.copy_rows]
    asset_types = [row[3] for row in conn._cursor.copy_rows]
    assert sources == ["yfinance", "yfinance"]
    assert asset_types == ["equity", "equity"]


def test_save_prices_writes_none_for_missing_values():
    conn = FakeConnection()
    df = make_df([["AAPL", date(2026, 1, 1), np.nan, 101.0, 99.0, 100.5, np.nan, 1000]])

    save_prices(conn, "yfinance", "equity", df)

    written_row = conn._cursor.copy_rows[0]
    assert written_row[4] is None  # open
    assert written_row[8] is None  # adj_close


def test_save_prices_casts_volume_to_int_for_bigint_column():
    conn = FakeConnection()
    # yfinance returns volume as a float (e.g. 37476700.0); COPY into a BIGINT column
    # rejects that literal text representation, so it must be cast to a real int.
    df = make_df([["AAPL", date(2026, 1, 1), 100.0, 101.0, 99.0, 100.5, 100.5, 37476700.0]])

    save_prices(conn, "yfinance", "equity", df)

    volume = conn._cursor.copy_rows[0][9]
    assert volume == 37476700
    assert isinstance(volume, int)


def test_save_prices_writes_none_for_missing_volume():
    conn = FakeConnection()
    df = make_df([["AAPL", date(2026, 1, 1), 100.0, 101.0, 99.0, 100.5, 100.5, np.nan]])

    save_prices(conn, "yfinance", "equity", df)

    assert conn._cursor.copy_rows[0][9] is None


def test_save_prices_supports_other_asset_types():
    conn = FakeConnection()
    df = make_df([["BTC-USD", date(2026, 1, 1), 50000.0, 51000.0, 49000.0, 50500.0, 50500.0, 1000]])

    save_prices(conn, "yfinance", "crypto", df)

    assert conn._cursor.copy_rows[0][3] == "crypto"
