from datetime import date

import pandas as pd
import pytest
from data_store.holdings.repository import (
    get_latest_tickers,
    normalize_holdings,
    save_holdings,
)

RAW_COLUMNS = [
    "ticker",
    "name",
    "sector",
    "asset class",
    "market value",
    "weight (%)",
    "notional value",
    "shares",
    "price",
    "location",
    "exchange",
    "market currency",
]


def make_raw_df(rows):
    return pd.DataFrame(rows, columns=RAW_COLUMNS)


def test_normalize_holdings_renames_to_db_columns():
    df = make_raw_df(
        [
            [
                "AAPL",
                "APPLE INC",
                "Technology",
                "Equity",
                "1,234.50",
                "5.12",
                "1,234.50",
                "10",
                "150.25",
                "United States",
                "NASDAQ",
                "USD",
            ]
        ]
    )

    result = normalize_holdings(df)

    assert list(result.columns) == [
        "holding_ticker",
        "holding_name",
        "sector",
        "asset_class",
        "market_value",
        "weight_percent",
        "notional_value",
        "shares",
        "price",
        "location",
        "exchange",
        "currency",
    ]


def test_normalize_holdings_casts_float_columns_and_strips_commas():
    df = make_raw_df(
        [
            [
                "AAPL",
                "APPLE INC",
                "Technology",
                "Equity",
                "1,234.50",
                "5.12",
                "1,234.50",
                "10",
                "150.25",
                "United States",
                "NASDAQ",
                "USD",
            ]
        ]
    )

    result = normalize_holdings(df)

    assert result["market_value"].iloc[0] == pytest.approx(1234.50)
    assert result["shares"].iloc[0] == pytest.approx(10.0)


def test_normalize_holdings_raises_on_missing_column():
    df = pd.DataFrame([{"ticker": "AAPL"}])

    with pytest.raises(ValueError, match="missing"):
        normalize_holdings(df)


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
    def __init__(self, fetch_result=None):
        self.executed = []
        self.copy_calls = []
        self.copy_rows = []
        self.fetch_result = fetch_result or []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

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
    def __init__(self, fetch_result=None):
        self._cursor = FakeCursor(fetch_result=fetch_result)
        self.committed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True


def test_save_holdings_deletes_then_copies():
    conn = FakeConnection()
    df = normalize_holdings(
        make_raw_df(
            [
                [
                    "AAPL",
                    "APPLE INC",
                    "Technology",
                    "Equity",
                    "1234.50",
                    "5.12",
                    "1234.50",
                    "10",
                    "150.25",
                    "United States",
                    "NASDAQ",
                    "USD",
                ]
            ]
        )
    )

    count = save_holdings(conn, "my_fund", date(2026, 1, 1), df)

    assert count == 1
    delete_sql, delete_params = conn._cursor.executed[0]
    assert "DELETE FROM etf_holdings" in delete_sql
    assert delete_params == ("my_fund", date(2026, 1, 1))
    assert "COPY etf_holdings" in conn._cursor.copy_calls[0]
    assert conn._cursor.copy_rows[0][0] == "my_fund"
    assert conn._cursor.copy_rows[0][1] == date(2026, 1, 1)
    assert conn.committed is True


def test_save_holdings_writes_none_for_missing_values():
    conn = FakeConnection()
    df = normalize_holdings(
        make_raw_df(
            [
                [
                    None,
                    "CASH",
                    "Cash",
                    "Cash",
                    "1234.50",
                    "5.12",
                    "1234.50",
                    "10",
                    "150.25",
                    "United States",
                    "-",
                    "USD",
                ]
            ]
        )
    )

    save_holdings(conn, "my_fund", date(2026, 1, 1), df)

    written_row = conn._cursor.copy_rows[0]
    assert written_row[2] is None  # holding_ticker, the first COLUMN_MAPPING column


def test_get_latest_tickers_queries_max_as_of_date():
    conn = FakeConnection(fetch_result=[("AAPL", "NASDAQ"), ("MSFT", "NASDAQ")])

    tickers = get_latest_tickers(conn, "my_fund")

    assert tickers == [("AAPL", "NASDAQ"), ("MSFT", "NASDAQ")]
    sql, params = conn._cursor.executed[0]
    assert "MAX(as_of_date)" in sql
    assert "exchange" in sql
    assert params == ("my_fund", "my_fund")


def test_get_latest_tickers_filters_to_equity():
    conn = FakeConnection(fetch_result=[])

    get_latest_tickers(conn, "my_fund")

    sql, _ = conn._cursor.executed[0]
    assert "asset_class" in sql
    assert "equity" in sql.lower()


def test_normalize_holdings_keeps_missing_string_values_missing():
    # Guards a version-dependent assumption rather than a past bug: `astype(str)` is only
    # NA-preserving from pandas 3.0, where it yields the `str` dtype. Under 2.x it produced
    # object dtype with the literal strings "nan"/"None", which pd.isna() does not detect --
    # so save_holdings would have written those strings into the DB instead of NULL, and
    # get_latest_tickers' `holding_ticker IS NOT NULL` filter would have let them through to
    # be priced. pandas>=3.0 is pinned in pyproject.toml; this fails loudly if that slips.
    df = make_raw_df(
        [
            [
                "AAPL",
                "APPLE INC",
                "Technology",
                "Equity",
                "1",
                "1",
                "1",
                "1",
                "1",
                "US",
                "NYSE",
                "USD",
            ],
            [
                float("nan"),
                "USD CASH",
                float("nan"),
                "Cash",
                "1",
                "1",
                "1",
                "1",
                "1",
                "US",
                "-",
                "USD",
            ],
        ]
    )

    result = normalize_holdings(df)

    assert result["holding_ticker"][0] == "AAPL"
    assert pd.isna(result["holding_ticker"][1])
    assert pd.isna(result["sector"][1])
    assert not any(
        value in ("nan", "None", "<NA>")
        for value in result["holding_ticker"]
        if isinstance(value, str)
    )


def test_save_holdings_writes_none_for_values_missing_in_the_parsed_csv():
    # The existing None-handling test feeds a hand-built frame containing None; a real run
    # feeds one from pd.read_csv, where an empty field arrives as NaN instead. Both must end
    # up as SQL NULL, so cover the path that actually happens in production.
    conn = FakeConnection()
    df = normalize_holdings(
        make_raw_df(
            [
                [
                    float("nan"),
                    "USD CASH",
                    float("nan"),
                    "Cash",
                    "500.00",
                    "1.00",
                    "500.00",
                    "500",
                    "1.00",
                    "United States",
                    "-",
                    "USD",
                ]
            ]
        )
    )

    save_holdings(conn, "my_fund", date(2026, 1, 1), df)

    written_row = conn._cursor.copy_rows[0]
    assert written_row[2] is None  # holding_ticker
    assert written_row[4] is None  # sector
