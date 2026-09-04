import argparse
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID

from portfolio import jobs
from portfolio.errors import PortfolioError
from portfolio.run_result import STATUS_NOTHING_TO_DO, STATUS_OK
from portfolio.settings import SettingsError
from portfolio.transactions.base import TRANSACTION_TYPES

_SUCCESS_STATUSES = {STATUS_OK, STATUS_NOTHING_TO_DO}


def _exit_code(result: dict[str, int | str]) -> int:
    """Non-zero unless a run is a known success, so an unrecognized status fails closed.

    "nothing_to_do" is deliberately a success: a quiet day is not a broken one, and the run has
    already said so on stdout.
    """
    return 0 if result["status"] in _SUCCESS_STATUSES else 1


def _report(result: dict[str, int | str], noun: str, *, count_key: str = "rows_saved") -> int:
    print(f"{result[count_key]} {noun}")
    return _exit_code(result)


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid UUID: {value!r}") from exc


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"invalid decimal: {value!r}") from exc


def _datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO 8601 datetime: {value!r}") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(f"datetime must include a UTC offset: {value!r}")
    return parsed


def _db_init(_args: argparse.Namespace) -> int:
    return _report(jobs.run_db_init(), "table(s) created")


def _account_add(args: argparse.Namespace) -> int:
    result = jobs.run_add_account(user_id=args.user, broker=args.broker, account_label=args.label)
    return _report(result, "account(s) created")


def _account_list(args: argparse.Namespace) -> int:
    result = jobs.run_list_accounts(user_id=args.user)
    for account in result["accounts"]:
        created_date = account["created_at"][:10]
        print(f"{account['id']}\t{account['account_label']}\t{account['broker']}\t{created_date}")
    return _report(result, "account(s)", count_key="accounts_listed")


def _broker_list(_args: argparse.Namespace) -> int:
    result = jobs.run_list_available_brokers()
    for broker in result["brokers"]:
        print(broker)
    return _report(result, "broker parser(s) available", count_key="brokers_available")


def _import_transactions(args: argparse.Namespace) -> int:
    result = jobs.run_import_transactions(
        user_id=args.user, account_label=args.account, file_path=args.file
    )
    return _report(result, "transaction(s) saved")


def _import_cashflow(args: argparse.Namespace) -> int:
    result = jobs.run_import_cashflow(
        user_id=args.user, account_label=args.account, file_path=args.file
    )
    return _report(result, "transaction(s) saved")


def _print_instrument_rows(instruments: list[dict[str, str]]) -> None:
    for instrument in instruments:
        print(f"{instrument['kind']}\t{instrument['identifier']}\t{instrument['broker']}")


def _print_failed_pairs(pairs: list[dict[str, str]]) -> None:
    for pair in pairs:
        print(f"{pair['account_label']}\t{pair['ticker']}\t{pair['error']}")


def _rebuild_positions(args: argparse.Namespace) -> int:
    result = jobs.run_rebuild_positions(user_id=args.user, account_label=args.account)
    _print_instrument_rows(result["blocked_instruments"])
    _print_failed_pairs(result["failed_pairs"])
    return _report(result, "position snapshot(s) saved")


def _print_blocked_tickers(tickers: list[dict[str, str]]) -> None:
    for ticker in tickers:
        print(f"{ticker['account_label']}\t{ticker['ticker']}")


def _rebuild_returns(args: argparse.Namespace) -> int:
    result = jobs.run_rebuild_returns(user_id=args.user, account_label=args.account)
    _print_blocked_tickers(result["blocked_tickers"])
    _print_failed_pairs(result["failed_pairs"])
    return _report(result, "return snapshot(s) saved")


def _list_unmapped_instruments(args: argparse.Namespace) -> int:
    result = jobs.run_list_unmapped_instruments(user_id=args.user)
    _print_instrument_rows(result["unmapped_instruments"])
    return _report(result, "instrument(s) unmapped", count_key="instruments_unmapped")


def _add_transaction(args: argparse.Namespace) -> int:
    result = jobs.run_add_transaction(
        user_id=args.user,
        account_label=args.account,
        broker_transaction_ref=args.ref,
        transaction_type=args.type,
        trade_datetime=args.datetime,
        native_amount=args.amount,
        native_currency=args.currency,
        instrument_identifier=args.instrument,
        instrument_identifier_kind=args.instrument_kind,
        quantity=args.quantity,
        price=args.price,
        fx_rate=args.fx_rate,
        fees_native=args.fees,
    )
    return _report(result, "transaction(s) saved")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="portfolio")
    subparsers = parser.add_subparsers(dest="command", required=True)

    db_parser = subparsers.add_parser("db", help="Database maintenance commands")
    db_subparsers = db_parser.add_subparsers(dest="db_command", required=True)
    init_parser = db_subparsers.add_parser("init", help="Apply the database schema")
    init_parser.set_defaults(handler=_db_init)

    account_parser = subparsers.add_parser("account", help="Account management commands")
    account_subparsers = account_parser.add_subparsers(dest="account_command", required=True)

    add_parser = account_subparsers.add_parser("add", help="Create an account")
    add_parser.add_argument("--user", required=True, type=_uuid, help="The auth-issued user id")
    add_parser.add_argument(
        "--broker",
        required=True,
        help="A free-text label for this account's broker -- not validated against a fixed list",
    )
    add_parser.add_argument("--label", required=True, help="This user's own name for the account")
    add_parser.set_defaults(handler=_account_add)

    list_parser = account_subparsers.add_parser("list", help="List a user's accounts")
    list_parser.add_argument("--user", required=True, type=_uuid, help="The auth-issued user id")
    list_parser.set_defaults(handler=_account_list)

    broker_parser = subparsers.add_parser("broker", help="Broker-parser plugin commands")
    broker_subparsers = broker_parser.add_subparsers(dest="broker_command", required=True)
    broker_list_parser = broker_subparsers.add_parser(
        "list", help="List brokers with a parser currently installed"
    )
    broker_list_parser.set_defaults(handler=_broker_list)

    txn_parser = subparsers.add_parser("add-transaction", help="Record a manual transaction")
    txn_parser.add_argument("--user", required=True, type=_uuid, help="The auth-issued user id")
    txn_parser.add_argument("--account", required=True, help="The --label of an existing account")
    txn_parser.add_argument(
        "--ref",
        required=True,
        help="A caller-chosen unique key -- re-running with the same --ref is a safe no-op",
    )
    txn_parser.add_argument(
        "--type", required=True, dest="type", choices=TRANSACTION_TYPES, help="Transaction type"
    )
    txn_parser.add_argument(
        "--datetime",
        required=True,
        type=_datetime,
        dest="datetime",
        help="ISO 8601 with a UTC offset, e.g. 2026-03-01T10:00:00+00:00",
    )
    txn_parser.add_argument("--amount", required=True, type=_decimal, help="Signed native amount")
    txn_parser.add_argument("--currency", required=True, help="ISO 4217, e.g. EUR")
    txn_parser.add_argument("--instrument", help="ISIN or symbol -- omit for cash-only rows")
    txn_parser.add_argument("--instrument-kind", dest="instrument_kind", choices=("ISIN", "SYMBOL"))
    txn_parser.add_argument("--quantity", type=_decimal, help="Signed: positive buy, negative sell")
    txn_parser.add_argument("--price", type=_decimal, help="Per unit, native currency")
    txn_parser.add_argument(
        "--fx-rate",
        type=_decimal,
        dest="fx_rate",
        help="Native units per 1 EUR. Required unless --currency EUR",
    )
    txn_parser.add_argument("--fees", type=_decimal, help="Native currency")
    txn_parser.set_defaults(handler=_add_transaction)

    import_parser = subparsers.add_parser("import-transactions", help="Import a broker export file")
    import_parser.add_argument("--user", required=True, type=_uuid, help="The auth-issued user id")
    import_parser.add_argument("--file", required=True, type=Path, help="Path to the export file")
    import_parser.add_argument(
        "--account",
        required=True,
        help="The --label of an existing account -- its own broker picks the parser used",
    )
    import_parser.set_defaults(handler=_import_transactions)

    unmapped_parser = subparsers.add_parser(
        "list-unmapped-instruments", help="List instruments with no ticker mapping"
    )
    unmapped_parser.add_argument(
        "--user", required=True, type=_uuid, help="The auth-issued user id"
    )
    unmapped_parser.set_defaults(handler=_list_unmapped_instruments)

    cashflow_parser = subparsers.add_parser(
        "import-cashflow", help="Import a broker's cash-ledger (Account) export"
    )
    cashflow_parser.add_argument(
        "--user", required=True, type=_uuid, help="The auth-issued user id"
    )
    cashflow_parser.add_argument("--file", required=True, type=Path, help="Path to the export file")
    cashflow_parser.add_argument(
        "--account",
        required=True,
        help="The --label of an existing account -- its own broker picks the parser used",
    )
    cashflow_parser.set_defaults(handler=_import_cashflow)

    positions_parser = subparsers.add_parser(
        "rebuild-positions", help="Replay transactions into position snapshots"
    )
    positions_parser.add_argument(
        "--user", required=True, type=_uuid, help="The auth-issued user id"
    )
    positions_parser.add_argument(
        "--account", help="The --label of one account to rebuild; omit to rebuild every account"
    )
    positions_parser.set_defaults(handler=_rebuild_positions)

    returns_parser = subparsers.add_parser(
        "rebuild-returns", help="Compute return snapshots from positions, prices, FX and dividends"
    )
    returns_parser.add_argument("--user", required=True, type=_uuid, help="The auth-issued user id")
    returns_parser.add_argument(
        "--account", help="The --label of one account to rebuild; omit to rebuild every account"
    )
    returns_parser.set_defaults(handler=_rebuild_returns)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except SettingsError as exc:
        # A misconfigured deployment (a missing env var) -- without this it would otherwise
        # surface as a raw traceback instead of a one-line, actionable message.
        print(f"configuration error: {exc}", file=sys.stderr)
        return 1
    except PortfolioError as exc:
        # A domain failure (e.g. an unknown account label, an unparseable broker file).
        # jobs.py deliberately lets these propagate rather than swallowing them into a
        # partial result, so every entry point maps them at its own boundary -- this is
        # cli.py's; the future HTTP API maps the same exceptions to status codes instead.
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
