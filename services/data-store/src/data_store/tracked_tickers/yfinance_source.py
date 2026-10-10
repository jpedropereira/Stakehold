"""Symbol lookup against yfinance. The responses this classifies were observed, not inferred --
see docs/architecture.md, "How yfinance answers a symbol lookup (verified)"."""

import logging

import yfinance as yf
from data_store.tracked_tickers.base import (
    OUTCOME_FOUND,
    OUTCOME_NOT_FOUND,
    OUTCOME_UNAVAILABLE,
    OUTCOME_UNSUPPORTED,
    LookupResult,
)
from data_store.yfinance_retry import _is_transient
from yfinance.exceptions import YFRateLimitError, YFTickerMissingError

logger = logging.getLogger(__name__)

# Same setting market_data/yfinance_source.py makes, for the same reason: left at its default,
# yfinance swallows errors and returns an empty frame, making "throttled" look like "no such
# symbol". Set here too so the lookup doesn't depend on which module happened to import first.
yf.config.debug.hide_exceptions = False

# yfinance's `instrumentType` -> config_loader.VALID_ASSET_TYPES. Anything not listed (INDEX,
# CURRENCY, OPTION, ...) can't be held in a portfolio and is reported as unsupported.
_ASSET_TYPES = {
    "EQUITY": "equity",
    "ETF": "etf",
    "CRYPTOCURRENCY": "crypto",
    "MUTUALFUND": "fund",
    "FUTURE": "commodity",
}

# A month rather than a few days, so a listing suspended for a week or two still counts as found.
_PERIOD = "1mo"


def _is_http_404(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) == 404


class YFinanceTickerLookupSource:
    name = "yfinance"

    def __init__(self, *, timeout_seconds: float = 10.0):
        self.timeout_seconds = timeout_seconds

    def lookup(self, ticker: str) -> LookupResult:
        yf_ticker = yf.Ticker(ticker)
        try:
            data = yf_ticker.history(period=_PERIOD, timeout=self.timeout_seconds)
        except YFTickerMissingError:
            return _not_found(ticker)
        except YFRateLimitError as e:
            logger.warning("ticker lookup: %s -- yfinance is rate limiting: %s", ticker, e)
            return _unavailable(ticker)
        except Exception as e:
            # The 404 check must come first: curl_cffi's HTTPError is an OSError, which
            # _is_transient counts as a network failure, so an unknown symbol would otherwise
            # read as "try again later".
            if _is_http_404(e):
                return _not_found(ticker)
            if _is_transient(e):
                logger.warning("ticker lookup: %s -- yfinance didn't answer: %s", ticker, e)
                return _unavailable(ticker)
            raise

        if data.empty:
            return _not_found(ticker)

        metadata = yf_ticker.history_metadata or {}
        asset_type = _ASSET_TYPES.get(metadata.get("instrumentType"))
        return LookupResult(
            outcome=OUTCOME_FOUND if asset_type else OUTCOME_UNSUPPORTED,
            ticker=metadata.get("symbol") or ticker,
            display_name=metadata.get("longName") or metadata.get("shortName") or ticker,
            currency=metadata.get("currency"),
            exchange=metadata.get("fullExchangeName") or metadata.get("exchangeName"),
            asset_type=asset_type,
        )


def _not_found(ticker: str) -> LookupResult:
    return LookupResult(OUTCOME_NOT_FOUND, ticker, None, None, None, None)


def _unavailable(ticker: str) -> LookupResult:
    return LookupResult(OUTCOME_UNAVAILABLE, ticker, None, None, None, None)
