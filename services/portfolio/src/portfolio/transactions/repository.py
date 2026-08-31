"""Persistence for the immutable transaction ledger.

The one write path both manual entry (pipeline.py) and, later, broker import (task 6) share --
splitting persistence into broker_import/ would let the two paths drift. See
docs/architecture.md's module table.
"""

from collections.abc import Sequence

from portfolio.transactions.base import CanonicalTransaction

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
