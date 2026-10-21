"""The benchmark a request measures against (RK-13, spec §5.6): fetched from data-store and
turned into the daily and weekly EUR return series risk/relative.py runs on. Labs' backtest and
ex-post risk both call it, so the benchmark is fetched and worded one way.

- **One ticker, chosen per request**, `VWCE.DE` (`base.DEFAULT_BENCHMARK`) unless the request
  names another. It is never saved (a saved choice needs a settings schema, deferred), and never
  swapped for another index over a window it doesn't cover: the caller greys that window
  instead.
- **Its whole history is fetched**, from 1970, so `first_date` is its own, the date a greyed
  window names ("benchmark history too short (from 2019-07-29)"). Benchmarks are a few
  thousand rows at most.
- **Its returns are stream 1** (benchmark/base.py's `build_benchmark`).
- **An unavailable benchmark is reported, never raised.** Unknown ("not tracked"), no history,
  no FX series, or a failed call ("couldn't reach data-store", the exception logged): the caller
  greys the relative figures with the reason, and everything else is still measured.
- It words the statuses as the rest of the risk path does (`eur_returns/eligibility.REASONS`).
- It fetches through `eur_returns/fetch.fetch_series`, even when the benchmark is also in a
  Labs mix: two more data-store calls, and one code path.
"""

import logging
from datetime import date

from portfolio.benchmark.base import Benchmark, BenchmarkUnavailable, build_benchmark
from portfolio.datastore_client.base import DataStoreClient
from portfolio.eur_returns import fetch
from portfolio.eur_returns.calculator import NO_FX_SERIES, Ineligible
from portfolio.eur_returns.eligibility import REASONS

logger = logging.getLogger(__name__)

EPOCH = date(1970, 1, 1)


def fetch_benchmark(
    datastore_client: DataStoreClient, ticker: str, end: date, *, context: str
) -> Benchmark | BenchmarkUnavailable:
    """`ticker`'s EUR returns up to `end`, or why there are none. `context` prefixes the log
    line ("backtest risk", "ex-post-risk")."""
    outcome = fetch.fetch_series(datastore_client, [ticker], EPOCH, end)[ticker]
    if isinstance(outcome, fetch.LeftOut):
        if outcome.status == fetch.FAILED:
            logger.error(
                "%s: benchmark %s: unavailable, a data-store call raised: %s",
                context,
                ticker,
                outcome.exc,
                exc_info=outcome.exc,
            )
            return BenchmarkUnavailable(ticker, outcome.status, REASONS[outcome.status])
        return _unavailable(context, ticker, outcome.status, REASONS[outcome.status])

    benchmark = build_benchmark(ticker, outcome)
    if isinstance(benchmark, Ineligible):
        if benchmark.reason == NO_FX_SERIES:
            reason = f"no FX series for {outcome.info.fx_currency}"
            return _unavailable(context, ticker, fetch.NO_FX_RATE, reason)
        return _unavailable(context, ticker, fetch.NO_HISTORY, REASONS[fetch.NO_HISTORY])

    daily = benchmark.daily
    logger.info(
        "%s: benchmark %s: %d daily return(s) from %s to %s.",
        context,
        ticker,
        len(daily.dates),
        daily.base_date,
        daily.last_date,
    )
    return benchmark


def _unavailable(context: str, ticker: str, status: str, reason: str) -> BenchmarkUnavailable:
    logger.warning("%s: benchmark %s: unavailable, %s.", context, ticker, reason)
    return BenchmarkUnavailable(ticker, status, reason)
