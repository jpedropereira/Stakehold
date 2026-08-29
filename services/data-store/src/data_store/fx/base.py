from datetime import date
from typing import Protocol

import pandas as pd


class FxRateSource(Protocol):
    name: str  # stamped into fx_rates.source by the repository

    def fetch(self, pairs: list[tuple[str, str]], start_date: date, end_date: date) -> pd.DataFrame:
        """Long-form rows: base_currency, quote_currency, rate_date, rate."""
        ...
