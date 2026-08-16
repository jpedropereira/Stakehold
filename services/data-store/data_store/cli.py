import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from data_store.config_loader import FundConfig, load_funds
from data_store.db.connection import apply_schema, get_connection
from data_store.pipeline import ingest_daily_ohlcv, ingest_holdings
from data_store.settings import load_settings

_SCHEMA_PATH = Path(__file__).parent / "db" / "schema.sql"


def _db_init(_args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = get_connection(settings.database_url)
    try:
        apply_schema(conn, _SCHEMA_PATH)
    finally:
        conn.close()
    return 0


def _select_funds(funds: list[FundConfig], fund_key: str | None) -> list[FundConfig]:
    if fund_key is not None:
        for fund in funds:
            if fund.key == fund_key:
                return [fund]
        raise ValueError(f"No fund configured with key: {fund_key!r}")
    return [fund for fund in funds if fund.enabled]


def _ingest_holdings(args: argparse.Namespace) -> int:
    settings = load_settings()
    funds = _select_funds(load_funds(settings.config_path), args.fund)
    conn = get_connection(settings.database_url)
    try:
        for fund in funds:
            count = ingest_holdings(conn, fund, date.today())
            print(f"{fund.key}: {count} holdings rows")
    finally:
        conn.close()
    return 0


def _ingest_ohlcv(args: argparse.Namespace) -> int:
    settings = load_settings()
    funds = _select_funds(load_funds(settings.config_path), args.fund)
    end_date = date.today()
    start_date = end_date - timedelta(days=args.days)
    conn = get_connection(settings.database_url)
    try:
        for fund in funds:
            count = ingest_daily_ohlcv(conn, fund, start_date, end_date)
            print(f"{fund.key}: {count} price rows")
    finally:
        conn.close()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="data-store")
    subparsers = parser.add_subparsers(dest="command", required=True)

    db_parser = subparsers.add_parser("db", help="Database maintenance commands")
    db_subparsers = db_parser.add_subparsers(dest="db_command", required=True)
    init_parser = db_subparsers.add_parser("init", help="Apply the database schema")
    init_parser.set_defaults(handler=_db_init)

    ingest_holdings_parser = subparsers.add_parser(
        "ingest-holdings", help="Scrape and load ETF holdings"
    )
    ingest_holdings_parser.add_argument(
        "--fund", help="Fund key to ingest (default: all enabled funds)"
    )
    ingest_holdings_parser.set_defaults(handler=_ingest_holdings)

    ingest_ohlcv_parser = subparsers.add_parser(
        "ingest-ohlcv", help="Fetch and load daily OHLCV prices"
    )
    ingest_ohlcv_parser.add_argument(
        "--fund", help="Fund key to ingest (default: all enabled funds)"
    )
    ingest_ohlcv_parser.add_argument(
        "--days", type=int, default=7, help="Lookback window in days (default: 7)"
    )
    ingest_ohlcv_parser.set_defaults(handler=_ingest_ohlcv)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
