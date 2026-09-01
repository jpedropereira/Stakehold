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


def _ingest_market_data(args: argparse.Namespace) -> int:
    result = jobs.run_ingest_market_data(fund=args.fund, days=args.days, tickers=args.ticker)
    return _report(result, "market data rows")


def _backfill_market_data(args: argparse.Namespace) -> int:
    result = jobs.run_backfill_market_data(
        fund=args.fund,
        tickers=args.ticker,
        earliest_date=args.earliest_date,
        delay_seconds=args.delay_seconds,
        force_refresh=args.force_refresh,
    )
    return _report(result, "market data rows")


def _calculate_returns(args: argparse.Namespace) -> int:
    result = jobs.run_calculate_returns(tickers=args.ticker)
    return _report(result, "return rows")


def _ingest_regimes(_args: argparse.Namespace) -> int:
    result = jobs.run_ingest_regimes()
    return _report(result, "regime rows")


def _ingest_macro_indicators(_args: argparse.Namespace) -> int:
    result = jobs.run_ingest_macro_indicators()
    return _report(result, "macro indicator rows")


def _calculate_macro_indicator_rates(_args: argparse.Namespace) -> int:
    result = jobs.run_calculate_macro_indicator_rates()
    return _report(result, "macro indicator rate rows")


def _ingest_fx_rates(args: argparse.Namespace) -> int:
    result = jobs.run_ingest_fx_rates(days=args.days, currencies=args.currency)
    return _report(result, "fx rate rows")


def _backfill_fx_rates(args: argparse.Namespace) -> int:
    result = jobs.run_backfill_fx_rates(
        currencies=args.currency,
        earliest_date=args.earliest_date,
        delay_seconds=args.delay_seconds,
        force_refresh=args.force_refresh,
    )
    return _report(result, "fx rate rows")


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

    ingest_market_data_parser = subparsers.add_parser(
        "ingest-market-data", help="Fetch and load daily OHLCV prices, dividends and stock splits"
    )
    ingest_market_data_parser.add_argument(
        "--fund",
        help=(
            "Only price this fund's holdings (default: all enabled funds). "
            "Tracked tickers from tracked_tickers.yaml are always included."
        ),
    )
    ingest_market_data_parser.add_argument(
        "--days", type=int, default=7, help="Lookback window in days (default: 7)"
    )
    ingest_market_data_parser.add_argument(
        "--ticker",
        action="append",
        help=(
            "Restrict to this final (post-suffix-resolution) ticker (repeatable), e.g. "
            "'AAPL' or '0700.HK' -- matches any currently followed ticker, from fund "
            "holdings or tracked_tickers.yaml. Default: all resolved tickers."
        ),
    )
    ingest_market_data_parser.set_defaults(handler=_ingest_market_data)

    backfill_market_data_parser = subparsers.add_parser(
        "backfill-market-data",
        help=(
            "Backfill full historical OHLCV, dividends and stock splits for all followed "
            "tickers, filling only what's missing before each ticker's earliest stored date. "
            "Deliberately slow; intended for rare, manual/on-demand runs that may take hours."
        ),
    )
    backfill_market_data_parser.add_argument("--fund", help="Only backfill this fund's holdings.")
    backfill_market_data_parser.add_argument(
        "--ticker",
        action="append",
        help=(
            "Restrict to this final (post-suffix-resolution) ticker (repeatable), e.g. "
            "'AAPL' or '0700.HK' -- matches any currently followed ticker, from fund "
            "holdings or tracked_tickers.yaml. Intended for small manual dry runs, or "
            "re-backfilling specific tickers that previously failed."
        ),
    )
    backfill_market_data_parser.add_argument(
        "--earliest-date",
        help=(
            "Reach further back than a previous run did: fetches each ticker the stretch "
            "between this date and its earliest stored date. Omit it for the normal case -- "
            "every ticker that already has data is skipped, and tickers with none are "
            "fetched as far back as the source goes, which is what makes a rerun cheap."
        ),
    )
    backfill_market_data_parser.add_argument(
        "--delay-seconds",
        type=float,
        default=10.0,
        help="Seconds to sleep after each ticker's fetch, to stay under yfinance's rate limiting.",
    )
    backfill_market_data_parser.add_argument(
        "--force-refresh",
        action="store_true",
        help=(
            "Re-fetch and rewrite each ticker's whole stored range instead of only the stretch "
            "below its earliest stored date. The repair path for a history left on two price "
            "scales -- when a ticker splits, the provider restates its entire history into the "
            "new scale, and any row the daily ingest did not re-fetch keeps the old one. Pair "
            "it with --ticker; without one it re-requests every followed ticker's full history."
        ),
    )
    backfill_market_data_parser.set_defaults(handler=_backfill_market_data)

    calculate_returns_parser = subparsers.add_parser(
        "calculate-returns",
        help=(
            "Rebuild market_returns (split-adjusted price and price/dividend/simple/log "
            "returns) from raw_market_data. Always a full rebuild -- a split retroactively "
            "changes the correct value of every earlier row for that ticker."
        ),
    )
    calculate_returns_parser.add_argument(
        "--ticker",
        action="append",
        help=(
            "Restrict to this ticker (repeatable), as stored in raw_market_data. "
            "Default: every ticker with stored market data."
        ),
    )
    calculate_returns_parser.set_defaults(handler=_calculate_returns)

    ingest_regimes_parser = subparsers.add_parser(
        "ingest-regimes", help="Load economic/market regimes from CSV"
    )
    ingest_regimes_parser.set_defaults(handler=_ingest_regimes)

    ingest_macro_indicators_parser = subparsers.add_parser(
        "ingest-macro-indicators",
        help="Fetch and load inflation/GDP indicators from FRED (US) and Eurostat (EU)",
    )
    ingest_macro_indicators_parser.set_defaults(handler=_ingest_macro_indicators)

    calculate_macro_indicator_rates_parser = subparsers.add_parser(
        "calculate-macro-indicator-rates",
        help=(
            "Rebuild macro_indicator_rates (comparable year-over-year rates) from "
            "raw_macro_indicators. Always a full rebuild."
        ),
    )
    calculate_macro_indicator_rates_parser.set_defaults(handler=_calculate_macro_indicator_rates)

    ingest_fx_rates_parser = subparsers.add_parser(
        "ingest-fx-rates",
        help="Fetch and load daily currency-conversion rates for config/currencies.yaml",
    )
    ingest_fx_rates_parser.add_argument(
        "--days", type=int, default=7, help="Lookback window in days (default: 7)"
    )
    ingest_fx_rates_parser.add_argument(
        "--currency",
        action="append",
        help=(
            "Restrict to this quote currency code (repeatable), e.g. 'USD' -- matches any "
            "currently tracked currency from config/currencies.yaml. Default: every enabled "
            "currency against base_currency."
        ),
    )
    ingest_fx_rates_parser.set_defaults(handler=_ingest_fx_rates)

    backfill_fx_rates_parser = subparsers.add_parser(
        "backfill-fx-rates",
        help=(
            "Backfill full historical currency-conversion rates for every currency in "
            "config/currencies.yaml, filling only what's missing before each pair's earliest "
            "stored date. Deliberately slow; intended for rare, manual/on-demand runs."
        ),
    )
    backfill_fx_rates_parser.add_argument(
        "--currency",
        action="append",
        help=(
            "Restrict to this quote currency code (repeatable), e.g. 'USD'. Intended for small "
            "manual dry runs, or re-backfilling specific currencies that previously failed."
        ),
    )
    backfill_fx_rates_parser.add_argument(
        "--earliest-date",
        help=(
            "Reach further back than a previous run did: fetches each pair the stretch between "
            "this date and its earliest stored date. Omit it for the normal case -- every pair "
            "that already has data is skipped, and pairs with none are fetched as far back as "
            "the source goes, which is what makes a rerun cheap."
        ),
    )
    backfill_fx_rates_parser.add_argument(
        "--delay-seconds",
        type=float,
        default=10.0,
        help="Seconds to sleep after each pair's fetch, to stay under yfinance's rate limiting.",
    )
    backfill_fx_rates_parser.add_argument(
        "--force-refresh",
        action="store_true",
        help=(
            "Re-fetch and rewrite each pair's whole stored range instead of only the stretch "
            "below its earliest stored date. Pair it with --currency; without one it "
            "re-requests every tracked currency's full history."
        ),
    )
    backfill_fx_rates_parser.set_defaults(handler=_backfill_fx_rates)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
