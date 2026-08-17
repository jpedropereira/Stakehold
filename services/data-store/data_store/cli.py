import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from data_store.config_loader import (
    FundConfig,
    TickerConfig,
    load_funds,
    load_tracked_tickers,
)
from data_store.db.connection import apply_schema, get_connection
from data_store.pipeline import (
    backfill_historical_ohlcv,
    ingest_daily_ohlcv,
    ingest_holdings,
)
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


def _load_enabled_tracked_tickers(path: Path) -> list[TickerConfig]:
    # tracked_tickers.yaml is optional — a setup with only fund-holdings-based pricing
    # shouldn't need to create it just to run ingest-ohlcv.
    if not path.is_file():
        return []
    return [tt for tt in load_tracked_tickers(path) if tt.enabled]


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
    tracked_tickers = _load_enabled_tracked_tickers(settings.tracked_tickers_path)
    end_date = date.today()
    start_date = end_date - timedelta(days=args.days)
    conn = get_connection(settings.database_url)
    try:
        count = ingest_daily_ohlcv(conn, funds, tracked_tickers, start_date, end_date)
        print(f"{count} price rows")
    finally:
        conn.close()
    return 0


def _backfill_ohlcv(args: argparse.Namespace) -> int:
    settings = load_settings()
    funds = _select_funds(load_funds(settings.config_path), args.fund)
    tracked_tickers = _load_enabled_tracked_tickers(settings.tracked_tickers_path)
    if args.ticker:
        wanted = set(args.ticker)
        funds = []
        tracked_tickers = [tt for tt in tracked_tickers if tt.ticker in wanted]
    end_date = date.today()
    earliest_date = date.fromisoformat(args.earliest_date)
    conn = get_connection(settings.database_url)
    try:
        count = backfill_historical_ohlcv(
            conn, funds, tracked_tickers, earliest_date, end_date, args.delay_seconds
        )
        print(f"{count} price rows")
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
        "--fund",
        help=(
            "Only price this fund's holdings (default: all enabled funds). "
            "Tracked tickers from tracked_tickers.yaml are always included."
        ),
    )
    ingest_ohlcv_parser.add_argument(
        "--days", type=int, default=7, help="Lookback window in days (default: 7)"
    )
    ingest_ohlcv_parser.set_defaults(handler=_ingest_ohlcv)

    backfill_ohlcv_parser = subparsers.add_parser(
        "backfill-ohlcv",
        help=(
            "Backfill full historical daily OHLCV for all followed tickers, filling only "
            "what's missing before each ticker's earliest stored date. Deliberately slow; "
            "intended for rare, manual/on-demand runs that may take hours."
        ),
    )
    backfill_ohlcv_parser.add_argument("--fund", help="Only backfill this fund's holdings.")
    backfill_ohlcv_parser.add_argument(
        "--ticker",
        action="append",
        help=(
            "Restrict to this ticker (repeatable), must be enabled in tracked_tickers.yaml. "
            "Intended for small manual dry runs."
        ),
    )
    backfill_ohlcv_parser.add_argument(
        "--earliest-date",
        default="1900-01-01",
        help=(
            "Lower bound requested from yfinance (default: 1900-01-01 -- yfinance clips to "
            "each ticker's actual listing date, so this is effectively 'as far back as "
            "available')."
        ),
    )
    backfill_ohlcv_parser.add_argument(
        "--delay-seconds",
        type=float,
        default=10.0,
        help="Seconds to sleep after each ticker's fetch, to stay under yfinance's rate limiting.",
    )
    backfill_ohlcv_parser.set_defaults(handler=_backfill_ohlcv)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
