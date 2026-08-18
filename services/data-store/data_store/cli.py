import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from data_store.config_loader import (
    FundConfig,
    TickerConfig,
    load_exchange_suffixes,
    load_funds,
    load_ticker_overrides,
    load_tracked_tickers,
)
from data_store.db.connection import apply_schema, get_connection
from data_store.pipeline import (
    backfill_dividend_events,
    backfill_historical_ohlcv,
    ingest_daily_ohlcv,
    ingest_dividend_events,
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
        total = 0
        for fund in funds:
            print(f"ingest-holdings: scraping {fund.key}...", flush=True)
            count = ingest_holdings(conn, fund, date.today())
            print(f"{fund.key}: {count} holdings rows")
            total += count
        print(
            f"ingest-holdings complete: {total} row(s) across {len(funds)} fund(s).",
            flush=True,
        )
    finally:
        conn.close()
    return 0


def _ingest_ohlcv(args: argparse.Namespace) -> int:
    settings = load_settings()
    funds = _select_funds(load_funds(settings.config_path), args.fund)
    tracked_tickers = _load_enabled_tracked_tickers(settings.tracked_tickers_path)
    exchange_suffixes = load_exchange_suffixes(settings.exchange_suffixes_path)
    ticker_overrides = load_ticker_overrides(settings.exchange_suffixes_path)
    only_tickers = set(args.ticker) if args.ticker else None
    end_date = date.today()
    start_date = end_date - timedelta(days=args.days)
    conn = get_connection(settings.database_url)
    try:
        count = ingest_daily_ohlcv(
            conn,
            funds,
            tracked_tickers,
            exchange_suffixes,
            start_date,
            end_date,
            only_tickers=only_tickers,
            ticker_overrides=ticker_overrides,
        )
        print(f"{count} price rows")
    finally:
        conn.close()
    return 0


def _backfill_ohlcv(args: argparse.Namespace) -> int:
    settings = load_settings()
    funds = _select_funds(load_funds(settings.config_path), args.fund)
    tracked_tickers = _load_enabled_tracked_tickers(settings.tracked_tickers_path)
    exchange_suffixes = load_exchange_suffixes(settings.exchange_suffixes_path)
    ticker_overrides = load_ticker_overrides(settings.exchange_suffixes_path)
    only_tickers = set(args.ticker) if args.ticker else None
    end_date = date.today()
    earliest_date = date.fromisoformat(args.earliest_date)
    conn = get_connection(settings.database_url)
    try:
        count = backfill_historical_ohlcv(
            conn,
            funds,
            tracked_tickers,
            exchange_suffixes,
            earliest_date,
            end_date,
            args.delay_seconds,
            only_tickers=only_tickers,
            ticker_overrides=ticker_overrides,
        )
        print(f"{count} price rows")
    finally:
        conn.close()
    return 0


def _ingest_dividends(args: argparse.Namespace) -> int:
    settings = load_settings()
    funds = _select_funds(load_funds(settings.config_path), args.fund)
    tracked_tickers = _load_enabled_tracked_tickers(settings.tracked_tickers_path)
    exchange_suffixes = load_exchange_suffixes(settings.exchange_suffixes_path)
    ticker_overrides = load_ticker_overrides(settings.exchange_suffixes_path)
    only_tickers = set(args.ticker) if args.ticker else None
    end_date = date.today()
    start_date = end_date - timedelta(days=args.days)
    conn = get_connection(settings.database_url)
    try:
        count = ingest_dividend_events(
            conn,
            funds,
            tracked_tickers,
            exchange_suffixes,
            start_date,
            end_date,
            only_tickers=only_tickers,
            ticker_overrides=ticker_overrides,
        )
        print(f"{count} dividend rows")
    finally:
        conn.close()
    return 0


def _backfill_dividends(args: argparse.Namespace) -> int:
    settings = load_settings()
    funds = _select_funds(load_funds(settings.config_path), args.fund)
    tracked_tickers = _load_enabled_tracked_tickers(settings.tracked_tickers_path)
    exchange_suffixes = load_exchange_suffixes(settings.exchange_suffixes_path)
    ticker_overrides = load_ticker_overrides(settings.exchange_suffixes_path)
    only_tickers = set(args.ticker) if args.ticker else None
    end_date = date.today()
    earliest_date = date.fromisoformat(args.earliest_date)
    conn = get_connection(settings.database_url)
    try:
        count = backfill_dividend_events(
            conn,
            funds,
            tracked_tickers,
            exchange_suffixes,
            earliest_date,
            end_date,
            args.delay_seconds,
            only_tickers=only_tickers,
            ticker_overrides=ticker_overrides,
        )
        print(f"{count} dividend rows")
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
    ingest_ohlcv_parser.add_argument(
        "--ticker",
        action="append",
        help=(
            "Restrict to this final (post-suffix-resolution) ticker (repeatable), e.g. "
            "'AAPL' or '0700.HK' -- matches any currently followed ticker, from fund "
            "holdings or tracked_tickers.yaml. Default: all resolved tickers."
        ),
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
            "Restrict to this final (post-suffix-resolution) ticker (repeatable), e.g. "
            "'AAPL' or '0700.HK' -- matches any currently followed ticker, from fund "
            "holdings or tracked_tickers.yaml. Intended for small manual dry runs, or "
            "re-backfilling specific tickers that previously failed."
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

    ingest_dividends_parser = subparsers.add_parser(
        "ingest-dividends", help="Fetch and load dividend events"
    )
    ingest_dividends_parser.add_argument(
        "--fund",
        help=(
            "Only price this fund's holdings (default: all enabled funds). "
            "Tracked tickers from tracked_tickers.yaml are always included."
        ),
    )
    ingest_dividends_parser.add_argument(
        "--days", type=int, default=7, help="Lookback window in days (default: 7)"
    )
    ingest_dividends_parser.add_argument(
        "--ticker",
        action="append",
        help=(
            "Restrict to this final (post-suffix-resolution) ticker (repeatable), e.g. "
            "'AAPL' or '0700.HK' -- matches any currently followed ticker, from fund "
            "holdings or tracked_tickers.yaml. Default: all resolved tickers."
        ),
    )
    ingest_dividends_parser.set_defaults(handler=_ingest_dividends)

    backfill_dividends_parser = subparsers.add_parser(
        "backfill-dividends",
        help=(
            "Backfill full dividend history for all followed tickers, filling only what's "
            "missing before each ticker's earliest stored ex_date. Deliberately slow; "
            "intended for rare, manual/on-demand runs that may take hours."
        ),
    )
    backfill_dividends_parser.add_argument("--fund", help="Only backfill this fund's holdings.")
    backfill_dividends_parser.add_argument(
        "--ticker",
        action="append",
        help=(
            "Restrict to this final (post-suffix-resolution) ticker (repeatable), e.g. "
            "'AAPL' or '0700.HK' -- matches any currently followed ticker, from fund "
            "holdings or tracked_tickers.yaml. Intended for small manual dry runs, or "
            "re-backfilling specific tickers that previously failed."
        ),
    )
    backfill_dividends_parser.add_argument(
        "--earliest-date",
        default="1900-01-01",
        help=(
            "Lower bound requested from yfinance (default: 1900-01-01 -- yfinance clips to "
            "each ticker's actual listing date, so this is effectively 'as far back as "
            "available')."
        ),
    )
    backfill_dividends_parser.add_argument(
        "--delay-seconds",
        type=float,
        default=10.0,
        help="Seconds to sleep after each ticker's fetch, to stay under yfinance's rate limiting.",
    )
    backfill_dividends_parser.set_defaults(handler=_backfill_dividends)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
