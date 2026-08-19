import argparse
import sys

from data_store import jobs
from data_store.run_result import STATUS_PARTIAL


def _exit_code(result: dict[str, int | str]) -> int:
    """Non-zero when a run only partly succeeded, so a scheduler can tell.

    "nothing_to_do" is deliberately a success: a quiet day is not a broken one, and the run has
    already said so on stdout.
    """
    return 1 if result["status"] == STATUS_PARTIAL else 0


def _report(result: dict[str, int | str], noun: str) -> int:
    print(f"{result['rows_saved']} {noun}")
    return _exit_code(result)


def _db_init(_args: argparse.Namespace) -> int:
    return _exit_code(jobs.run_db_init())


def _ingest_holdings(args: argparse.Namespace) -> int:
    return _exit_code(jobs.run_ingest_holdings(fund=args.fund))


def _ingest_ohlcv(args: argparse.Namespace) -> int:
    result = jobs.run_ingest_ohlcv(fund=args.fund, days=args.days, tickers=args.ticker)
    return _report(result, "price rows")


def _ingest_dividends(args: argparse.Namespace) -> int:
    result = jobs.run_ingest_dividends(fund=args.fund, days=args.days, tickers=args.ticker)
    return _report(result, "dividend rows")


def _backfill_ohlcv(args: argparse.Namespace) -> int:
    result = jobs.run_backfill_ohlcv(
        fund=args.fund,
        tickers=args.ticker,
        earliest_date=args.earliest_date,
        delay_seconds=args.delay_seconds,
    )
    return _report(result, "price rows")


def _backfill_dividends(args: argparse.Namespace) -> int:
    result = jobs.run_backfill_dividends(
        fund=args.fund,
        tickers=args.ticker,
        earliest_date=args.earliest_date,
        delay_seconds=args.delay_seconds,
    )
    return _report(result, "dividend rows")


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
        help=(
            "Reach further back than a previous run did: fetches each ticker the stretch "
            "between this date and its earliest stored date. Omit it for the normal case -- "
            "every ticker that already has data is skipped, and tickers with none are "
            "fetched as far back as the source goes, which is what makes a rerun cheap."
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
        help=(
            "Reach further back than a previous run did: fetches each ticker the stretch "
            "between this date and its earliest stored date. Omit it for the normal case -- "
            "every ticker that already has data is skipped, and tickers with none are "
            "fetched as far back as the source goes, which is what makes a rerun cheap."
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
