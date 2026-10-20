"""The I/O around twr/calculator.py (RK-10): read every account's return rows and latest position
rows for one user, fetch each held ticker's series from data-store, convert them to EUR returns,
and hand all of it to compute_twr.

Its own package rather than another read in holdings/flows.py, because it is the first
user-data read that also calls data-store. User isolation stays in accounts/repository.py: the
accounts are resolved through list_by_user, and every row read is by one of those accounts' own
ids. The fetch is eur_returns/fetch.py's, shared with Labs, so one failing ticker is excluded and
named, never failing the whole series.

Every held ticker gets a log line: included, or left out with its reason. The job logs the
start and the summary.
"""

import logging
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from portfolio.accounts import repository as accounts_repository
from portfolio.datastore_client.base import DataStoreClient
from portfolio.eur_returns import fetch
from portfolio.eur_returns.calculator import NO_FX_SERIES, EurReturns, eur_daily_returns
from portfolio.positions import repository as positions_repository
from portfolio.returns import repository as returns_repository
from portfolio.twr.calculator import TwrPoint, compute_twr

logger = logging.getLogger(__name__)

_REASONS = {
    fetch.NOT_TRACKED: "not tracked",
    fetch.NO_HISTORY: "no history yet",
    # The exception itself goes to the log: it names internal URLs, and the user can only act on
    # "try again later".
    fetch.FAILED: "couldn't reach data-store",
}


@dataclass(frozen=True)
class ExcludedHolding:
    ticker: str
    status: str  # not_tracked, no_history, no_fx_rate or failed
    reason: str


@dataclass(frozen=True)
class PortfolioTwr:
    base_date: date | None  # growth's level 1; None when there's no series
    points: list[TwrPoint]
    n_gaps: int
    excluded: list[ExcludedHolding]  # by ticker; their weight went to the rest


def compute_portfolio_twr(
    conn,
    datastore_client: DataStoreClient,
    *,
    user_id: UUID,
    today: date | None = None,
) -> PortfolioTwr:
    """This user's daily TWR of invested positions, across every account. Read-only: it never
    triggers a rebuild, so its weights are what the last returns rebuild stored."""
    today = today or date.today()
    snapshots = []
    closed = set()
    for account in accounts_repository.list_by_user(conn, user_id):
        snapshots.extend(returns_repository.list_snapshots(conn, account_id=account.id))
        closed.update(
            (position.account_id, position.canonical_ticker)
            for position in positions_repository.list_latest_snapshots(conn, account.id)
            if position.quantity == 0
        )
    if not snapshots:
        logger.info("portfolio-twr: no return rows in any account yet, nothing to fetch.")
        return PortfolioTwr(base_date=None, points=[], n_gaps=0, excluded=[])

    starts: dict[str, date] = {}
    for row in snapshots:
        ticker = row.canonical_ticker
        starts[ticker] = min(starts.get(ticker, row.price_date), row.price_date)
    tickers = sorted(starts)
    logger.info(
        "portfolio-twr: %d ticker(s) held, rows from %s to %s.",
        len(tickers),
        min(starts.values()),
        max(row.price_date for row in snapshots),
    )

    returns: dict[str, EurReturns] = {}
    excluded = []
    for ticker, outcome in fetch.fetch_series(datastore_client, tickers, starts, today).items():
        if isinstance(outcome, fetch.LeftOut):
            if outcome.status == fetch.NO_FX_RATE:
                status, reason = outcome.status, "no FX series for its currency"
            else:
                status, reason = outcome.status, _REASONS[outcome.status]
            if outcome.status == fetch.FAILED:
                logger.error(
                    "portfolio-twr: %s: failed, left out -- a data-store call raised: %s",
                    ticker,
                    outcome.exc,
                    exc_info=outcome.exc,
                )
            else:
                logger.warning("portfolio-twr: %s: left out, %s.", ticker, reason)
            excluded.append(ExcludedHolding(ticker, status, reason))
            continue

        series = eur_daily_returns(outcome.prices, outcome.fx)
        if isinstance(series, EurReturns):
            logger.info(
                "portfolio-twr: %s: included, %d day(s) of EUR returns.", ticker, len(series.dates)
            )
            returns[ticker] = series
            continue
        if series.reason == NO_FX_SERIES:
            status, reason = fetch.NO_FX_RATE, f"no FX series for {outcome.info.fx_currency}"
        else:
            status, reason = fetch.NO_HISTORY, _REASONS[fetch.NO_HISTORY]
        logger.warning("portfolio-twr: %s: left out, %s.", ticker, reason)
        excluded.append(ExcludedHolding(ticker, status, reason))

    series = compute_twr(snapshots, returns, closed=closed)
    return PortfolioTwr(
        base_date=series.base_date, points=series.points, n_gaps=series.n_gaps, excluded=excluded
    )
