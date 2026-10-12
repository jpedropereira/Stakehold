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

from portfolio import pipeline
from portfolio.accounts import repository as accounts_repository
from portfolio.holdings.aggregator import (
    AccountHoldingInputs,
    HoldingsResult,
    aggregate_holdings,
)
from portfolio.positions import repository as positions_repository
from portfolio.returns import repository as returns_repository
from portfolio.returns.performance import (
    PortfolioPoint,
    TickerGrowth,
    compute_series,
    compute_ticker_growth,
)
from portfolio.ticker_mapping.base import TickerMappings


@dataclass(frozen=True)
class PortfolioReturns:
    series: list[PortfolioPoint]
    by_ticker: list[TickerGrowth]


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
    mappings: TickerMappings,
    start: date | None = None,
    end: date | None = None,
) -> PortfolioReturns:
    """This user's whole-portfolio return series, one point per priced day, and each ticker's
    latest growth across the accounts holding it. Every account's full return history and
    BUY/SELL flows go into returns/performance.py at once, keyed on (account, ticker), so growth
    is one time-weighted figure, never an average of per-account ones.

    `start` and `end` only crop `series`, after it is computed: growth always runs from the first
    priced day, so a shorter range never restarts it at zero, and `by_ticker` ignores the range.
    A user with no accounts, or no rows yet, gets an empty result rather than an error.
    """
    snapshots = []
    flows = []
    for account in accounts_repository.list_by_user(conn, user_id):
        snapshots.extend(returns_repository.list_snapshots(conn, account_id=account.id))
        flows.extend(pipeline.list_trade_flows(conn, account, mappings=mappings))
    series = [
        point
        for point in compute_series(snapshots, flows)
        if (start is None or point.price_date >= start) and (end is None or point.price_date <= end)
    ]
    return PortfolioReturns(series=series, by_ticker=compute_ticker_growth(snapshots, flows))
