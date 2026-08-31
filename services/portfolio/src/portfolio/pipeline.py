"""The flows -- orchestration that composes domain repositories, never touches SQL or HTTP
directly. jobs.py wraps each of these in configuration + a connection + a structured result; a
future HTTP API route would be a call into one of these and nothing else. See
docs/architecture.md's layering section.

Repositories are imported as modules, not as individual names, so unit tests can
monkeypatch.setattr(pipeline, "accounts_repository", fake) the same way auth's tests swap
service.py's repository modules.
"""

import logging
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from portfolio.accounts import repository as accounts_repository
from portfolio.accounts.repository import Account
from portfolio.errors import (
    AccountLabelConflict,
    AccountNotFound,
    InvalidTransaction,
    UnknownBroker,
)
from portfolio.transactions import repository as transactions_repository
from portfolio.transactions.base import (
    FX_RATE_SOURCE_MANUAL,
    CanonicalTransaction,
    to_eur,
    validate,
)
from shared.db.connection import transaction

logger = logging.getLogger(__name__)

# Placeholder until task 6 builds broker_import/'s BROKER_PARSERS registry, at which point this
# becomes tuple(BROKER_PARSERS) -- see docs/architecture.md.
KNOWN_BROKERS = ("acme", "globex")


def add_account(conn, *, user_id: UUID, broker: str, account_label: str) -> int:
    """Create one account. Returns 1 if created, 0 if this (user, label) already exists under
    the same broker -- a safe no-op, reported upstream as nothing_to_do. Raises UnknownBroker
    for an unrecognized broker, or AccountLabelConflict if the label is already taken by this
    user under a *different* broker -- silently swallowing that disagreement would be exactly
    the silent-success the logging rule forbids.
    """
    if broker not in KNOWN_BROKERS:
        raise UnknownBroker(f"unrecognized broker: {broker!r}")

    with transaction(conn):
        account_id = accounts_repository.insert_account(
            conn, user_id=user_id, broker=broker, account_label=account_label
        )
        if account_id is not None:
            return 1

        existing = accounts_repository.get_by_label(
            conn, user_id=user_id, account_label=account_label
        )
        if existing is None:
            # insert_account just reported a conflict on this (user_id, account_label), but the
            # row is gone by the time we re-read it -- a concurrent delete raced us. Not a state
            # this caller can resolve; surface it as a clean domain error rather than crashing
            # on `.broker` of None.
            raise AccountNotFound(
                f"account {account_label!r} was deleted concurrently with this request"
            )
        if existing.broker != broker:
            raise AccountLabelConflict(
                f"account {account_label!r} already exists for this user with broker "
                f"{existing.broker!r}, not {broker!r}"
            )
        return 0


def list_accounts(conn, *, user_id: UUID) -> list[Account]:
    return accounts_repository.list_by_user(conn, user_id)


def add_manual_transaction(
    conn,
    *,
    user_id: UUID,
    account_label: str,
    broker_transaction_ref: str,
    transaction_type: str,
    trade_datetime: datetime,
    native_amount: Decimal,
    native_currency: str,
    instrument_identifier: str | None = None,
    instrument_identifier_kind: str | None = None,
    quantity: Decimal | None = None,
    price: Decimal | None = None,
    fx_rate: Decimal | None = None,
    fees_native: Decimal | None = None,
) -> int:
    """Record one manually-entered transaction. Returns 1 if written, 0 if
    (account, broker_transaction_ref) already existed -- manual entry's own idempotency, since
    --ref is mandatory on the CLI. Raises AccountNotFound if account_label doesn't resolve for
    this user (the isolation chokepoint in action) or InvalidTransaction if the entry fails
    transactions/base.py's rules -- including a missing --fx-rate for a non-EUR currency.
    """
    account = accounts_repository.get_by_label(conn, user_id=user_id, account_label=account_label)
    if account is None:
        raise AccountNotFound(f"no account {account_label!r} for this user")

    if native_currency == "EUR":
        if fx_rate is not None:
            raise InvalidTransaction("a EUR transaction must not carry an fx_rate")
        fx_rate_source = None
    else:
        if fx_rate is None:
            raise InvalidTransaction(f"{native_currency} transactions require --fx-rate")
        fx_rate_source = FX_RATE_SOURCE_MANUAL

    txn = CanonicalTransaction(
        account_id=account.id,
        broker_transaction_ref=broker_transaction_ref,
        transaction_type=transaction_type,
        trade_datetime=trade_datetime,
        instrument_identifier=instrument_identifier,
        instrument_identifier_kind=instrument_identifier_kind,
        quantity=quantity,
        price=price,
        native_currency=native_currency,
        native_amount=native_amount,
        eur_amount=to_eur(native_amount, fx_rate),
        fx_rate=fx_rate,
        fx_rate_source=fx_rate_source,
        fees_native=fees_native,
    )
    validate(txn)

    with transaction(conn):
        return transactions_repository.insert_transactions(conn, [txn])
