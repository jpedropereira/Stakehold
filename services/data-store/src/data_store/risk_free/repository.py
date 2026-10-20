import logging
from datetime import date

import pandas as pd
from shared.db.connection import transaction

logger = logging.getLogger(__name__)

# NUMERIC(8,4) -> 4 integer digits. Checked before COPY, as fx/repository.py does, so one
# garbage value drops just that row rather than the whole series' fetched history.
_RATE_BOUND = 10**4


def save_risk_free_rates(conn, source: str, df: pd.DataFrame) -> int:
    """Upsert into risk_free_rates via a temp staging table -- COPY doesn't support ON CONFLICT
    directly, and the daily window refetches days already stored."""
    with transaction(conn), conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE tmp_risk_free_rates (
                series VARCHAR(20), rate_date DATE, source VARCHAR(20), rate NUMERIC(8,4)
            ) ON COMMIT DROP
            """)

        with cur.copy(
            "COPY tmp_risk_free_rates (series, rate_date, source, rate) FROM STDIN"
        ) as copy:
            for _, row in df.iterrows():
                if abs(row["rate"]) >= _RATE_BOUND:
                    logger.warning(
                        "save_risk_free_rates: dropping %s %s -- rate=%s is out of range for "
                        "its column, skipping this row.",
                        row["series"],
                        row["rate_date"],
                        row["rate"],
                    )
                    continue
                copy.write_row([row["series"], row["rate_date"], source, row["rate"]])

        cur.execute("""
            INSERT INTO risk_free_rates (series, rate_date, source, rate)
            SELECT series, rate_date, source, rate
            FROM tmp_risk_free_rates
            ON CONFLICT (series, rate_date, source) DO UPDATE SET
                rate = EXCLUDED.rate
            """)
        # rowcount, not len(df): what the database wrote, after any rows dropped above.
        written = cur.rowcount
    return written


def get_latest_risk_free_dates(conn, source: str, series: list[str]) -> dict[str, date]:
    """{series: latest stored rate_date} for each series with at least one row for this source.
    A series with nothing stored is absent, which is how the ingest tells "never fetched" apart
    from "fetched up to date X"."""
    if not series:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT series, MAX(rate_date)
            FROM risk_free_rates
            WHERE source = %s AND series = ANY(%s)
            GROUP BY series
            """,
            (source, list(series)),
        )
        return {row[0]: row[1] for row in cur.fetchall()}
