import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import pandas as pd
import yfinance as yf
from data_store.yfinance_retry import _is_transient

logger = logging.getLogger(__name__)

_OUTPUT_COLUMNS = ["base_currency", "quote_currency", "rate_date", "rate"]

# Caps concurrent Ticker.history() calls per fetch() invocation -- same rationale as
# market_data/yfinance_source.py's _MAX_WORKERS, and there are far fewer pairs than tickers in
# practice, so this ceiling is rarely the binding constraint.
_MAX_WORKERS = 10


def _ticker_for(base: str, quote: str) -> str:
    return f"{base}{quote}=X"


def _reshape_one(base: str, quote: str, data: pd.DataFrame) -> pd.DataFrame:
    reshaped = data.reset_index().rename(columns={"Date": "rate_date", "Close": "rate"})
    reshaped["base_currency"] = base
    reshaped["quote_currency"] = quote
    reshaped["rate_date"] = pd.to_datetime(reshaped["rate_date"]).dt.date
    # A day with no quoted close is not a real observation -- FX has no OHLC-all-missing vs.
    # volume-only-missing distinction to preserve the way market_data does, since only Close is
    # kept here.
    reshaped = reshaped.dropna(subset=["rate"])
    return reshaped[_OUTPUT_COLUMNS]


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=_OUTPUT_COLUMNS)


class YFinanceFxRateSource:
    name = "yfinance"

    def __init__(
        self,
        *,
        max_retries: int = 3,
        backoff_base_seconds: float = 1.0,
        max_workers: int = _MAX_WORKERS,
    ):
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.max_workers = max_workers

    def fetch(self, pairs: list[tuple[str, str]], start_date: date, end_date: date) -> pd.DataFrame:
        if not pairs:
            raise ValueError("No currency pairs provided.")
        if start_date > end_date:
            raise ValueError("start_date must be before end_date.")

        frames = []
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(pairs))) as pool:
            futures = {
                pool.submit(self._fetch_one, base, quote, start_date, end_date): (base, quote)
                for base, quote in pairs
            }
            for future in as_completed(futures):
                result = future.result()
                if result is not None and not result.empty:
                    frames.append(result)

        if not frames:
            return _empty_result()
        return pd.concat(frames, ignore_index=True)

    def _fetch_one(
        self, base: str, quote: str, start_date: date, end_date: date
    ) -> pd.DataFrame | None:
        """One pair's whole retry loop. Never raises for a data-source failure -- a
        permanently-failing pair (after exhausting retries, or a non-transient error) is logged
        and simply omitted from fetch()'s result, so one bad pair can't cost every other pair
        fetched alongside it. Only fetch()'s own input validation above can raise out of this
        class."""
        ticker = _ticker_for(base, quote)
        attempt = 0
        while True:
            attempt += 1
            try:
                data = yf.Ticker(ticker).history(
                    start=start_date.isoformat(), end=end_date.isoformat()
                )
                if data.empty:
                    return None
                return _reshape_one(base, quote, data)
            except Exception as e:
                if _is_transient(e) and attempt < self.max_retries:
                    sleep_seconds = self.backoff_base_seconds * (2 ** (attempt - 1))
                    sleep_seconds += random.uniform(0, 0.25)
                    logger.warning(
                        "yfinance transient error on attempt %d/%d for %s: %s. Retrying in "
                        "%.2fs.",
                        attempt,
                        self.max_retries,
                        ticker,
                        e,
                        sleep_seconds,
                    )
                    time.sleep(sleep_seconds)
                    continue
                logger.warning(
                    "yfinance: %s failed after %d attempt(s), skipping: %s",
                    ticker,
                    attempt,
                    e,
                    exc_info=True,
                )
                return None
