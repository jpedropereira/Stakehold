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

from portfolio.returns.calculator import ReturnSnapshot

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
