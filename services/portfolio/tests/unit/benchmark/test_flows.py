"""benchmark/flows.py: fetches the benchmark a request names (RK-13, spec §5.6) and turns it
into the daily and weekly EUR return series the relative measures run on. An unknown or
unavailable benchmark is reported with its reason and logged, never raised: it only greys the
relative figures.

The EUR conversion itself is eur_returns/'s (tested there); here, what is fetched, how each
outcome is worded, and what is logged.
"""

import logging
from datetime import date
from decimal import Decimal

import pytest
from portfolio.benchmark import flows
from portfolio.benchmark.base import Benchmark, BenchmarkUnavailable
from portfolio.benchmark.flows import fetch_benchmark
from portfolio.datastore_client.base import FxPoint, PricePoint, TickerInfo
from portfolio.errors import DataStoreApiError
from portfolio.risk.types import Frequency

END = date(2026, 10, 21)
EUR = TickerInfo(exists=True, has_returns=True, currency="EUR", fx_currency="EUR")
USD = TickerInfo(exists=True, has_returns=True, currency="USD", fx_currency="USD")

# Monday 2026-01-05 to Friday 2026-01-16. The daily base is the first price date, Monday the
# 5th, so the weekly series is based on Friday the 9th and has one whole week, ending the 16th.
DAYS = [date(2026, 1, d) for d in (5, 6, 7, 8, 9, 12, 13, 14, 15, 16)]


def _prices(returns):
    return [
        PricePoint(day, Decimal("100"), None if r is None else Decimal(r))
        for day, r in zip(DAYS, returns)
    ]


class FakeClient:
    def __init__(self, infos, prices=None, fx=None, fail=None):
        self.infos = infos
        self.prices = prices or {}
        self.fx = fx or {}
        self.fail = fail or set()
        self.price_calls = []
        self.fx_calls = []

    def get_ticker_info(self, ticker):
        return self.infos.get(ticker, TickerInfo(exists=False, has_returns=False))

    def get_price_series(self, ticker, start, end):
        self.price_calls.append((ticker, start, end))
        if ticker in self.fail:
            raise DataStoreApiError(f"boom for {ticker}")
        return self.prices.get(ticker, [])

    def get_fx_rate_series(self, base, quote, start, end):
        self.fx_calls.append((base, quote, start, end))
        return self.fx.get(quote, [])


def test_a_eur_benchmark_becomes_daily_and_weekly_return_series():
    returns = [None, "0.01", "-0.02", "0.005", "0.0", "0.01", "0.01", None, "0.02", "-0.01"]
    client = FakeClient({"VWCE.DE": EUR}, prices={"VWCE.DE": _prices(returns)})

    result = fetch_benchmark(client, "VWCE.DE", END, context="test")

    assert isinstance(result, Benchmark)
    assert result.ticker == "VWCE.DE"
    assert result.first_date == DAYS[0]
    assert result.daily.frequency is Frequency.DAILY
    assert result.daily.base_date == DAYS[0]
    assert result.daily.dates == tuple(DAYS[1:])
    assert result.daily.values == (0.01, -0.02, 0.005, 0.0, 0.01, 0.01, None, 0.02, -0.01)
    assert result.weekly.frequency is Frequency.WEEKLY
    assert result.weekly.base_date == date(2026, 1, 9)
    assert result.weekly.dates == (date(2026, 1, 16),)
    assert result.weekly.values == (None,)  # a NULL day makes its week NULL (R6)
    assert result.fetched.info == EUR


def test_its_whole_history_is_fetched_so_the_first_date_is_its_own():
    client = FakeClient({"IWDA.AS": EUR}, prices={"IWDA.AS": _prices([None] + ["0.01"] * 9)})

    fetch_benchmark(client, "IWDA.AS", END, context="test")

    assert client.price_calls == [("IWDA.AS", flows.EPOCH, END)]


def test_a_non_eur_benchmark_is_converted_to_eur():
    client = FakeClient(
        {"SPY": USD},
        prices={"SPY": _prices([None, "0.02"] + ["0.0"] * 8)},
        fx={"USD": [FxPoint(DAYS[0], Decimal("1.10")), FxPoint(DAYS[1], Decimal("1.12"))]},
    )

    result = fetch_benchmark(client, "SPY", END, context="test")

    # Spec §4.2's worked example: +2% local while EURUSD goes 1.10 to 1.12.
    assert result.daily.values[0] == pytest.approx(1.02 * 1.10 / 1.12 - 1, abs=1e-12)


@pytest.mark.parametrize(
    "infos, prices, fx, status, reason",
    [
        ({}, {}, {}, "not_tracked", "not tracked"),
        (
            {"X": TickerInfo(exists=True, has_returns=False)},
            {},
            {},
            "no_history",
            "no history yet",
        ),
        ({"X": EUR}, {"X": []}, {}, "no_history", "no history yet"),
        (
            {"X": TickerInfo(exists=True, has_returns=True, currency="HKD", fx_currency=None)},
            {},
            {},
            "no_fx_rate",
            "no FX series for its currency",
        ),
        ({"X": USD}, {"X": _prices([None, "0.01"])}, {}, "no_fx_rate", "no FX series for USD"),
    ],
)
def test_an_unavailable_benchmark_is_reported_with_its_reason(
    infos, prices, fx, status, reason, caplog
):
    client = FakeClient(infos, prices=prices, fx=fx)

    with caplog.at_level(logging.WARNING, logger="portfolio.benchmark.flows"):
        result = fetch_benchmark(client, "X", END, context="test")

    assert result == BenchmarkUnavailable("X", status, reason)
    assert f"test: benchmark X: unavailable, {reason}." in caplog.messages


def test_a_failed_fetch_is_reported_not_raised_and_its_exception_logged(caplog):
    client = FakeClient({"VWCE.DE": EUR}, fail={"VWCE.DE"})

    with caplog.at_level(logging.ERROR, logger="portfolio.benchmark.flows"):
        result = fetch_benchmark(client, "VWCE.DE", END, context="test")

    assert result == BenchmarkUnavailable("VWCE.DE", "failed", "couldn't reach data-store")
    [record] = caplog.records
    assert record.levelno == logging.ERROR
    assert "boom for VWCE.DE" in record.getMessage()
    assert record.exc_info is not None


def test_a_fetched_benchmark_logs_its_span(caplog):
    client = FakeClient({"VWCE.DE": EUR}, prices={"VWCE.DE": _prices([None] + ["0.01"] * 9)})

    with caplog.at_level(logging.INFO, logger="portfolio.benchmark.flows"):
        fetch_benchmark(client, "VWCE.DE", END, context="test")

    assert caplog.messages == [
        "test: benchmark VWCE.DE: 9 daily return(s) from 2026-01-05 to 2026-01-16."
    ]
