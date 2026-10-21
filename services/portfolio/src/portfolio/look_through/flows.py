"""The I/O around look_through/calculator.py (RK-22): what each held ticker is, and the latest
snapshot of every fund that can be looked through, from data-store.

Two concurrent phases, as eur_returns/fetch.py has:

1. `get_ticker_info` per ticker, `list_tracked_tickers` once (for `asset_type`) and
   `list_etf_funds` once (which ticker is a fund with holdings).
2. `get_latest_etf_holdings` for every held fund that has a snapshot.

A failure is isolated where an honest partial answer exists. A ticker whose info call fails is
left out, and the other weights are renormalised. A fund whose snapshot call fails is shown
"unmapped", with the reason. The two list calls fail the request instead: without them an ETF
would be read as a stock, which is a wrong answer, not a partial one.

How a ticker is classified, in order:
1. instruments.ETCS: a commodity. data-store's `asset_type` is never trusted for these.
2. A fund in GET /etf-holdings with a snapshot: looked through. instruments.HEDGED marks one
   whose currency exposure is its hedge.
3. A tracked `asset_type` of `etf` or `etc`: unmapped, with why.
4. Anything else: a direct stock. That includes tickers data-store prices only through a
   fund's holdings (BRK-B), which aren't in `tracked_tickers`.
"""

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal

from portfolio.datastore_client.base import DataStoreClient, EtfHoldings
from portfolio.look_through.calculator import (
    COMMODITY,
    DIRECT,
    LOOK_THROUGH,
    UNMAPPED,
    HeldAsset,
    LookThrough,
    compute_look_through,
)
from portfolio.look_through.instruments import ETCS, HEDGED

logger = logging.getLogger(__name__)

_MAX_WORKERS = 8

INCLUDED = "included"
NOT_TRACKED = "not_tracked"
FAILED = "failed"

_REASONS = {NOT_TRACKED: "not tracked", FAILED: "couldn't reach data-store"}
_FUND_TYPES = frozenset({"etf", "etc"})

NO_HOLDINGS_DATA = "no holdings data for this ETF"
NEVER_INGESTED = "holdings never ingested"
NO_SNAPSHOT = "no holdings snapshot"
SNAPSHOT_FAILED = "holdings unavailable: couldn't reach data-store"
UNKNOWN_ETC = "ETC not in portfolio's instrument table"


@dataclass(frozen=True)
class LookThroughAsset:
    ticker: str
    status: str  # included, not_tracked or failed
    reason: str | None  # why it was left out; None when included
    kind: str | None  # the calculator's kind; None when left out
    weight: Decimal | None  # normalised over the included assets; None when left out
    matched_in: str | None  # a direct stock: the fund whose line describes it
    note: str | None  # an unmapped asset: why it isn't looked through


@dataclass(frozen=True)
class MixLookThrough:
    assets: list[LookThroughAsset]  # every requested asset, in input order
    exposures: LookThrough | None  # None exactly when nothing was included


def look_through_mix(
    datastore_client: DataStoreClient, mix: Sequence[tuple[str, Decimal]]
) -> MixLookThrough:
    """The look-through of `mix` (ticker, weight) pairs. The mix is assumed valid (non-empty,
    unique tickers, positive weights): the API checks that first."""
    tickers = [ticker for ticker, _ in mix]
    logger.info("look-through: %d asset(s) requested.", len(tickers))

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        tracked_future = pool.submit(datastore_client.list_tracked_tickers, tickers=tickers)
        funds_future = pool.submit(datastore_client.list_etf_funds)
        infos, info_failures = _fetch_all(pool, tickers, datastore_client.get_ticker_info)
        asset_types = {t.ticker: t.asset_type for t in tracked_future.result()}
        funds = {f.ticker: f for f in funds_future.result() if f.ticker is not None}

        left_out: dict[str, str] = {}
        for ticker in tickers:
            if ticker in info_failures:
                left_out[ticker] = FAILED
                logger.error(
                    "look-through: %s: failed, left out -- a data-store call raised: %s",
                    ticker,
                    info_failures[ticker],
                    exc_info=info_failures[ticker],
                )
            elif not infos[ticker].exists:
                left_out[ticker] = NOT_TRACKED
                logger.warning("look-through: %s: left out, not tracked.", ticker)

        included = [(t, w) for t, w in mix if t not in left_out]
        to_fetch = [
            t
            for t, _ in included
            if t not in ETCS and t in funds and funds[t].latest_as_of_date is not None
        ]
        snapshots_or_none, snapshot_failures = _fetch_all(
            pool, to_fetch, lambda t: datastore_client.get_latest_etf_holdings(funds[t].fund_key)
        )

    snapshots: dict[str, EtfHoldings] = {}
    for ticker in to_fetch:
        if ticker in snapshot_failures:
            logger.error(
                "look-through: %s: holdings snapshot of %s failed, shown unmapped -- %s",
                ticker,
                funds[ticker].fund_key,
                snapshot_failures[ticker],
                exc_info=snapshot_failures[ticker],
            )
        elif snapshots_or_none[ticker] is None:
            logger.warning(
                "look-through: %s: data-store has no snapshot of %s any more, shown unmapped.",
                ticker,
                funds[ticker].fund_key,
            )
        else:
            snapshot = snapshots_or_none[ticker]
            snapshots[ticker] = snapshot
            logger.info(
                "look-through: %s: snapshot of %s from %s, %d line(s).",
                ticker,
                snapshot.fund_key,
                snapshot.as_of_date,
                len(snapshot.constituents),
            )

    total = sum((w for _, w in included), Decimal("0"))
    held: list[HeldAsset] = []
    notes: dict[str, str | None] = {}
    for ticker, weight in included:
        kind, note = _classify(ticker, asset_types, funds, snapshots, snapshot_failures)
        notes[ticker] = note
        held.append(
            HeldAsset(
                ticker=ticker,
                weight=weight / total,
                kind=kind,
                listing_currency=infos[ticker].fx_currency,
                hedged=ticker in HEDGED,
            )
        )

    exposures = compute_look_through(held, snapshots) if held else None
    results = {a.ticker: a for a in exposures.assets} if exposures else {}
    assets = []
    for ticker in tickers:
        if ticker in left_out:
            status = left_out[ticker]
            assets.append(
                LookThroughAsset(ticker, status, _REASONS[status], None, None, None, None)
            )
            continue
        result = results[ticker]
        _log_included(result.ticker, result.kind, result.weight, result.matched_in, notes[ticker])
        assets.append(
            LookThroughAsset(
                ticker=ticker,
                status=INCLUDED,
                reason=None,
                kind=result.kind,
                weight=result.weight,
                matched_in=result.matched_in,
                note=notes[ticker],
            )
        )
    return MixLookThrough(assets=assets, exposures=exposures)


def _classify(ticker, asset_types, funds, snapshots, snapshot_failures) -> tuple[str, str | None]:
    if ticker in ETCS:
        return COMMODITY, None
    if ticker in snapshots:
        return LOOK_THROUGH, None
    if ticker in funds:
        if ticker in snapshot_failures:
            return UNMAPPED, SNAPSHOT_FAILED
        if funds[ticker].latest_as_of_date is None:
            return UNMAPPED, NEVER_INGESTED
        return UNMAPPED, NO_SNAPSHOT
    asset_type = asset_types.get(ticker)
    if asset_type == "etc":
        return UNMAPPED, UNKNOWN_ETC
    if asset_type in _FUND_TYPES:
        return UNMAPPED, NO_HOLDINGS_DATA
    return DIRECT, None


def _log_included(ticker, kind, weight, matched_in, note) -> None:
    pct = f"{weight * 100:.1f}%"
    if kind == LOOK_THROUGH:
        logger.info("look-through: %s: looked through, weight %s.", ticker, pct)
    elif kind == DIRECT and matched_in is not None:
        logger.info("look-through: %s: direct, matched in %s, weight %s.", ticker, matched_in, pct)
    elif kind == DIRECT:
        logger.info(
            "look-through: %s: direct, in no looked-through fund (sector and country unknown), "
            "weight %s.",
            ticker,
            pct,
        )
    elif kind == COMMODITY:
        logger.info(
            "look-through: %s: commodity (%s), weight %s.", ticker, ETCS[ticker].commodity, pct
        )
    else:
        logger.info("look-through: %s: unmapped (%s), weight %s.", ticker, note, pct)


def _fetch_all(
    pool: ThreadPoolExecutor, keys: Sequence[str], fetch: Callable
) -> tuple[dict, dict[str, BaseException]]:
    """fetch(key) for every key concurrently: the results, and each failed key's exception."""
    futures = {key: pool.submit(fetch, key) for key in keys}
    results, failures = {}, {}
    for key, future in futures.items():
        try:
            results[key] = future.result()
        except Exception as exc:
            failures[key] = exc
    return results, failures
