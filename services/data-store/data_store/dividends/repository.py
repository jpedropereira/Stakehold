from datetime import date

import pandas as pd

_COLUMNS = ["ticker", "ex_date", "amount"]


def save_dividends(conn, source: str, asset_type: str, df: pd.DataFrame) -> int:
    """Upsert into dividend_events via a temp staging table — same COPY + ON CONFLICT DO
    UPDATE pattern as save_prices, for the same reason: successive runs overlap date windows
    and occasional late-corrected special dividends need to overwrite cleanly on rerun."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE tmp_dividend_events (
                ticker VARCHAR(20), ex_date DATE, source VARCHAR(20), asset_type VARCHAR(20),
                amount NUMERIC(18,4)
            ) ON COMMIT DROP
            """)

        copy_columns = ["ticker", "ex_date", "source", "asset_type"] + _COLUMNS[2:]
        with cur.copy(f"COPY tmp_dividend_events ({', '.join(copy_columns)}) FROM STDIN") as copy:
            for _, row in df.iterrows():
                copy.write_row([row["ticker"], row["ex_date"], source, asset_type, row["amount"]])

        cur.execute("""
            INSERT INTO dividend_events (ticker, ex_date, source, asset_type, amount)
            SELECT ticker, ex_date, source, asset_type, amount
            FROM tmp_dividend_events
            ON CONFLICT (ticker, ex_date, source) DO UPDATE SET
                asset_type = EXCLUDED.asset_type,
                amount = EXCLUDED.amount
            """)
    conn.commit()
    return len(df)


def get_earliest_dividend_dates(conn, source: str, tickers: list[str]) -> dict[str, date]:
    """{ticker: earliest stored ex_date} for tickers with at least one row for this source. A
    ticker with no rows at all is absent from the result — callers distinguish "never
    backfilled" from "backfilled starting on date X"."""
    if not tickers:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ticker, MIN(ex_date)
            FROM dividend_events
            WHERE source = %s AND ticker = ANY(%s)
            GROUP BY ticker
            """,
            (source, tickers),
        )
        return {row[0]: row[1] for row in cur.fetchall()}
