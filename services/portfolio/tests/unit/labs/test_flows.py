"""labs/flows.py: fetches each asset's ticker info, prices and FX from data-store, isolates a
failure to the asset it belongs to, and hands the rest to labs/calculator.py.

The calculator's own arithmetic is tested in test_calculator.py, so these tests compare against
compute_backtest run directly on the same inputs rather than re-deriving figures by hand.
"""

import logging
import threading
from datetime import date
from decimal import Decimal

from portfolio.datastore_client.base import FxPoint, PricePoint, TickerInfo
from portfolio.errors import DataStoreApiError
from portfolio.labs import flows
from portfolio.labs.calculator import AssetInput, Period, compute_backtest

TODAY = date(2026, 1, 9)

EUR = TickerInfo(exists=True, has_returns=True, currency="EUR", fx_currency="EUR")
USD = TickerInfo(exists=True, has_returns=True, currency="USD", fx_currency="USD")


def _prices(*returns):
    """One point a day from 2026-01-05, the first with a NULL return as data-store stores it."""
    return [
        PricePoint(date(2026, 1, 5 + i), Decimal("100"), None if r is None else Decimal(r))
        for i, r in enumerate((None, *returns))
    ]


USD_FX = [
    FxPoint(date(2026, 1, 5), Decimal("1.10")),
    FxPoint(date(2026, 1, 7), Decimal("1.00")),
]


class _FakeDataStoreClient:
    """Canned answers per ticker (or per currency, for FX). An Exception instance as the answer is
    raised instead. Calls are recorded; the flow calls from worker threads, hence the lock."""

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


def _run(client, mix, period=Period.MAX):
    return flows.run_backtest(client, [(t, Decimal(w)) for t, w in mix], period, today=TODAY)


def _statuses(outcome):
    return [(s.ticker, s.status, s.reason) for s in outcome.statuses]


def test_included_assets_get_the_calculators_result_and_statuses_in_input_order():
    client = _FakeDataStoreClient(
        info={"B": USD, "A": EUR},
        prices={"B": _prices("0.01", "0.02", "0"), "A": _prices("0.05", "-0.02", "0.01")},
        fx={"USD": USD_FX},
    )

    outcome = _run(client, [("B", "60"), ("A", "40")])

    expected = compute_backtest(
        [
            AssetInput("B", Decimal("60"), _prices("0.01", "0.02", "0"), USD_FX),
            AssetInput("A", Decimal("40"), _prices("0.05", "-0.02", "0.01"), None),
        ],
        growth_period=Period.MAX,
    )
    assert outcome.result == expected
    assert _statuses(outcome) == [("B", "included", None), ("A", "included", None)]


def test_whole_histories_are_fetched_from_the_epoch_to_today():
    client = _FakeDataStoreClient(
        info={"B": USD}, prices={"B": _prices("0.01")}, fx={"USD": USD_FX}
    )

    _run(client, [("B", "1")])

    assert ("prices", "B", flows.EPOCH, TODAY) in client.calls
    assert ("fx", "EUR", "USD", flows.EPOCH, TODAY) in client.calls


def test_an_untracked_ticker_is_not_tracked_and_its_prices_are_never_fetched():
    client = _FakeDataStoreClient(
        info={"A": EUR, "X": TickerInfo(exists=False, has_returns=False)},
        prices={"A": _prices("0.01")},
    )

    outcome = _run(client, [("A", "1"), ("X", "1")])

    assert _statuses(outcome) == [("A", "included", None), ("X", "not_tracked", "not tracked")]
    assert not any(call[:2] == ("prices", "X") for call in client.calls)
    assert [row.ticker for row in outcome.result.assets] == ["A"]


def test_a_ticker_without_returns_yet_has_no_history_and_its_prices_are_never_fetched():
    onboarding = TickerInfo(exists=True, has_returns=False, status="backfilling", currency="EUR")
    client = _FakeDataStoreClient(info={"A": EUR, "N": onboarding}, prices={"A": _prices("0.01")})

    outcome = _run(client, [("A", "1"), ("N", "1")])

    assert _statuses(outcome)[1] == ("N", "no_history", "no history yet")
    assert not any(call[:2] == ("prices", "N") for call in client.calls)


def test_an_empty_price_series_has_no_history():
    client = _FakeDataStoreClient(info={"A": EUR, "E": EUR}, prices={"A": _prices("0.01"), "E": []})

    outcome = _run(client, [("A", "1"), ("E", "1")])

    assert _statuses(outcome)[1] == ("E", "no_history", "no history yet")


def test_a_non_eur_ticker_without_an_fx_currency_has_no_fx_rate():
    unknown = TickerInfo(exists=True, has_returns=True, currency=None, fx_currency=None)
    client = _FakeDataStoreClient(
        info={"A": EUR, "U": unknown}, prices={"A": _prices("0.01"), "U": _prices("0.01")}
    )

    outcome = _run(client, [("A", "1"), ("U", "1")])

    assert _statuses(outcome)[1] == ("U", "no_fx_rate", "no FX rate")
    assert not any(call[0] == "fx" for call in client.calls)


def test_an_empty_fx_series_has_no_fx_rate():
    client = _FakeDataStoreClient(
        info={"A": EUR, "B": USD},
        prices={"A": _prices("0.01"), "B": _prices("0.01")},
        fx={"USD": []},
    )

    outcome = _run(client, [("A", "1"), ("B", "1")])

    assert _statuses(outcome)[1] == ("B", "no_fx_rate", "no FX rate")


def test_a_failed_ticker_info_call_fails_only_that_asset():
    client = _FakeDataStoreClient(
        info={"A": EUR, "F": DataStoreApiError("boom")}, prices={"A": _prices("0.01")}
    )

    outcome = _run(client, [("A", "1"), ("F", "1")])

    assert _statuses(outcome) == [
        ("A", "included", None),
        ("F", "failed", "couldn't reach data-store"),
    ]
    assert outcome.result.assets[0].weight == Decimal("1")


def test_a_failed_price_call_fails_only_that_asset_and_the_rest_are_reweighted():
    client = _FakeDataStoreClient(
        info={"A": EUR, "C": EUR, "F": EUR},
        prices={"A": _prices("0.01"), "C": _prices("0.02"), "F": DataStoreApiError("boom")},
    )

    outcome = _run(client, [("A", "1"), ("F", "2"), ("C", "1")])

    assert [s.status for s in outcome.statuses] == ["included", "failed", "included"]
    assert {row.ticker: row.weight for row in outcome.result.assets} == {
        "A": Decimal("0.5"),
        "C": Decimal("0.5"),
    }


def test_a_failed_fx_call_fails_every_asset_in_that_currency_and_only_those():
    gbp = TickerInfo(exists=True, has_returns=True, currency="GBp", fx_currency="GBP")
    client = _FakeDataStoreClient(
        info={"A": EUR, "B": USD, "B2": USD, "G": gbp},
        prices={t: _prices("0.01") for t in ("A", "B", "B2", "G")},
        fx={"USD": DataStoreApiError("boom"), "GBP": USD_FX},
    )

    outcome = _run(client, [("A", "1"), ("B", "1"), ("B2", "1"), ("G", "1")])

    assert [s.status for s in outcome.statuses] == ["included", "failed", "failed", "included"]


def test_two_assets_in_one_currency_share_one_fx_fetch():
    client = _FakeDataStoreClient(
        info={"B": USD, "B2": USD},
        prices={"B": _prices("0.01"), "B2": _prices("0.02")},
        fx={"USD": USD_FX},
    )

    _run(client, [("B", "1"), ("B2", "1")])

    assert sum(1 for call in client.calls if call[0] == "fx") == 1


def test_when_every_asset_is_left_out_the_result_is_empty_and_every_status_is_listed():
    client = _FakeDataStoreClient(
        info={"X": TickerInfo(exists=False, has_returns=False), "F": DataStoreApiError("boom")}
    )

    outcome = _run(client, [("X", "1"), ("F", "1")])

    assert outcome.result.end_date is None
    assert outcome.result.mix is None
    assert outcome.result.assets == []
    assert outcome.result.growth is None
    assert [s.status for s in outcome.statuses] == ["not_tracked", "failed"]


def test_each_asset_logs_its_outcome_and_a_failure_logs_its_exception(caplog):
    client = _FakeDataStoreClient(
        info={"A": EUR, "X": TickerInfo(exists=False, has_returns=False), "F": EUR},
        prices={"A": _prices("0.01", "0.02"), "F": DataStoreApiError("boom")},
    )

    with caplog.at_level(logging.INFO, logger="portfolio.labs.flows"):
        _run(client, [("A", "1"), ("X", "1"), ("F", "1")])

    messages = {r.getMessage(): r for r in caplog.records}
    assert any("A: included" in m and "3 day(s)" in m and "EUR" in m for m in messages)
    assert any("X: left out" in m and "not tracked" in m for m in messages)
    failed = next(r for m, r in messages.items() if "F: failed" in m)
    assert failed.levelno == logging.ERROR
    assert failed.exc_info is not None
