"""Runnable units of work, callable without a shell.

Each `run_*` here is one complete operation: it loads settings and configuration, opens and
closes its own database connection, invokes the pipeline flow, and returns that flow's result.

This layer exists so the CLI and Airflow share one execution path rather than two that drift.
`cli.py` is argument parsing plus a call into here; an Airflow task is a call into here and
nothing else. Anything that lives in only one of those two is, by construction, untested by the
other.

Arguments are plain builtins -- `str`, `int`, `list[str]`, `None` -- and so are results (see
`run_result`). Airflow passes task arguments across a process boundary by pickling them, and
`earliest_date` in particular arrives from a DAG parameter as an ISO string, so parsing it here
rather than at each caller keeps that conversion in one place instead of two.
"""

import logging
from datetime import date, timedelta
from pathlib import Path

from data_store.config_loader import (
    FundConfig,
    TickerConfig,
    load_funds,
    load_ticker_resolution,
    load_tracked_tickers,
)
from data_store.db.connection import apply_schema, get_connection
from data_store.logging_config import configure_logging
from data_store.pipeline import (
    backfill_market_data,
    calculate_returns,
    ingest_holdings,
    ingest_market_data,
)
from data_store.run_result import build_run_result
from data_store.settings import load_settings

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "db" / "schema.sql"


def run_db_init() -> dict[str, int | str]:
    """Apply the schema. Idempotent -- see docs/schema.md on why every statement is guarded."""
    configure_logging()
    settings = load_settings()
    conn = get_connection(settings.database_url)
    try:
        apply_schema(conn, SCHEMA_PATH)
    finally:
        conn.close()
    logger.info("db init: schema applied.")
    return build_run_result(units_total=1, rows_saved=0, failed=0)


def run_ingest_holdings(*, fund: str | None = None) -> dict[str, int | str]:
    """Scrape and load each selected fund's holdings.

    Unlike the pricing flows this loops in the job rather than the pipeline, because
    `pipeline.ingest_holdings` is a per-fund primitive: one scrape, one snapshot. Run-level
    aggregation across funds is this layer's job, which is why the per-fund failure isolation
    lives here too.
    """
    configure_logging()
    settings = load_settings()
    funds = _select_funds(load_funds(settings.config_path), fund)
    conn = get_connection(settings.database_url)
    try:
        total = 0
        failed = 0
        for fund_config in funds:
            logger.info(f"ingest-holdings: scraping {fund_config.key}...")
            # Scraping drives a real browser against a third-party site -- the flakiest step
            # in the service. One fund's failure shouldn't cost every fund queued behind it,
            # and the run must still reach its summary rather than dying before printing it.
            try:
                count = ingest_holdings(conn, fund_config, date.today())
            except Exception as e:
                failed += 1
                # exc_info: the message alone rarely identifies a scraping failure, and
                # under Airflow the task log is the only diagnostic artifact there is.
                logger.warning(
                    "ingest-holdings: %s failed, skipping: %s",
                    fund_config.key,
                    e,
                    exc_info=True,
                )
                continue
            logger.info(f"{fund_config.key}: {count} holdings rows")
            total += count
        logger.info(
            f"ingest-holdings complete: {total} row(s) across {len(funds)} fund(s), "
            f"{failed} fund(s) failed."
        )
    finally:
        conn.close()
    return build_run_result(
        units_total=len(funds),
        rows_saved=total,
        failed=failed,
        funds=len(funds),
        funds_failed=failed,
    )


def run_ingest_market_data(
    *, fund: str | None = None, days: int = 7, tickers: list[str] | None = None
) -> dict[str, int | str]:
    """Prices, dividends and stock splits for the selected funds' holdings plus every tracked
    ticker, over a rolling window."""
    configure_logging()
    end_date = date.today()
    return _run_priced_flow(
        ingest_market_data,
        fund=fund,
        tickers=tickers,
        positional=(end_date - timedelta(days=days), end_date),
    )


def run_backfill_market_data(
    *,
    fund: str | None = None,
    tickers: list[str] | None = None,
    earliest_date: str | None = None,
    delay_seconds: float = 10.0,
    force_refresh: bool = False,
) -> dict[str, int | str]:
    """Fill missing history (prices, dividends, stock splits). `earliest_date` is an ISO date
    string or None -- see `pipeline._backfill_window` for what omitting it means (resume rather
    than deepen), and what `force_refresh` overrides (rewrite rows that already exist)."""
    configure_logging()
    return _run_priced_flow(
        backfill_market_data,
        fund=fund,
        tickers=tickers,
        positional=(_parse_earliest_date(earliest_date), date.today(), delay_seconds),
        extra_kwargs={"force_refresh": force_refresh},
    )


def run_calculate_returns(*, tickers: list[str] | None = None) -> dict[str, int | str]:
    """Rebuild market_returns from whatever is currently in raw_market_data.

    Much simpler than the pricing flows: no funds, no tracked tickers, no ticker resolution --
    calculate_returns reads only what raw_market_data already contains, so this is just
    settings, a connection, and the flow call.
    """
    configure_logging()
    settings = load_settings()
    conn = get_connection(settings.database_url)
    try:
        return calculate_returns(conn, only_tickers=set(tickers) if tickers else None)
    finally:
        conn.close()


def _run_priced_flow(
    flow,
    *,
    fund: str | None,
    tickers: list[str] | None,
    positional: tuple,
    extra_kwargs: dict | None = None,
) -> dict[str, int | str]:
    """Assemble the inputs the four ticker-universe flows all take, then run one.

    They differ only in which flow function is called and in the date/delay arguments that
    follow the universe; everything before that -- settings, fund selection, tracked tickers,
    ticker resolution, the --ticker filter, the connection lifecycle -- is identical, and was
    copy-pasted four times before this. `extra_kwargs` carries the options only one flow has
    (currently backfill's `force_refresh`), keeping them out of the shared positional tuple.
    """
    settings = load_settings()
    funds = _select_funds(load_funds(settings.config_path), fund)
    tracked_tickers = _load_enabled_tracked_tickers(settings.tracked_tickers_path)
    resolution = load_ticker_resolution(settings.exchange_suffixes_path)
    conn = get_connection(settings.database_url)
    try:
        return flow(
            conn,
            funds,
            tracked_tickers,
            resolution.exchange_suffixes,
            *positional,
            only_tickers=set(tickers) if tickers else None,
            ticker_overrides=resolution.ticker_overrides,
            **(extra_kwargs or {}),
        )
    finally:
        conn.close()


def _parse_earliest_date(earliest_date: str | None) -> date | None:
    return date.fromisoformat(earliest_date) if earliest_date is not None else None


def _select_funds(funds: list[FundConfig], fund_key: str | None) -> list[FundConfig]:
    if fund_key is not None:
        for fund in funds:
            if fund.key == fund_key:
                return [fund]
        raise ValueError(f"No fund configured with key: {fund_key!r}")
    return [fund for fund in funds if fund.enabled]


def _load_enabled_tracked_tickers(path: Path) -> list[TickerConfig]:
    # tracked_tickers.yaml is optional — a setup with only fund-holdings-based pricing
    # shouldn't need to create it just to run ingest-market-data. "Copied the template but
    # haven't filled it in yet" is the same situation as "never created it", so an empty file is
    # tolerated too rather than failing the ingest/backfill commands.
    if not path.is_file():
        return []
    return [tt for tt in load_tracked_tickers(path) if tt.enabled]
