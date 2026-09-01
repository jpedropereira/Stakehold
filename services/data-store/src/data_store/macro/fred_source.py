import logging
import random
import time

import pandas as pd
from data_store.macro.retry import is_transient
from fredapi import Fred

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

# FRED series pulled today. Both are raw levels, not rates -- CPIAUCSL is a price index, GDPC1
# is a chained-dollar output level -- landed here verbatim per raw_macro_indicators' "faithful
# copy" contract; macro/calculator.py derives a comparable year-over-year rate from each
# downstream. `indicator_type` names the concept the series feeds, not the shape of this
# particular value -- the `unit` column is what tells a reader that.
FRED_SERIES = {
    "CPIAUCSL": {
        "indicator_type": "inflation",
        "frequency": "monthly",
        "unit": "Index 1982-1984=100, not seasonally adjusted",
    },
    "GDPC1": {
        "indicator_type": "growth",
        "frequency": "quarterly",
        "unit": "Billions of chained 2017 dollars, seasonally adjusted annual rate",
    },
}


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=_OUTPUT_COLUMNS)


class FredMacroIndicatorSource:
    name = "fred"

    def __init__(self, *, api_key: str, max_retries: int = 3, backoff_base_seconds: float = 1.0):
        self._client = Fred(api_key=api_key)
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds

    def fetch(self, regions: list[str]) -> pd.DataFrame:
        if regions != ["US"]:
            raise ValueError(f"FredMacroIndicatorSource only serves region 'US', got {regions!r}")

        frames = []
        for series_id, meta in FRED_SERIES.items():
            series = self._fetch_one(series_id)
            if series is None or series.empty:
                continue
            frame = series.reset_index()
            frame.columns = ["observation_date", "value"]
            frame["observation_date"] = pd.to_datetime(frame["observation_date"]).dt.date
            frame["indicator_code"] = series_id
            frame["region"] = "US"
            frame["indicator_type"] = meta["indicator_type"]
            frame["frequency"] = meta["frequency"]
            frame["unit"] = meta["unit"]
            frames.append(frame[_OUTPUT_COLUMNS])

        if not frames:
            return _empty_result()
        return pd.concat(frames, ignore_index=True)

    def _fetch_one(self, series_id: str) -> pd.Series | None:
        """One series' whole retry loop. Never raises -- a permanently-failing series is logged
        and omitted from fetch()'s result, mirroring market_data/yfinance_source.py's per-ticker
        isolation, so one bad series can't cost the other."""
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._client.get_series(series_id)
            except Exception as e:
                if is_transient(e) and attempt < self.max_retries:
                    sleep_seconds = self.backoff_base_seconds * (2 ** (attempt - 1))
                    sleep_seconds += random.uniform(0, 0.25)
                    logger.warning(
                        "fred: transient error on attempt %d/%d for %s: %s. Retrying in %.2fs.",
                        attempt,
                        self.max_retries,
                        series_id,
                        e,
                        sleep_seconds,
                    )
                    time.sleep(sleep_seconds)
                    continue
                logger.warning(
                    "fred: %s failed after %d attempt(s), skipping: %s",
                    series_id,
                    attempt,
                    e,
                    exc_info=True,
                )
                return None
