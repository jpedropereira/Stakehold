from datetime import date
from typing import Protocol

import pandas as pd


class DividendSource(Protocol):
    name: str  # stamped into dividend_events.source by the repository

    def fetch(self, tickers: list[str], start_date: date, end_date: date) -> pd.DataFrame:
        """Long-form rows: ticker, ex_date, amount."""
        ...
