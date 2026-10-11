"""Persistence for derived, materialized position snapshots.

Loading strategy is delete-then-insert per (account_id, canonical_ticker), not upsert -- unlike a
fixed daily grid, the event-driven dates themselves can change on rebuild (a corrected historical
transaction can add, remove, or shift which dates count as position-changing events), so this is a
full snapshot replacement, not an update-in-place. See docs/schema.md's "Loading strategy" note on
position_snapshots.

Does not commit anywhere in this module -- same as every other repository in this service.
pipeline.py wraps each (account, ticker) pair's replace_snapshots call in its own
`with transaction(conn):`, which is what makes that one pair's replacement atomic without
inverting the "repositories never commit" rule for this module alone.
"""

from collections.abc import Sequence
from uuid import UUID

from portfolio.positions.calculator import PositionSnapshot

_DELETE_SQL = "DELETE FROM position_snapshots WHERE account_id = %s AND canonical_ticker = %s"

_DELETE_BY_ACCOUNT_SQL = "DELETE FROM position_snapshots WHERE account_id = %s"

_INSERT_SQL = """
    INSERT INTO position_snapshots (
        account_id, canonical_ticker, as_of_date, quantity, average_cost_native,
        native_currency, average_cost_eur, cost_basis_eur
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""

_LIST_TICKERS_SQL = "SELECT DISTINCT canonical_ticker FROM position_snapshots WHERE account_id = %s"

_LIST_SNAPSHOTS_SQL = """
    SELECT account_id, canonical_ticker, as_of_date, quantity, average_cost_native,
           native_currency, average_cost_eur, cost_basis_eur
    FROM position_snapshots
    WHERE account_id = %s AND canonical_ticker = %s
    ORDER BY as_of_date
"""

_LIST_LATEST_SNAPSHOTS_SQL = """
    SELECT DISTINCT ON (canonical_ticker)
           account_id, canonical_ticker, as_of_date, quantity, average_cost_native,
           native_currency, average_cost_eur, cost_basis_eur
    FROM position_snapshots
    WHERE account_id = %s
    ORDER BY canonical_ticker, as_of_date DESC
"""


def replace_snapshots(
    conn, *, account_id: int, canonical_ticker: str, snapshots: Sequence[PositionSnapshot]
) -> int:
    """Delete every existing snapshot for this (account, ticker), then insert the freshly
    replayed set. Returns the count inserted. Does not commit -- the caller wraps this in one
    transaction so a concurrent reader never observes the pair with zero rows (docs/schema.md)."""
    with conn.cursor() as cur:
        cur.execute(_DELETE_SQL, (account_id, canonical_ticker))
        if snapshots:
            cur.executemany(
                _INSERT_SQL,
                [
                    (
                        snapshot.account_id,
                        snapshot.canonical_ticker,
                        snapshot.as_of_date,
                        snapshot.quantity,
                        snapshot.average_cost_native,
                        snapshot.native_currency,
                        snapshot.average_cost_eur,
                        snapshot.cost_basis_eur,
                    )
                    for snapshot in snapshots
                ],
            )
    return len(snapshots)


def list_tickers(conn, account_id: int) -> list[str]:
    """Every canonical_ticker this account currently has a snapshot for -- lets a rebuild find
    tickers no longer produced (e.g. a ticker mapping was re-pointed) so it can prune them."""
    with conn.cursor() as cur:
        cur.execute(_LIST_TICKERS_SQL, (account_id,))
        return [row[0] for row in cur.fetchall()]


def list_accounts_holding(conn, tickers: list[str]) -> list[tuple[UUID, str]]:
    """(user_id, account_label) of every account, any user, with a snapshot for one of
    `tickers` -- whose returns change when one of those tickers gets prices."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT a.user_id, a.account_label
            FROM position_snapshots p
            JOIN accounts a ON a.id = p.account_id
            WHERE p.canonical_ticker = ANY(%s)
            ORDER BY a.user_id, a.account_label
            """,
            (list(tickers),),
        )
        return [(row[0], row[1]) for row in cur.fetchall()]


def delete_snapshots(conn, *, account_id: int, canonical_ticker: str) -> int:
    """Removes every snapshot for one (account, ticker) with nothing to replace it -- a rebuild
    that no longer produces this ticker at all, as opposed to replace_snapshots' delete-then-
    reinsert. Returns the row count removed. Does not commit -- same as replace_snapshots."""
    with conn.cursor() as cur:
        cur.execute(_DELETE_SQL, (account_id, canonical_ticker))
        return cur.rowcount


def delete_by_account(conn, account_id: int) -> int:
    """Removes every position snapshot in one account, whatever its ticker, ahead of deleting the
    account itself. Returns the row count removed. Does not commit."""
    with conn.cursor() as cur:
        cur.execute(_DELETE_BY_ACCOUNT_SQL, (account_id,))
        return cur.rowcount


def list_snapshots(conn, *, account_id: int, canonical_ticker: str) -> list[PositionSnapshot]:
    """Every stored snapshot for one (account, ticker), oldest first -- the position's full
    history, needed by rebuild_returns to know quantity/cost_basis_eur as of each priced day."""
    with conn.cursor() as cur:
        cur.execute(_LIST_SNAPSHOTS_SQL, (account_id, canonical_ticker))
        rows = cur.fetchall()
    return [PositionSnapshot(*row) for row in rows]


def list_latest_snapshots(conn, account_id: int) -> list[PositionSnapshot]:
    """The current holding per ticker for this account -- one row per canonical_ticker, its
    most recent as_of_date. Serves a "my current positions" read without replaying the
    transaction log; the event-driven grain (docs/schema.md) means the most recent row already
    is the current holding, closed positions included (quantity 0 rows are not pruned here)."""
    with conn.cursor() as cur:
        cur.execute(_LIST_LATEST_SNAPSHOTS_SQL, (account_id,))
        rows = cur.fetchall()
    return [PositionSnapshot(*row) for row in rows]
