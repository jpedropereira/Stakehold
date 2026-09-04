"""Persistence for derived, materialized return snapshots.

Loading strategy is a true upsert (INSERT ... ON CONFLICT ... DO UPDATE), matching
market_returns exactly -- unlike position_snapshots, the trading-day calendar here comes from
data-store's external price series, not something rebuild_returns invents, so a rebuild only
ever overwrites the *values* at an already-written date. See docs/schema.md's "Loading strategy"
note on return_snapshots.

Does not commit anywhere in this module -- same as every other repository in this service.
pipeline.py wraps each (account, ticker) pair's upsert_snapshots call in its own
`with transaction(conn):`.
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

_UPSERT_SQL = """
    INSERT INTO return_snapshots (
        account_id, canonical_ticker, price_date, quantity, market_value_native,
        market_value_eur, cost_basis_eur, unrealized_return_eur, unrealized_return_pct,
        dividends_received_eur, total_return_eur, total_return_pct
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (account_id, canonical_ticker, price_date) DO UPDATE SET
        quantity                = EXCLUDED.quantity,
        market_value_native     = EXCLUDED.market_value_native,
        market_value_eur        = EXCLUDED.market_value_eur,
        cost_basis_eur           = EXCLUDED.cost_basis_eur,
        unrealized_return_eur   = EXCLUDED.unrealized_return_eur,
        unrealized_return_pct   = EXCLUDED.unrealized_return_pct,
        dividends_received_eur  = EXCLUDED.dividends_received_eur,
        total_return_eur        = EXCLUDED.total_return_eur,
        total_return_pct        = EXCLUDED.total_return_pct,
        calculated_at            = now()
"""


def upsert_snapshots(conn, snapshots: Sequence[ReturnSnapshot]) -> int:
    """Insert or update every given ReturnSnapshot. Returns the count upserted. Does not
    commit -- the caller wraps this in one transaction per (account, ticker) pair."""
    if not snapshots:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            _UPSERT_SQL,
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
