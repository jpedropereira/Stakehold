import logging
import time
from contextlib import contextmanager
from datetime import date

from data_store.config_loader import FundConfig, TickerConfig
from data_store.holdings.parser import fetch_holdings_csv
from data_store.holdings.repository import (
    get_latest_tickers,
    normalize_holdings,
    save_holdings,
)
from data_store.holdings.scraper import get_holdings_csv_url
from data_store.market_data.repository import get_earliest_dates, save_raw_market_data
from data_store.market_data.yfinance_source import YFinanceMarketDataSource
from data_store.run_result import build_run_result

logger = logging.getLogger(__name__)

# Lower bound sent to yfinance when a backfill is asked for "as far back as available".
# yfinance clips to each ticker's actual listing date, so this is effectively unbounded.
UNBOUNDED_EARLIEST_DATE = date(1900, 1, 1)

# Sleep between the full-history re-fetches triggered by a split. Splits cluster (an index-wide
# event can move several holdings on one day), and each refresh is a whole-history request, so
# firing them back to back is exactly the shape that trips yfinance's rate limiting. Matches
# backfill's default delay; not exposed as a parameter because nothing chooses it per run.
SPLIT_REFRESH_DELAY_SECONDS = 10.0

# A new fund provider or price source is a new registry entry here, not a branch in the
# functions below.
SCRAPERS = {
    "ishares": get_holdings_csv_url,
}

MARKET_DATA_SOURCES = {
    "yfinance": YFinanceMarketDataSource,
}


# Per-exchange raw-ticker normalization applied before appending the exchange suffix.
# yfinance's ticker syntax for a market doesn't always match the raw symbol iShares scrapes --
# these are the specific, verified-against-real-yfinance mismatches found so far. Scoped by
# exact exchange name (not applied globally) so an exchange with no known quirk is never
# touched.
def _zero_pad_hk(raw_ticker: str) -> str:
    return raw_ticker.zfill(4)


def _normalize_london(raw_ticker: str) -> str:
    # London raw tickers use "." two ways: a trailing full-stop with no meaning of its own
    # (e.g. "BP.", "NG.") -- stripped, or it doubles the "." already in the ".L" suffix -- and
    # a mid-string class-share separator (e.g. "BT.A") -- which yfinance expects as "-", same
    # as the Nordic/Toronto class-share convention. Stripping the trailing dot first means any
    # dot left over is necessarily the class-share kind.
    return raw_ticker.rstrip(".").replace(".", "-")


def _space_to_hyphen(raw_ticker: str) -> str:
    return raw_ticker.replace(" ", "-")


def _dot_to_hyphen(raw_ticker: str) -> str:
    return raw_ticker.replace(".", "-")


_TICKER_NORMALIZERS = {
    "Hong Kong Exchanges And Clearing Ltd": _zero_pad_hk,
    "London Stock Exchange": _normalize_london,
    "Nasdaq Omx Nordic": _space_to_hyphen,
    "Nasdaq Omx Helsinki Ltd.": _space_to_hyphen,
    "Omx Nordic Exchange Copenhagen A/S": _space_to_hyphen,
    "Oslo Bors Asa": _space_to_hyphen,
    "Toronto Stock Exchange": _dot_to_hyphen,
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
    -- see _resolve_yfinance_ticker. Logs per-group progress and a final summary;
    logging_config decides where that output lands."""
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
    logged (inside the source itself) and simply comes back empty here rather than raising --
    fetch() no longer distinguishes "genuinely no data" from "gave up after retries" at this
    layer, since both now isolate at the ticker level inside the source; either way this ticker
    contributes 0 rows and the run continues. `only_tickers`, when given, restricts the run to
    exactly those final (post-suffix-resolution) tickers -- e.g. "0700.HK", not the raw "0700"
    -- so it can target holdings-derived tickers too, not just tracked_tickers entries.
    `ticker_overrides` is forwarded to ticker resolution -- see _resolve_yfinance_ticker.

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
        )

    total_rows = 0
    processed = skipped = failed = 0
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
        f"({skipped} already covered, {failed} failed), {total_rows} rows written"
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
    (logged, not fetched -- see _resolve_yfinance_ticker). Explicit tracked_tickers entries --
    already fully yfinance-ready -- are never resolved or overridden, and win for tickers that
    appear in both."""
    resolved: dict[str, tuple[str, str]] = {}
    for fund in funds:
        for raw_ticker, exchange in get_latest_tickers(conn, fund.key):
            ticker = (raw_ticker or "").strip()
            # "-" is how the source spells "this line has no symbol" (cash, some derivative
            # legs). It survives the emptiness check, and on a mapped exchange it would be
            # sent as a real request like "-.L".
            if not ticker or ticker == "-":
                continue
            yf_ticker = _resolve_yfinance_ticker(
                ticker, exchange, exchange_suffixes, ticker_overrides
            )
            if yf_ticker is None:
                continue
            resolved.setdefault(yf_ticker, ("equity", fund.price_source))
    for tt in tracked_tickers:
        resolved[tt.ticker] = (tt.asset_type, tt.price_source)
    return resolved


def _resolve_yfinance_ticker(
    ticker: str,
    exchange: str | None,
    exchange_suffixes: dict[str, str | None],
    ticker_overrides: dict[str, str] | None = None,
) -> str | None:
    """`ticker` + the yfinance suffix mapped for `exchange`, with any exchange-specific
    normalization applied first (e.g. zero-padding Hong Kong codes, hyphenating Nordic
    share-class tickers -- see _TICKER_NORMALIZERS). `ticker_overrides`, when it has an entry
    for `ticker`, wins outright and skips exchange resolution entirely -- for one-off cases a
    general rule can't cover: a raw ticker needing a full rewrite, or one whose source exchange
    tagging is simply wrong. Returns None (after logging a warning naming both) when `exchange`
    has no entry in exchange_suffixes and no override applies -- an unresolvable ticker must
    never be sent to yfinance bare, since yfinance won't error on a wrong/bare ticker, it just
    silently returns no data."""
    if ticker_overrides and ticker in ticker_overrides:
        return ticker_overrides[ticker]

    exchange_key = (exchange or "").strip()
    if exchange_key not in exchange_suffixes:
        logger.warning(
            "No yfinance suffix mapped for exchange %r (ticker %r); skipping from this run.",
            exchange_key,
            ticker,
        )
        return None

    normalizer = _TICKER_NORMALIZERS.get(exchange_key)
    if normalizer:
        ticker = normalizer(ticker)

    suffix = exchange_suffixes[exchange_key] or ""
    return f"{ticker}{suffix}"


def _group_by_source_and_type(
    resolved: dict[str, tuple[str, str]],
) -> dict[tuple[str, str], list[str]]:
    groups: dict[tuple[str, str], list[str]] = {}
    for ticker, (asset_type, price_source) in resolved.items():
        groups.setdefault((price_source, asset_type), []).append(ticker)
    return groups
