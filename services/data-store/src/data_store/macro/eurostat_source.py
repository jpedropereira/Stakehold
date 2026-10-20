import logging
from datetime import date

import eurostat
import pandas as pd
from data_store.provider_retry import with_retries

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

        result = (
            pd.concat(frames, ignore_index=True)
            if frames
            else pd.DataFrame(columns=_OUTPUT_COLUMNS)
        )
        # A region requested but never present in any dataset's response (a config typo, e.g.
        # the ISO2 code 'GR' instead of Eurostat's own 'EL' for Greece) would otherwise vanish
        # from every future ingest with no sign anything was wrong -- the run still reports
        # status: ok on the regions that did match.
        missing_regions = set(regions) - set(result["region"])
        if missing_regions:
            logger.warning(
                "eurostat: requested region(s) %s never appeared in any dataset response -- "
                "check the geo code in config/macro_regions.yaml.",
                ", ".join(sorted(missing_regions)),
            )
        return result

    def _fetch_one(
        self, dataset_code: str, geo_codes: list[str], filter_pars: dict
    ) -> pd.DataFrame | None:
        """One dataset's whole retry loop. A permanently-failing dataset is logged and omitted
        from fetch()'s result, so one bad dataset can't cost the other."""
        pars = dict(filter_pars, geo=geo_codes)
        return with_retries(
            lambda: eurostat.get_data_df(dataset_code, flags=False, filter_pars=pars),
            description=dataset_code,
            log_prefix="eurostat",
            max_retries=self.max_retries,
            backoff_base_seconds=self.backoff_base_seconds,
        )


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
    long = data.melt(
        id_vars=[geo_column], value_vars=time_columns, var_name="period", value_name="value"
    )
    long["region"] = long[geo_column].map(region_by_geo)
    long = long.dropna(subset=["region", "value"])

    # One _parse_period call per distinct period label (not per row) so the "unrecognized
    # period" warning still fires once per bad column, matching the row-by-row loop this
    # replaces rather than once per row that column produced.
    parsed_by_period = {period: _parse_period(str(period)) for period in long["period"].unique()}
    for period, parsed in parsed_by_period.items():
        if parsed is None:
            logger.warning(
                "eurostat: %s has an unrecognized time period column %r, skipping it.",
                dataset_code,
                period,
            )
    long["observation_date"] = long["period"].map(parsed_by_period)
    long = long.dropna(subset=["observation_date"])

    long["indicator_code"] = dataset_code
    long["indicator_type"] = meta["indicator_type"]
    long["frequency"] = meta["frequency"]
    long["unit"] = meta["unit_label"]
    long["value"] = long["value"].astype(float)
    return long[_OUTPUT_COLUMNS].reset_index(drop=True)


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
