from datetime import date

import pandas as pd
from data_store.holdings.column_mapping import COLUMN_MAPPING


def normalize_holdings(df: pd.DataFrame) -> pd.DataFrame:
    """Rename the raw (lowercased CSV column) DataFrame to DB column names and cast dtypes."""
    rename_map = {spec["csv_col"]: db_col for db_col, spec in COLUMN_MAPPING.items()}
    missing = set(rename_map.keys()) - set(df.columns)
    if missing:
        raise ValueError(f"Holdings CSV missing expected column(s): {sorted(missing)}")

    df = df[list(rename_map.keys())].rename(columns=rename_map)

    for db_col, spec in COLUMN_MAPPING.items():
        if spec["dtype"] == "float":
            df[db_col] = df[db_col].astype(str).str.replace(",", "", regex=False)
            df[db_col] = pd.to_numeric(df[db_col], errors="coerce")
        else:
            # Safe only because pandas >= 3.0 (pinned in pyproject.toml) makes astype(str)
            # yield the NA-preserving `str` dtype: an empty CSV field stays NA rather than
            # becoming the literal "nan"/"None" it turned into under 2.x. save_holdings maps
            # NA to SQL NULL via pd.isna(), which would not detect those strings -- a cash
            # line's blank ticker would land in the DB as the text "nan" and then pass
            # get_latest_tickers' `holding_ticker IS NOT NULL` filter on its way to being
            # priced. See test_normalize_holdings_keeps_missing_string_values_missing.
            df[db_col] = df[db_col].astype(str)

    return df


def save_holdings(conn, fund_key: str, as_of_date: date, df: pd.DataFrame) -> int:
    """Delete-then-insert holdings for (fund_key, as_of_date) — a full snapshot replacement."""
    columns = ["fund_key", "as_of_date"] + list(COLUMN_MAPPING.keys())
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM etf_holdings WHERE fund_key = %s AND as_of_date = %s",
            (fund_key, as_of_date),
        )
        with cur.copy(f"COPY etf_holdings ({', '.join(columns)}) FROM STDIN") as copy:
            for _, row in df.iterrows():
                values = [None if pd.isna(row[c]) else row[c] for c in COLUMN_MAPPING.keys()]
                copy.write_row([fund_key, as_of_date] + values)
    conn.commit()
    return len(df)


def get_latest_tickers(conn, fund_key: str) -> list[tuple[str, str | None]]:
    """(holding_ticker, exchange) pairs from the most recent as_of_date snapshot for fund_key.
    Excludes cash/derivative lines (no ticker, or a futures/FX contract code rather than a
    tradeable equity ticker yfinance can price). `exchange` is the raw scraped string --
    resolving it into a yfinance suffix is the caller's job, not this function's."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT holding_ticker, exchange FROM etf_holdings
            WHERE fund_key = %s
              AND as_of_date = (
                  SELECT MAX(as_of_date) FROM etf_holdings WHERE fund_key = %s
              )
              AND holding_ticker IS NOT NULL
              AND asset_class ILIKE 'equity'
            """,
            (fund_key, fund_key),
        )
        return [(row[0], row[1]) for row in cur.fetchall()]


def get_latest_snapshot(conn, fund_key: str) -> tuple[date, list[tuple]] | None:
    """(as_of_date, rows) for fund_key's most recent snapshot, or None when it has none.

    Every line, unlike get_latest_tickers: cash, FX and futures lines too, so the weights sum to
    the whole fund. Each row is (holding_ticker, holding_name, sector, asset_class, location,
    exchange, currency, weight_percent), raw as stored, heaviest first. The tiebreaks after the
    weight only make the order deterministic."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT as_of_date, holding_ticker, holding_name, sector, asset_class, location,
                   exchange, currency, weight_percent
            FROM etf_holdings
            WHERE fund_key = %s
              AND as_of_date = (
                  SELECT MAX(as_of_date) FROM etf_holdings WHERE fund_key = %s
              )
            ORDER BY weight_percent DESC NULLS LAST, holding_ticker NULLS LAST, holding_name, id
            """,
            (fund_key, fund_key),
        )
        rows = cur.fetchall()
    if not rows:
        return None
    return rows[0][0], [tuple(row[1:]) for row in rows]


def get_latest_as_of_dates(conn) -> dict[str, date]:
    """Each stored fund's most recent snapshot date. A fund with no snapshot is absent."""
    with conn.cursor() as cur:
        cur.execute("SELECT fund_key, MAX(as_of_date) FROM etf_holdings GROUP BY fund_key")
        return {fund_key: as_of_date for fund_key, as_of_date in cur.fetchall()}


def get_listing_currencies(conn) -> list[tuple[str, str | None, str]]:
    """(holding_ticker, exchange, currency) for every equity listing any snapshot of any fund
    has held, each from the newest snapshot that lists it, newest first.

    Keyed by (ticker, exchange), not ticker: iShares reuses one raw symbol on different
    exchanges ("DG" is both Vinci in Paris and Dollar General in New York). Every snapshot, not
    each fund's latest, so a holding that has left the index still has a currency for the
    history already stored. Same row filter as get_latest_tickers, so only a listing that could
    have been priced is offered. Matching a listing to a priced ticker needs ticker resolution,
    which is the caller's job, as it is for get_latest_tickers."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT holding_ticker, exchange, currency FROM (
                SELECT DISTINCT ON (holding_ticker, exchange)
                    holding_ticker, exchange, currency, as_of_date, fund_key
                FROM etf_holdings
                WHERE holding_ticker IS NOT NULL
                  AND asset_class ILIKE 'equity'
                  AND currency IS NOT NULL
                ORDER BY holding_ticker, exchange, as_of_date DESC, fund_key
            ) AS listing
            ORDER BY as_of_date DESC, fund_key, holding_ticker, exchange
            """)
        return [(row[0], row[1], row[2]) for row in cur.fetchall()]
