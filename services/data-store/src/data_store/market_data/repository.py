import logging
from datetime import date

import pandas as pd
from data_store.db.connection import transaction

logger = logging.getLogger(__name__)

_COLUMNS = [
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
]

# Matches the columns tmp_raw_market_data is staged into below. The NUMERIC(18,4)/(18,6) bounds
# come from that precision: 18 digits total, so the integer part tops out at (18 - scale) digits.
# Yahoo's own API occasionally returns a garbage adjclose (confirmed against yfinance/utils.py's
# parse_quotes, which takes Yahoo's "adjclose" field verbatim -- no computation of its own to
# blame) that blows past this; checked before COPY so one bad day drops just that row, not the
# ticker's whole fetched history for the run. volume's bound is BIGINT's own range for the same
# reason -- it isn't NUMERIC, but it can overflow its column just the same.
_NUMERIC_BOUNDS = {
    "open": 10**14,
    "high": 10**14,
    "low": 10**14,
    "close": 10**14,
    "adj_close": 10**14,
    "volume": 2**63,
    "dividends": 10**14,
    "stock_splits": 10**12,
}


def save_raw_market_data(conn, source: str, asset_type: str, df: pd.DataFrame) -> int:
    """Upsert into raw_market_data via a temp staging table — COPY doesn't support ON CONFLICT
    directly, and successive ingestion runs routinely overlap/restate recent dates."""
    with transaction(conn), conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE tmp_raw_market_data (
                ticker VARCHAR(20), price_date DATE, source VARCHAR(20),
                asset_type VARCHAR(20), open NUMERIC(18,4), high NUMERIC(18,4),
                low NUMERIC(18,4), close NUMERIC(18,4), adj_close NUMERIC(18,4),
                volume BIGINT, dividends NUMERIC(18,4), stock_splits NUMERIC(18,6)
            ) ON COMMIT DROP
            """)

        copy_columns = ["ticker", "price_date", "source", "asset_type"] + _COLUMNS[2:]
        with cur.copy(f"COPY tmp_raw_market_data ({', '.join(copy_columns)}) FROM STDIN") as copy:
            for _, row in df.iterrows():
                cast_values = [_cast_value(column, row[column]) for column in _COLUMNS[2:]]
                out_of_range = _first_out_of_range(_COLUMNS[2:], cast_values)
                if out_of_range is not None:
                    column, value = out_of_range
                    logger.warning(
                        "save_raw_market_data: dropping %s %s -- %s=%s is out of range "
                        "for its column, skipping this row.",
                        row["ticker"],
                        row["price_date"],
                        column,
                        value,
                    )
                    continue
                values = [row["ticker"], row["price_date"], source, asset_type] + cast_values
                copy.write_row(values)

        cur.execute("""
            INSERT INTO raw_market_data (ticker, price_date, source, asset_type, open, high,
                                          low, close, adj_close, volume, dividends,
                                          stock_splits)
            SELECT ticker, price_date, source, asset_type, open, high, low, close, adj_close,
                   volume, dividends, stock_splits
            FROM tmp_raw_market_data
            ON CONFLICT (ticker, price_date, source) DO UPDATE SET
                asset_type = EXCLUDED.asset_type,
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                adj_close = EXCLUDED.adj_close,
                volume = EXCLUDED.volume,
                dividends = EXCLUDED.dividends,
                stock_splits = EXCLUDED.stock_splits
            """)
        # rowcount, not len(df): the frame is what we asked the database to write, this is
        # what it actually wrote. They agree today (every staged row either inserts or
        # updates), so this is a correctness-of-source change rather than a fix to a wrong
        # number -- but it's the value a run's summary reports, and it should come from the
        # statement that did the work.
        written = cur.rowcount
    return written


def _cast_value(column: str, value):
    if pd.isna(value):
        return None
    if column == "volume":
        return int(value)
    return value


def _first_out_of_range(columns: list[str], values: list) -> tuple[str, object] | None:
    for column, value in zip(columns, values):
        bound = _NUMERIC_BOUNDS.get(column)
        if bound is not None and value is not None and abs(value) >= bound:
            return column, value
    return None


def get_earliest_dates(conn, source: str, tickers: list[str]) -> dict[str, date]:
    """{ticker: earliest stored price_date} for tickers with at least one row for this
    source. A ticker with no rows at all is absent from the result — callers distinguish
    "never fetched" from "fetched starting on date X"."""
    if not tickers:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ticker, MIN(price_date)
            FROM raw_market_data
            WHERE source = %s AND ticker = ANY(%s)
            GROUP BY ticker
            """,
            (source, tickers),
        )
        return {row[0]: row[1] for row in cur.fetchall()}
