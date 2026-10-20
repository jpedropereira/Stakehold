"""eur_returns/fetch.py: fetches each ticker's info, prices and FX series from data-store
concurrently, and isolates a failure to the tickers it belongs to.

Nothing is computed here, so the expected values are the fake's canned answers and the calls it
recorded.
"""

import threading
from datetime import date
from decimal import Decimal

from portfolio.datastore_client.base import FxPoint, PricePoint, TickerInfo
from portfolio.errors import DataStoreApiError
from portfolio.eur_returns import fetch
from portfolio.eur_returns.fetch import Fetched, LeftOut

START = date(2020, 1, 1)
END = date(2026, 1, 9)

EUR = TickerInfo(exists=True, has_returns=True, currency="EUR", fx_currency="EUR")
USD = TickerInfo(exists=True, has_returns=True, currency="USD", fx_currency="USD")
GBP = TickerInfo(exists=True, has_returns=True, currency="GBp", fx_currency="GBP")

PRICES = [PricePoint(date(2026, 1, 5), Decimal("100"), None)]
USD_FX = [FxPoint(date(2026, 1, 5), Decimal("1.10"))]
GBP_FX = [FxPoint(date(2026, 1, 5), Decimal("0.85"))]


class _FakeDataStoreClient:
    """Canned answers per ticker (or per currency, for FX). An Exception instance as the answer is
    raised instead. Calls are recorded; the fetch calls from worker threads, hence the lock."""

    def __init__(self, *, info=None, prices=None, fx=None):
        self._info = info or {}
        self._prices = prices or {}
        self._fx = fx or {}
        self._lock = threading.Lock()
        self.calls = []

    def _answer(self, call, answer):
        with self._lock:
            self.calls.append(call)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def get_ticker_info(self, ticker):
        return self._answer(("info", ticker), self._info[ticker])

    def get_price_series(self, ticker, start, end):
        return self._answer(("prices", ticker, start, end), self._prices[ticker])

    def get_fx_rate_series(self, base, quote, start, end):
        return self._answer(("fx", base, quote, start, end), self._fx[quote])


def _calls(client, kind):
    return sorted(call for call in client.calls if call[0] == kind)


def test_a_eur_ticker_is_fetched_without_fx_and_a_usd_one_with_its_currencys_series():
    client = _FakeDataStoreClient(
        info={"A": EUR, "B": USD}, prices={"A": PRICES, "B": PRICES}, fx={"USD": USD_FX}
    )

    result = fetch.fetch_series(client, ["A", "B"], START, END)

    assert result == {
        "A": Fetched(info=EUR, prices=PRICES, fx=None),
        "B": Fetched(info=USD, prices=PRICES, fx=USD_FX),
    }
    assert _calls(client, "prices") == [("prices", "A", START, END), ("prices", "B", START, END)]
    assert _calls(client, "fx") == [("fx", "EUR", "USD", START, END)]


def test_each_ticker_is_fetched_from_its_own_start_and_fx_from_its_currencys_earliest():
    b_start, b2_start = date(2022, 3, 1), date(2021, 6, 1)
    client = _FakeDataStoreClient(
        info={"B": USD, "B2": USD}, prices={"B": PRICES, "B2": PRICES}, fx={"USD": USD_FX}
    )

    fetch.fetch_series(client, ["B", "B2"], {"B": b_start, "B2": b2_start}, END)

    assert _calls(client, "prices") == [
        ("prices", "B", b_start, END),
        ("prices", "B2", b2_start, END),
    ]
    assert _calls(client, "fx") == [("fx", "EUR", "USD", b2_start, END)]


def test_two_tickers_in_one_currency_share_one_fx_fetch():
    client = _FakeDataStoreClient(
        info={"B": USD, "B2": USD}, prices={"B": PRICES, "B2": PRICES}, fx={"USD": USD_FX}
    )

    fetch.fetch_series(client, ["B", "B2"], START, END)

    assert len(_calls(client, "fx")) == 1


def test_an_untracked_ticker_is_not_tracked_and_its_prices_are_never_fetched():
    client = _FakeDataStoreClient(info={"X": TickerInfo(exists=False, has_returns=False)})

    result = fetch.fetch_series(client, ["X"], START, END)

    assert result == {"X": LeftOut(fetch.NOT_TRACKED, None)}
    assert _calls(client, "prices") == []


def test_a_ticker_without_returns_yet_has_no_history_and_its_prices_are_never_fetched():
    client = _FakeDataStoreClient(info={"X": TickerInfo(exists=True, has_returns=False)})

    result = fetch.fetch_series(client, ["X"], START, END)

    assert result == {"X": LeftOut(fetch.NO_HISTORY, None)}
    assert _calls(client, "prices") == []


def test_a_ticker_without_an_fx_currency_has_no_fx_rate():
    info = TickerInfo(exists=True, has_returns=True, currency="XYZ", fx_currency=None)
    client = _FakeDataStoreClient(info={"X": info})

    result = fetch.fetch_series(client, ["X"], START, END)

    assert result == {"X": LeftOut(fetch.NO_FX_RATE, None)}
    assert client.calls == [("info", "X")]


def test_a_failed_ticker_info_call_fails_only_that_ticker():
    error = DataStoreApiError("boom")
    client = _FakeDataStoreClient(info={"A": EUR, "X": error}, prices={"A": PRICES})

    result = fetch.fetch_series(client, ["A", "X"], START, END)

    assert result == {
        "A": Fetched(info=EUR, prices=PRICES, fx=None),
        "X": LeftOut(fetch.FAILED, error),
    }


def test_a_failed_price_call_fails_only_that_ticker():
    error = DataStoreApiError("boom")
    client = _FakeDataStoreClient(info={"A": EUR, "X": EUR}, prices={"A": PRICES, "X": error})

    result = fetch.fetch_series(client, ["A", "X"], START, END)

    assert result["A"] == Fetched(info=EUR, prices=PRICES, fx=None)
    assert result["X"] == LeftOut(fetch.FAILED, error)


def test_a_failed_fx_call_fails_every_ticker_in_that_currency_and_only_those():
    error = DataStoreApiError("boom")
    client = _FakeDataStoreClient(
        info={"A": EUR, "B": USD, "B2": USD, "G": GBP},
        prices={t: PRICES for t in ("A", "B", "B2", "G")},
        fx={"USD": error, "GBP": GBP_FX},
    )

    result = fetch.fetch_series(client, ["A", "B", "B2", "G"], START, END)

    assert result == {
        "A": Fetched(info=EUR, prices=PRICES, fx=None),
        "B": LeftOut(fetch.FAILED, error),
        "B2": LeftOut(fetch.FAILED, error),
        "G": Fetched(info=GBP, prices=PRICES, fx=GBP_FX),
    }


def test_no_tickers_fetches_nothing():
    client = _FakeDataStoreClient()

    assert fetch.fetch_series(client, [], START, END) == {}
    assert client.calls == []
