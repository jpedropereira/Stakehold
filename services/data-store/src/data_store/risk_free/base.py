from datetime import date
from typing import Protocol

import pandas as pd


class RiskFreeRateSource(Protocol):
    name: str  # stamped into risk_free_rates.source by the repository
    series: tuple[str, ...]  # every series this source serves, by our own name ('estr', ...)
    # Series that stopped being published, with their last date. Nothing new can appear for
    # them, so once stored they need no refetch -- see pipeline.ingest_risk_free_rates.
    discontinued: dict[str, date]

    def fetch(self, series: str, start_date: date, end_date: date) -> pd.DataFrame:
        """Long-form rows: series, rate_date, rate -- one series per call, both dates inclusive,
        so one series failing costs only that series. Raises once a series can't be fetched."""
        ...
