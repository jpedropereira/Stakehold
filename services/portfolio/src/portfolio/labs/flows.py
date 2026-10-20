"""The I/O around labs/calculator.py (LB-4): fetch each asset's ticker info, price series and FX
series from data-store, then hand what was fetched to compute_backtest.

Orchestration, the same layer as holdings/flows.py, kept out of pipeline.py for the same reason
(near the 64KB limit) and because a backtest touches no database at all: a lab is not stored, and
every input is data-store's. See docs/architecture.md's "Labs orchestration" note.

The fetch itself -- two concurrent phases, a failure isolated to the assets it belongs to -- is
eur_returns/fetch.py's, shared with the portfolio TWR (RK-10). This module words each asset's
status and logs it.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from portfolio.datastore_client.base import DataStoreClient
from portfolio.eur_returns import fetch
from portfolio.eur_returns.fetch import Fetched
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

INCLUDED = "included"
NOT_TRACKED = fetch.NOT_TRACKED
NO_HISTORY_STATUS = fetch.NO_HISTORY
NO_FX_RATE_STATUS = fetch.NO_FX_RATE
FAILED = fetch.FAILED

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
    fetched_or_not = fetch.fetch_series(
        datastore_client, [ticker for ticker, _ in mix], EPOCH, today
    )
    left_out: dict[str, tuple[str, BaseException | None]] = {
        ticker: (outcome.status, outcome.exc)
        for ticker, outcome in fetched_or_not.items()
        if not isinstance(outcome, Fetched)
    }
    fetched = {
        ticker: outcome
        for ticker, outcome in fetched_or_not.items()
        if isinstance(outcome, Fetched)
    }

    inputs = [
        AssetInput(
            ticker=ticker,
            weight=weight,
            prices=fetched[ticker].prices,
            fx=fetched[ticker].fx,
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
