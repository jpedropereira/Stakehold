"""Pure arithmetic normalizing raw_macro_indicators into comparable year-over-year rates -- no
I/O, mirroring returns/calculator.py's split from returns/repository.py.
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)

_OUTPUT_COLUMNS = [
    "source",
    "region",
    "indicator_type",
    "observation_date",
    "frequency",
    "rate_value",
    "calculation_method",
]

SOURCE_REPORTED = "source_reported"
YOY_PCT_CHANGE = "yoy_pct_change"

# Eurostat's chosen dimension slices (see eurostat_source.EUROSTAT_DATASETS) are already a
# year-over-year rate by construction -- prc_hicp_manr's own name is literal, and namq_10_gdp's
# CLV_PCH_SM unit is defined as "percentage change on same period of previous year". FRED's
# CPIAUCSL/GDPC1 are raw levels, so their YoY rate has to be computed here instead.
_SOURCE_REPORTED_ALREADY = {"eurostat"}

# Rows back to compare against for a YoY change, assuming a regular, gap-free series -- the same
# assumption returns/calculator.py's shift(1) makes for adjacent trading days.
_YOY_LAG = {"monthly": 12, "quarterly": 4}


def compute_indicator_rates(raw: pd.DataFrame) -> pd.DataFrame:
    """raw: source, indicator_code, region, observation_date, indicator_type, frequency, value,
    unit (any row order, as read back from raw_macro_indicators).

    One output row per input row for which a rate is available -- a source-reported rate passes
    through unchanged; a level series drops its first `_YOY_LAG[frequency]` observations per
    (source, region, indicator_type) group, since there is no prior-year value to compare the
    earliest ones against.
    """
    df = raw.sort_values(["source", "region", "indicator_type", "observation_date"]).reset_index(
        drop=True
    )
    # psycopg 3 returns decimal.Decimal for NUMERIC columns, which makes the pandas column
    # object-dtype and breaks shift/division -- see returns/calculator.py for the same cast.
    df["value"] = df["value"].astype("float64")

    frames = []
    for (source, region, indicator_type), group in df.groupby(
        ["source", "region", "indicator_type"], sort=False
    ):
        group = group.reset_index(drop=True)
        # macro_indicator_rates' own key is (source, region, indicator_type, observation_date) --
        # no indicator_code column -- so two distinct series sharing an indicator_type here would
        # silently interleave into one YoY computation and collide on upsert. Every source today
        # keeps that 1:1 (see FRED_SERIES / EUROSTAT_DATASETS), so this is a loud, skip-the-group
        # failure rather than a schema change, which needs sign-off per data-schema.md.
        indicator_codes = group["indicator_code"].unique()
        if len(indicator_codes) > 1:
            logger.warning(
                "compute_indicator_rates: %s/%s/%s has more than one indicator_code (%s) -- "
                "macro_indicator_rates has no column to keep them apart, skipping this group "
                "rather than silently blending them.",
                source,
                region,
                indicator_type,
                ", ".join(sorted(indicator_codes)),
            )
            continue
        if source in _SOURCE_REPORTED_ALREADY:
            group["rate_value"] = group["value"]
            group["calculation_method"] = SOURCE_REPORTED
        else:
            frequency = group["frequency"].iloc[0]
            lag = _YOY_LAG.get(frequency)
            if lag is None:
                logger.warning(
                    "compute_indicator_rates: %s/%s/%s has unrecognized frequency %r, skipping.",
                    source,
                    region,
                    indicator_type,
                    frequency,
                )
                continue
            group["rate_value"] = (group["value"] / group["value"].shift(lag) - 1) * 100
            group["calculation_method"] = YOY_PCT_CHANGE
            group = group.dropna(subset=["rate_value"])
        frames.append(group)

    if not frames:
        return pd.DataFrame(columns=_OUTPUT_COLUMNS)
    result = pd.concat(frames, ignore_index=True)
    return result[_OUTPUT_COLUMNS]
