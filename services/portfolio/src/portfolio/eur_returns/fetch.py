"""Fetches what eur_returns/calculator.py needs for a set of tickers from data-store: each one's
ticker info, price series and, for a non-EUR ticker, its currency's FX series. Labs (LB-4) and
the portfolio TWR (RK-10) both fetch this way; it was lifted out of labs/flows.py so the risk
path wouldn't keep a second copy.

Two concurrent phases, one after the other, because the FX pair depends on the ticker info:

1. `get_ticker_info` for every ticker.
2. `get_price_series` for every ticker still in play, and `get_fx_rate_series` once per distinct
   non-EUR `fx_currency` (two USD tickers share one fetch), from the earliest start among that
   currency's tickers.

The client is synchronous (requests), so the fan-out is a thread pool. Every call's exception is
caught on its own: one failing ticker is reported as `failed`, never failing the whole fetch. A
failed FX fetch fails every ticker in that currency, and only those.

No logging and no wording: the caller logs each ticker's outcome and words its reason, since Labs
and the TWR word the same status differently.
"""

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date

from portfolio.datastore_client.base import (
    DataStoreClient,
    FxPoint,
    PricePoint,
    TickerInfo,
)

# data-store serves its sync routes from a thread pool, one DB connection per request, so a few
# calls in flight per request is a modest load. The client itself holds no shared state.
_MAX_WORKERS = 8

NOT_TRACKED = "not_tracked"
NO_HISTORY = "no_history"
NO_FX_RATE = "no_fx_rate"
FAILED = "failed"


@dataclass(frozen=True)
class Fetched:
    info: TickerInfo
    prices: list[PricePoint]
    fx: list[FxPoint] | None  # None for a EUR ticker


@dataclass(frozen=True)
class LeftOut:
    status: str  # NOT_TRACKED, NO_HISTORY, NO_FX_RATE or FAILED
    exc: BaseException | None  # the raised exception when FAILED, for the caller's log


def fetch_series(
    datastore_client: DataStoreClient,
    tickers: Sequence[str],
    start: date | Mapping[str, date],
    end: date,
) -> dict[str, Fetched | LeftOut]:
    """Every ticker's series from `start` (one date for all, or one per ticker) to `end`, or why
    it was left out. `tickers` must be unique."""
    starts = start if isinstance(start, Mapping) else {ticker: start for ticker in tickers}
    left_out: dict[str, LeftOut] = {}
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        infos = _fetch_all(pool, tickers, datastore_client.get_ticker_info, left_out)
        in_play = []
        for ticker in tickers:
            info = infos.get(ticker)
            if info is None:
                continue
            if not info.exists:
                left_out[ticker] = LeftOut(NOT_TRACKED, None)
            elif not info.has_returns:
                left_out[ticker] = LeftOut(NO_HISTORY, None)
            elif info.fx_currency is None:
                left_out[ticker] = LeftOut(NO_FX_RATE, None)
            else:
                in_play.append(ticker)

        fx_starts: dict[str, date] = {}
        for ticker in in_play:
            currency = infos[ticker].fx_currency
            if currency != "EUR":
                fx_starts[currency] = min(fx_starts.get(currency, starts[ticker]), starts[ticker])
        fx_failures: dict[str, LeftOut] = {}
        price_futures = {
            t: pool.submit(datastore_client.get_price_series, t, starts[t], end) for t in in_play
        }
        fx_series = _fetch_all(
            pool,
            sorted(fx_starts),
            lambda currency: datastore_client.get_fx_rate_series(
                "EUR", currency, fx_starts[currency], end
            ),
            fx_failures,
        )
        prices = {}
        for ticker, future in price_futures.items():
            try:
                prices[ticker] = future.result()
            except Exception as exc:
                left_out[ticker] = LeftOut(FAILED, exc)

    result: dict[str, Fetched | LeftOut] = {}
    for ticker in tickers:
        currency = infos[ticker].fx_currency if ticker in in_play else None
        if ticker in left_out:
            result[ticker] = left_out[ticker]
        elif currency in fx_failures:
            result[ticker] = fx_failures[currency]
        else:
            fx = None if currency == "EUR" else fx_series[currency]
            result[ticker] = Fetched(info=infos[ticker], prices=prices[ticker], fx=fx)
    return result


def _fetch_all(
    pool: ThreadPoolExecutor,
    keys: Sequence[str],
    fetch: Callable,
    failures: dict[str, LeftOut],
) -> dict:
    """fetch(key) for every key concurrently. A key whose call raised is recorded in `failures`
    and left out of the result."""
    futures = {key: pool.submit(fetch, key) for key in keys}
    results = {}
    for key, future in futures.items():
        try:
            results[key] = future.result()
        except Exception as exc:
            failures[key] = LeftOut(FAILED, exc)
    return results
