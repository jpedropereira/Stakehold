import logging

import pandas as pd
from data_store.db.connection import transaction

logger = logging.getLogger(__name__)

_PRICE_COLUMNS = ["ticker", "price_date", "close", "dividends", "stock_splits", "volume"]

_COLUMNS = [
    "ticker",
    "price_date",
    "split_adjusted_close",
    "future_split_factor",
    "price_return",
    "dividend_return",
    "simple_return",
    "log_return",
]

# Mirrors market_data/repository.py's _NUMERIC_BOUNDS: 18 total digits per column, so the
# integer part tops out at (18 - scale) digits. Guards the same failure mode that bit
# raw_market_data (f9583ad, a2264cb) -- one bad computed value shouldn't fail a whole batch's
# COPY.
_NUMERIC_BOUNDS = {
    "split_adjusted_close": 10**14,
    "future_split_factor": 10**15,
    "price_return": 10**10,
    "dividend_return": 10**10,
    "simple_return": 10**10,
    "log_return": 10**10,
}

# The matching *lower* bound, and only for future_split_factor -- every other column here is a
# return, for which exactly 0 is a flat day rather than a defect. A split factor is a product of
# positive ratios, so it is never legitimately 0: a 0 means the value underflowed the column's
# scale on the way in, and a stored 0 is indistinguishable from a real factor while silently
# zeroing any as-traded price derived from it (BMPS.MI, 3,814 rows, under the old NUMERIC(18,6)).
# No fixed scale is underflow-proof -- NUMERIC(30,15) puts the threshold far out of reach of real
# cumulative reverse splits, and this guard makes crossing it loud rather than silent.
_NUMERIC_MIN_MAGNITUDE = {
    "future_split_factor": 1e-15,
}


def get_sources(conn) -> list[str]:
    """Every distinct `source` present in raw_market_data -- what data actually exists, not
    what the market-data source registry knows how to fetch. Keeps working for a retired
    source that still has stored rows, and needs no import of that registry."""
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT source FROM raw_market_data ORDER BY source")
        return [row[0] for row in cur.fetchall()]


def get_tickers(conn, source: str) -> list[str]:
    """Every distinct ticker stored for `source` -- the resolved universe for a
    calculate-returns run, with no config or fetch-registry involved."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ticker FROM raw_market_data WHERE source = %s ORDER BY ticker",
            (source,),
        )
        return [row[0] for row in cur.fetchall()]


def iter_price_series(conn, source: str, tickers: list[str] | None, batch_size: int):
    """Yields one DataFrame (ticker, price_date, close, dividends, stock_splits, volume) per
    batch of up to `batch_size` tickers, ordered by (ticker, price_date).

    Returns are purely per-ticker, so batching this way keeps memory bounded on the full
    9M-row table without any cross-batch bookkeeping. `tickers=None` discovers every ticker
    stored for `source`.
    """
    if tickers is None:
        tickers = get_tickers(conn, source)

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ticker, price_date, close, dividends, stock_splits, volume
                FROM raw_market_data
                WHERE source = %s AND ticker = ANY(%s)
                ORDER BY ticker, price_date
                """,
                (source, batch),
            )
            rows = cur.fetchall()
        yield pd.DataFrame(rows, columns=_PRICE_COLUMNS)


def ticker_exists(conn, ticker: str, source: str) -> bool:
    """Whether `ticker` has any ingested row for `source` in raw_market_data -- the ingested
    universe, not market_returns, since a ticker can be ingested before returns are computed
    for it. This is the API's existence primitive; there is no ticker reference table."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM raw_market_data WHERE ticker = %s AND source = %s)",
            (ticker, source),
        )
        return cur.fetchall()[0][0]


def get_returns_date_range(conn, ticker: str, source: str) -> tuple:
    """(earliest, latest) price_date in market_returns for (ticker, source), or (None, None) if
    returns haven't been computed for it yet -- an aggregate over zero rows still returns one
    row of NULLs, not an empty result."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT MIN(price_date), MAX(price_date)
            FROM market_returns
            WHERE ticker = %s AND source = %s
            """,
            (ticker, source),
        )
        return cur.fetchall()[0]


def get_return_series(conn, ticker: str, source: str, start, end) -> list[tuple]:
    """Raw rows (price_date, split_adjusted_close, future_split_factor, price_return,
    dividend_return, simple_return, log_return) for one ticker over a date range, ordered by
    date. The four return columns come back exactly as stored -- including NULL together on a
    ticker's first date -- for the read API to serialize verbatim rather than coerce to 0."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT price_date, split_adjusted_close, future_split_factor,
                   price_return, dividend_return, simple_return, log_return
            FROM market_returns
            WHERE ticker = %s AND source = %s AND price_date BETWEEN %s AND %s
            ORDER BY price_date
            """,
            (ticker, source, start, end),
        )
        return cur.fetchall()


def save_market_returns(conn, source: str, df: pd.DataFrame) -> int:
    """Upsert into market_returns via a temp staging table, mirroring
    market_data.repository.save_raw_market_data's shape -- COPY doesn't support ON CONFLICT
    directly, and a full rebuild routinely restates rows it has already written."""
    with transaction(conn), conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE tmp_market_returns (
                ticker VARCHAR(20), price_date DATE, source VARCHAR(20),
                split_adjusted_close NUMERIC(18,4), future_split_factor NUMERIC(30,15),
                price_return NUMERIC(18,8), dividend_return NUMERIC(18,8),
                simple_return NUMERIC(18,8), log_return NUMERIC(18,8)
            ) ON COMMIT DROP
            """)

        copy_columns = ["ticker", "price_date", "source"] + _COLUMNS[2:]
        with cur.copy(f"COPY tmp_market_returns ({', '.join(copy_columns)}) FROM STDIN") as copy:
            for _, row in df.iterrows():
                cast_values = [_cast_value(row[column]) for column in _COLUMNS[2:]]
                out_of_range = _first_out_of_range(_COLUMNS[2:], cast_values)
                if out_of_range is not None:
                    column, value = out_of_range
                    logger.warning(
                        "save_market_returns: dropping %s %s -- %s=%s is out of range for "
                        "its column, skipping this row.",
                        row["ticker"],
                        row["price_date"],
                        column,
                        value,
                    )
                    continue
                values = [row["ticker"], row["price_date"], source] + cast_values
                copy.write_row(values)

        cur.execute("""
            INSERT INTO market_returns (ticker, price_date, source, split_adjusted_close,
                                         future_split_factor, price_return, dividend_return,
                                         simple_return, log_return)
            SELECT ticker, price_date, source, split_adjusted_close, future_split_factor,
                   price_return, dividend_return, simple_return, log_return
            FROM tmp_market_returns
            ON CONFLICT (ticker, price_date, source) DO UPDATE SET
                split_adjusted_close = EXCLUDED.split_adjusted_close,
                future_split_factor = EXCLUDED.future_split_factor,
                price_return = EXCLUDED.price_return,
                dividend_return = EXCLUDED.dividend_return,
                simple_return = EXCLUDED.simple_return,
                log_return = EXCLUDED.log_return,
                calculated_at = now()
            """)
        written = cur.rowcount
    return written


def _cast_value(value):
    return None if pd.isna(value) else value


def _first_out_of_range(columns: list[str], values: list) -> tuple[str, object] | None:
    for column, value in zip(columns, values):
        if value is None:
            continue
        bound = _NUMERIC_BOUNDS.get(column)
        if bound is not None and abs(value) >= bound:
            return column, value
        floor = _NUMERIC_MIN_MAGNITUDE.get(column)
        if floor is not None and abs(value) < floor:
            return column, value
    return None
