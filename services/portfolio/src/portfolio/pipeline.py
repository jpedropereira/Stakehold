"""The flows -- orchestration that composes domain repositories, never touches SQL or HTTP
directly. jobs.py wraps each of these in configuration + a connection + a structured result; a
future HTTP API route would be a call into one of these and nothing else. See
docs/architecture.md's layering section.

Repositories are imported as modules, not as individual names, so unit tests can
monkeypatch.setattr(pipeline, "accounts_repository", fake) the same way auth's tests swap
service.py's repository modules.
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from portfolio.accounts import repository as accounts_repository
from portfolio.accounts.repository import Account
from portfolio.broker_import.base import BrokerParser, CashflowParser
from portfolio.broker_import.discovery import (
    discover_broker_parsers,
    discover_cashflow_parsers,
)
from portfolio.datastore_client.base import DataStoreClient
from portfolio.errors import (
    AccountLabelConflict,
    AccountNotFound,
    BrokerMismatch,
    BrokerParserNotFound,
    InvalidBroker,
    InvalidTransaction,
)
from portfolio.ticker_mapping.loader import TickerMappings
from portfolio.transactions import repository as transactions_repository
from portfolio.transactions.base import (
    FX_RATE_SOURCE_MANUAL,
    CanonicalTransaction,
    to_eur,
    validate,
)
from shared.db.connection import transaction

logger = logging.getLogger(__name__)

# Same pattern as data-store's SCRAPERS / MARKET_DATA_SOURCES -- a Protocol plus a registry dict,
# no branching in flow functions. Populated once, at import time, by discovering whatever
# implementations are installed under the two entry-point groups -- pipeline.py itself never
# imports a concrete parser. See docs/architecture.md's "Extension points" section.
BROKER_PARSERS: dict[str, BrokerParser] = discover_broker_parsers()
CASHFLOW_PARSERS: dict[str, CashflowParser] = discover_cashflow_parsers()


def list_available_brokers() -> list[str]:
    """The broker keys a parser is currently registered for. Pure, in-memory -- no connection."""
    return sorted(BROKER_PARSERS)


def add_account(conn, *, user_id: UUID, broker: str, account_label: str) -> int:
    """Create one account. Returns 1 if created, 0 if this (user, label) already exists under
    the same broker -- a safe no-op, reported upstream as nothing_to_do. `broker` is a free-text
    label, not checked against BROKER_PARSERS -- account creation (and therefore manual
    transaction entry) must work with zero broker-parser plugins installed; only
    import_transactions needs a registered parser, and checks for one itself. Raises
    InvalidBroker for a blank/whitespace-only broker, or AccountLabelConflict if the label is
    already taken by this user under a *different* broker -- silently swallowing that
    disagreement would be exactly the silent-success the logging rule forbids.
    """
    if not broker.strip():
        raise InvalidBroker("broker must not be blank")

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


def import_transactions(
    conn, *, user_id: UUID, account_label: str, broker: str, file_path: Path
) -> int:
    """Parse one broker export file and persist its transactions for one account.

    Returns the count actually written -- re-importing a file whose date range overlaps a
    previous import is a safe no-op for the rows already seen, via the same
    (account_id, broker_transaction_ref) idempotency add_manual_transaction relies on. Raises
    BrokerParserNotFound if no parser is registered for --broker, AccountNotFound if --account
    doesn't resolve for this user (the isolation chokepoint), BrokerMismatch if --broker
    disagrees with the resolved account's own broker, or whatever the parser/validate() raise
    for a malformed file.
    """
    parser = BROKER_PARSERS.get(broker)
    if parser is None:
        raise BrokerParserNotFound(
            f"no parser registered for broker {broker!r} -- check for a typo, or install the "
            f"plugin package that provides a parser for it. Currently registered: "
            f"{sorted(BROKER_PARSERS) or '(none)'}"
        )

    account = accounts_repository.get_by_label(conn, user_id=user_id, account_label=account_label)
    if account is None:
        raise AccountNotFound(f"no account {account_label!r} for this user")
    if account.broker != broker:
        raise BrokerMismatch(
            f"account {account_label!r} is a {account.broker!r} account, not {broker!r}"
        )

    transactions = parser.parse(file_path, account.id)
    for txn in transactions:
        validate(txn)

    with transaction(conn):
        return transactions_repository.insert_transactions(conn, transactions)


def list_unmapped_instruments(
    conn, *, user_id: UUID, mappings: TickerMappings
) -> list[tuple[str, str, str]]:
    """Every (kind, identifier, broker) this user's transactions reference that `mappings` can't
    resolve to a canonical_ticker -- see docs/architecture.md's "unmapped instrument blocks its
    own rebuild, loudly" rule. Read-only; diffed in application code since the mapping is a YAML
    file, not a database table (docs/schema.md)."""
    instruments = transactions_repository.list_distinct_instruments(conn, user_id)
    return [
        (kind, identifier, broker)
        for kind, identifier, broker in instruments
        if mappings.resolve(kind, identifier, broker=broker) is None
    ]


def import_cashflow(
    conn,
    *,
    user_id: UUID,
    account_label: str,
    broker: str,
    file_path: Path,
    datastore_client: DataStoreClient,
) -> int:
    """Parse and persist one broker's cash-ledger export (deposits, withdrawals, dividends, ...)
    for one account, via the CASHFLOW_PARSERS registry -- the separate registry
    import_transactions/BROKER_PARSERS doesn't share, since a cash-ledger format needs a live
    FX-rate lookup mid-parse that a plain BrokerParser never carries. See broker_import/base.py's
    CashflowParser Protocol.

    Returns the count actually written, idempotent the same way import_transactions is. Raises
    BrokerParserNotFound if no cashflow parser is registered for --broker, AccountNotFound if
    --account doesn't resolve for this user (the isolation chokepoint), BrokerMismatch if
    --broker disagrees with the resolved account's own broker, or whatever the parser's
    parse()/validate() raise for a malformed file or an unresolvable FX rate.
    """
    parser = CASHFLOW_PARSERS.get(broker)
    if parser is None:
        raise BrokerParserNotFound(
            f"no cashflow parser registered for broker {broker!r} -- check for a typo, or "
            f"install the plugin package that provides one. Currently registered: "
            f"{sorted(CASHFLOW_PARSERS) or '(none)'}"
        )

    account = accounts_repository.get_by_label(conn, user_id=user_id, account_label=account_label)
    if account is None:
        raise AccountNotFound(f"no account {account_label!r} for this user")
    if account.broker != broker:
        raise BrokerMismatch(
            f"account {account_label!r} is a {account.broker!r} account, not {broker!r}"
        )

    def resolve_fx_rate(currency: str, on_date: date) -> Decimal | None:
        return datastore_client.get_fx_rate("EUR", currency, on_date)

    transactions = parser.parse(file_path, account.id, resolve_fx_rate)
    for txn in transactions:
        validate(txn)

    with transaction(conn):
        return transactions_repository.insert_transactions(conn, transactions)
