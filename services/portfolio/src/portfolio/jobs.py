"""Runnable units: load settings, open a connection, run one flow, close it, return a result.

The seam every entry point goes through. `cli.py` is argument parsing plus a call into here,
and `api/routes.py` is the same shape -- anything that lived in only one of those two would be,
by construction, untested by the other. See docs/architecture.md's layering section.

Every job takes an optional `settings: Settings | None = None`, defaulting to a fresh
`load_settings()` -- `cli.py` never passes one, so its behaviour is unchanged. `api/routes.py`
passes `create_app()`'s own injected `Settings` instead, so a test app built with a known
`Settings` (docs/architecture.md's "HTTP API" section) actually reaches the database it names,
rather than every route but `/health` silently falling back to the environment.
"""

import logging
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from portfolio import pipeline
from portfolio.accounts.repository import Account
from portfolio.datastore_client.http_client import HttpDataStoreClient
from portfolio.run_result import build_run_result
from portfolio.settings import Settings, SettingsError, load_settings
from portfolio.ticker_mapping.repository import TickerMappingRow
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from shared.db.connection import apply_schema, get_connection, transaction
from shared.logging_config import configure_logging

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent / "db" / "schema.sql"

_TARGET_TABLES = (
    "accounts",
    "transactions",
    "position_snapshots",
    "return_snapshots",
    "ticker_mappings",
)


def _existing_tables(conn, schema: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name = ANY(%s)",
            (schema, list(_TARGET_TABLES)),
        )
        return {row[0] for row in cur.fetchall()}


def _ensure_role_and_schema(admin_conn, role: str, password: str) -> None:
    """Create this service's own non-superuser role and its like-named schema, if missing.

    `admin_conn` is a Postgres superuser only because the official postgres image's bootstrap
    (`POSTGRES_USER`) always grants that -- this is the one place that privilege gets used, so
    nothing after it (the rest of this run, or the running app) ever holds it. Owning its own
    schema is sufficient isolation on its own: Postgres denies cross-schema access by default,
    so `role` gets no more reach than `schema`, and no GRANTs beyond AUTHORIZATION are needed.
    A role connecting under its own name gets this schema first on its search_path for free
    (Postgres's built-in `"$user"` entry), which is why nothing downstream has to
    schema-qualify a single table name.
    """
    with transaction(admin_conn):
        with admin_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            if cur.fetchone() is None:
                # CREATE ROLE embeds the password as a literal, not a bind parameter -- with
                # `log_statement=mod` (docker-compose.yml) that would otherwise write it to the
                # server log verbatim. SET LOCAL reverts automatically when this transaction
                # commits, so it can't leak into later connections or statements.
                cur.execute("SET LOCAL log_statement = 'none'")
                cur.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(password)
                    )
                )
            cur.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION {}").format(
                    sql.Identifier(role), sql.Identifier(role)
                )
            )


def run_db_init() -> dict[str, int | str]:
    """Apply the schema. Idempotent -- see docs/schema.md on why every statement is guarded.

    The before/after table count is what makes a rerun distinguishable from a first run in the
    output: a fresh database reports `ok` and the tables it created, an already-migrated one
    reports `nothing_to_do`, and neither looks like a failure.
    """
    configure_logging()
    settings = load_settings()
    conn_info = conninfo_to_dict(settings.database_url)
    role = conn_info["user"]

    admin_conn = get_connection(settings.admin_database_url)
    try:
        logger.info("db init: ensuring role and schema %r exist.", role)
        _ensure_role_and_schema(admin_conn, role, conn_info["password"])
    finally:
        admin_conn.close()

    conn = get_connection(settings.database_url)
    try:
        logger.info(
            "db init: applying %s (tables: %s).", SCHEMA_PATH.name, ", ".join(_TARGET_TABLES)
        )
        before = _existing_tables(conn, role)
        apply_schema(conn, SCHEMA_PATH)
    finally:
        conn.close()

    created = len(set(_TARGET_TABLES) - before)
    logger.info("db init: schema applied, %d table(s) created.", created)
    # units_total is the work there was to do -- the tables that were missing -- so a rerun
    # that created nothing reports nothing_to_do rather than a hollow ok.
    return build_run_result(units_total=created, rows_saved=created, failed=0)


def _account_to_dict(account: Account) -> dict:
    return {
        "id": account.id,
        "account_label": account.account_label,
        "broker": account.broker,
        "created_at": account.created_at.isoformat(),
    }


def run_add_account(
    *, user_id: UUID, broker: str, account_label: str, settings: Settings | None = None
) -> dict[str, int | str]:
    """Create one account. See pipeline.add_account for what `created` means."""
    configure_logging()
    settings = settings or load_settings()
    conn = get_connection(settings.database_url)
    try:
        logger.info(
            "account add: creating account %r (broker=%r) for user %s.",
            account_label,
            broker,
            user_id,
        )
        created = pipeline.add_account(
            conn, user_id=user_id, broker=broker, account_label=account_label
        )
    finally:
        conn.close()

    logger.info("account add: done, %d account(s) created.", created)
    return build_run_result(
        units_total=created, rows_saved=created, failed=0, accounts_created=created
    )


def run_list_accounts(*, user_id: UUID, settings: Settings | None = None) -> dict[str, int | str]:
    """List every account owned by `user_id`. Saves nothing, so rows_saved is always 0 -- an
    empty result is still reported as `nothing_to_do`, not `ok`, per the logging rule."""
    configure_logging()
    settings = settings or load_settings()
    conn = get_connection(settings.database_url)
    try:
        logger.info("account list: listing accounts for user %s.", user_id)
        accounts = pipeline.list_accounts(conn, user_id=user_id)
    finally:
        conn.close()

    logger.info("account list: done, %d account(s) found.", len(accounts))
    result = build_run_result(
        units_total=len(accounts), rows_saved=0, failed=0, accounts_listed=len(accounts)
    )
    # The one job whose result carries a payload beyond named counts -- merged in after
    # build_run_result rather than through its **extra: int, which is for named counts only.
    result["accounts"] = [_account_to_dict(account) for account in accounts]
    return result


def run_list_available_brokers() -> dict[str, int | str]:
    """The broker keys a parser is currently registered for. Unlike every other job here, this
    one loads no settings and opens no connection -- BROKER_PARSERS is an in-memory,
    process-local fact, not something a database or config file can answer. Still routed
    through jobs.py rather than read directly by cli.py, so the "cli.py is argument parsing plus
    one call into jobs.py" rule holds even for the one job with nothing to configure or connect.
    Saves nothing, so rows_saved is always 0 -- an empty registry is reported as
    `nothing_to_do`, not `ok`, per the logging rule (the meaningful signal for "no broker
    plugins installed" is the count, not a failure status)."""
    configure_logging()
    logger.info("broker list: checking the parser registry.")
    brokers = pipeline.list_available_brokers()

    logger.info("broker list: done, %d broker(s) available.", len(brokers))
    result = build_run_result(
        units_total=len(brokers), rows_saved=0, failed=0, brokers_available=len(brokers)
    )
    result["brokers"] = brokers
    return result


def _require_datastore_client(settings: Settings, *, command: str) -> HttpDataStoreClient:
    """A data-store client for a command that can't run without one, or SettingsError naming
    the command. Checked per command rather than in settings.py, since most commands -- and
    manual entry of anything but a TICKER -- never touch data-store."""
    if not settings.datastore_api_url or not settings.internal_api_secret:
        raise SettingsError(
            f"DATASTORE_API_URL and INTERNAL_API_SECRET must both be set for {command}"
        )
    return HttpDataStoreClient(
        base_url=settings.datastore_api_url, internal_api_secret=settings.internal_api_secret
    )


# What a write job reports under `positions_rebuild` when it didn't run one, or when one raised.
# Every other value is the rebuild run's own `status` (ok / partial / nothing_to_do).
POSITIONS_REBUILD_SKIPPED = "skipped"
POSITIONS_REBUILD_FAILED = "failed"


def _rebuild_positions_after_write(
    *, saved: int, user_id: UUID, account_label: str, settings: Settings
) -> str:
    """Rebuild one account's positions right after a write to its transaction log committed, so
    `position_snapshots` (all GET /positions ever reads) never silently lags the log it derives
    from. Returns the value each write job reports as `positions_rebuild`.

    Skipped when the write saved nothing: the log is unchanged, so the snapshots are too.

    Runs only after the write's own connection is closed and its transaction committed, so a
    rebuild failure can't roll the write back -- and is therefore caught, logged with its
    traceback, and reported as `failed` rather than propagated. Propagating would turn a durably
    saved transaction into an error response, and a caller retrying on that would be acting on
    a false signal. Deliberately reported alongside the write's own `status`, never folded into
    it: an unmapped instrument blocking its own ticker is a fact about the rebuild, not about
    whether the transaction was saved. See docs/architecture.md's "Positions follow every
    transaction write".
    """
    if not saved:
        logger.info(
            "positions rebuild skipped for account %r (user %s): no transactions were saved.",
            account_label,
            user_id,
        )
        return POSITIONS_REBUILD_SKIPPED
    return _rebuild_positions_isolated(
        user_id=user_id, account_label=account_label, settings=settings, saved_what="transactions"
    )


def _rebuild_positions_isolated(
    *, user_id: UUID, account_label: str, settings: Settings, saved_what: str
) -> str:
    """One account's positions rebuild, run after some other write has already committed: a
    failure is logged with its traceback and reported as `failed`, never raised, since raising
    would make the committed write look like it failed. `saved_what` names that write in the
    log line."""
    try:
        result = run_rebuild_positions(
            user_id=user_id, account_label=account_label, settings=settings
        )
    except Exception:
        logger.exception(
            "positions rebuild after write failed for account %r (user %s) -- the %s "
            "are saved; run `portfolio rebuild-positions --user %s --account %s` to retry.",
            account_label,
            user_id,
            saved_what,
            user_id,
            account_label,
        )
        return POSITIONS_REBUILD_FAILED
    return result["status"]


def run_add_transaction(
    *,
    user_id: UUID,
    account_label: str,
    broker_transaction_ref: str,
    transaction_type: str,
    trade_datetime: datetime,
    native_amount: Decimal | None,
    native_currency: str,
    instrument_identifier: str | None = None,
    instrument_identifier_kind: str | None = None,
    quantity: Decimal | None = None,
    price: Decimal | None = None,
    fx_rate: Decimal | None = None,
    fees_native: Decimal | None = None,
    settings: Settings | None = None,
) -> dict[str, int | str]:
    """Record one manually-entered transaction. See pipeline.add_manual_transaction for what
    `saved` means.

    A TICKER-kind instrument needs data-store to confirm it exists, so only then is a client
    built -- and SettingsError raised if data-store isn't configured. Any other kind keeps
    working with no data-store at all."""
    configure_logging()
    settings = settings or load_settings()
    datastore_client = (
        _require_datastore_client(settings, command="add-transaction with a TICKER instrument")
        if instrument_identifier_kind == "TICKER"
        else None
    )
    conn = get_connection(settings.database_url)
    try:
        logger.info(
            "add-transaction: recording %r for account %r (user %s).",
            broker_transaction_ref,
            account_label,
            user_id,
        )
        saved = pipeline.add_manual_transaction(
            conn,
            user_id=user_id,
            account_label=account_label,
            broker_transaction_ref=broker_transaction_ref,
            transaction_type=transaction_type,
            trade_datetime=trade_datetime,
            native_amount=native_amount,
            native_currency=native_currency,
            instrument_identifier=instrument_identifier,
            instrument_identifier_kind=instrument_identifier_kind,
            quantity=quantity,
            price=price,
            fx_rate=fx_rate,
            fees_native=fees_native,
            datastore_client=datastore_client,
        )
    finally:
        conn.close()

    logger.info("add-transaction: done, %d transaction(s) saved.", saved)
    result = build_run_result(
        units_total=saved, rows_saved=saved, failed=0, transactions_saved=saved
    )
    result["positions_rebuild"] = _rebuild_positions_after_write(
        saved=saved, user_id=user_id, account_label=account_label, settings=settings
    )
    return result


def run_import_transactions(
    *, user_id: UUID, account_label: str, file_path: Path, settings: Settings | None = None
) -> dict[str, int | str]:
    """Parse and persist one broker export file. See pipeline.import_transactions for what
    `saved` means."""
    configure_logging()
    settings = settings or load_settings()
    conn = get_connection(settings.database_url)
    try:
        logger.info(
            "import-transactions: importing %s into account %r (user %s).",
            file_path,
            account_label,
            user_id,
        )
        saved = pipeline.import_transactions(
            conn,
            user_id=user_id,
            account_label=account_label,
            file_path=file_path,
        )
    finally:
        conn.close()

    logger.info("import-transactions: done, %d transaction(s) saved.", saved)
    result = build_run_result(
        units_total=saved, rows_saved=saved, failed=0, transactions_saved=saved
    )
    result["positions_rebuild"] = _rebuild_positions_after_write(
        saved=saved, user_id=user_id, account_label=account_label, settings=settings
    )
    return result


def run_list_unmapped_instruments(
    *, user_id: UUID, settings: Settings | None = None
) -> dict[str, int | str]:
    """Every (kind, identifier, broker) this user's transactions reference that no saved ticker
    mapping resolves. Saves nothing, so rows_saved is always 0 -- an
    empty result is still reported as `nothing_to_do`, not `ok`, per the logging rule."""
    configure_logging()
    settings = settings or load_settings()
    conn = get_connection(settings.database_url)
    try:
        logger.info("list-unmapped-instruments: checking instruments for user %s.", user_id)
        mappings = pipeline.load_ticker_mappings(conn)
        unmapped = pipeline.list_unmapped_instruments(conn, user_id=user_id, mappings=mappings)
    finally:
        conn.close()

    logger.info("list-unmapped-instruments: done, %d unmapped instrument(s) found.", len(unmapped))
    result = build_run_result(
        units_total=len(unmapped), rows_saved=0, failed=0, instruments_unmapped=len(unmapped)
    )
    result["unmapped_instruments"] = [
        {"kind": kind, "identifier": identifier, "broker": broker}
        for kind, identifier, broker in unmapped
    ]
    return result


def run_import_cashflow(
    *, user_id: UUID, account_label: str, file_path: Path, settings: Settings | None = None
) -> dict[str, int | str]:
    """Parse and persist one broker's cash-ledger ("Account") export. See
    pipeline.import_cashflow for what `saved` means.

    Unlike every other command, this one needs a live data-store instance to resolve non-EUR
    rows' FX rates -- DATASTORE_API_URL and INTERNAL_API_SECRET must both be set, checked here
    rather than in settings.py itself, the same way data-store's own API checks
    internal_api_secret at its own startup rather than unconditionally in load_settings().
    """
    configure_logging()
    settings = settings or load_settings()
    if not settings.datastore_api_url or not settings.internal_api_secret:
        raise SettingsError(
            "DATASTORE_API_URL and INTERNAL_API_SECRET must both be set for import-cashflow"
        )
    datastore_client = HttpDataStoreClient(
        base_url=settings.datastore_api_url, internal_api_secret=settings.internal_api_secret
    )

    conn = get_connection(settings.database_url)
    try:
        logger.info(
            "import-cashflow: importing %s into account %r (user %s).",
            file_path,
            account_label,
            user_id,
        )
        saved = pipeline.import_cashflow(
            conn,
            user_id=user_id,
            account_label=account_label,
            file_path=file_path,
            datastore_client=datastore_client,
        )
    finally:
        conn.close()

    logger.info("import-cashflow: done, %d transaction(s) saved.", saved)
    result = build_run_result(
        units_total=saved, rows_saved=saved, failed=0, transactions_saved=saved
    )
    result["positions_rebuild"] = _rebuild_positions_after_write(
        saved=saved, user_id=user_id, account_label=account_label, settings=settings
    )
    return result


def run_rebuild_positions(
    *, user_id: UUID, account_label: str | None = None, settings: Settings | None = None
) -> dict[str, int | str]:
    """Replay this user's transaction log into position_snapshots. See
    pipeline.rebuild_positions for what each count means.

    An unmapped instrument blocks only its own ticker -- reported here, and in the run's
    `partial` status, per docs/architecture.md's "never a silent skip" rule -- while every other
    (account, ticker) still gets rebuilt. A ledger anomaly (docs/schema.md's "warn but compute"
    policy) is logged but never affects `status`.
    """
    configure_logging()
    settings = settings or load_settings()
    scope = f"account {account_label!r}" if account_label else "every account"
    conn = get_connection(settings.database_url)
    try:
        logger.info("rebuild-positions: rebuilding positions for user %s (%s).", user_id, scope)
        mappings = pipeline.load_ticker_mappings(conn)
        outcome = pipeline.rebuild_positions(
            conn, user_id=user_id, mappings=mappings, account_label=account_label
        )
    finally:
        conn.close()

    for kind, identifier, broker in outcome.blocked:
        logger.warning(
            "rebuild-positions: instrument blocked -- %s %s (broker %s) has no ticker mapping.",
            kind,
            identifier,
            broker,
        )
    for message in outcome.anomalies:
        logger.warning("rebuild-positions: %s", message)
    # Each failed pair was already logged with its traceback at the point of failure
    # (pipeline.rebuild_positions), so this is only the completion tally, not a re-log.

    logger.info(
        "rebuild-positions: done, %d position(s) saved across %d ticker(s), %d removed, "
        "%d instrument(s) blocked, %d pair(s) failed.",
        outcome.snapshots_saved,
        outcome.positions_rebuilt,
        outcome.positions_removed,
        len(outcome.blocked),
        len(outcome.failed_pairs),
    )
    result = build_run_result(
        units_total=(
            outcome.positions_rebuilt
            + outcome.positions_removed
            + len(outcome.blocked)
            + len(outcome.failed_pairs)
        ),
        rows_saved=outcome.snapshots_saved,
        failed=len(outcome.blocked) + len(outcome.failed_pairs),
        positions_rebuilt=outcome.positions_rebuilt,
        positions_removed=outcome.positions_removed,
        instruments_blocked=len(outcome.blocked),
        positions_with_anomalies=outcome.positions_with_anomalies,
        pairs_failed=len(outcome.failed_pairs),
    )
    # The blocked-instrument/failed-pair payloads are merged in after build_run_result, same
    # carve-out run_list_accounts/run_list_unmapped_instruments use -- **extra is for named
    # counts only.
    result["blocked_instruments"] = [
        {"kind": kind, "identifier": identifier, "broker": broker}
        for kind, identifier, broker in outcome.blocked
    ]
    result["failed_pairs"] = [
        {"account_label": account_label, "ticker": ticker, "error": error}
        for account_label, ticker, error in outcome.failed_pairs
    ]
    return result


def run_rebuild_returns(
    *, user_id: UUID, account_label: str | None = None, settings: Settings | None = None
) -> dict[str, int | str]:
    """Compute return_snapshots from position_snapshots, data-store's price/FX series, and this
    user's DIVIDEND transactions. See pipeline.rebuild_returns for what each count means.

    Needs a live data-store instance, same as import-cashflow -- DATASTORE_API_URL and
    INTERNAL_API_SECRET must both be set, checked here rather than in settings.py itself.

    A ticker data-store doesn't track (or has no computed returns for) blocks only that ticker
    -- reported here, and in the run's `partial` status, per docs/architecture.md's "never a
    silent skip" rule -- while every other (account, ticker) still gets rebuilt.
    """
    configure_logging()
    settings = settings or load_settings()
    if not settings.datastore_api_url or not settings.internal_api_secret:
        raise SettingsError(
            "DATASTORE_API_URL and INTERNAL_API_SECRET must both be set for rebuild-returns"
        )
    datastore_client = HttpDataStoreClient(
        base_url=settings.datastore_api_url, internal_api_secret=settings.internal_api_secret
    )

    scope = f"account {account_label!r}" if account_label else "every account"
    conn = get_connection(settings.database_url)
    try:
        logger.info("rebuild-returns: rebuilding returns for user %s (%s).", user_id, scope)
        mappings = pipeline.load_ticker_mappings(conn)
        outcome = pipeline.rebuild_returns(
            conn,
            user_id=user_id,
            mappings=mappings,
            datastore_client=datastore_client,
            account_label=account_label,
        )
    finally:
        conn.close()

    # Each blocked ticker and failed pair was already logged with its own reason
    # (pipeline.rebuild_returns), so this is only the completion tally, not a re-log.

    logger.info(
        "rebuild-returns: done, %d return snapshot(s) saved across %d ticker(s), %d blocked, "
        "%d pair(s) failed.",
        outcome.snapshots_saved,
        outcome.tickers_rebuilt,
        len(outcome.blocked),
        len(outcome.failed_pairs),
    )
    result = build_run_result(
        units_total=outcome.tickers_rebuilt + len(outcome.blocked) + len(outcome.failed_pairs),
        rows_saved=outcome.snapshots_saved,
        failed=len(outcome.blocked) + len(outcome.failed_pairs),
        tickers_rebuilt=outcome.tickers_rebuilt,
        tickers_blocked=len(outcome.blocked),
        pairs_failed=len(outcome.failed_pairs),
    )
    result["blocked_tickers"] = [
        {"account_label": account_label, "ticker": ticker}
        for account_label, ticker in outcome.blocked
    ]
    result["failed_pairs"] = [
        {"account_label": account_label, "ticker": ticker, "error": error}
        for account_label, ticker, error in outcome.failed_pairs
    ]
    return result


def _mapping_to_dict(row: TickerMappingRow) -> dict:
    return {
        "id": row.id,
        "identifier_kind": row.identifier_kind,
        "identifier": row.identifier,
        "broker": row.broker,
        "canonical_ticker": row.canonical_ticker,
        "updated_by": str(row.updated_by),
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _describe_mapping(row: TickerMappingRow) -> str:
    broker = f" (broker {row.broker})" if row.broker else ""
    return f"{row.identifier_kind} {row.identifier}{broker} -> {row.canonical_ticker}"


def _rebuild_accounts_for_mapping(
    accounts: list[tuple[UUID, str]], *, settings: Settings, command: str
) -> dict[str, int]:
    """Rebuild the positions of every account a mapping change affects -- across users, since
    a mapping is global. Each rebuild is isolated: one failing doesn't stop the rest, and none
    of them can undo the mapping change, which has already committed."""
    logger.info("%s: rebuilding positions for %d account(s).", command, len(accounts))
    failed = 0
    for user_id, account_label in accounts:
        status = _rebuild_positions_isolated(
            user_id=user_id, account_label=account_label, settings=settings, saved_what="mapping"
        )
        if status == POSITIONS_REBUILD_FAILED:
            failed += 1
    return {"accounts_rebuilt": len(accounts) - failed, "rebuilds_failed": failed}


def _mapping_write_result(row: TickerMappingRow, rebuilds: dict[str, int]) -> dict:
    # Like a transaction write, `status` describes the mapping change alone; how the follow-on
    # rebuilds went is reported beside it, never folded in.
    result = build_run_result(units_total=1, rows_saved=1, failed=0, **rebuilds)
    result["mapping"] = _mapping_to_dict(row)
    return result


def run_set_ticker_mapping(
    *,
    identifier_kind: str,
    identifier: str,
    broker: str | None,
    canonical_ticker: str,
    updated_by: UUID,
    settings: Settings | None = None,
) -> dict:
    """Save (or re-point) one global ticker mapping, then rebuild the positions of every account
    whose transactions it covers. See pipeline.set_ticker_mapping. Needs data-store, to check
    the ticker exists."""
    configure_logging()
    settings = settings or load_settings()
    datastore_client = _require_datastore_client(settings, command="ticker-mapping set")
    conn = get_connection(settings.database_url)
    try:
        logger.info(
            "ticker-mapping set: mapping %s %s%s to %s (by user %s).",
            identifier_kind,
            identifier,
            f" at broker {broker}" if broker else "",
            canonical_ticker,
            updated_by,
        )
        row = pipeline.set_ticker_mapping(
            conn,
            identifier_kind=identifier_kind,
            identifier=identifier,
            broker=broker,
            canonical_ticker=canonical_ticker,
            updated_by=updated_by,
            datastore_client=datastore_client,
        )
        accounts = pipeline.list_accounts_using_instrument(
            conn, identifier_kind=row.identifier_kind, identifier=row.identifier, broker=row.broker
        )
    finally:
        conn.close()

    logger.info("ticker-mapping set: saved %s.", _describe_mapping(row))
    rebuilds = _rebuild_accounts_for_mapping(
        accounts, settings=settings, command="ticker-mapping set"
    )
    logger.info(
        "ticker-mapping set: done, %d account(s) rebuilt, %d failed.",
        rebuilds["accounts_rebuilt"],
        rebuilds["rebuilds_failed"],
    )
    return _mapping_write_result(row, rebuilds)


def run_delete_ticker_mapping(
    *, mapping_id: int, updated_by: UUID, settings: Settings | None = None
) -> dict:
    """Delete one ticker mapping, then rebuild the accounts it covered. `updated_by` is only
    logged: the row it would be recorded on is the one being deleted. See
    pipeline.delete_ticker_mapping for what happens to positions the mapping produced."""
    configure_logging()
    settings = settings or load_settings()
    conn = get_connection(settings.database_url)
    try:
        logger.info(
            "ticker-mapping delete: deleting mapping %d (by user %s).", mapping_id, updated_by
        )
        row = pipeline.delete_ticker_mapping(conn, mapping_id=mapping_id)
        accounts = pipeline.list_accounts_using_instrument(
            conn, identifier_kind=row.identifier_kind, identifier=row.identifier, broker=row.broker
        )
    finally:
        conn.close()

    logger.info("ticker-mapping delete: deleted %s.", _describe_mapping(row))
    rebuilds = _rebuild_accounts_for_mapping(
        accounts, settings=settings, command="ticker-mapping delete"
    )
    logger.info(
        "ticker-mapping delete: done, %d account(s) rebuilt, %d failed.",
        rebuilds["accounts_rebuilt"],
        rebuilds["rebuilds_failed"],
    )
    return _mapping_write_result(row, rebuilds)


def get_ticker_mappings(*, settings: Settings | None = None) -> list[dict]:
    """Every ticker mapping, for the admin page and `ticker-mapping list`."""
    configure_logging()
    settings = settings or load_settings()
    conn = get_connection(settings.database_url)
    try:
        logger.info("ticker-mapping list: listing every mapping.")
        rows = pipeline.list_ticker_mappings(conn)
    finally:
        conn.close()
    logger.info("ticker-mapping list: done, %d mapping(s).", len(rows))
    return [_mapping_to_dict(row) for row in rows]


def run_list_all_unmapped_instruments(*, settings: Settings | None = None) -> dict:
    """run_list_unmapped_instruments across every user -- what an admin has left to map."""
    configure_logging()
    settings = settings or load_settings()
    conn = get_connection(settings.database_url)
    try:
        logger.info("list-unmapped-instruments: checking instruments across every user.")
        mappings = pipeline.load_ticker_mappings(conn)
        unmapped = pipeline.list_all_unmapped_instruments(conn, mappings=mappings)
    finally:
        conn.close()

    logger.info("list-unmapped-instruments: done, %d unmapped instrument(s) found.", len(unmapped))
    result = build_run_result(
        units_total=len(unmapped), rows_saved=0, failed=0, instruments_unmapped=len(unmapped)
    )
    result["unmapped_instruments"] = [
        {"kind": kind, "identifier": identifier, "broker": broker}
        for kind, identifier, broker in unmapped
    ]
    return result


def get_tickers(*, settings: Settings | None = None) -> list[str]:
    """Every ticker data-store prices, for manual entry's picker. A read job like the other
    `get_*` here: returns data, not a run result."""
    configure_logging()
    settings = settings or load_settings()
    datastore_client = _require_datastore_client(settings, command="the ticker list")
    logger.info("get-tickers: listing data-store's tickers.")
    tickers = pipeline.list_tickers(datastore_client=datastore_client)
    logger.info("get-tickers: done, %d ticker(s) found.", len(tickers))
    return tickers


def get_transactions(
    *, user_id: UUID, account_label: str, settings: Settings | None = None
) -> list[dict]:
    """One account's transaction log, oldest first -- for the read API. Named `get_*` rather
    than `run_*`, and returns data rather than a run result, matching data-store's own read-job
    convention (jobs.get_price_series): there is nothing to save and no {"status", "rows_saved"}
    to report. Raises AccountNotFound if account_label doesn't resolve for this user."""
    configure_logging()
    settings = settings or load_settings()
    conn = get_connection(settings.database_url)
    try:
        logger.info(
            "get-transactions: listing transactions for account %r (user %s).",
            account_label,
            user_id,
        )
        transactions = pipeline.list_transactions(
            conn, user_id=user_id, account_label=account_label
        )
    finally:
        conn.close()

    logger.info("get-transactions: done, %d transaction(s) found.", len(transactions))
    return [asdict(txn) for txn in transactions]


def get_positions(
    *, user_id: UUID, account_label: str, settings: Settings | None = None
) -> list[dict]:
    """This account's current holdings -- one snapshot per canonical_ticker, its most recent
    as_of_date. Raises AccountNotFound if account_label doesn't resolve for this user."""
    configure_logging()
    settings = settings or load_settings()
    conn = get_connection(settings.database_url)
    try:
        logger.info(
            "get-positions: listing positions for account %r (user %s).", account_label, user_id
        )
        positions = pipeline.list_positions(conn, user_id=user_id, account_label=account_label)
    finally:
        conn.close()

    logger.info("get-positions: done, %d position(s) found.", len(positions))
    return [asdict(position) for position in positions]


def get_returns(
    *,
    user_id: UUID,
    account_label: str,
    canonical_ticker: str | None = None,
    start: date | None = None,
    end: date | None = None,
    settings: Settings | None = None,
) -> list[dict]:
    """This account's return history, optionally narrowed to one ticker and/or a date range.
    Served per ticker -- aggregating across tickers into one portfolio-level series is left to
    the caller (see pipeline.list_returns). Raises AccountNotFound if account_label doesn't
    resolve for this user."""
    configure_logging()
    settings = settings or load_settings()
    conn = get_connection(settings.database_url)
    try:
        logger.info(
            "get-returns: listing returns for account %r (user %s).", account_label, user_id
        )
        returns = pipeline.list_returns(
            conn,
            user_id=user_id,
            account_label=account_label,
            canonical_ticker=canonical_ticker,
            start=start,
            end=end,
        )
    finally:
        conn.close()

    logger.info("get-returns: done, %d return snapshot(s) found.", len(returns))
    return [asdict(snapshot) for snapshot in returns]


def get_latest_returns(
    *, user_id: UUID, account_label: str, settings: Settings | None = None
) -> list[dict]:
    """This account's most recent return snapshot per ticker -- a "current returns" summary.
    Raises AccountNotFound if account_label doesn't resolve for this user."""
    configure_logging()
    settings = settings or load_settings()
    conn = get_connection(settings.database_url)
    try:
        logger.info(
            "get-latest-returns: listing latest returns for account %r (user %s).",
            account_label,
            user_id,
        )
        returns = pipeline.list_latest_returns(conn, user_id=user_id, account_label=account_label)
    finally:
        conn.close()

    logger.info("get-latest-returns: done, %d return snapshot(s) found.", len(returns))
    return [asdict(snapshot) for snapshot in returns]
