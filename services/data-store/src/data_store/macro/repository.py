import logging

import pandas as pd
from shared.db.connection import transaction

logger = logging.getLogger(__name__)

_RAW_COLUMNS = [
    "source",
    "indicator_code",
    "region",
    "observation_date",
    "indicator_type",
    "frequency",
    "value",
    "unit",
]

# NUMERIC(20,6) -> 14 integer digits before the row is rejected by Postgres outright; guarded
# here (mirroring market_data/repository.py's _NUMERIC_BOUNDS) so one bad value from a provider
# drops just that row instead of aborting the whole COPY batch.
_RAW_NUMERIC_BOUNDS = {"value": 10**14}

_RATE_COLUMNS = [
    "source",
    "region",
    "indicator_type",
    "observation_date",
    "frequency",
    "rate_value",
    "calculation_method",
]

# NUMERIC(18,8) -> 10 integer digits, same derivation as returns/repository.py's return columns.
_RATE_NUMERIC_BOUNDS = {"rate_value": 10**10}


def save_raw_macro_indicators(conn, df: pd.DataFrame) -> int:
    """Upsert into raw_macro_indicators via a temp staging table -- same shape as
    market_data.repository.save_raw_market_data: COPY doesn't support ON CONFLICT directly, and
    FRED/Eurostat both restate recent figures (flash -> revised estimates), so overwriting on
    rerun is the normal case."""
    with transaction(conn), conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE tmp_raw_macro_indicators (
                source VARCHAR(20), indicator_code VARCHAR(50), region VARCHAR(10),
                observation_date DATE, indicator_type VARCHAR(20), frequency VARCHAR(10),
                value NUMERIC(20,6), unit VARCHAR(100)
            ) ON COMMIT DROP
            """)

        with cur.copy(
            f"COPY tmp_raw_macro_indicators ({', '.join(_RAW_COLUMNS)}) FROM STDIN"
        ) as copy:
            for _, row in df.iterrows():
                out_of_range = _first_out_of_range(_RAW_NUMERIC_BOUNDS, row)
                if out_of_range is not None:
                    column, value = out_of_range
                    logger.warning(
                        "save_raw_macro_indicators: dropping %s %s %s -- %s=%s is out of range "
                        "for its column, skipping this row.",
                        row["source"],
                        row["indicator_code"],
                        row["observation_date"],
                        column,
                        value,
                    )
                    continue
                copy.write_row([row[column] for column in _RAW_COLUMNS])

        cur.execute("""
            INSERT INTO raw_macro_indicators (source, indicator_code, region, observation_date,
                                               indicator_type, frequency, value, unit)
            SELECT source, indicator_code, region, observation_date, indicator_type, frequency,
                   value, unit
            FROM tmp_raw_macro_indicators
            ON CONFLICT (source, indicator_code, region, observation_date) DO UPDATE SET
                indicator_type = EXCLUDED.indicator_type,
                frequency = EXCLUDED.frequency,
                value = EXCLUDED.value,
                unit = EXCLUDED.unit
            """)
        written = cur.rowcount
    return written


def get_raw_indicators(conn) -> pd.DataFrame:
    """Every row in raw_macro_indicators. calculate_macro_indicator_rates always rebuilds from
    everything stored -- the same "no config, no fetch registry" pattern
    returns/repository.py's get_sources/get_tickers uses -- and this table is a few hundred rows
    at most, nowhere near raw_market_data's 8.95M, so no batching is needed."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(_RAW_COLUMNS)} FROM raw_macro_indicators")
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=_RAW_COLUMNS)


def save_macro_indicator_rates(conn, df: pd.DataFrame) -> int:
    """Upsert into macro_indicator_rates -- same COPY-then-upsert shape as
    returns.repository.save_market_returns, since a full rebuild routinely restates rows it has
    already written."""
    with transaction(conn), conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE tmp_macro_indicator_rates (
                source VARCHAR(20), region VARCHAR(10), indicator_type VARCHAR(20),
                observation_date DATE, frequency VARCHAR(10), rate_value NUMERIC(18,8),
                calculation_method VARCHAR(30)
            ) ON COMMIT DROP
            """)

        with cur.copy(
            f"COPY tmp_macro_indicator_rates ({', '.join(_RATE_COLUMNS)}) FROM STDIN"
        ) as copy:
            for _, row in df.iterrows():
                out_of_range = _first_out_of_range(_RATE_NUMERIC_BOUNDS, row)
                if out_of_range is not None:
                    column, value = out_of_range
                    logger.warning(
                        "save_macro_indicator_rates: dropping %s %s %s -- %s=%s is out of range "
                        "for its column, skipping this row.",
                        row["source"],
                        row["region"],
                        row["observation_date"],
                        column,
                        value,
                    )
                    continue
                copy.write_row([row[column] for column in _RATE_COLUMNS])

        cur.execute("""
            INSERT INTO macro_indicator_rates (source, region, indicator_type, observation_date,
                                                frequency, rate_value, calculation_method)
            SELECT source, region, indicator_type, observation_date, frequency, rate_value,
                   calculation_method
            FROM tmp_macro_indicator_rates
            ON CONFLICT (source, region, indicator_type, observation_date) DO UPDATE SET
                frequency = EXCLUDED.frequency,
                rate_value = EXCLUDED.rate_value,
                calculation_method = EXCLUDED.calculation_method,
                calculated_at = now()
            """)
        written = cur.rowcount
    return written


def _first_out_of_range(bounds: dict[str, int], row) -> tuple[str, object] | None:
    for column, bound in bounds.items():
        value = row[column]
        if value is not None and not pd.isna(value) and abs(value) >= bound:
            return column, value
    return None
