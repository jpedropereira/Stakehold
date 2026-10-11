from datetime import date
from typing import Protocol

import pandas as pd


class MarketDataSource(Protocol):
    name: str  # stamped into raw_market_data.source by the repository
    # Tickers the latest fetch() couldn't reach the provider for, as opposed to ones it has no
    # data for. Both are absent from the frame; backfill counts only these as failed.
    unreachable: set[str]

    def fetch(self, tickers: list[str], start_date: date, end_date: date) -> pd.DataFrame:
        """Long-form rows: ticker, price_date, open, high, low, close, adj_close, volume,
        dividends, stock_splits."""
        ...
