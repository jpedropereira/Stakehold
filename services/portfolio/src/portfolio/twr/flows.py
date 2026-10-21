"""The I/O around twr/calculator.py (RK-10): read every account's return rows and latest position
rows for one user, fetch each held ticker's series from data-store, convert them to EUR returns,
and hand all of it to compute_twr.

Its own package rather than another read in holdings/flows.py, because it is the first
user-data read that also calls data-store. User isolation stays in accounts/repository.py: the
accounts are resolved through list_by_user, and every row read is by one of those accounts' own
ids. The fetch is eur_returns/eligibility.py's, shared with ex-ante risk (on fetch.py's fan-out,
shared with Labs), so one failing ticker is excluded and named, never failing the whole series.

Every held ticker gets a log line: included, or left out with its reason. The job logs the
start and the summary.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from portfolio.accounts import repository as accounts_repository
from portfolio.datastore_client.base import DataStoreClient
from portfolio.eur_returns.eligibility import ExcludedHolding, fetch_eur_returns
from portfolio.positions import repository as positions_repository
from portfolio.returns import repository as returns_repository
from portfolio.twr.calculator import TwrDay, TwrPoint, compute_twr

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class PortfolioTwr:
    base_date: date | None  # growth's level 1; None when there's no series
    points: list[TwrPoint]
    n_gaps: int
    excluded: list[ExcludedHolding]  # by ticker; their weight went to the rest
    # For ex-post risk (RK-11), not the TWR route: each point's tickers and whether they all
    # traded on it, and each included ticker's FX currency.
    days: tuple[TwrDay, ...] = ()
    currencies: dict[str, str] = field(default_factory=dict)


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

    eligible = fetch_eur_returns(
        datastore_client, tickers, starts, today, logger=logger, context="portfolio-twr"
    )
    series = compute_twr(snapshots, eligible.returns, closed=closed)
    return PortfolioTwr(
        base_date=series.base_date,
        points=series.points,
        n_gaps=series.n_gaps,
        excluded=eligible.excluded,
        days=series.days,
        currencies=eligible.currencies,
    )
