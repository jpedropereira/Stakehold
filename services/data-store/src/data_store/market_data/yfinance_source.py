import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import pandas as pd
import yfinance as yf
from data_store.yfinance_retry import _is_transient, _is_unreachable

logger = logging.getLogger(__name__)

# yfinance defaults to swallowing exceptions -- returning an empty frame and logging internally
# via its own logger -- rather than raising them (confirmed against the installed yfinance
# 1.6.0's scrapers/history.py, which gates every non-YFRateLimitError exception behind
# `YfConfig.debug.hide_exceptions`, default True). Left alone, a transient network failure and
# "this ticker genuinely has no data" would be indistinguishable from the return value, and the
# retry loop below would never fire. This is yfinance's own documented replacement for the
# deprecated `raise_errors` parameter, and mirrors what yf.download() does internally for its
# own per-ticker fetches (temporarily flips the equivalent flag for the duration of a download).
yf.config.debug.hide_exceptions = False

_OUTPUT_COLUMNS = [
    "ticker",
    "price_date",
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "volume",
    "dividends",
    "stock_splits",
]

_COLUMN_RENAMES = {
    "Date": "price_date",
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
    "Dividends": "dividends",
    "Stock Splits": "stock_splits",
}

# Caps concurrent Ticker.history() calls per fetch() invocation. There is no bulk endpoint being
# protected here -- Ticker.history() is always one HTTP request per ticker, and so was
# yf.download() under the hood (confirmed: it fans out to one _download_one call per ticker,
# threaded) -- so this is purely a concurrency cap, mirroring the spirit of yf.download()'s own
# `min(len(tickers), cpu_count()*2)` heuristic without being unbounded.
_MAX_WORKERS = 10


def _reshape_one(ticker: str, data: pd.DataFrame) -> pd.DataFrame:
    reshaped = data.reset_index().rename(columns=_COLUMN_RENAMES)
    reshaped["ticker"] = ticker
    reshaped["price_date"] = pd.to_datetime(reshaped["price_date"]).dt.date
    # A day with no close isn't a finished observation. When a request ends before the current
    # session, Yahoo appends the in-progress session's bar without a close, stamped with the last
    # day requested (seen 2026-10-13: ASML.AS's live 10-13 bar came back as 10-12). Storing it
    # would leave a mis-dated row the daily ingest's catch-up never re-fetches, and
    # returns/calculator.py can't anchor the next day's return to it. Left out, the date stays
    # missing and a later run fetches it. A day missing only volume (or with a zero
    # dividend/split, which yfinance always fills rather than omitting) is still real and kept.
    reshaped = reshaped.dropna(subset=["close"])
    return reshaped[_OUTPUT_COLUMNS]


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=_OUTPUT_COLUMNS)


class YFinanceMarketDataSource:
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
        # The tickers the latest fetch() gave up on because yfinance didn't answer. They are
        # missing from the frame exactly as a ticker with no data is, so this is the only way a
        # caller can tell the two apart. Replaced on every fetch().
        self.unreachable: set[str] = set()

    def fetch(self, tickers: list[str], start_date: date, end_date: date) -> pd.DataFrame:
        if not tickers:
            raise ValueError("No tickers provided.")
        if start_date > end_date:
            raise ValueError("start_date must be before end_date.")

        frames = []
        self.unreachable = set()
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(tickers))) as pool:
            futures = {
                pool.submit(self._fetch_one, ticker, start_date, end_date): ticker
                for ticker in tickers
            }
            for future in as_completed(futures):
                result = future.result()
                if result is not None and not result.empty:
                    frames.append(result)

        if not frames:
            return _empty_result()
        return pd.concat(frames, ignore_index=True)

    def _fetch_one(self, ticker: str, start_date: date, end_date: date) -> pd.DataFrame | None:
        """One ticker's whole retry loop. Never raises for a data-source failure -- a
        permanently-failing ticker (after exhausting retries, or a non-transient error) is
        logged and simply omitted from fetch()'s result (and, if yfinance never answered,
        recorded in `unreachable`), so one bad ticker in a group can't cost
        every other ticker fetched alongside it. Only fetch()'s own input validation above can
        raise out of this class."""
        attempt = 0
        while True:
            attempt += 1
            try:
                data = yf.Ticker(ticker).history(
                    start=start_date.isoformat(),
                    end=end_date.isoformat(),
                    auto_adjust=False,
                    actions=True,
                )
                if data.empty:
                    return None
                return _reshape_one(ticker, data)
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
                if _is_unreachable(e):
                    # set.add from a worker thread is atomic; fetch() reads it after the pool
                    # has finished.
                    self.unreachable.add(ticker)
                return None
