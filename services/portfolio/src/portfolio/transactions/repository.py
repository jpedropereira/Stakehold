"""Persistence for the transaction ledger: append-only except for an explicit delete by its owner.

The one write path both manual entry (pipeline.py) and, later, broker import (task 6) share --
splitting persistence into broker_import/ would let the two paths drift. See
docs/architecture.md's module table.
"""

from collections.abc import Sequence
from uuid import UUID

from portfolio.transactions.base import CanonicalTransaction

_DISTINCT_INSTRUMENTS_SQL = """
    SELECT DISTINCT t.instrument_identifier_kind, t.instrument_identifier, a.broker
    FROM transactions t
    JOIN accounts a ON a.id = t.account_id
    WHERE a.user_id = %s AND t.instrument_identifier IS NOT NULL
    ORDER BY t.instrument_identifier_kind, t.instrument_identifier, a.broker
"""

# Unscoped: the admin page's view across every user. Only kind/identifier/broker leave here --
# never which user or account holds the instrument.
_DISTINCT_INSTRUMENTS_ALL_SQL = """
    SELECT DISTINCT t.instrument_identifier_kind, t.instrument_identifier, a.broker
    FROM transactions t
    JOIN accounts a ON a.id = t.account_id
    WHERE t.instrument_identifier IS NOT NULL
    ORDER BY t.instrument_identifier_kind, t.instrument_identifier, a.broker
"""

# %(broker)s IS NULL matches any broker: an ISIN mapping applies at every broker, a SYMBOL
# mapping only at its own.
_ACCOUNTS_USING_INSTRUMENT_SQL = """
    SELECT DISTINCT a.user_id, a.account_label
    FROM transactions t
    JOIN accounts a ON a.id = t.account_id
    WHERE t.instrument_identifier_kind = %(identifier_kind)s
      AND t.instrument_identifier = %(identifier)s
      AND (%(broker)s::text IS NULL OR a.broker = %(broker)s)
    ORDER BY a.user_id, a.account_label
"""

_LIST_BY_ACCOUNTS_SQL = """
    SELECT account_id, broker_transaction_ref, transaction_type, trade_datetime,
           instrument_identifier, instrument_identifier_kind, quantity, price,
           native_currency, native_amount, eur_amount, fx_rate, fx_rate_source, fees_native
    FROM transactions
    WHERE account_id = ANY(%s)
"""

_DELETE_ONE_SQL = "DELETE FROM transactions WHERE account_id = %s AND broker_transaction_ref = %s"

_DELETE_BY_ACCOUNT_SQL = "DELETE FROM transactions WHERE account_id = %s"

_INSERT_SQL = """
    INSERT INTO transactions (
        account_id, broker_transaction_ref, transaction_type, trade_datetime,
        instrument_identifier, instrument_identifier_kind, quantity, price,
        native_currency, native_amount, eur_amount, fx_rate, fx_rate_source, fees_native
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (account_id, broker_transaction_ref) DO NOTHING
"""


def insert_transactions(conn, transactions: Sequence[CanonicalTransaction]) -> int:
    """Insert every row, skipping one already seen for its (account_id, ref) pair.

    Returns the count actually written -- a re-import/re-entry is a safe no-op for rows already
    present, not a resync (see schema.md's "Loading strategy" for transactions). One execute()
    per row rather than executemany(): rowcount after a single-row execute is unambiguous, and
    executemany's aggregate rowcount isn't a guarantee this code wants to lean on. Does not
    commit -- the calling pipeline owns the transaction.
    """
    if not transactions:
        return 0

    saved = 0
    with conn.cursor() as cur:
        for txn in transactions:
            cur.execute(
                _INSERT_SQL,
                (
                    txn.account_id,
                    txn.broker_transaction_ref,
                    txn.transaction_type,
                    txn.trade_datetime,
                    txn.instrument_identifier,
                    txn.instrument_identifier_kind,
                    txn.quantity,
                    txn.price,
                    txn.native_currency,
                    txn.native_amount,
                    txn.eur_amount,
                    txn.fx_rate,
                    txn.fx_rate_source,
                    txn.fees_native,
                ),
            )
            saved += cur.rowcount
    return saved


def list_distinct_instruments(conn, user_id: UUID) -> list[tuple[str, str, str]]:
    """Every (kind, identifier, broker) triple this user's transactions reference.

    Powers list-unmapped-instruments: the caller diffs these against the loaded TickerMappings
    in application code, through the same TickerMappings.resolve a rebuild uses, so "unmapped"
    can never mean something different in the two places. Scoped through accounts.user_id, the
    isolation chokepoint, and includes the
    owning account's broker because SYMBOL mappings are broker-scoped (schema.md); joining here
    is also what removes the need for an unscoped account lookup.
    """
    with conn.cursor() as cur:
        cur.execute(_DISTINCT_INSTRUMENTS_SQL, (user_id,))
        return [(kind, identifier, broker) for kind, identifier, broker in cur.fetchall()]


def list_distinct_instruments_all(conn) -> list[tuple[str, str, str]]:
    """list_distinct_instruments across every user, for the admin's global unmapped list."""
    with conn.cursor() as cur:
        cur.execute(_DISTINCT_INSTRUMENTS_ALL_SQL)
        return [(kind, identifier, broker) for kind, identifier, broker in cur.fetchall()]


def list_accounts_using_instrument(
    conn, *, identifier_kind: str, identifier: str, broker: str | None
) -> list[tuple[UUID, str]]:
    """(user_id, account_label) of every account whose transactions reference this instrument --
    the accounts a mapping change has to rebuild. `broker` None means any broker (an ISIN)."""
    with conn.cursor() as cur:
        cur.execute(
            _ACCOUNTS_USING_INSTRUMENT_SQL,
            {"identifier_kind": identifier_kind, "identifier": identifier, "broker": broker},
        )
        return [(user_id, account_label) for user_id, account_label in cur.fetchall()]


def list_by_accounts(
    conn, account_ids: Sequence[int], *, transaction_types: Sequence[str] | None = None
) -> dict[int, list[CanonicalTransaction]]:
    """Every one of `account_ids`' transaction logs in one round trip, grouped by account_id and
    each ordered oldest first -- the read the positions rebuild (task 7) replays. Avoids the N+1
    query a rebuild spanning every account a user owns would otherwise cost by fetching one
    account at a time. Unscoped by user_id, like insert_transactions: the caller only ever
    reaches this with account_ids already resolved through accounts/repository.py, the isolation
    chokepoint. `transaction_types`, when given, filters at the SQL level so rows a caller would
    only discard anyway (rebuild_positions' cash-only rows) never cross the wire. An account_id
    with no matching rows simply has no key -- read via `.get(account_id, [])`, not
    `[account_id]`."""
    if not account_ids:
        return {}

    sql = _LIST_BY_ACCOUNTS_SQL
    params: list[object] = [list(account_ids)]
    if transaction_types is not None:
        sql += " AND transaction_type = ANY(%s)"
        params.append(list(transaction_types))
    sql += " ORDER BY account_id, trade_datetime, broker_transaction_ref"

    with conn.cursor() as cur:
        cur.execute(sql, params)
        grouped: dict[int, list[CanonicalTransaction]] = {}
        for row in cur.fetchall():
            txn = CanonicalTransaction(*row)
            grouped.setdefault(txn.account_id, []).append(txn)
        return grouped


def list_by_account(
    conn, account_id: int, *, transaction_types: Sequence[str] | None = None
) -> list[CanonicalTransaction]:
    """One account's transaction log, oldest first -- a single-account view over
    list_by_accounts, for a caller that only ever needs one (e.g. a rebuild scoped via
    --account). Ordered by (trade_datetime, broker_transaction_ref) -- the ref is only a
    tiebreaker for same-timestamp rows, served by the existing (account_id, trade_datetime)
    index."""
    return list_by_accounts(conn, [account_id], transaction_types=transaction_types).get(
        account_id, []
    )


def delete_one(conn, *, account_id: int, broker_transaction_ref: str) -> int:
    """Removes one transaction, addressed by the same (account_id, broker_transaction_ref) key
    imports are idempotent on. Returns the row count removed (0 or 1). Unscoped by user_id, like
    insert_transactions: the caller resolves account_id through accounts/repository.py, the
    isolation chokepoint. Does not commit."""
    with conn.cursor() as cur:
        cur.execute(_DELETE_ONE_SQL, (account_id, broker_transaction_ref))
        return cur.rowcount


def delete_by_account(conn, account_id: int) -> int:
    """Removes every transaction in one account, ahead of deleting the account itself (the
    foreign key has no ON DELETE). Returns the row count removed. Unscoped by user_id, like
    delete_one. Does not commit."""
    with conn.cursor() as cur:
        cur.execute(_DELETE_BY_ACCOUNT_SQL, (account_id,))
        return cur.rowcount
