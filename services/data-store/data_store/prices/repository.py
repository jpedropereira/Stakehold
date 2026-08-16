import pandas as pd

_COLUMNS = ["ticker", "price_date", "open", "high", "low", "close", "adj_close", "volume"]


def save_prices(conn, source: str, df: pd.DataFrame) -> int:
    """Upsert into daily_ohlcv via a temp staging table — COPY doesn't support ON CONFLICT
    directly, and successive ingestion runs routinely overlap/restate recent dates."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE tmp_daily_ohlcv (
                ticker VARCHAR(20), price_date DATE, source VARCHAR(20),
                open NUMERIC(18,4), high NUMERIC(18,4), low NUMERIC(18,4),
                close NUMERIC(18,4), adj_close NUMERIC(18,4), volume BIGINT
            ) ON COMMIT DROP
            """)

        copy_columns = ["ticker", "price_date", "source"] + _COLUMNS[2:]
        with cur.copy(f"COPY tmp_daily_ohlcv ({', '.join(copy_columns)}) FROM STDIN") as copy:
            for _, row in df.iterrows():
                values = [row["ticker"], row["price_date"], source] + [
                    _cast_value(column, row[column]) for column in _COLUMNS[2:]
                ]
                copy.write_row(values)

        cur.execute("""
            INSERT INTO daily_ohlcv (ticker, price_date, source, open, high, low, close,
                                      adj_close, volume)
            SELECT ticker, price_date, source, open, high, low, close, adj_close, volume
            FROM tmp_daily_ohlcv
            ON CONFLICT (ticker, price_date, source) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                adj_close = EXCLUDED.adj_close,
                volume = EXCLUDED.volume
            """)
    conn.commit()
    return len(df)


def _cast_value(column: str, value):
    if pd.isna(value):
        return None
    if column == "volume":
        return int(value)
    return value
