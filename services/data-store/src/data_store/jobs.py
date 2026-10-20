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
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from data_store import ticker_onboarding
from data_store.config_loader import (
    FundConfig,
    TickerConfig,
    load_currencies,
    load_funds,
    load_macro_regions,
    load_ticker_resolution,
    load_tracked_tickers,
)
from data_store.pipeline import (
    backfill_fx_rates,
    backfill_market_data,
    calculate_macro_indicator_rates,
    calculate_returns,
    ingest_fx_rates,
    ingest_holdings,
    ingest_macro_indicators,
    ingest_market_data,
    ingest_regimes,
    ingest_risk_free_rates,
)
from data_store.read_flows import fx_rate_series, price_series, ticker_info, ticker_list
from data_store.run_result import build_run_result
from data_store.settings import SettingsError, load_settings
from data_store.ticker_onboarding import (
    currencies_with_tracked_tickers,
    process_ticker_requests,
    sync_config_tickers,
    tracked_ticker_configs,
)
from data_store.tracked_tickers.yfinance_source import YFinanceTickerLookupSource
from shared.db.connection import apply_schema, get_connection
from shared.logging_config import configure_logging

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "db" / "schema.sql"

# How far back the daily ingests reach for a ticker or currency pair that fell behind their
# rolling window, e.g. after the DAG didn't run for a while (see pipeline._fetch_windows). A
# year covers any outage worth recovering from without a backfill, and bounds what a delisted
# ticker, which never catches up, costs every run.
CATCH_UP_DAYS = 366


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


def run_sync_tracked_tickers() -> dict[str, int | str]:
    """Bring the tracked_tickers table in line with tracked_tickers.yaml.

    Every YAML entry is passed, disabled ones included: `enabled: false` is how the YAML turns a
    config row off. A missing file is no entries, not an error -- the YAML is optional, the same
    as for the pricing flows.
    """
    configure_logging()
    settings = load_settings()
    config_tickers = _load_config_tickers(settings.tracked_tickers_path)
    conn = get_connection(settings.database_url)
    try:
        return sync_config_tickers(conn, config_tickers)
    finally:
        conn.close()


def run_process_ticker_requests() -> dict[str, int | str]:
    """One onboarding run: sync the YAML in, then take each due `pending` tracked ticker
    through backfill, FX and returns to `ready` -- see ticker_onboarding.process_ticker_requests.
    What the `ticker_onboarding` DAG runs every few minutes."""
    configure_logging()
    settings = load_settings()
    config_tickers = _load_config_tickers(settings.tracked_tickers_path)
    currencies_config = load_currencies(settings.currencies_path)
    conn = get_connection(settings.database_url)
    try:
        # Synced first, so a ticker just added to the YAML is onboarded in this same run.
        sync_config_tickers(conn, config_tickers)
        return process_ticker_requests(
            conn,
            currencies=currencies_config,
            lookup_source=YFinanceTickerLookupSource(),
            now=datetime.now(timezone.utc),
            today=date.today(),
        )
    finally:
        conn.close()


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
    ticker, over a rolling window -- reaching further back, up to CATCH_UP_DAYS, for a ticker
    that fell behind it."""
    configure_logging()
    end_date = date.today()
    return _run_priced_flow(
        ingest_market_data,
        fund=fund,
        tickers=tickers,
        ready_only=True,
        positional=(end_date - timedelta(days=days), end_date),
        extra_kwargs={"catch_up_limit": end_date - timedelta(days=CATCH_UP_DAYS)},
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
        ready_only=False,
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


def run_ingest_fx_rates(
    *, days: int = 7, currencies: list[str] | None = None
) -> dict[str, int | str]:
    """Currency-conversion rates for every enabled currency in config/currencies.yaml, plus every
    enabled tracked ticker's currency, against base_currency, over a rolling window --
    reaching further back, up to CATCH_UP_DAYS, for a pair that fell behind it."""
    configure_logging()
    settings = load_settings()
    currencies_config = load_currencies(settings.currencies_path)
    end_date = date.today()
    conn = get_connection(settings.database_url)
    try:
        return ingest_fx_rates(
            conn,
            currencies_with_tracked_tickers(conn, currencies_config),
            end_date - timedelta(days=days),
            end_date,
            only_currencies=set(currencies) if currencies else None,
            catch_up_limit=end_date - timedelta(days=CATCH_UP_DAYS),
        )
    finally:
        conn.close()


def run_backfill_fx_rates(
    *,
    currencies: list[str] | None = None,
    earliest_date: str | None = None,
    delay_seconds: float = 10.0,
    force_refresh: bool = False,
) -> dict[str, int | str]:
    """Fill missing FX rate history. `earliest_date` is an ISO date string or None -- see
    `pipeline._backfill_window` for what omitting it means (resume rather than deepen), and what
    `force_refresh` overrides (rewrite rows that already exist)."""
    configure_logging()
    settings = load_settings()
    currencies_config = load_currencies(settings.currencies_path)
    conn = get_connection(settings.database_url)
    try:
        return backfill_fx_rates(
            conn,
            currencies_with_tracked_tickers(conn, currencies_config),
            _parse_earliest_date(earliest_date),
            date.today(),
            delay_seconds,
            only_currencies=set(currencies) if currencies else None,
            force_refresh=force_refresh,
        )
    finally:
        conn.close()


def run_ingest_risk_free_rates(*, days: int = 7) -> dict[str, int | str]:
    """The ECB's €STR and EONIA over a rolling window, reaching further back, up to
    CATCH_UP_DAYS, for a series that fell behind it. A series with nothing stored yet is fetched
    in full -- see pipeline.ingest_risk_free_rates."""
    configure_logging()
    settings = load_settings()
    end_date = date.today()
    conn = get_connection(settings.database_url)
    try:
        return ingest_risk_free_rates(
            conn,
            end_date - timedelta(days=days),
            end_date,
            catch_up_limit=end_date - timedelta(days=CATCH_UP_DAYS),
        )
    finally:
        conn.close()


def run_ingest_macro_indicators(*, eu_regions: list[str] | None = None) -> dict[str, int | str]:
    """Land raw inflation/GDP indicators from FRED (US) and Eurostat (EU) into
    raw_macro_indicators. `eu_regions` defaults to the enabled entries in
    config/macro_regions.yaml; pass it explicitly to override (e.g. from a caller that has
    already resolved its own list)."""
    configure_logging()
    settings = load_settings()
    if not settings.fred_api_key:
        raise SettingsError("FRED_API_KEY is not set")
    regions = (
        eu_regions
        if eu_regions is not None
        else _load_enabled_macro_regions(settings.macro_regions_path)
    )
    conn = get_connection(settings.database_url)
    try:
        return ingest_macro_indicators(conn, fred_api_key=settings.fred_api_key, eu_regions=regions)
    finally:
        conn.close()


def run_calculate_macro_indicator_rates() -> dict[str, int | str]:
    """Rebuild macro_indicator_rates from whatever is currently in raw_macro_indicators.

    Much simpler than the pricing flows, same reason as run_calculate_returns: no config, no
    region resolution -- calculate_macro_indicator_rates reads only what raw_macro_indicators
    already contains.
    """
    configure_logging()
    settings = load_settings()
    conn = get_connection(settings.database_url)
    try:
        return calculate_macro_indicator_rates(conn)
    finally:
        conn.close()


def run_ingest_regimes() -> dict[str, int | str]:
    """Load regimes from the configured CSV file into the database."""
    configure_logging()
    settings = load_settings()
    conn = get_connection(settings.database_url)
    try:
        return ingest_regimes(conn, settings.regimes_csv_path)
    finally:
        conn.close()


def get_price_series(*, ticker: str, source: str, start: date, end: date) -> dict:
    """Price/return series for one ticker, for the read API.

    Named `get_*` rather than `run_*`, and returns data rather than a run result: unlike every
    flow above, there is nothing to save and no `{"status", "rows_saved"}` to report -- the
    caller wants a payload, not a summary of a write. Raises `TickerNotFound` (see
    `read_flows.py`) for an unknown ticker; the API layer turns that into a 404.
    """
    configure_logging()
    settings = load_settings()
    conn = get_connection(settings.database_url)
    try:
        return price_series(conn, ticker, source, start, end)
    finally:
        conn.close()


def get_ticker_info(*, ticker: str, source: str) -> dict:
    """Existence/validity info for one ticker, for the read API. Never raises for an unknown
    ticker -- returning `exists: False` *is* the answer this endpoint exists to give."""
    configure_logging()
    settings = load_settings()
    resolution = load_ticker_resolution(settings.exchange_suffixes_path)
    conn = get_connection(settings.database_url)
    try:
        return ticker_info(conn, ticker, source, resolution)
    finally:
        conn.close()


def get_ticker_list(*, source: str) -> dict:
    """Every ingested ticker for `source`, for the read API."""
    configure_logging()
    settings = load_settings()
    conn = get_connection(settings.database_url)
    try:
        return ticker_list(conn, source)
    finally:
        conn.close()


def get_ticker_lookup(*, ticker: str, source: str) -> dict:
    """What adding `ticker` would do, for the read API: already tracked (with its status), or
    the provider's answer. Raises `ticker_onboarding.InvalidSymbol` or `UnknownLookupSource`."""
    configure_logging()
    settings = load_settings()
    lookup_source = ticker_onboarding.lookup_source_for(source)
    conn = get_connection(settings.database_url)
    try:
        return ticker_onboarding.lookup_ticker(
            conn, ticker, lookup_source, ticker_onboarding.LOOKUP_CACHE
        )
    finally:
        conn.close()


def run_add_tracked_ticker(*, ticker: str, requested_by, enforce_limit: bool, source: str) -> dict:
    """Add a user's ticker for onboarding, for the read API. Returns the row plus `created`.
    Raises the `ticker_onboarding` request exceptions the API maps to status codes."""
    configure_logging()
    settings = load_settings()
    lookup_source = ticker_onboarding.lookup_source_for(source)
    conn = get_connection(settings.database_url)
    try:
        row, created = ticker_onboarding.add_tracked_ticker(
            conn,
            ticker,
            requested_by=requested_by,
            enforce_limit=enforce_limit,
            limit_per_day=settings.ticker_requests_per_day,
            lookup_source=lookup_source,
            cache=ticker_onboarding.LOOKUP_CACHE,
            now=datetime.now(timezone.utc),
        )
    finally:
        conn.close()
    return {**asdict(row), "created": created}


def get_tracked_tickers(
    *,
    source: str,
    requested_by=None,
    statuses: list[str] | None = None,
    tickers: list[str] | None = None,
) -> dict:
    """Tracked tickers, narrowed by any filters given, for the read API."""
    configure_logging()
    settings = load_settings()
    conn = get_connection(settings.database_url)
    try:
        rows = ticker_onboarding.list_tracked(
            conn, source=source, requested_by=requested_by, statuses=statuses, tickers=tickers
        )
    finally:
        conn.close()
    return {"tracked_tickers": [asdict(row) for row in rows]}


def run_update_tracked_ticker(
    *, ticker: str, source: str, enabled: bool | None, retry: bool
) -> dict:
    """An admin's change to one tracked ticker, for the read API."""
    configure_logging()
    settings = load_settings()
    conn = get_connection(settings.database_url)
    try:
        row = ticker_onboarding.update_tracked(conn, ticker, source, enabled=enabled, retry=retry)
    finally:
        conn.close()
    return asdict(row)


def get_fx_series(*, base: str, quote: str, source: str, start: date, end: date) -> dict:
    """FX rate series for a currency pair, for the read API. Loads currencies.yaml the same way
    run_ingest_fx_rates does, since fx_rate_series needs the configured base currency to derive
    an inverse or triangulated rate, widened with tracked tickers' currencies the same way the
    FX flows are. Raises `CurrencyNotTracked` for a currency outside that list; the API layer
    turns that into a 404.
    """
    configure_logging()
    settings = load_settings()
    currencies_config = load_currencies(settings.currencies_path)
    conn = get_connection(settings.database_url)
    try:
        return fx_rate_series(
            conn,
            currencies_with_tracked_tickers(conn, currencies_config),
            base,
            quote,
            source,
            start,
            end,
        )
    finally:
        conn.close()


def _run_priced_flow(
    flow,
    *,
    fund: str | None,
    tickers: list[str] | None,
    ready_only: bool,
    positional: tuple,
    extra_kwargs: dict | None = None,
) -> dict[str, int | str]:
    """Assemble the inputs the four ticker-universe flows all take, then run one.

    They differ only in which flow function is called and in the date/delay arguments that
    follow the universe; everything before that -- settings, fund selection, tracked tickers,
    ticker resolution, the --ticker filter, the connection lifecycle -- is identical, and was
    copy-pasted four times before this. `extra_kwargs` carries the options only one flow has
    (currently backfill's `force_refresh`), keeping them out of the shared positional tuple.

    Tracked tickers come from the tracked_tickers table, after syncing the YAML into it.
    `ready_only` is the one difference between the flows there -- see
    `ticker_onboarding.tracked_ticker_configs` for why the daily ingest needs it.
    """
    settings = load_settings()
    funds = _select_funds(load_funds(settings.config_path), fund)
    config_tickers = _load_config_tickers(settings.tracked_tickers_path)
    resolution = load_ticker_resolution(settings.exchange_suffixes_path)
    conn = get_connection(settings.database_url)
    try:
        # Synced first, so a ticker just added to the YAML is in the table before it's read.
        sync_config_tickers(conn, config_tickers)
        tracked_tickers = tracked_ticker_configs(conn, ready_only=ready_only)
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


def _load_config_tickers(path: Path) -> list[TickerConfig]:
    """Every tracked_tickers.yaml entry, disabled ones included -- the sync needs those to turn
    a config row off.

    The file is optional: a setup with only fund-holdings-based pricing shouldn't need to create
    it. "Copied the template but haven't filled it in yet" is the same situation as "never
    created it", so an empty file is tolerated too (load_tracked_tickers returns [] for it).
    """
    if not path.is_file():
        return []
    return load_tracked_tickers(path)


def _load_enabled_macro_regions(path: Path) -> list[str]:
    return [r.code for r in load_macro_regions(path) if r.enabled]
