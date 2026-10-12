"""Reads that span every account a user holds: the I/O around holdings/aggregator.py (PF-1)
and returns/performance.py's cross-account return series (PF-5).

Kept out of pipeline.py, which is near the 64KB limit. User isolation stays in
accounts/repository.py: the accounts are resolved through list_by_user, and every snapshot and
transaction read is by one of those accounts' own ids, never by user_id directly. See
docs/architecture.md's "holdings/" note.
"""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from portfolio.accounts import repository as accounts_repository
from portfolio.holdings.aggregator import (
    AccountHoldingInputs,
    HoldingsResult,
    aggregate_holdings,
)
from portfolio.positions import repository as positions_repository
from portfolio.returns import repository as returns_repository
from portfolio.returns.performance import PortfolioPoint, compute_series


@dataclass(frozen=True)
class PortfolioReturns:
    series: list[PortfolioPoint]


def list_holdings(conn, *, user_id: UUID) -> HoldingsResult:
    """Everything this user holds, summed per ticker across their accounts, plus the totals.
    Read-only: it never triggers a rebuild, so it shows what the last rebuild stored. A user
    with no accounts gets an empty result rather than an error."""
    per_account = [
        AccountHoldingInputs(
            account_label=account.account_label,
            positions=positions_repository.list_latest_snapshots(conn, account.id),
            returns=returns_repository.list_latest_snapshots(conn, account.id),
        )
        for account in accounts_repository.list_by_user(conn, user_id)
    ]
    return aggregate_holdings(per_account)


def list_portfolio_returns(
    conn,
    *,
    user_id: UUID,
    start: date | None = None,
    end: date | None = None,
) -> PortfolioReturns:
    """This user's whole-portfolio return series, one point per priced day. Every account's full
    return history goes into returns/performance.py at once, keyed on (account, ticker).

    `start` and `end` only crop the series, after it is computed: the read is never ranged, so a
    pair whose last row is before `start` still forward-fills into the range. A user with no
    accounts, or no rows yet, gets an empty result rather than an error.
    """
    snapshots = []
    for account in accounts_repository.list_by_user(conn, user_id):
        snapshots.extend(returns_repository.list_snapshots(conn, account_id=account.id))
    series = [
        point
        for point in compute_series(snapshots)
        if (start is None or point.price_date >= start) and (end is None or point.price_date <= end)
    ]
    return PortfolioReturns(series=series)
