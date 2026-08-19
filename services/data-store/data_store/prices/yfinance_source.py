import logging
import random
import time
from datetime import date

import pandas as pd
import yfinance as yf
from data_store.prices._yfinance_retry import _batch_size, _batches, _is_transient

logger = logging.getLogger(__name__)

_OUTPUT_COLUMNS = ["ticker", "price_date", "open", "high", "low", "close", "adj_close", "volume"]


def _reshape(data: pd.DataFrame) -> pd.DataFrame:
    stacked = data.stack(level="Ticker", future_stack=True).reset_index()
    stacked = stacked.rename(
        columns={
            "Date": "price_date",
            "Ticker": "ticker",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Adj Close": "adj_close",
            "Volume": "volume",
        }
    )
    stacked["price_date"] = pd.to_datetime(stacked["price_date"]).dt.date
    # `future_stack=True` deliberately keeps every (date, ticker) combination, including the
    # all-NaN ones -- and a multi-ticker download's index is the *union* of the batch's trading
    # days, so every ticker gets a row for every other market's holiday, and a ticker with no
    # data at all gets one for every date. Left in, those become rows of NULL prices that make
    # an unpriceable ticker look priced, and (via save_prices' ON CONFLICT DO UPDATE) can
    # overwrite a previously-correct row with NULLs. Rows with *some* OHLC are kept -- a real
    # observation missing only volume is still a real observation.
    stacked = stacked.dropna(subset=["open", "high", "low", "close"], how="all")
    return stacked[_OUTPUT_COLUMNS]


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=_OUTPUT_COLUMNS)


class YFinancePriceSource:
    name = "yfinance"

    def __init__(self, *, max_retries: int = 3, backoff_base_seconds: float = 1.0):
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds

    def fetch(self, tickers: list[str], start_date: date, end_date: date) -> pd.DataFrame:
        if not tickers:
            raise ValueError("No tickers provided.")
        if start_date > end_date:
            raise ValueError("start_date must be before end_date.")

        frames = []
        for batch in _batches(tickers, _batch_size(start_date, end_date)):
            batch_df = self._fetch_batch(batch, start_date, end_date)
            if not batch_df.empty:
                frames.append(batch_df)

        if not frames:
            return _empty_result()
        return pd.concat(frames, ignore_index=True)

    def _fetch_batch(self, tickers: list[str], start_date: date, end_date: date) -> pd.DataFrame:
        attempt = 0
        while True:
            attempt += 1
            try:
                data = yf.download(
                    tickers,
                    start=start_date.isoformat(),
                    end=end_date.isoformat(),
                    progress=False,
                    threads=True,
                    auto_adjust=False,
                )
            except Exception as e:
                if attempt >= self.max_retries or not _is_transient(e):
                    raise
                sleep_seconds = self.backoff_base_seconds * (2 ** (attempt - 1))
                sleep_seconds += random.uniform(0, 0.25)
                logger.warning(
                    "yfinance transient error on attempt %d/%d for %s: %s. Retrying in %.2fs.",
                    attempt,
                    self.max_retries,
                    tickers,
                    e,
                    sleep_seconds,
                )
                time.sleep(sleep_seconds)
                continue

            if data.empty:
                return _empty_result()
            return _reshape(data)
