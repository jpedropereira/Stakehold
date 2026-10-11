"""The yfinance symbol lookup: which provider response maps to which outcome.

The responses faked here are the ones recorded in docs/architecture.md, "How yfinance answers a
symbol lookup (verified)" -- in particular, an unknown symbol raises an HTTP 404 that is an
OSError, which the shared transient classifier would otherwise call a network blip.
"""

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest
from data_store.tracked_tickers.base import (
    OUTCOME_FOUND,
    OUTCOME_NOT_FOUND,
    OUTCOME_UNAVAILABLE,
    OUTCOME_UNSUPPORTED,
)
from data_store.tracked_tickers.yfinance_source import YFinanceTickerLookupSource
from yfinance.exceptions import YFPricesMissingError, YFRateLimitError, YFTzMissingError

ASML_METADATA = {
    "symbol": "ASML.AS",
    "currency": "EUR",
    "exchangeName": "AMS",
    "fullExchangeName": "Amsterdam",
    "instrumentType": "EQUITY",
    "longName": "ASML Holding N.V.",
    "shortName": "ASML HOLDING",
}


def history_frame(rows=3):
    idx = pd.DatetimeIndex([date(2026, 10, d) for d in range(1, rows + 1)], name="Date")
    return pd.DataFrame({"Close": [1.0] * rows}, index=idx)


class HttpError(OSError):
    """Shaped like curl_cffi's HTTPError: an OSError carrying the response."""

    def __init__(self, status_code):
        super().__init__(f"HTTP Error {status_code}: ")
        self.response = SimpleNamespace(status_code=status_code)


def fake_ticker(response, metadata=None):
    calls = []

    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol
            self.history_metadata = metadata or {}

        def history(self, **kwargs):
            calls.append((self.symbol, kwargs))
            if isinstance(response, Exception):
                raise response
            return response

    FakeTicker.calls = calls
    return FakeTicker


@pytest.fixture
def use_ticker(monkeypatch):
    def install(response, metadata=None):
        fake = fake_ticker(response, metadata)
        monkeypatch.setattr("data_store.tracked_tickers.yfinance_source.yf.Ticker", fake)
        return fake

    return install


def test_name_is_yfinance():
    assert YFinanceTickerLookupSource().name == "yfinance"


def test_a_listing_with_prices_is_found_with_its_details(use_ticker):
    use_ticker(history_frame(), ASML_METADATA)

    result = YFinanceTickerLookupSource().lookup("asml.as")

    assert result.outcome == OUTCOME_FOUND
    assert result.ticker == "ASML.AS"  # the provider's canonical spelling, not what was typed
    assert result.display_name == "ASML Holding N.V."
    assert result.currency == "EUR"
    assert result.exchange == "Amsterdam"
    assert result.asset_type == "equity"


def test_asks_for_a_month_with_a_timeout(use_ticker):
    fake = use_ticker(history_frame(), ASML_METADATA)

    YFinanceTickerLookupSource(timeout_seconds=7).lookup("ASML.AS")

    assert fake.calls == [("ASML.AS", {"period": "1mo", "timeout": 7})]


def test_display_name_falls_back_to_the_short_name(use_ticker):
    use_ticker(history_frame(), {**ASML_METADATA, "longName": None, "shortName": "Gold Dec 26"})

    assert YFinanceTickerLookupSource().lookup("GC=F").display_name == "Gold Dec 26"


def test_display_name_falls_back_to_the_ticker_when_both_names_are_missing(use_ticker):
    use_ticker(history_frame(), {**ASML_METADATA, "longName": None, "shortName": None})

    assert YFinanceTickerLookupSource().lookup("ASML.AS").display_name == "ASML.AS"


@pytest.mark.parametrize(
    "instrument_type, asset_type",
    [
        ("EQUITY", "equity"),
        ("ETF", "etf"),
        ("CRYPTOCURRENCY", "crypto"),
        ("MUTUALFUND", "fund"),
        ("FUTURE", "commodity"),
    ],
)
def test_holdable_instrument_types_map_to_asset_types(use_ticker, instrument_type, asset_type):
    use_ticker(history_frame(), {**ASML_METADATA, "instrumentType": instrument_type})

    result = YFinanceTickerLookupSource().lookup("X")

    assert (result.outcome, result.asset_type) == (OUTCOME_FOUND, asset_type)


@pytest.mark.parametrize("instrument_type", ["INDEX", "CURRENCY", "OPTION", None])
def test_instruments_that_cannot_be_held_are_unsupported(use_ticker, instrument_type):
    use_ticker(history_frame(), {**ASML_METADATA, "instrumentType": instrument_type})

    result = YFinanceTickerLookupSource().lookup("^GSPC")

    assert result.outcome == OUTCOME_UNSUPPORTED
    assert result.asset_type is None
    # Still described, so the caller can say *what* it found and why it can't be added.
    assert result.display_name == "ASML Holding N.V."


def test_a_404_is_not_found_not_a_network_error(use_ticker):
    use_ticker(HttpError(404))

    result = YFinanceTickerLookupSource().lookup("NOTAREALTICKER123")

    assert result.outcome == OUTCOME_NOT_FOUND
    assert result.ticker == "NOTAREALTICKER123"


@pytest.mark.parametrize(
    "error",
    [
        YFPricesMissingError("SIVB", "(period=1mo)"),
        YFTzMissingError("SIVB"),
    ],
)
def test_a_missing_ticker_error_is_not_found(use_ticker, error):
    use_ticker(error)

    assert YFinanceTickerLookupSource().lookup("SIVB").outcome == OUTCOME_NOT_FOUND


def test_an_empty_response_is_not_found(use_ticker):
    use_ticker(pd.DataFrame(), ASML_METADATA)

    assert YFinanceTickerLookupSource().lookup("ASML.AS").outcome == OUTCOME_NOT_FOUND


@pytest.mark.parametrize(
    "error",
    [YFRateLimitError(), HttpError(503), TimeoutError("timed out"), ConnectionError("reset")],
)
def test_throttling_and_network_errors_are_unavailable(use_ticker, error):
    use_ticker(error)

    result = YFinanceTickerLookupSource().lookup("ASML.AS")

    assert result.outcome == OUTCOME_UNAVAILABLE


def test_a_programming_error_is_not_swallowed(use_ticker):
    use_ticker(KeyError("symbol"))

    with pytest.raises(KeyError):
        YFinanceTickerLookupSource().lookup("ASML.AS")
