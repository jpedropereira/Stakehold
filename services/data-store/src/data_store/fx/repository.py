import logging
from datetime import date

import pandas as pd
from shared.db.connection import transaction

logger = logging.getLogger(__name__)

_COLUMNS = ["base_currency", "quote_currency", "rate_date", "rate"]

# NUMERIC(18,8): 18 digits total, 8 fractional, so the integer part tops out at 10 digits --
# same garbage-value defense as market_data/repository.py's _NUMERIC_BOUNDS, checked before COPY
# so one bad day drops just that row, not the pair's whole fetched history for the run.
_RATE_BOUND = 10**10


def save_fx_rates(conn, source: str, df: pd.DataFrame) -> int:
    """Upsert into fx_rates via a temp staging table -- COPY doesn't support ON CONFLICT
    directly, and successive ingestion runs routinely overlap/restate recent dates."""
    with transaction(conn), conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE tmp_fx_rates (
                base_currency VARCHAR(3), quote_currency VARCHAR(3), rate_date DATE,
                source VARCHAR(20), rate NUMERIC(18,8)
            ) ON COMMIT DROP
            """)

        with cur.copy(
            "COPY tmp_fx_rates (base_currency, quote_currency, rate_date, source, rate) "
            "FROM STDIN"
        ) as copy:
            for _, row in df.iterrows():
                rate = _cast_rate(row["rate"])
                if rate is not None and abs(rate) >= _RATE_BOUND:
                    logger.warning(
                        "save_fx_rates: dropping %s/%s %s -- rate=%s is out of range for its "
                        "column, skipping this row.",
                        row["base_currency"],
                        row["quote_currency"],
                        row["rate_date"],
                        rate,
                    )
                    continue
                copy.write_row(
                    [row["base_currency"], row["quote_currency"], row["rate_date"], source, rate]
                )

        cur.execute("""
            INSERT INTO fx_rates (base_currency, quote_currency, rate_date, source, rate)
            SELECT base_currency, quote_currency, rate_date, source, rate
            FROM tmp_fx_rates
            ON CONFLICT (base_currency, quote_currency, rate_date, source) DO UPDATE SET
                rate = EXCLUDED.rate
            """)
        # rowcount, not len(df): the frame is what we asked the database to write, this is what
        # it actually wrote -- see market_data/repository.py's save_raw_market_data for the same
        # rationale.
        written = cur.rowcount
    return written


def _cast_rate(value):
    if pd.isna(value):
        return None
    return value


def get_earliest_fx_dates(
    conn, source: str, pairs: list[tuple[str, str]]
) -> dict[tuple[str, str], date]:
    """{(base_currency, quote_currency): earliest stored rate_date} for pairs with at least one
    row for this source. A pair with no rows at all is absent from the result -- callers
    distinguish "never fetched" from "fetched starting on date X"."""
    if not pairs:
        return {}
    placeholders = ", ".join(["(%s, %s)"] * len(pairs))
    params = [source]
    for base, quote in pairs:
        params.extend([base, quote])
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT base_currency, quote_currency, MIN(rate_date)
            FROM fx_rates
            WHERE source = %s AND (base_currency, quote_currency) IN ({placeholders})
            GROUP BY base_currency, quote_currency
            """,
            params,
        )
        return {(row[0], row[1]): row[2] for row in cur.fetchall()}


def get_rate_series(
    conn, base: str, quote: str, source: str, start: date, end: date
) -> list[tuple]:
    """Raw (rate_date, rate) rows for one stored (base, quote) pair over a date range, ordered
    by date. Only ever finds rows for a pair that was actually fetched -- there is no cross-rate
    computation at this layer, see read_flows.fx_rate_series for that."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT rate_date, rate
            FROM fx_rates
            WHERE base_currency = %s AND quote_currency = %s AND source = %s
              AND rate_date BETWEEN %s AND %s
            ORDER BY rate_date
            """,
            (base, quote, source, start, end),
        )
        return cur.fetchall()


def get_dates_with_rate(conn, currency: str, source: str, start: date, end: date) -> list[date]:
    """Every rate_date where `currency` appears on either side of a stored pair -- the trading-
    calendar proxy for an identity (base == quote) request, which has no row of its own since a
    currency is never paired against itself in fx_rates."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT rate_date
            FROM fx_rates
            WHERE (base_currency = %s OR quote_currency = %s) AND source = %s
              AND rate_date BETWEEN %s AND %s
            ORDER BY rate_date
            """,
            (currency, currency, source, start, end),
        )
        return [row[0] for row in cur.fetchall()]
