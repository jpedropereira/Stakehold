"""Persistence for derived, materialized return snapshots.

Loading strategy is delete-then-insert per (account_id, canonical_ticker), the same as
position_snapshots. It used to be a true upsert, on the grounds that a rebuild only ever
overwrites the values at an already-written date, but deleting a transaction can move a pair's
window start later (an earlier BUY gone) and leave rows an upsert never revisits. Each rebuild
recomputes a pair's whole window, so replacing it loses nothing a current transaction supports.
See docs/schema.md's "Loading strategy" note on return_snapshots.

Does not commit anywhere in this module -- same as every other repository in this service.
pipeline.py wraps each (account, ticker) pair's replace_snapshots or delete_snapshots call in its
own `with transaction(conn):`, which is what makes that one pair's replacement atomic.
"""

from collections.abc import Sequence
from datetime import date

from portfolio.returns.calculator import ReturnSnapshot

_SELECT_COLUMNS = """
    account_id, canonical_ticker, price_date, quantity, market_value_native,
    market_value_eur, cost_basis_eur, unrealized_return_eur, unrealized_return_pct,
    dividends_received_eur, total_return_eur, total_return_pct
"""

_LIST_SNAPSHOTS_SQL = f"""
    SELECT {_SELECT_COLUMNS}
    FROM return_snapshots
    WHERE account_id = %s
"""

_LIST_LATEST_SNAPSHOTS_SQL = f"""
    SELECT DISTINCT ON (canonical_ticker) {_SELECT_COLUMNS}
    FROM return_snapshots
    WHERE account_id = %s
    ORDER BY canonical_ticker, price_date DESC
"""

_DELETE_SQL = "DELETE FROM return_snapshots WHERE account_id = %s AND canonical_ticker = %s"

_DELETE_BY_ACCOUNT_SQL = "DELETE FROM return_snapshots WHERE account_id = %s"

_INSERT_SQL = """
    INSERT INTO return_snapshots (
        account_id, canonical_ticker, price_date, quantity, market_value_native,
        market_value_eur, cost_basis_eur, unrealized_return_eur, unrealized_return_pct,
        dividends_received_eur, total_return_eur, total_return_pct
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_LIST_TICKERS_SQL = "SELECT DISTINCT canonical_ticker FROM return_snapshots WHERE account_id = %s"


def replace_snapshots(
    conn, *, account_id: int, canonical_ticker: str, snapshots: Sequence[ReturnSnapshot]
) -> int:
    """Replace every return snapshot for one (account, ticker) with `snapshots`: delete, then
    one batched insert. Deletes even when `snapshots` is empty. Returns the count inserted. Does
    not commit -- the caller wraps this in one transaction per pair."""
    with conn.cursor() as cur:
        cur.execute(_DELETE_SQL, (account_id, canonical_ticker))
        if snapshots:
            cur.executemany(
                _INSERT_SQL,
                [
                    (
                        s.account_id,
                        s.canonical_ticker,
                        s.price_date,
                        s.quantity,
                        s.market_value_native,
                        s.market_value_eur,
                        s.cost_basis_eur,
                        s.unrealized_return_eur,
                        s.unrealized_return_pct,
                        s.dividends_received_eur,
                        s.total_return_eur,
                        s.total_return_pct,
                    )
                    for s in snapshots
                ],
            )
    return len(snapshots)


def list_tickers(conn, account_id: int) -> list[str]:
    """Every canonical_ticker this account has return snapshots for -- lets a rebuild find
    tickers with no position snapshots left (a deleted transaction, a re-pointed mapping) so it
    can prune them."""
    with conn.cursor() as cur:
        cur.execute(_LIST_TICKERS_SQL, (account_id,))
        return [row[0] for row in cur.fetchall()]


def delete_snapshots(conn, *, account_id: int, canonical_ticker: str) -> int:
    """Removes every return snapshot for one (account, ticker) with nothing to replace it.
    Returns the row count removed. Does not commit -- same as replace_snapshots."""
    with conn.cursor() as cur:
        cur.execute(_DELETE_SQL, (account_id, canonical_ticker))
        return cur.rowcount


def delete_by_account(conn, account_id: int) -> int:
    """Removes every return snapshot in one account, whatever its ticker, ahead of deleting the
    account itself. Returns the row count removed. Does not commit."""
    with conn.cursor() as cur:
        cur.execute(_DELETE_BY_ACCOUNT_SQL, (account_id,))
        return cur.rowcount


def list_snapshots(
    conn,
    *,
    account_id: int,
    canonical_ticker: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> list[ReturnSnapshot]:
    """This account's return history, ordered by ticker then date -- optionally narrowed to one
    ticker and/or a date range, for a return-over-time read. `canonical_ticker`/`start`/`end` are
    filtered at the SQL level, the same optional-filter shape
    transactions/repository.list_by_accounts uses for transaction_types."""
    sql = _LIST_SNAPSHOTS_SQL
    params: list[object] = [account_id]
    if canonical_ticker is not None:
        sql += " AND canonical_ticker = %s"
        params.append(canonical_ticker)
    if start is not None:
        sql += " AND price_date >= %s"
        params.append(start)
    if end is not None:
        sql += " AND price_date <= %s"
        params.append(end)
    sql += " ORDER BY canonical_ticker, price_date"

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [ReturnSnapshot(*row) for row in rows]


def list_latest_snapshots(conn, account_id: int) -> list[ReturnSnapshot]:
    """The most recent return snapshot per ticker for this account -- one row per
    canonical_ticker, for a "current returns" summary read without scanning the whole history."""
    with conn.cursor() as cur:
        cur.execute(_LIST_LATEST_SNAPSHOTS_SQL, (account_id,))
        rows = cur.fetchall()
    return [ReturnSnapshot(*row) for row in rows]
