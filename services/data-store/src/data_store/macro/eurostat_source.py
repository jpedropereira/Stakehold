import logging
import random
import time
from datetime import date

import eurostat
import pandas as pd
from data_store.macro.retry import is_transient

logger = logging.getLogger(__name__)

_OUTPUT_COLUMNS = [
    "indicator_code",
    "region",
    "observation_date",
    "indicator_type",
    "frequency",
    "value",
    "unit",
]

# Verified against Eurostat's live SDMX API (2026-09-02) by querying each dataset with these
# exact dimension codes and confirming both the label and the returned values -- not guessed:
#   prc_hicp_manr: unit=RCH_A ("Annual rate of change"), coicop=CP00 ("All-items HICP")
#   namq_10_gdp:   unit=CLV_PCH_SM ("Chain linked volumes, percentage change compared to same
#                  period in previous year"), na_item=B1GQ ("GDP at market prices"),
#                  s_adj=SCA ("Seasonally and calendar adjusted data")
# Each spec isolates one already-YoY-comparable rate series per dataset -- prc_hicp_manr's own
# name is literal ("annual rate of change"); namq_10_gdp needs all three pinned or the query
# would also match quarter-on-quarter, non-seasonally-adjusted, or non-GDP rows.
EUROSTAT_DATASETS = {
    "prc_hicp_manr": {
        "indicator_type": "inflation",
        "frequency": "monthly",
        "filter_pars": {"unit": ["RCH_A"], "coicop": ["CP00"]},
        "unit_label": "Annual rate of change (HICP, all-items)",
    },
    "namq_10_gdp": {
        "indicator_type": "growth",
        "frequency": "quarterly",
        "filter_pars": {"unit": ["CLV_PCH_SM"], "na_item": ["B1GQ"], "s_adj": ["SCA"]},
        "unit_label": (
            "Chain-linked volumes, % change on same quarter of previous year "
            "(seasonally and calendar adjusted)"
        ),
    },
}

# Our own region code -> Eurostat's own geo code. 'EA' is a stable alias we control; Eurostat's
# euro-area aggregate code itself has changed as membership grew (EA19 -> EA20 ...), so this is
# the one place that needs updating when it changes again, not every caller. Every other region
# code is passed through as the literal Eurostat geo code, which does not always match the ISO2
# code (Greece is 'EL', not 'GR') -- see config/macro_regions.yaml.example.
_REGION_TO_GEO = {"EA": "EA20"}

_DIMENSION_COLUMNS = {"freq", "unit", "coicop", "na_item", "s_adj", "geo"}


class EurostatMacroIndicatorSource:
    name = "eurostat"

    def __init__(self, *, max_retries: int = 3, backoff_base_seconds: float = 1.0):
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds

    def fetch(self, regions: list[str]) -> pd.DataFrame:
        if not regions:
            raise ValueError("No regions provided.")

        geo_codes = [_REGION_TO_GEO.get(region, region) for region in regions]
        region_by_geo = dict(zip(geo_codes, regions))

        frames = []
        for dataset_code, meta in EUROSTAT_DATASETS.items():
            data = self._fetch_one(dataset_code, geo_codes, meta["filter_pars"])
            if data is None or data.empty:
                continue
            frame = _reshape(data, dataset_code, meta, region_by_geo)
            if not frame.empty:
                frames.append(frame)

        if not frames:
            return pd.DataFrame(columns=_OUTPUT_COLUMNS)
        return pd.concat(frames, ignore_index=True)

    def _fetch_one(
        self, dataset_code: str, geo_codes: list[str], filter_pars: dict
    ) -> pd.DataFrame | None:
        """One dataset's whole retry loop. Never raises -- a permanently-failing dataset is
        logged and omitted from fetch()'s result, so one bad dataset can't cost the other."""
        pars = dict(filter_pars, geo=geo_codes)
        attempt = 0
        while True:
            attempt += 1
            try:
                return eurostat.get_data_df(dataset_code, flags=False, filter_pars=pars)
            except Exception as e:
                if is_transient(e) and attempt < self.max_retries:
                    sleep_seconds = self.backoff_base_seconds * (2 ** (attempt - 1))
                    sleep_seconds += random.uniform(0, 0.25)
                    logger.warning(
                        "eurostat: transient error on attempt %d/%d for %s: %s. Retrying in "
                        "%.2fs.",
                        attempt,
                        self.max_retries,
                        dataset_code,
                        e,
                        sleep_seconds,
                    )
                    time.sleep(sleep_seconds)
                    continue
                logger.warning(
                    "eurostat: %s failed after %d attempt(s), skipping: %s",
                    dataset_code,
                    attempt,
                    e,
                    exc_info=True,
                )
                return None


def _reshape(
    data: pd.DataFrame, dataset_code: str, meta: dict, region_by_geo: dict[str, str]
) -> pd.DataFrame:
    """The eurostat package returns one dimension column per requested non-geo dimension (named
    after the dimension itself, e.g. 'unit', 'na_item') plus one geo/time column and one column
    per time period. Confirmed against a live call: the geo column is actually named
    'geo\\TIME_PERIOD' (Eurostat's raw TSV header packs the last dimension and "TIME_PERIOD"
    into one field), not plain 'geo' -- so it's found by prefix rather than exact match. Every
    other column not in the known dimension-name set is a time period."""
    geo_column = next((c for c in data.columns if c.startswith("geo")), None)
    if geo_column is None:
        logger.warning("eurostat: %s response has no geo dimension column, skipping.", dataset_code)
        return pd.DataFrame(columns=_OUTPUT_COLUMNS)

    time_columns = [c for c in data.columns if c not in _DIMENSION_COLUMNS and c != geo_column]
    rows = []
    for _, row in data.iterrows():
        region = region_by_geo.get(row[geo_column])
        if region is None:
            continue
        for time_column in time_columns:
            value = row[time_column]
            if pd.isna(value):
                continue
            observation_date = _parse_period(str(time_column))
            if observation_date is None:
                logger.warning(
                    "eurostat: %s has an unrecognized time period column %r, skipping it.",
                    dataset_code,
                    time_column,
                )
                continue
            rows.append(
                {
                    "indicator_code": dataset_code,
                    "region": region,
                    "observation_date": observation_date,
                    "indicator_type": meta["indicator_type"],
                    "frequency": meta["frequency"],
                    "value": float(value),
                    "unit": meta["unit_label"],
                }
            )
    return pd.DataFrame(rows, columns=_OUTPUT_COLUMNS)


def _parse_period(label: str) -> date | None:
    """Eurostat time-period column labels, as period-start dates -- matching the convention
    FRED's own series index already uses. Handles every format confirmed against a live call:
    'YYYY-Q#' (quarterly, e.g. namq_10_gdp's '2024-Q1'), 'YYYY-MM' (monthly, e.g.
    prc_hicp_manr's '2024-01'), and 'YYYY' (annual, for any future dataset added here)."""
    label = label.strip()
    try:
        if "Q" in label:
            year_str, quarter_str = label.split("-Q")
            year, quarter = int(year_str), int(quarter_str)
            return date(year, (quarter - 1) * 3 + 1, 1)
        if "-" in label:
            year_str, month_str = label.split("-")
            return date(int(year_str), int(month_str), 1)
        return date(int(label), 1, 1)
    except ValueError, IndexError:
        return None
