import logging
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from data_store.config_loader import CurrenciesConfig, FundConfig, TickerConfig
from data_store.fx.repository import get_earliest_fx_dates, save_fx_rates
from data_store.fx.yfinance_source import YFinanceFxRateSource
from data_store.holdings.parser import fetch_holdings_csv
from data_store.holdings.repository import (
    get_latest_tickers,
    normalize_holdings,
    save_holdings,
)
from data_store.holdings.scraper import get_holdings_csv_url
from data_store.macro.calculator import compute_indicator_rates
from data_store.macro.eurostat_source import EurostatMacroIndicatorSource
from data_store.macro.fred_source import FredMacroIndicatorSource
from data_store.macro.repository import (
    get_raw_indicators,
    save_macro_indicator_rates,
    save_raw_macro_indicators,
)
from data_store.market_data.repository import get_earliest_dates, save_raw_market_data
from data_store.market_data.yfinance_source import YFinanceMarketDataSource
from data_store.regimes.loader import load_regimes
from data_store.regimes.repository import save_regimes
from data_store.returns.calculator import compute_returns
from data_store.returns.repository import (
    get_sources,
    get_tickers,
    iter_price_series,
    save_market_returns,
)
from data_store.run_result import build_run_result
from data_store.ticker_resolution import holding_symbol, resolve_yfinance_ticker

logger = logging.getLogger(__name__)

# Lower bound sent to yfinance when a backfill is asked for "as far back as available".
# yfinance clips to each ticker's actual listing date, so this is effectively unbounded.
UNBOUNDED_EARLIEST_DATE = date(1900, 1, 1)

# Sleep between the full-history re-fetches triggered by a split. Splits cluster (an index-wide
# event can move several holdings on one day), and each refresh is a whole-history request, so
# firing them back to back is exactly the shape that trips yfinance's rate limiting. Matches
# backfill's default delay; not exposed as a parameter because nothing chooses it per run.
SPLIT_REFRESH_DELAY_SECONDS = 10.0

# Tickers per calculate_returns batch. Returns are purely per-ticker, so this is the unit that
# keeps a full-table rebuild's memory bounded -- the table is 8.95M rows across 1,278 tickers,
# far past what "one SELECT, one DataFrame" can hold comfortably in an Airflow worker.
RETURNS_BATCH_SIZE = 250

# A new fund provider or price source is a new registry entry here, not a branch in the
# functions below.
SCRAPERS = {
    "ishares": get_holdings_csv_url,
}

MARKET_DATA_SOURCES = {
    "yfinance": YFinanceMarketDataSource,
}

FX_RATE_SOURCES = {
    "yfinance": YFinanceFxRateSource,
}

MACRO_INDICATOR_SOURCES = {
    "fred": FredMacroIndicatorSource,
    "eurostat": EurostatMacroIndicatorSource,
}


@contextmanager
def _yfinance_logging_silenced():
    """Quiet yfinance's own logger for the duration of a backfill, then put it back.

    yfinance logs an ERROR-level "Failed download"/"Data doesn't exist" line on every empty
    result -- which during a backfill is the *expected* outcome for every ticker once it's
    requested back past its actual listing date. Left alone that noise drowns out the
    per-ticker progress below. Restoring on the way out matters because the level is global to
    the process: leaving it at CRITICAL silences yfinance for anything sharing that process
    (a later ingest in the same run, or the rest of a test session), which would hide the
    genuinely interesting empty results the daily flow relies on seeing.
    """
    yf_logger = logging.getLogger("yfinance")
    previous_level = yf_logger.level
    yf_logger.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        yf_logger.setLevel(previous_level)


def ingest_holdings(conn, fund_config: FundConfig, today: date) -> int:
    try:
        resolve_url = SCRAPERS[fund_config.provider]
    except KeyError:
        raise ValueError(f"Unknown provider: {fund_config.provider!r}") from None

    csv_url = resolve_url(fund_config.url)
    raw = fetch_holdings_csv(csv_url)
    normalized = normalize_holdings(raw)
    return save_holdings(conn, fund_config.key, today, normalized)


def ingest_market_data(
    conn,
    funds: list[FundConfig],
    tracked_tickers: list[TickerConfig],
    exchange_suffixes: dict[str, str | None],
    start_date: date,
    end_date: date,
    only_tickers: set[str] | None = None,
    ticker_overrides: dict[str, str] | None = None,
) -> dict[str, int | str]:
    """Prices, dividends and stock splits for every fund's holdings plus every tracked ticker,
    in one pass -- one fetch per ticker per run, since a MarketDataSource returns all three
    together (see market_data/yfinance_source.py). Tickers are resolved into a single deduped
    map first (not fetched per-source) so a ticker that's both a fund holding and an explicitly
    tracked ticker — or held by more than one fund — is only fetched once, not once per source.
    `only_tickers`, when given, restricts the run to exactly those final (post-suffix-resolution)
    tickers -- e.g. "0700.HK", not the raw "0700" -- so it can target holdings-derived tickers
    too, not just tracked_tickers entries. `ticker_overrides` is forwarded to ticker resolution
    -- see ticker_resolution.py. Logs per-group progress and a final summary; logging_config
    decides where that output lands."""
    resolved = _resolve_priceable_tickers(
        conn, funds, tracked_tickers, exchange_suffixes, ticker_overrides
    )
    resolved, unmatched = _apply_only_tickers(resolved, only_tickers, "ingest-market-data")
    if not resolved:
        logger.info("ingest-market-data: no tickers to price, nothing to do.")
        return build_run_result(
            units_total=0,
            rows_saved=0,
            failed=unmatched,
            tickers_resolved=0,
            tickers_unmatched=unmatched,
            groups=0,
            groups_failed=0,
        )

    groups = _group_by_source_and_type(resolved)
    logger.info(
        f"ingest-market-data: resolved {len(resolved)} ticker(s) across {len(groups)} "
        "(source, asset_type) group(s)."
    )

    total = 0
    failed = 0
    splits_refreshed = 0
    splits_refresh_failed = 0
    for (source_name, asset_type), tickers in groups.items():
        try:
            source_cls = MARKET_DATA_SOURCES[source_name]
        except KeyError:
            # A misconfigured source is a config error, not a runtime hiccup: it will fail
            # identically on every rerun, so it stays fatal rather than being retried nightly.
            raise ValueError(f"Unknown market data source: {source_name!r}") from None

        source = source_cls()
        logger.info(
            f"ingest-market-data: fetching market data for {len(tickers)} ticker(s) "
            f"(source={source_name}, asset_type={asset_type})."
        )
        # One group's failure must not cost the groups after it. This runs unattended on a
        # schedule, and groups saved before an escaping exception are already committed -- so
        # letting it propagate turns a partial success into a wholly failed, partially written
        # run. In practice this now mostly guards save_raw_market_data -- fetch() itself already
        # isolates failures per ticker (see YFinanceMarketDataSource._fetch_one), so a single bad
        # ticker no longer needs this group-level catch to avoid taking the rest of the group
        # down with it.
        try:
            data = source.fetch(tickers, start_date, end_date)
            if data.empty:
                logger.info(
                    f"ingest-market-data: no data for this group "
                    f"(source={source_name}, asset_type={asset_type})."
                )
                continue

            saved = save_raw_market_data(conn, source.name, asset_type, data)
        except Exception as e:
            failed += 1
            logger.warning(
                "ingest-market-data: group (source=%s, asset_type=%s, %d ticker(s)) failed, "
                "skipping: %s",
                source_name,
                asset_type,
                len(tickers),
                e,
                exc_info=True,
            )
            continue

        logger.info(
            f"ingest-market-data: saved {saved} row(s) "
            f"(source={source_name}, asset_type={asset_type})."
        )
        total += saved

        # A split in today's data invalidates everything stored outside this window -- see
        # _refresh_split_histories. Deliberately outside the group try/except above: a repair
        # failure must not be reported as the group having failed, since the group's own rows
        # are saved and correct.
        refresh_rows, refreshed, refresh_failed = _refresh_split_histories(
            conn, source, asset_type, _tickers_with_splits(data), start_date, end_date
        )
        total += refresh_rows
        splits_refreshed += refreshed
        splits_refresh_failed += refresh_failed

    logger.info(
        f"ingest-market-data complete: {total} row(s) across {len(resolved)} ticker(s), "
        f"{failed} group(s) failed, {splits_refreshed} post-split history refresh(es) "
        f"({splits_refresh_failed} failed)."
    )
    return build_run_result(
        units_total=len(resolved),
        tickers_resolved=len(resolved),
        tickers_unmatched=unmatched,
        rows_saved=total,
        failed=failed + unmatched + splits_refresh_failed,
        groups=len(groups),
        groups_failed=failed,
        splits_refreshed=splits_refreshed,
        splits_refresh_failed=splits_refresh_failed,
    )


def _tickers_with_splits(data) -> list[str]:
    """Tickers in a just-ingested frame that recorded a split, i.e. whose stored history is now
    on the wrong scale.

    Tolerates a frame without the column at all rather than requiring it: `stock_splits` is a
    convention of the sources we have, not part of any interface a MarketDataSource must satisfy,
    and a source that doesn't report splits should ingest normally instead of raising here.
    """
    if "stock_splits" not in getattr(data, "columns", ()):
        return []
    with_splits = data[data["stock_splits"].fillna(0) != 0]
    return sorted(with_splits["ticker"].unique())


def _refresh_split_histories(
    conn, source, asset_type: str, tickers: list[str], start_date: date, end_date: date
) -> tuple[int, int, int]:
    """Re-fetch and rewrite the full stored history of each ticker that just split.

    Yahoo restates a ticker's *entire* price and dividend history into the post-split scale --
    AAPL's 2020-08-28 close reads 124.8075 against the 499.23 it actually traded at, and its
    2014-05-08 dividend reads 0.1175 against the $3.29 declared. The daily ingest only re-fetches
    a rolling window, so the rows outside it keep the pre-split scale and the series ends up on
    two scales at once: a step change at the window boundary that is indistinguishable from a
    real several-hundred-percent move, and which would silently poison every derived return.

    Returns (rows_saved, refreshed, failed). Failures are isolated and never fatal -- the day's
    own rows are already committed and correct, and losing them because a repair fetch failed
    would turn a good run into a bad one.

    Sharp edge, deliberately not solved with a durable work queue: the retry only happens while
    the split day stays inside the rolling ingest window. Once it ages out, nothing re-triggers
    this and the ticker is left on two scales with no further warning -- which is why the failure
    below is a warning naming `--force-refresh`, not a routine skip.
    """
    if not tickers:
        return 0, 0, 0

    floors = get_earliest_dates(conn, source.name, tickers)
    rows = refreshed = failed = 0
    for ticker in tickers:
        floor = floors.get(ticker)
        if floor is None or floor >= start_date:
            # Nothing stored below the window we just re-fetched, so nothing is left on the old
            # scale. Skipping keeps a newly-followed ticker from costing a whole-history request.
            continue

        logger.info(
            "ingest-market-data: %s split -- restating its stored history from %s.",
            ticker,
            floor,
        )
        try:
            data = source.fetch([ticker], floor, end_date)
            if data.empty:
                raise ValueError("source returned no rows for the full history")
            saved = save_raw_market_data(conn, source.name, asset_type, data)
        except Exception as e:
            failed += 1
            logger.warning(
                "ingest-market-data: %s split but its stored history could NOT be restated "
                "(%s). Rows before %s are still on the pre-split scale, so this ticker's "
                "history is inconsistent and any return derived from it will be wrong. This "
                "retries only while the split day stays in the ingest window -- repair it with "
                "`data-store backfill-market-data --ticker %s --force-refresh`.",
                ticker,
                e,
                start_date,
                ticker,
                exc_info=True,
            )
            time.sleep(SPLIT_REFRESH_DELAY_SECONDS)
            continue

        rows += saved
        refreshed += 1
        logger.info(
            f"ingest-market-data: {ticker} history restated -> {saved} row(s) "
            f"({floor} to {end_date})."
        )
        time.sleep(SPLIT_REFRESH_DELAY_SECONDS)
    return rows, refreshed, failed


def calculate_returns(conn, only_tickers: set[str] | None = None) -> dict[str, int | str]:
    """Full rebuild of `market_returns` from `raw_market_data`, batched by ticker.

    Full rebuild, not incremental: a split retroactively changes the correct value of every
    earlier row for that ticker (see returns/calculator.py), so an incremental design would be
    actively wrong on a rerun, not merely wasteful. Sources are discovered from the data itself
    (`get_sources`) rather than the market-data fetch registry -- this reads what's already
    stored, and never needs to know how to fetch anything.

    No ticker resolution, no config: operates on exactly what `raw_market_data` already
    contains. `only_tickers`, when given, restricts the run and follows the same convention as
    the pricing flows -- a name that matches nothing in any source is a failure, not silence.

    Batching is the failure-isolation unit: one batch's save failing (e.g. a bounds-violating
    computed value) is warned about and skipped, and does not cost the batches around it.
    """
    sources = get_sources(conn)
    if not sources:
        logger.info("calculate-returns: no market data in raw_market_data, nothing to do.")

    matched_tickers: set[str] = set()
    total_rows = 0
    batches = 0
    batches_failed = 0

    for source in sources:
        tickers = get_tickers(conn, source)
        if only_tickers is not None:
            tickers = [t for t in tickers if t in only_tickers]
        if not tickers:
            continue
        matched_tickers.update(tickers)

        logger.info(
            "calculate-returns: recalculating %d ticker(s) for source=%s.", len(tickers), source
        )
        for batch in iter_price_series(conn, source, tickers, RETURNS_BATCH_SIZE):
            batches += 1
            if batch.empty:
                continue
            try:
                computed = compute_returns(batch)
                saved = save_market_returns(conn, source, computed)
            except Exception as e:
                batches_failed += 1
                logger.warning(
                    "calculate-returns: a batch failed for source=%s, skipping: %s",
                    source,
                    e,
                    exc_info=True,
                )
                continue
            total_rows += saved

    unmatched_tickers = (only_tickers - matched_tickers) if only_tickers is not None else set()
    if unmatched_tickers:
        logger.warning(
            "calculate-returns: no stored data matches %s.",
            ", ".join(repr(t) for t in sorted(unmatched_tickers)),
        )

    logger.info(
        f"calculate-returns complete: {total_rows} row(s) across {len(matched_tickers)} "
        f"ticker(s), {batches} batch(es) ({batches_failed} failed), "
        f"{len(unmatched_tickers)} ticker(s) unmatched."
    )
    return build_run_result(
        units_total=len(matched_tickers),
        rows_saved=total_rows,
        failed=batches_failed + len(unmatched_tickers),
        tickers_processed=len(matched_tickers),
        tickers_unmatched=len(unmatched_tickers),
        batches=batches,
        batches_failed=batches_failed,
    )


def ingest_regimes(conn, csv_path: Path) -> dict[str, int | str]:
    """Load regimes from a local CSV file into the regimes table.

    Full sync: the table is replaced each run from the CSV. Each run is a single transaction:
    DELETE all existing rows, then COPY the CSV's rows in. No upserts, no merge.

    csv_path is absolute or relative to the service root (resolved via settings._resolve).
    """
    logger.info(f"ingest-regimes: loading regimes from {csv_path}")
    regimes = load_regimes(csv_path)

    if not regimes:
        logger.info("ingest-regimes: no regimes found in CSV, nothing to do.")
    else:
        logger.info(f"ingest-regimes: loaded {len(regimes)} regime(s) from CSV")
        logger.info("ingest-regimes: writing to database")
    saved = save_regimes(conn, regimes)

    logger.info(f"ingest-regimes complete: {saved} regime row(s)")
    return build_run_result(units_total=len(regimes), rows_saved=saved, failed=0)


def _resolve_fx_pairs(currencies: CurrenciesConfig) -> list[tuple[str, str]]:
    """Every enabled currency (other than the base itself) paired against `base_currency`, in
    the (base, quote) shape both FxRateSource.fetch and fx_rates itself use."""
    base = currencies.base_currency
    return [(base, c.code) for c in currencies.currencies if c.enabled and c.code != base]


def _apply_only_currencies(
    pairs: list[tuple[str, str]], only_currencies: set[str] | None, prefix: str
) -> tuple[list[tuple[str, str]], int]:
    """Restrict `pairs` to those whose quote currency is in `only_currencies`, warning about any
    requested code that matched nothing -- same convention as _apply_only_tickers."""
    if only_currencies is None:
        return pairs, 0

    filtered = [(base, quote) for base, quote in pairs if quote in only_currencies]
    matched = {quote for _, quote in filtered}
    unmatched = sorted(only_currencies - matched)
    if unmatched:
        logger.warning(
            "%s: no currently tracked currency matches %s.",
            prefix,
            ", ".join(repr(c) for c in unmatched),
        )
    return filtered, len(unmatched)


def ingest_fx_rates(
    conn,
    currencies: CurrenciesConfig,
    start_date: date,
    end_date: date,
    only_currencies: set[str] | None = None,
) -> dict[str, int | str]:
    """Currency-conversion rates for every enabled currency against `currencies.base_currency`,
    in one pass -- one fetch, since a single FxRateSource call covers every pair. Simpler than
    ingest_market_data: one fixed source, no exchange resolution, no fund coupling, no
    split-restatement pass -- none of those concerns apply to FX rates."""
    pairs = _resolve_fx_pairs(currencies)
    pairs, unmatched = _apply_only_currencies(pairs, only_currencies, "ingest-fx-rates")
    if not pairs:
        logger.info("ingest-fx-rates: no currency pairs to price, nothing to do.")
        return build_run_result(
            units_total=0,
            rows_saved=0,
            failed=unmatched,
            currencies_resolved=0,
            currencies_unmatched=unmatched,
        )

    source = FX_RATE_SOURCES["yfinance"]()
    logger.info(f"ingest-fx-rates: fetching {len(pairs)} currency pair(s) (source=yfinance).")
    data = source.fetch(pairs, start_date, end_date)
    if data.empty:
        logger.info("ingest-fx-rates: no data returned.")
        saved = 0
    else:
        saved = save_fx_rates(conn, source.name, data)

    logger.info(f"ingest-fx-rates complete: {saved} row(s) across {len(pairs)} currency pair(s).")
    return build_run_result(
        units_total=len(pairs),
        currencies_resolved=len(pairs),
        currencies_unmatched=unmatched,
        rows_saved=saved,
        failed=unmatched,
    )


@_yfinance_logging_silenced()
def backfill_fx_rates(
    conn,
    currencies: CurrenciesConfig,
    earliest_date: date | None,
    end_date: date,
    delay_seconds: float,
    only_currencies: set[str] | None = None,
    force_refresh: bool = False,
) -> dict[str, int | str]:
    """For each currency pair (same resolution as ingest_fx_rates), fetches and stores the
    stretch of history it's still missing -- same three-mode semantics as
    backfill_market_data/_backfill_window, with fx_rates as its own checkpoint via
    get_earliest_fx_dates. One pair at a time, sleeping delay_seconds after each live fetch
    (success or failure), never after a skip."""
    pairs = _resolve_fx_pairs(currencies)
    pairs, unmatched = _apply_only_currencies(pairs, only_currencies, "backfill-fx-rates")
    if not pairs:
        logger.info("backfill-fx-rates: no currency pairs to fetch, nothing to do.")
        return build_run_result(
            units_total=0,
            currencies_resolved=0,
            currencies_unmatched=unmatched,
            rows_saved=0,
            failed=unmatched,
            currencies_fetched=0,
            currencies_skipped=0,
            currencies_failed=0,
        )

    source = FX_RATE_SOURCES["yfinance"]()
    floors = get_earliest_fx_dates(conn, source.name, pairs)

    total_rows = 0
    processed = skipped = failed = 0
    pair_count = len(pairs)
    for base, quote in pairs:
        label = f"{base}/{quote}"
        floor = floors.get((base, quote))
        window = _backfill_window(floor, earliest_date, end_date, force_refresh)
        if window is None:
            skipped += 1
            processed += 1
            logger.info(_already_covered_message("backfill-fx-rates", label, floor))
            continue
        fetch_start, fetch_end = window

        try:
            data = source.fetch([(base, quote)], fetch_start, fetch_end)
        except Exception as e:
            failed += 1
            logger.warning("backfill-fx-rates: %s failed, skipping: %s", label, e, exc_info=True)
            time.sleep(delay_seconds)
            processed += 1
            continue

        if not data.empty:
            try:
                saved = save_fx_rates(conn, source.name, data)
                total_rows += saved
            except Exception as e:
                failed += 1
                logger.warning(
                    "backfill-fx-rates: %s failed to save, skipping: %s",
                    label,
                    e,
                    exc_info=True,
                )
                time.sleep(delay_seconds)
                processed += 1
                continue
            logger.info(
                f"backfill-fx-rates: {label} -> {saved} rows ({fetch_start} to {fetch_end})"
            )
        else:
            logger.info(f"backfill-fx-rates: {label} -> no data ({fetch_start} to {fetch_end})")
        time.sleep(delay_seconds)
        processed += 1

    logger.info(
        f"backfill-fx-rates complete: {pair_count} pair(s) "
        f"({skipped} already covered, {failed} failed), {total_rows} rows written"
    )
    return build_run_result(
        units_total=pair_count,
        currencies_resolved=pair_count,
        currencies_unmatched=unmatched,
        rows_saved=total_rows,
        failed=failed + unmatched,
        currencies_fetched=processed - skipped - failed,
        currencies_skipped=skipped,
        currencies_failed=failed,
    )


@_yfinance_logging_silenced()
def backfill_market_data(
    conn,
    funds: list[FundConfig],
    tracked_tickers: list[TickerConfig],
    exchange_suffixes: dict[str, str | None],
    earliest_date: date | None,
    end_date: date,
    delay_seconds: float,
    only_tickers: set[str] | None = None,
    ticker_overrides: dict[str, str] | None = None,
    force_refresh: bool = False,
) -> dict[str, int | str]:
    """For each priceable ticker (same resolution as ingest_market_data), fetches and stores
    the stretch of history it's still missing — see _backfill_window for how `earliest_date`
    selects between resuming (omitted) and reaching further back than a previous run
    (given). Either way raw_market_data is its own checkpoint; no separate state table is needed.

    One ticker at a time, sleeping delay_seconds after each live fetch (success or failure) —
    never after a skip. A ticker that fails after YFinanceMarketDataSource's internal retries is
    logged (inside the source itself) and comes back empty here rather than raising; either way
    it contributes 0 rows and the run continues. An empty frame alone can't say whether the
    provider has no data or never answered, so the source's `unreachable` says which: an
    unreachable ticker counts as failed (`tickers_unreachable`, and the run is `partial`), while
    a ticker with no data -- including one the provider says doesn't exist -- does not. That
    keeps a delisted holding from failing every run, and a network outage from passing as a
    quiet one; ticker onboarding relies on it to tell a user which happened.

    `only_tickers`, when given, restricts the run to exactly those final (post-suffix-resolution)
    tickers -- e.g. "0700.HK", not the raw "0700" -- so it can target holdings-derived tickers
    too, not just tracked_tickers entries.
    `ticker_overrides` is forwarded to ticker resolution -- see ticker_resolution.py.

    Decorated rather than the daily flow because an empty result means something different
    here: during a backfill it's the expected end state for every ticker, whereas for
    ingest_market_data it more likely means a delisted or mistyped ticker worth seeing.
    """
    resolved = _resolve_priceable_tickers(
        conn, funds, tracked_tickers, exchange_suffixes, ticker_overrides
    )
    resolved, unmatched = _apply_only_tickers(resolved, only_tickers, "backfill-market-data")
    if not resolved:
        logger.info("backfill-market-data: no tickers to fetch, nothing to do.")
        return build_run_result(
            units_total=0,
            tickers_resolved=0,
            tickers_unmatched=unmatched,
            rows_saved=0,
            failed=unmatched,
            tickers_fetched=0,
            tickers_skipped=0,
            tickers_failed=0,
            tickers_unreachable=0,
        )

    total_rows = 0
    processed = skipped = failed = unreachable = 0
    ticker_count = len(resolved)
    for (source_name, asset_type), tickers in _group_by_source_and_type(resolved).items():
        try:
            source_cls = MARKET_DATA_SOURCES[source_name]
        except KeyError:
            raise ValueError(f"Unknown market data source: {source_name!r}") from None

        source = source_cls()
        # source.name, not the registry key: rows are written under .name, so floors must be
        # read under it too or a source whose key differs never finds its own data.
        floors = get_earliest_dates(conn, source.name, tickers)
        for ticker in tickers:
            floor = floors.get(ticker)
            window = _backfill_window(floor, earliest_date, end_date, force_refresh)
            if window is None:
                skipped += 1
                processed += 1
                logger.info(_already_covered_message("backfill-market-data", ticker, floor))
                continue
            fetch_start, fetch_end = window

            try:
                data = source.fetch([ticker], fetch_start, fetch_end)
            except Exception as e:
                # A truly unexpected failure (not a per-ticker fetch problem, which the source
                # already absorbs -- see the docstring above), e.g. a bug in the source itself.
                failed += 1
                logger.warning(
                    "backfill-market-data: %s failed, skipping: %s", ticker, e, exc_info=True
                )
                time.sleep(delay_seconds)
                processed += 1
                continue

            if ticker in source.unreachable:
                failed += 1
                unreachable += 1
                logger.warning(
                    "backfill-market-data: %s couldn't reach the provider, skipping.", ticker
                )
                time.sleep(delay_seconds)
                processed += 1
                continue

            if not data.empty:
                try:
                    saved = save_raw_market_data(conn, source.name, asset_type, data)
                    total_rows += saved
                except Exception as e:
                    # Unlike a fetch failure, this is data the source successfully returned --
                    # it just couldn't be written, e.g. a value out of the column's range. That's
                    # an upstream data-quality problem, not a reason to lose every ticker still
                    # to come in this run.
                    failed += 1
                    logger.warning(
                        "backfill-market-data: %s failed to save, skipping: %s",
                        ticker,
                        e,
                        exc_info=True,
                    )
                    time.sleep(delay_seconds)
                    processed += 1
                    continue
                logger.info(
                    f"backfill-market-data: {ticker} -> {saved} rows "
                    f"({fetch_start} to {fetch_end})"
                )
            else:
                logger.info(
                    f"backfill-market-data: {ticker} -> no data ({fetch_start} to {fetch_end})"
                )
            time.sleep(delay_seconds)
            processed += 1
            if processed % 25 == 0:
                logger.info(
                    f"backfill-market-data progress: {processed}/{ticker_count} tickers, "
                    f"{total_rows} rows so far"
                )

    logger.info(
        f"backfill-market-data complete: {ticker_count} tickers "
        f"({skipped} already covered, {failed} failed ({unreachable} unreachable)), "
        f"{total_rows} rows written"
    )
    return build_run_result(
        units_total=ticker_count,
        tickers_resolved=ticker_count,
        tickers_unmatched=unmatched,
        rows_saved=total_rows,
        failed=failed + unmatched,
        tickers_fetched=processed - skipped - failed,
        tickers_skipped=skipped,
        tickers_failed=failed,
        tickers_unreachable=unreachable,
    )


def _apply_only_tickers(
    resolved: dict[str, tuple[str, str]], only_tickers: set[str] | None, prefix: str
) -> tuple[dict[str, tuple[str, str]], int]:
    """Restrict a resolved universe to `only_tickers`, warning about any that matched nothing.

    Without the warning a typo'd --ticker and a genuinely empty universe produce identical
    output ("nothing to do"), which is exactly the pair the logging rules say must never look
    the same. An unmatched ticker is a real anomaly, so it's logger.warning (visible by
    default) rather than a progress print.
    """
    if only_tickers is None:
        return resolved, 0

    filtered = {t: v for t, v in resolved.items() if t in only_tickers}
    unmatched = sorted(only_tickers - filtered.keys())
    if unmatched:
        logger.warning(
            "%s: no currently followed ticker matches %s -- check spelling and note these are "
            "matched post-suffix-resolution (e.g. '0700.HK', not '0700').",
            prefix,
            ", ".join(repr(t) for t in unmatched),
        )
    return filtered, len(unmatched)


def _backfill_window(
    floor: date | None,
    earliest_date: date | None,
    end_date: date,
    force_refresh: bool = False,
) -> tuple[date, date] | None:
    """The (start, end) one ticker still needs fetched, or None when there's nothing left.

    Three modes, because "how far back have we already looked?" is not something the data itself
    records:

    - `force_refresh`: rewrite the ticker's whole range, never skip. The repair path for a
      history left on two price scales by a split the daily ingest could not restate (see
      _refresh_split_histories). The other two modes only ever fetch *below* the earliest stored
      date, so without this there is no supported way to rewrite rows that already exist.

    - `earliest_date is None` (the plain rerun): a ticker with any stored row is done. Sound
      because a source returns a ticker's whole requested range in one call -- an interrupted
      backfill leaves each ticker either fully fetched or untouched, never half-done -- so
      "has rows" really does mean "already walked back as far as this ticker goes". This is
      what makes a rerun cheap; without it every completed ticker gets re-requested (and
      re-slept) forever, since its floor is its listing date, which is always well above any
      sane explicit earliest_date.
    - `earliest_date` given: reach further back than a previous run did, so a ticker with rows
      is not skipped -- it gets the stretch below its floor. `fetch_end` is the floor itself,
      not floor - 1 day: fetch()'s end is exclusive (confirmed against real yfinance), so the
      floor already stops the day before it, and subtracting a day would silently drop the
      trading day immediately before it.
    """
    if force_refresh:
        return (earliest_date or UNBOUNDED_EARLIEST_DATE, end_date)

    if earliest_date is None:
        if floor is not None:
            return None
        return (UNBOUNDED_EARLIEST_DATE, end_date)

    fetch_end = floor if floor else end_date
    if fetch_end <= earliest_date:
        return None
    return (earliest_date, fetch_end)


def _already_covered_message(prefix: str, ticker: str, floor: date | None) -> str:
    covered = f"back to {floor}" if floor else "for the requested window"
    return f"{prefix}: {ticker} already covered {covered}, skipping"


def _resolve_priceable_tickers(
    conn,
    funds: list[FundConfig],
    tracked_tickers: list[TickerConfig],
    exchange_suffixes: dict[str, str | None],
    ticker_overrides: dict[str, str] | None = None,
) -> dict[str, tuple[str, str]]:
    """{ticker: (asset_type, price_source)}. Holdings-derived tickers are resolved into their
    yfinance-ready suffixed form (e.g. "0700" on "Hong Kong Exchanges And Clearing Ltd" ->
    "0700.HK") via exchange_suffixes and default to ("equity", fund.price_source); a raw
    ticker whose exchange has no mapping entry (and no ticker_overrides entry) is dropped
    (warned about here, not fetched -- see ticker_resolution.py). Explicit tracked_tickers
    entries -- already fully yfinance-ready -- are never resolved or overridden, and win for
    tickers that appear in both."""
    resolved: dict[str, tuple[str, str]] = {}
    for fund in funds:
        for raw_ticker, exchange in get_latest_tickers(conn, fund.key):
            ticker = holding_symbol(raw_ticker)
            if ticker is None:
                continue
            yf_ticker = resolve_yfinance_ticker(
                ticker, exchange, exchange_suffixes, ticker_overrides
            )
            if yf_ticker is None:
                logger.warning(
                    "No yfinance suffix mapped for exchange %r (ticker %r); skipping from this "
                    "run.",
                    (exchange or "").strip(),
                    ticker,
                )
                continue
            resolved.setdefault(yf_ticker, ("equity", fund.price_source))
    for tt in tracked_tickers:
        resolved[tt.ticker] = (tt.asset_type, tt.price_source)
    return resolved


def _group_by_source_and_type(
    resolved: dict[str, tuple[str, str]],
) -> dict[tuple[str, str], list[str]]:
    groups: dict[tuple[str, str], list[str]] = {}
    for ticker, (asset_type, price_source) in resolved.items():
        groups.setdefault((price_source, asset_type), []).append(ticker)
    return groups


def ingest_macro_indicators(
    conn, *, fred_api_key: str, eu_regions: list[str]
) -> dict[str, int | str]:
    """Lands raw inflation/GDP indicators from FRED (fixed to region 'US') and Eurostat
    (`eu_regions`, from config/macro_regions.yaml) into raw_macro_indicators.

    Each source's fetch and save both live inside one try/except -- a Eurostat outage (or a
    Eurostat *save* failure) shouldn't cost the FRED data already landed, or vice versa, the
    same per-unit failure isolation every other flow here uses (per fund when scraping, per
    ticker when pricing).
    """
    sources = [
        MACRO_INDICATOR_SOURCES["fred"](api_key=fred_api_key),
        MACRO_INDICATOR_SOURCES["eurostat"](),
    ]
    regions_by_source = {"fred": ["US"], "eurostat": eu_regions}

    total_saved = 0
    failed = 0
    for source in sources:
        regions = regions_by_source[source.name]
        if not regions:
            logger.info(
                f"ingest-macro-indicators: no regions configured for {source.name}, skipping."
            )
            continue
        logger.info(f"ingest-macro-indicators: fetching {source.name} ({', '.join(regions)}).")
        # fetch and save both live in this one try/except -- a source saved before an escaping
        # exception is already committed, so letting a save failure propagate would turn a
        # partial success into a wholly failed run and cost the other source's data too. Mirrors
        # ingest_market_data's group-level catch.
        try:
            data = source.fetch(regions)
            if data.empty:
                logger.info(f"ingest-macro-indicators: {source.name} returned no data.")
                continue
            # fetch() doesn't stamp its own rows with the source -- MacroIndicatorSource.name is
            # what the caller (this loop) already knows, mirroring MarketDataSource's fetch()
            # not embedding a source column either.
            saved = save_raw_macro_indicators(conn, data.assign(source=source.name))
        except Exception as e:
            failed += 1
            logger.warning(
                "ingest-macro-indicators: %s failed, skipping: %s", source.name, e, exc_info=True
            )
            continue
        logger.info(f"ingest-macro-indicators: {source.name} saved {saved} row(s).")
        total_saved += saved

    logger.info(
        f"ingest-macro-indicators complete: {total_saved} row(s) across {len(sources)} "
        f"source(s), {failed} source(s) failed."
    )
    return build_run_result(
        units_total=len(sources),
        rows_saved=total_saved,
        failed=failed,
        sources=len(sources),
        sources_failed=failed,
    )


def calculate_macro_indicator_rates(conn) -> dict[str, int | str]:
    """Rebuild macro_indicator_rates from whatever is currently in raw_macro_indicators.

    Always a full rebuild, mirroring calculate_returns: a revised raw value should always
    propagate to the derived rate, and this table is a few hundred rows at most (nowhere near
    market_returns' 8.95M), so there is no volume reason to do anything incremental.
    """
    raw = get_raw_indicators(conn)
    if raw.empty:
        logger.info(
            "calculate-macro-indicator-rates: raw_macro_indicators is empty, nothing to do."
        )
        return build_run_result(units_total=0, rows_saved=0, failed=0)

    rates = compute_indicator_rates(raw)
    saved = save_macro_indicator_rates(conn, rates)
    logger.info(f"calculate-macro-indicator-rates complete: {saved} row(s).")
    return build_run_result(units_total=len(raw), rows_saved=saved, failed=0)
