"""The I/O around labs/calculator.py (LB-4): fetch each asset's ticker info, price series and FX
series from data-store, then hand what was fetched to compute_backtest.

Orchestration, the same layer as holdings/flows.py, kept out of pipeline.py for the same reason
(near the 64KB limit) and because a backtest touches no database at all: a lab is not stored, and
every input is data-store's. See docs/architecture.md's "Labs orchestration" note.

Two concurrent phases, one after the other, because the FX pair depends on the ticker info:

1. `get_ticker_info` for every asset.
2. `get_price_series` for every asset still in play, and `get_fx_rate_series` once per distinct
   non-EUR `fx_currency` (two USD assets share one fetch).

The client is synchronous (requests), so the fan-out is a thread pool. Every call's exception is
caught on its own: one failing asset is reported as `failed`, never failing the whole backtest. A
failed FX fetch fails every asset in that currency, and only those.
"""

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from portfolio.datastore_client.base import (
    DataStoreClient,
    FxPoint,
    PricePoint,
    TickerInfo,
)
from portfolio.labs.calculator import (
    NO_FX_RATE,
    NO_HISTORY,
    AssetInput,
    BacktestResult,
    Period,
    compute_backtest,
)

logger = logging.getLogger(__name__)

# Max needs each asset's whole history, and data-store's series endpoints require a start.
EPOCH = date(1970, 1, 1)
# data-store serves its sync routes from a thread pool, one DB connection per request, so a few
# calls in flight per backtest is a modest load. The client itself holds no shared state.
_MAX_WORKERS = 8

INCLUDED = "included"
NOT_TRACKED = "not_tracked"
NO_HISTORY_STATUS = "no_history"
NO_FX_RATE_STATUS = "no_fx_rate"
FAILED = "failed"

_REASONS = {
    NOT_TRACKED: "not tracked",
    NO_HISTORY_STATUS: NO_HISTORY,
    NO_FX_RATE_STATUS: NO_FX_RATE,
    # The exception itself goes to the log: it names internal URLs, and the user can only act on
    # "try again later".
    FAILED: "couldn't reach data-store",
}
# compute_backtest's own Unavailable reasons, mapped back to a status.
_CALCULATOR_STATUSES = {NO_HISTORY: NO_HISTORY_STATUS, NO_FX_RATE: NO_FX_RATE_STATUS}


@dataclass(frozen=True)
class AssetStatus:
    ticker: str
    status: str  # included, not_tracked, no_history, no_fx_rate or failed
    reason: str | None  # None when included


@dataclass(frozen=True)
class LabsBacktest:
    result: BacktestResult
    statuses: list[AssetStatus]  # every requested asset, in input order


@dataclass(frozen=True)
class _Fetched:
    info: TickerInfo
    prices: list[PricePoint]


def run_backtest(
    datastore_client: DataStoreClient,
    mix: Sequence[tuple[str, Decimal]],
    period: Period,
    *,
    today: date | None = None,
) -> LabsBacktest:
    """Backtest `mix` (ticker, weight) pairs, with the growth series for `period`. The mix is
    assumed valid (non-empty, unique tickers, positive weights): the API checks that first."""
    today = today or date.today()
    left_out: dict[str, tuple[str, BaseException | None]] = {}
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        infos = _fetch_all(
            pool, [ticker for ticker, _ in mix], datastore_client.get_ticker_info, left_out
        )
        in_play = []
        for ticker, _ in mix:
            info = infos.get(ticker)
            if info is None:
                continue
            if not info.exists:
                left_out[ticker] = (NOT_TRACKED, None)
            elif not info.has_returns:
                left_out[ticker] = (NO_HISTORY_STATUS, None)
            elif info.fx_currency is None:
                left_out[ticker] = (NO_FX_RATE_STATUS, None)
            else:
                in_play.append(ticker)

        currencies = sorted({infos[t].fx_currency for t in in_play} - {"EUR"})
        fx_failures: dict[str, tuple[str, BaseException | None]] = {}
        price_futures = {
            t: pool.submit(datastore_client.get_price_series, t, EPOCH, today) for t in in_play
        }
        fx_series = _fetch_all(
            pool,
            currencies,
            lambda currency: datastore_client.get_fx_rate_series("EUR", currency, EPOCH, today),
            fx_failures,
        )
        prices = {}
        for ticker, future in price_futures.items():
            try:
                prices[ticker] = future.result()
            except Exception as exc:
                left_out[ticker] = (FAILED, exc)

    fetched = {}
    for ticker in in_play:
        currency = infos[ticker].fx_currency
        if currency in fx_failures:
            left_out.setdefault(ticker, fx_failures[currency])
        elif ticker not in left_out:
            fetched[ticker] = _Fetched(infos[ticker], prices[ticker])

    inputs = [
        AssetInput(
            ticker=ticker,
            weight=weight,
            prices=fetched[ticker].prices,
            fx=_fx_for(fetched[ticker].info, fx_series),
        )
        for ticker, weight in mix
        if ticker in fetched
    ]
    if inputs:
        result = compute_backtest(inputs, growth_period=period)
    else:
        result = BacktestResult(end_date=None, mix=None, assets=[], unavailable=[], growth=None)
    for unavailable in result.unavailable:
        left_out[unavailable.ticker] = (_CALCULATOR_STATUSES[unavailable.reason], None)

    statuses = []
    for ticker, _ in mix:
        if ticker in left_out:
            status, exc = left_out[ticker]
            _log_left_out(ticker, status, exc)
            statuses.append(AssetStatus(ticker, status, _REASONS[status]))
        else:
            info = fetched[ticker].info
            logger.info(
                "backtest: %s: included, %d day(s) of history, in %s.",
                ticker,
                len(fetched[ticker].prices),
                info.currency or info.fx_currency,
            )
            statuses.append(AssetStatus(ticker, INCLUDED, None))
    return LabsBacktest(result=result, statuses=statuses)


def _fetch_all(
    pool: ThreadPoolExecutor,
    keys: Sequence[str],
    fetch: Callable,
    failures: dict[str, tuple[str, BaseException | None]],
) -> dict:
    """fetch(key) for every key concurrently. A key whose call raised is recorded in `failures`
    and left out of the result."""
    futures = {key: pool.submit(fetch, key) for key in keys}
    results = {}
    for key, future in futures.items():
        try:
            results[key] = future.result()
        except Exception as exc:
            failures[key] = (FAILED, exc)
    return results


def _fx_for(info: TickerInfo, fx_series: dict[str, list[FxPoint]]) -> list[FxPoint] | None:
    if info.fx_currency == "EUR":
        return None
    return fx_series[info.fx_currency]


def _log_left_out(ticker: str, status: str, exc: BaseException | None) -> None:
    if status == FAILED:
        logger.error(
            "backtest: %s: failed, left out -- a data-store call raised: %s",
            ticker,
            exc,
            exc_info=exc,
        )
    else:
        logger.warning("backtest: %s: left out, %s.", ticker, _REASONS[status])
