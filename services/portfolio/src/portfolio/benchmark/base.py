"""The benchmark's types and its conversion to return series (RK-13). Pure: benchmark/flows.py
fetches, and labs/risk.py and risk/ex_post.py take what it built.

Its returns are spec stream 1: the ticker's daily EUR total return from eur_returns/, NULLs kept
as gaps (R5), compounded to W-FRI weeks (R6) for the weekly relative measures.
"""

from dataclasses import dataclass
from datetime import date

from portfolio.eur_returns.calculator import Ineligible, eur_daily_returns
from portfolio.eur_returns.fetch import Fetched
from portfolio.risk.types import Frequency, ReturnSeries
from portfolio.risk.weekly import to_weekly

DEFAULT_BENCHMARK = "VWCE.DE"


@dataclass(frozen=True)
class Benchmark:
    ticker: str
    first_date: date  # the level its first return is measured from
    fetched: Fetched  # what was fetched, so Labs can price its growth line
    daily: ReturnSeries
    weekly: ReturnSeries


@dataclass(frozen=True)
class BenchmarkUnavailable:
    ticker: str
    status: str  # eur_returns/fetch's: not_tracked, no_history, no_fx_rate or failed
    reason: str


@dataclass(frozen=True)
class BenchmarkStatus:
    """What a response says about its benchmark (spec §5.6: the benchmark and its start date are
    shown wherever relative measures appear)."""

    ticker: str
    available: bool
    reason: str | None  # why not, when it isn't
    first_date: date | None  # its first day, when it was fetched


def status_of(benchmark: Benchmark | BenchmarkUnavailable) -> BenchmarkStatus:
    if isinstance(benchmark, BenchmarkUnavailable):
        return BenchmarkStatus(benchmark.ticker, False, benchmark.reason, None)
    return BenchmarkStatus(benchmark.ticker, True, None, benchmark.first_date)


def unavailable_reason(benchmark: BenchmarkUnavailable) -> str:
    """What greys the relative figures when there is no benchmark to measure against."""
    return f"benchmark unavailable: {benchmark.reason}"


def build_benchmark(ticker: str, fetched: Fetched) -> Benchmark | Ineligible:
    returns = eur_daily_returns(fetched.prices, fetched.fx)
    if isinstance(returns, Ineligible):
        return returns
    daily = ReturnSeries(
        Frequency.DAILY,
        returns.anchor,
        tuple(returns.dates),
        tuple(None if r is None else float(r) for r in returns.r_eur),
    )
    return Benchmark(ticker, returns.anchor, fetched, daily, to_weekly(daily))
