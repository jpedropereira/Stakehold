from datetime import date
from io import StringIO

import pandas as pd
import requests
from data_store.provider_retry import with_retries

_OUTPUT_COLUMNS = ["series", "rate_date", "rate"]

_BASE_URL = "https://data-api.ecb.europa.eu/service/data"

# Our series name -> the ECB Data Portal's "<dataflow>/<key>". Verified against the live API on
# 2026-10-20 by fetching each one, not guessed:
#   estr:  "Euro short-term rate, Volume-weighted trimmed mean rate", UNIT_MEASURE=PC,
#          2019-10-01 -> today; 2019-10-01 = -0.549, the ECB's first published €STR.
#   eonia: EONIA, 1999-01-04 -> 2021-12-31; 2019-10-01 = -0.464 = €STR + 0.085, the fixed
#          spread EONIA was redefined as from that day.
# Both values are an annualised percentage, ACT/360.
ECB_SERIES = {
    "estr": "EST/B.EU000A2X2A25.WT",
    "eonia": "EON/D.EONIA_TO.RATE",
}

# EONIA's last publication. Its key still answers, with this as the last row.
DISCONTINUED = {"eonia": date(2021, 12, 31)}

# Generous: a full EONIA history is one ~6,000-row response.
_TIMEOUT_SECONDS = 60


class EcbRiskFreeRateSource:
    name = "ecb"
    series = tuple(ECB_SERIES)
    discontinued = DISCONTINUED

    def __init__(
        self,
        *,
        session=None,
        max_retries: int = 3,
        backoff_base_seconds: float = 1.0,
    ):
        self._session = session or requests.Session()
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds

    def fetch(self, series: str, start_date: date, end_date: date) -> pd.DataFrame:
        if series not in ECB_SERIES:
            raise ValueError(f"Unknown ECB series {series!r}; known: {', '.join(ECB_SERIES)}.")

        # with_retries never raises: it logs and returns None once it gives up. A parsed
        # response is never None, so None here means exactly "this series failed", which
        # fetch() turns back into an exception for the flow to count.
        body = with_retries(
            lambda: self._get(series, start_date, end_date),
            description=series,
            log_prefix="ecb",
            max_retries=self.max_retries,
            backoff_base_seconds=self.backoff_base_seconds,
        )
        if body is None:
            raise RuntimeError(f"ecb: {series} could not be fetched (see the warning above).")
        return _parse(series, body)

    def _get(self, series: str, start_date: date, end_date: date) -> str:
        response = self._session.get(
            f"{_BASE_URL}/{ECB_SERIES[series]}",
            params={
                "format": "csvdata",
                "detail": "dataonly",
                "startPeriod": start_date.isoformat(),
                "endPeriod": end_date.isoformat(),
            },
            timeout=_TIMEOUT_SECONDS,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            # requests.HTTPError is an OSError, which provider_retry.is_transient treats as
            # retryable. A 4xx other than 429 (the portal answers a wrong key with 404) will fail
            # the same way every time, so it's re-raised as a type that isn't retried.
            if 400 <= response.status_code < 500 and response.status_code != 429:
                raise ValueError(str(e)) from e
            raise
        return response.text


def _parse(series: str, body: str) -> pd.DataFrame:
    """The portal's `csvdata` with `detail=dataonly`: one row per published day, the date in
    TIME_PERIOD and the rate in OBS_VALUE. A range with no published days comes back as HTTP 200
    with an empty body, not a header-only CSV, so that's an empty frame here, not an error."""
    if not body.strip():
        return pd.DataFrame(columns=_OUTPUT_COLUMNS)
    data = pd.read_csv(StringIO(body), usecols=["TIME_PERIOD", "OBS_VALUE"])
    data = data.dropna(subset=["OBS_VALUE"])
    return pd.DataFrame(
        {
            "series": series,
            "rate_date": pd.to_datetime(data["TIME_PERIOD"]).dt.date,
            "rate": data["OBS_VALUE"].astype(float),
        },
        columns=_OUTPUT_COLUMNS,
    ).reset_index(drop=True)
