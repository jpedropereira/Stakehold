"""The flows -- orchestration that composes domain repositories, never touches SQL or HTTP
directly. jobs.py wraps each of these in configuration + a connection + a structured result; a
future HTTP API route would be a call into one of these and nothing else. See
docs/architecture.md's layering section.

Repositories are imported as modules, not as individual names, so unit tests can
monkeypatch.setattr(pipeline, "accounts_repository", fake) the same way auth's tests swap
service.py's repository modules.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
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
from portfolio.datastore_client.base import DataStoreClient, TickerInfo
from portfolio.errors import (
    AccountLabelConflict,
    AccountNotFound,
    BrokerParserNotFound,
    BrokerPluginError,
    InvalidBroker,
    InvalidTransaction,
)
from portfolio.positions import repository as positions_repository
from portfolio.positions.calculator import replay
from portfolio.returns import repository as returns_repository
from portfolio.returns.calculator import DividendEvent, compute_returns
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
#
# A broken plugin installation must not take down every CLI command -- account creation and
# manual entry need neither registry (see add_account's docstring), so a discovery failure is
# caught here and re-raised lazily, only from the functions that actually read the affected
# registry (list_available_brokers/import_transactions for BROKER_PARSERS,
# import_cashflow for CASHFLOW_PARSERS).
BROKER_PARSERS: dict[str, BrokerParser] = {}
CASHFLOW_PARSERS: dict[str, CashflowParser] = {}
_BROKER_DISCOVERY_ERROR: BrokerPluginError | None = None
_CASHFLOW_DISCOVERY_ERROR: BrokerPluginError | None = None

try:
    BROKER_PARSERS = discover_broker_parsers()
except BrokerPluginError as exc:
    _BROKER_DISCOVERY_ERROR = exc
    logger.error("broker-parser plugin discovery failed at import time -- %s", exc)

try:
    CASHFLOW_PARSERS = discover_cashflow_parsers()
except BrokerPluginError as exc:
    _CASHFLOW_DISCOVERY_ERROR = exc
    logger.error("cashflow-parser plugin discovery failed at import time -- %s", exc)


def list_available_brokers() -> list[str]:
    """The broker keys a parser is currently registered for. Pure, in-memory -- no connection."""
    if _BROKER_DISCOVERY_ERROR is not None:
        raise _BROKER_DISCOVERY_ERROR
    return sorted(BROKER_PARSERS)


def _resolve_account(conn, *, user_id: UUID, account_label: str) -> Account:
    """Resolve `account_label` to this user's own Account, or raise the isolation chokepoint's
    domain error. Shared by every flow that starts from a caller-supplied --account label."""
    account = accounts_repository.get_by_label(conn, user_id=user_id, account_label=account_label)
    if account is None:
        raise AccountNotFound(f"no account {account_label!r} for this user")
    return account


def _resolve_import_context(
    conn,
    *,
    registry: dict[str, object],
    discovery_error: BrokerPluginError | None,
    parser_noun: str,
    user_id: UUID,
    account_label: str,
) -> tuple[object, Account]:
    """Shared by import_transactions/import_cashflow: resolve the target account and its
    registered parser, or raise the same domain errors either flow raises on a bad --account.
    The account's own `broker` column is the source of truth for which parser to use -- there is
    no separate --broker argument to agree or disagree with it. `parser_noun` only changes
    BrokerParserNotFound's message ("parser" vs "cashflow parser")."""
    if discovery_error is not None:
        raise discovery_error

    account = _resolve_account(conn, user_id=user_id, account_label=account_label)

    parser = registry.get(account.broker)
    if parser is None:
        raise BrokerParserNotFound(
            f"no {parser_noun} registered for broker {account.broker!r} (account "
            f"{account_label!r}) -- check for a typo, or install the plugin package that "
            f"provides one. Currently registered: {sorted(registry) or '(none)'}"
        )
    return parser, account


_MAX_BROKER_LENGTH = 50  # must match accounts.broker's VARCHAR(50) in db/schema.sql


def add_account(conn, *, user_id: UUID, broker: str, account_label: str) -> int:
    """Create one account. Returns 1 if created, 0 if this (user, label) already exists under
    the same broker -- a safe no-op, reported upstream as nothing_to_do. `broker` is a free-text
    label, not checked against BROKER_PARSERS -- account creation (and therefore manual
    transaction entry) must work with zero broker-parser plugins installed; only
    import_transactions needs a registered parser, and checks for one itself. Raises
    InvalidBroker for a blank/whitespace-only broker or one longer than the column can hold, or
    AccountLabelConflict if the label is already taken by this user under a *different* broker --
    silently swallowing that disagreement would be exactly the silent-success the logging rule
    forbids. Stored with surrounding whitespace stripped, so incidental whitespace (a stray
    space from copy-pasting --broker) can't make an otherwise-identical broker compare unequal
    later, e.g. against import_transactions/import_cashflow's own --broker.
    """
    broker = broker.strip()
    if not broker:
        raise InvalidBroker("broker must not be blank")
    if len(broker) > _MAX_BROKER_LENGTH:
        raise InvalidBroker(f"broker must be at most {_MAX_BROKER_LENGTH} characters: {broker!r}")

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
    account = _resolve_account(conn, user_id=user_id, account_label=account_label)

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


def import_transactions(conn, *, user_id: UUID, account_label: str, file_path: Path) -> int:
    """Parse one broker export file and persist its transactions for one account.

    Returns the count actually written -- re-importing a file whose date range overlaps a
    previous import is a safe no-op for the rows already seen, via the same
    (account_id, broker_transaction_ref) idempotency add_manual_transaction relies on. The
    parser used is looked up from the resolved account's own `broker` column -- there is no
    separate --broker argument to keep in agreement with it. Raises AccountNotFound if
    --account doesn't resolve for this user (the isolation chokepoint), BrokerParserNotFound if
    no parser is registered for that account's broker, or whatever the parser/validate() raise
    for a malformed file.
    """
    parser, account = _resolve_import_context(
        conn,
        registry=BROKER_PARSERS,
        discovery_error=_BROKER_DISCOVERY_ERROR,
        parser_noun="parser",
        user_id=user_id,
        account_label=account_label,
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
    file_path: Path,
    datastore_client: DataStoreClient,
) -> int:
    """Parse and persist one broker's cash-ledger export (deposits, withdrawals, dividends, ...)
    for one account, via the CASHFLOW_PARSERS registry -- the separate registry
    import_transactions/BROKER_PARSERS doesn't share, since a cash-ledger format needs a live
    FX-rate lookup mid-parse that a plain BrokerParser never carries. See broker_import/base.py's
    CashflowParser Protocol.

    Returns the count actually written, idempotent the same way import_transactions is. The
    parser used is looked up from the resolved account's own `broker` column, same as
    import_transactions. Raises AccountNotFound if --account doesn't resolve for this user (the
    isolation chokepoint), BrokerParserNotFound if no cashflow parser is registered for that
    account's broker, or whatever the parser's parse()/validate() raise for a malformed file or
    an unresolvable FX rate.
    """
    parser, account = _resolve_import_context(
        conn,
        registry=CASHFLOW_PARSERS,
        discovery_error=_CASHFLOW_DISCOVERY_ERROR,
        parser_noun="cashflow parser",
        user_id=user_id,
        account_label=account_label,
    )

    # A cash-ledger export commonly has many rows sharing one (currency, date) -- a dividend
    # batch, several same-day fees -- so this cache turns N row-level HTTP calls into one per
    # distinct pair actually seen in this file.
    fx_rate_cache: dict[tuple[str, date], Decimal | None] = {}

    def resolve_fx_rate(currency: str, on_date: date) -> Decimal | None:
        key = (currency, on_date)
        if key not in fx_rate_cache:
            fx_rate_cache[key] = datastore_client.get_fx_rate("EUR", currency, on_date)
        return fx_rate_cache[key]

    transactions = parser.parse(file_path, account.id, resolve_fx_rate)
    for txn in transactions:
        validate(txn)

    with transaction(conn):
        return transactions_repository.insert_transactions(conn, transactions)


_POSITION_MOVING_TYPES = ("BUY", "SELL")


@dataclass(frozen=True)
class RebuildOutcome:
    snapshots_saved: int
    positions_rebuilt: int
    positions_removed: int
    blocked: list[tuple[str, str, str]]  # (kind, identifier, broker)
    anomalies: list[str]
    positions_with_anomalies: int
    failed_pairs: list[tuple[str, str, str]]  # (account_label, canonical_ticker, error message)


def rebuild_positions(
    conn, *, user_id: UUID, mappings: TickerMappings, account_label: str | None = None
) -> RebuildOutcome:
    """Replay every relevant account's transaction log into position_snapshots.

    Needs no price or FX data -- quantity and average cost are pure functions of the transaction
    log (docs/architecture.md). Scoped per account: an --account rebuilds only that account
    (raising AccountNotFound if it doesn't resolve for this user, the isolation chokepoint);
    omitting it rebuilds every account this user owns. Every account's transaction log is fetched
    in one batched read (transactions_repository.list_by_accounts) rather than one query per
    account, so this scales in round trips with the number of rebuilds, not the number of
    accounts in it.

    An unmapped instrument blocks only its own ticker -- reported in `blocked`, never a silent
    skip -- while every other (account, ticker) in the run still gets rebuilt. A ledger anomaly
    (the same ticker traded in two currencies within one account, or a running quantity going
    negative) is reported in `anomalies` but never blocks anything -- see
    positions/calculator.py's "warn but compute" policy.

    A known sharp edge, not fixable from inside this function: blocking is keyed by
    (instrument_identifier_kind, instrument_identifier), the only key `mappings.resolve` can look
    up -- there is no way to know, from an unresolved identifier alone, which (if any) resolved
    ticker it's actually the same real-world instrument as. If one account's transactions for a
    single holding are split across a mapped identifier kind (e.g. ISIN) and an unmapped one for
    that broker (e.g. SYMBOL), that ticker's snapshot is built only from the mapped rows -- it is
    silently a partial view, not the blocked-and-untouched outcome the paragraph above describes,
    because the code has no way to know the two identifiers name the same instrument until
    configs/ticker_mappings.yaml says so for both. The fix lives in that YAML file (map every
    identifier kind a broker actually emits for an instrument), not in this function.

    Each (account, ticker) pair's delete-then-insert is wrapped in its own transaction here,
    rather than inside positions/repository.py, so that pair's replacement is atomic without
    inverting the "repositories never commit" rule for that one module. That same per-pair
    boundary is also this function's failure-isolation unit: a driver-level failure writing or
    pruning one pair (a constraint violation, a dropped connection) is caught, logged with its
    traceback, and reported in `failed_pairs` rather than aborting the whole run -- every other
    (account, ticker) still gets attempted, the same partial-tolerance already extended to
    unmapped instruments and anomalies. `shared.db.connection.transaction` rolls back on any
    exception before this catches it, which is what leaves the connection usable for the next
    pair rather than stuck in Postgres's aborted-transaction state.
    """
    if account_label is not None:
        accounts = [_resolve_account(conn, user_id=user_id, account_label=account_label)]
    else:
        accounts = accounts_repository.list_by_user(conn, user_id)

    blocked: list[tuple[str, str, str]] = []
    anomalies: list[str] = []
    failed_pairs: list[tuple[str, str, str]] = []
    snapshots_saved = 0
    positions_rebuilt = 0
    positions_removed = 0
    positions_with_anomalies = 0

    transactions_by_account = transactions_repository.list_by_accounts(
        conn, [account.id for account in accounts], transaction_types=_POSITION_MOVING_TYPES
    )

    for account in accounts:
        by_ticker: dict[str, list[CanonicalTransaction]] = {}
        account_blocked: list[tuple[str, str, str]] = []
        for txn in transactions_by_account.get(account.id, []):
            ticker = mappings.resolve(
                txn.instrument_identifier_kind, txn.instrument_identifier, broker=account.broker
            )
            if ticker is None:
                account_blocked.append(
                    (txn.instrument_identifier_kind, txn.instrument_identifier, account.broker)
                )
                continue
            by_ticker.setdefault(ticker, []).append(txn)

        for ticker, ticker_transactions in by_ticker.items():
            snapshots, ticker_anomalies = replay(account.id, ticker, ticker_transactions)
            anomalies.extend(ticker_anomalies)
            if ticker_anomalies:
                positions_with_anomalies += 1
            try:
                with transaction(conn):
                    saved = positions_repository.replace_snapshots(
                        conn, account_id=account.id, canonical_ticker=ticker, snapshots=snapshots
                    )
            except Exception as exc:
                failed_pairs.append((account.account_label, ticker, str(exc)))
                logger.error(
                    "rebuild-positions: account %r ticker %s -- failed to save snapshots, "
                    "leaving its previous snapshots in place -- %s",
                    account.account_label,
                    ticker,
                    exc,
                    exc_info=True,
                )
                continue
            snapshots_saved += saved
            positions_rebuilt += 1
            logger.info(
                "rebuild-positions: account %r ticker %s -- %d snapshot(s) saved.",
                account.account_label,
                ticker,
                saved,
            )

        # Pruning a ticker this rebuild no longer produced (e.g. a mapping was re-pointed) is
        # only safe when nothing in this account is currently blocked -- a blocked instrument's
        # old canonical_ticker (from before its mapping was removed) is indistinguishable from a
        # genuinely stale one here, and "blocked means don't touch, not delete"
        # (docs/architecture.md) means the ambiguous case must not delete.
        if not account_blocked:
            stale_tickers = set(positions_repository.list_tickers(conn, account.id)) - set(
                by_ticker
            )
            for ticker in stale_tickers:
                try:
                    with transaction(conn):
                        positions_repository.delete_snapshots(
                            conn, account_id=account.id, canonical_ticker=ticker
                        )
                except Exception as exc:
                    failed_pairs.append((account.account_label, ticker, str(exc)))
                    logger.error(
                        "rebuild-positions: account %r ticker %s -- failed to remove stale "
                        "snapshots -- %s",
                        account.account_label,
                        ticker,
                        exc,
                        exc_info=True,
                    )
                    continue
                positions_removed += 1
                logger.info(
                    "rebuild-positions: account %r ticker %s -- stale snapshot(s) removed.",
                    account.account_label,
                    ticker,
                )

        blocked.extend(account_blocked)

    return RebuildOutcome(
        snapshots_saved=snapshots_saved,
        positions_rebuilt=positions_rebuilt,
        positions_removed=positions_removed,
        blocked=sorted(set(blocked)),
        anomalies=anomalies,
        positions_with_anomalies=positions_with_anomalies,
        failed_pairs=failed_pairs,
    )


_DIVIDEND_TYPES = ("DIVIDEND",)


@dataclass(frozen=True)
class ReturnsRebuildOutcome:
    snapshots_saved: int
    tickers_rebuilt: int
    blocked: list[tuple[str, str]]  # (account_label, canonical_ticker) -- not tracked by data-store
    failed_pairs: list[tuple[str, str, str]]  # (account_label, canonical_ticker, error message)


def rebuild_returns(
    conn,
    *,
    user_id: UUID,
    mappings: TickerMappings,
    datastore_client: DataStoreClient,
    account_label: str | None = None,
) -> ReturnsRebuildOutcome:
    """Compute return_snapshots for every (account, ticker) pair that ever had a position,
    from position_snapshots plus data-store's price/FX series plus this pair's own DIVIDEND
    transactions (docs/architecture.md's cross-service prerequisite; docs/schema.md's
    return_snapshots columns).

    Scoped per account, same as rebuild_positions: an --account rebuilds only that account
    (raising AccountNotFound if it doesn't resolve for this user), omitting it rebuilds every
    account this user owns. `mappings` is needed here for exactly one thing --
    canonical_ticker itself is already resolved on position_snapshots -- resolving each
    DIVIDEND transaction's instrument to the same canonical_ticker its BUY/SELL rows resolved
    to, via the same mappings.resolve rebuild_positions already uses. A DIVIDEND row that
    doesn't resolve is silently excluded from dividends_received_eur, the same sharp edge
    already documented for positions (an instrument split across a mapped and an unmapped
    identifier kind for one broker) -- it does not block the pair, since the ticker may still
    be fully rebuildable from its BUY/SELL rows.

    A ticker data-store has never ingested, or has no computed returns for (`get_ticker_info`),
    blocks only that (account, ticker) pair -- reported in `blocked`, never a silent skip, same
    "never a silent skip" contract rebuild_positions established for unmapped instruments. A
    `DataStoreApiError` (or any other failure) fetching or saving one pair is caught, logged,
    and reported in `failed_pairs` rather than aborting every other account/ticker still left to
    process -- deliberately broader than rebuild_positions's own per-pair catch, which only
    wraps driver-level exceptions: this is the first rebuild flow with an HTTP dependency, and a
    data-store call failing for one ticker must not abort the run any more than a bad database
    write would.

    The window fetched per pair is bounded, not open-ended (docs/architecture.md's ReturnSnapshot
    note): from the position's first stored snapshot (always its opening date) through today if
    still open, or through its closing date if the latest snapshot shows zero quantity --
    `compute_returns` itself never invents a date outside what data-store's price series actually
    returns for that window.
    """
    if account_label is not None:
        accounts = [_resolve_account(conn, user_id=user_id, account_label=account_label)]
    else:
        accounts = accounts_repository.list_by_user(conn, user_id)

    dividend_txns_by_account = transactions_repository.list_by_accounts(
        conn, [account.id for account in accounts], transaction_types=_DIVIDEND_TYPES
    )

    blocked: list[tuple[str, str]] = []
    failed_pairs: list[tuple[str, str, str]] = []
    snapshots_saved = 0
    tickers_rebuilt = 0
    ticker_info_cache: dict[str, TickerInfo] = {}

    for account in accounts:
        dividends_by_ticker: dict[str, list[DividendEvent]] = {}
        for txn in dividend_txns_by_account.get(account.id, []):
            ticker = mappings.resolve(
                txn.instrument_identifier_kind, txn.instrument_identifier, broker=account.broker
            )
            if ticker is None:
                continue
            dividends_by_ticker.setdefault(ticker, []).append(
                DividendEvent(
                    date=txn.trade_datetime.astimezone(UTC).date(), eur_amount=txn.eur_amount
                )
            )

        for ticker in positions_repository.list_tickers(conn, account.id):
            if ticker not in ticker_info_cache:
                try:
                    ticker_info_cache[ticker] = datastore_client.get_ticker_info(ticker)
                except Exception as exc:
                    failed_pairs.append((account.account_label, ticker, str(exc)))
                    logger.error(
                        "rebuild-returns: account %r ticker %s -- failed to check data-store "
                        "for ticker info -- %s",
                        account.account_label,
                        ticker,
                        exc,
                        exc_info=True,
                    )
                    continue

            ticker_info = ticker_info_cache[ticker]
            if not ticker_info.exists or not ticker_info.has_returns:
                blocked.append((account.account_label, ticker))
                logger.warning(
                    "rebuild-returns: ticker %s has no computed returns in data-store -- check "
                    "it's tracked and calculate-returns has run for it.",
                    ticker,
                )
                continue

            position_snapshots = positions_repository.list_snapshots(
                conn, account_id=account.id, canonical_ticker=ticker
            )
            if not position_snapshots:
                continue

            start = position_snapshots[0].as_of_date
            end = (
                position_snapshots[-1].as_of_date
                if position_snapshots[-1].quantity == 0
                else date.today()
            )

            try:
                prices = datastore_client.get_price_series(ticker, start, end)
                currencies = {s.native_currency for s in position_snapshots} - {"EUR"}
                fx_rates = {
                    currency: datastore_client.get_fx_rate_series("EUR", currency, start, end)
                    for currency in currencies
                }
                return_snapshots = compute_returns(
                    account.id,
                    ticker,
                    position_snapshots,
                    prices,
                    fx_rates,
                    dividends_by_ticker.get(ticker, []),
                )
                with transaction(conn):
                    saved = returns_repository.upsert_snapshots(conn, return_snapshots)
            except Exception as exc:
                failed_pairs.append((account.account_label, ticker, str(exc)))
                logger.error(
                    "rebuild-returns: account %r ticker %s -- failed to compute/save return "
                    "snapshots -- %s",
                    account.account_label,
                    ticker,
                    exc,
                    exc_info=True,
                )
                continue

            snapshots_saved += saved
            tickers_rebuilt += 1
            logger.info(
                "rebuild-returns: account %r ticker %s -- %d return snapshot(s) saved.",
                account.account_label,
                ticker,
                saved,
            )

    return ReturnsRebuildOutcome(
        snapshots_saved=snapshots_saved,
        tickers_rebuilt=tickers_rebuilt,
        blocked=sorted(set(blocked)),
        failed_pairs=failed_pairs,
    )
