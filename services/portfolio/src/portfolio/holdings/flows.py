"""Reads that span every account a user holds: the I/O around holdings/aggregator.py.

Kept out of pipeline.py, which is near the 64KB limit; PF-5's cross-account return series
belongs here too. User isolation stays in accounts/repository.py: the accounts are resolved
through list_by_user, and every snapshot read is by one of those accounts' own ids, never by
user_id directly. See docs/architecture.md's "holdings/" note.
"""

from uuid import UUID

from portfolio.accounts import repository as accounts_repository
from portfolio.holdings.aggregator import (
    AccountHoldingInputs,
    HoldingsResult,
    aggregate_holdings,
)
from portfolio.positions import repository as positions_repository
from portfolio.returns import repository as returns_repository


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
