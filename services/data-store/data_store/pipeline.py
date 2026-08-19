import logging
import time
from contextlib import contextmanager
from datetime import date

from data_store.config_loader import FundConfig, TickerConfig
from data_store.dividends.repository import get_earliest_dividend_dates, save_dividends
from data_store.dividends.yfinance_source import YFinanceDividendSource
from data_store.holdings.parser import fetch_holdings_csv
from data_store.holdings.repository import (
    get_latest_tickers,
    normalize_holdings,
    save_holdings,
)
from data_store.holdings.scraper import get_holdings_csv_url
from data_store.prices.repository import get_earliest_price_dates, save_prices
from data_store.prices.yfinance_source import YFinancePriceSource
from data_store.run_result import build_run_result

logger = logging.getLogger(__name__)

# Lower bound sent to yfinance when a backfill is asked for "as far back as available".
# yfinance clips to each ticker's actual listing date, so this is effectively unbounded.
UNBOUNDED_EARLIEST_DATE = date(1900, 1, 1)

# A new fund provider or price source is a new registry entry here, not a branch in the
# functions below.
SCRAPERS = {
    "ishares": get_holdings_csv_url,
}

PRICE_SOURCES = {
    "yfinance": YFinancePriceSource,
}

# Kept separate from PRICE_SOURCES even though yfinance is (so far) the only entry in both:
# a dividend source and a price source are different Protocols (see prices/base.py,
# dividends/base.py) with different fetch() shapes, so collapsing the two registries would mean
# picking a class out of PRICE_SOURCES and hoping it also happens to expose a dividend-fetch
# surface -- exactly the coupling this keeps out.
DIVIDEND_SOURCES = {
    "yfinance": YFinanceDividendSource,
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


def ingest_daily_ohlcv(
    conn,
    funds: list[FundConfig],
    tracked_tickers: list[TickerConfig],
    exchange_suffixes: dict[str, str | None],
    start_date: date,
    end_date: date,
    only_tickers: set[str] | None = None,
    ticker_overrides: dict[str, str] | None = None,
) -> dict[str, int | str]:
    """Prices every fund's holdings plus every tracked ticker in one pass. Tickers are
    resolved into a single deduped map first (not fetched per-source) so a ticker that's both
    a fund holding and an explicitly tracked ticker — or held by more than one fund — is only
    fetched once, not once per source. `only_tickers`, when given, restricts the run to exactly
    those final (post-suffix-resolution) tickers -- e.g. "0700.HK", not the raw "0700" -- so it
    can target holdings-derived tickers too, not just tracked_tickers entries. `ticker_overrides`
    is forwarded to ticker resolution -- see _resolve_yfinance_ticker. Prints per-group progress
    and a final summary; logging_config decides where that output lands."""
    resolved = _resolve_priceable_tickers(
        conn, funds, tracked_tickers, exchange_suffixes, ticker_overrides
    )
    resolved, unmatched = _apply_only_tickers(resolved, only_tickers, "ingest-ohlcv")
    if not resolved:
        logger.info("ingest-ohlcv: no tickers to price, nothing to do.")
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
        f"ingest-ohlcv: resolved {len(resolved)} ticker(s) across {len(groups)} "
        "(source, asset_type) group(s)."
    )

    total = 0
    failed = 0
    for (price_source_name, asset_type), tickers in groups.items():
        try:
            price_source_cls = PRICE_SOURCES[price_source_name]
        except KeyError:
            # A misconfigured source is a config error, not a runtime hiccup: it will fail
            # identically on every rerun, so it stays fatal rather than being retried nightly.
            raise ValueError(f"Unknown price source: {price_source_name!r}") from None

        price_source = price_source_cls()
        logger.info(
            f"ingest-ohlcv: fetching prices for {len(tickers)} ticker(s) "
            f"(source={price_source_name}, asset_type={asset_type})."
        )
        # One group's failure must not cost the groups after it. This runs unattended on a
        # schedule, and groups saved before an escaping exception are already committed -- so
        # letting it propagate turns a partial success into a wholly failed, partially written
        # run. Mirrors the per-ticker isolation the backfills already have.
        try:
            prices = price_source.fetch(tickers, start_date, end_date)
            if prices.empty:
                logger.info(
                    f"ingest-ohlcv: no price data for this group "
                    f"(source={price_source_name}, asset_type={asset_type})."
                )
                continue

            saved = save_prices(conn, price_source.name, asset_type, prices)
        except Exception as e:
            failed += 1
            logger.warning(
                "ingest-ohlcv: group (source=%s, asset_type=%s, %d ticker(s)) failed, "
                "skipping: %s",
                price_source_name,
                asset_type,
                len(tickers),
                e,
            )
            continue

        logger.info(
            f"ingest-ohlcv: saved {saved} price row(s) "
            f"(source={price_source_name}, asset_type={asset_type})."
        )
        total += saved

    logger.info(
        f"ingest-ohlcv complete: {total} row(s) across {len(resolved)} ticker(s), "
        f"{failed} group(s) failed."
    )
    return build_run_result(
        units_total=len(resolved),
        tickers_resolved=len(resolved),
        tickers_unmatched=unmatched,
        rows_saved=total,
        failed=failed + unmatched,
        groups=len(groups),
        groups_failed=failed,
    )


def ingest_dividend_events(
    conn,
    funds: list[FundConfig],
    tracked_tickers: list[TickerConfig],
    exchange_suffixes: dict[str, str | None],
    start_date: date,
    end_date: date,
    only_tickers: set[str] | None = None,
    ticker_overrides: dict[str, str] | None = None,
) -> dict[str, int | str]:
    """Direct mirror of ingest_daily_ohlcv: same ticker-universe resolution
    (_resolve_priceable_tickers, _group_by_source_and_type), swapping DIVIDEND_SOURCES for
    PRICE_SOURCES and save_dividends for save_prices. `only_tickers`/`ticker_overrides` behave
    identically to ingest_daily_ohlcv's -- see that docstring."""
    resolved = _resolve_priceable_tickers(
        conn, funds, tracked_tickers, exchange_suffixes, ticker_overrides
    )
    resolved, unmatched = _apply_only_tickers(resolved, only_tickers, "ingest-dividends")
    if not resolved:
        logger.info("ingest-dividends: no tickers to price, nothing to do.")
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
        f"ingest-dividends: resolved {len(resolved)} ticker(s) across {len(groups)} "
        "(source, asset_type) group(s)."
    )

    total = 0
    failed = 0
    for (source_name, asset_type), tickers in groups.items():
        try:
            dividend_source_cls = DIVIDEND_SOURCES[source_name]
        except KeyError:
            raise ValueError(f"Unknown dividend source: {source_name!r}") from None

        dividend_source = dividend_source_cls()
        logger.info(
            f"ingest-dividends: fetching dividends for {len(tickers)} ticker(s) "
            f"(source={source_name}, asset_type={asset_type})."
        )
        # Same per-group isolation as ingest_daily_ohlcv -- see the comment there.
        try:
            dividends = dividend_source.fetch(tickers, start_date, end_date)
            if dividends.empty:
                logger.info(
                    f"ingest-dividends: no dividend data for this group "
                    f"(source={source_name}, asset_type={asset_type}) -- either none of these "
                    f"{len(tickers)} ticker(s) paid a dividend in this window, or the fetch "
                    "returned nothing."
                )
                continue

            saved = save_dividends(conn, dividend_source.name, asset_type, dividends)
        except Exception as e:
            failed += 1
            logger.warning(
                "ingest-dividends: group (source=%s, asset_type=%s, %d ticker(s)) failed, "
                "skipping: %s",
                source_name,
                asset_type,
                len(tickers),
                e,
            )
            continue

        logger.info(
            f"ingest-dividends: saved {saved} dividend row(s) "
            f"(source={source_name}, asset_type={asset_type})."
        )
        total += saved

    logger.info(
        f"ingest-dividends complete: {total} row(s) across {len(resolved)} ticker(s), "
        f"{failed} group(s) failed."
    )
    return build_run_result(
        units_total=len(resolved),
        tickers_resolved=len(resolved),
        tickers_unmatched=unmatched,
        rows_saved=total,
        failed=failed + unmatched,
        groups=len(groups),
        groups_failed=failed,
    )


@_yfinance_logging_silenced()
def backfill_historical_ohlcv(
    conn,
    funds: list[FundConfig],
    tracked_tickers: list[TickerConfig],
    exchange_suffixes: dict[str, str | None],
    earliest_date: date | None,
    end_date: date,
    delay_seconds: float,
    only_tickers: set[str] | None = None,
    ticker_overrides: dict[str, str] | None = None,
) -> dict[str, int | str]:
    """For each priceable ticker (same resolution as ingest_daily_ohlcv), fetches and stores
    the stretch of history it's still missing — see _backfill_window for how `earliest_date`
    selects between resuming (omitted) and reaching further back than a previous run
    (given). Either way daily_ohlcv is its own checkpoint; no separate state table is needed.

    One ticker at a time, sleeping delay_seconds after each live fetch (success or failure) —
    never after a skip. A ticker that fails after YFinancePriceSource's internal retries is
    logged and skipped, not fatal to the run. `only_tickers`, when given, restricts the run to
    exactly those final (post-suffix-resolution) tickers -- e.g. "0700.HK", not the raw "0700"
    -- so it can target holdings-derived tickers too, not just tracked_tickers entries.
    `ticker_overrides` is forwarded to ticker resolution -- see _resolve_yfinance_ticker.

    Decorated rather than the daily flow because an empty result means something different
    here: during a backfill it's the expected end state for every ticker, whereas for
    ingest_daily_ohlcv it more likely means a delisted or mistyped ticker worth seeing.
    """
    resolved = _resolve_priceable_tickers(
        conn, funds, tracked_tickers, exchange_suffixes, ticker_overrides
    )
    resolved, unmatched = _apply_only_tickers(resolved, only_tickers, "backfill")
    if not resolved:
        logger.info("backfill: no tickers to fetch, nothing to do.")
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
    for (price_source_name, asset_type), tickers in _group_by_source_and_type(resolved).items():
        try:
            price_source_cls = PRICE_SOURCES[price_source_name]
        except KeyError:
            raise ValueError(f"Unknown price source: {price_source_name!r}") from None

        price_source = price_source_cls()
        # price_source.name, not the registry key: rows are written under .name, so floors
        # must be read under it too or a source whose key differs never finds its own data.
        floors = get_earliest_price_dates(conn, price_source.name, tickers)
        for ticker in tickers:
            floor = floors.get(ticker)
            window = _backfill_window(floor, earliest_date, end_date)
            if window is None:
                skipped += 1
                processed += 1
                logger.info(_already_covered_message("backfill", ticker, floor))
                continue
            fetch_start, fetch_end = window

            try:
                prices = price_source.fetch([ticker], fetch_start, fetch_end)
            except Exception as e:
                failed += 1
                logger.warning("backfill: %s failed after retries, skipping: %s", ticker, e)
                time.sleep(delay_seconds)
                processed += 1
                continue

            if not prices.empty:
                total_rows += save_prices(conn, price_source.name, asset_type, prices)
                logger.info(
                    f"backfill: {ticker} -> {len(prices)} rows ({fetch_start} to {fetch_end})"
                )
            else:
                logger.info(f"backfill: {ticker} -> no data ({fetch_start} to {fetch_end})")
            time.sleep(delay_seconds)
            processed += 1
            if processed % 25 == 0:
                logger.info(
                    f"backfill progress: {processed}/{ticker_count} tickers, "
                    f"{total_rows} rows so far"
                )

    logger.info(
        f"backfill complete: {ticker_count} tickers "
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


@_yfinance_logging_silenced()
def backfill_dividend_events(
    conn,
    funds: list[FundConfig],
    tracked_tickers: list[TickerConfig],
    exchange_suffixes: dict[str, str | None],
    earliest_date: date | None,
    end_date: date,
    delay_seconds: float,
    only_tickers: set[str] | None = None,
    ticker_overrides: dict[str, str] | None = None,
) -> dict[str, int | str]:
    """Direct structural mirror of backfill_historical_ohlcv: per-ticker window computed from
    get_earliest_dividend_dates instead of get_earliest_price_dates, same optional
    `earliest_date` resume/deep-fill modes (see _backfill_window), same
    silence-yfinance-logger / sleep-after-fetch-or-failure-not-skip / progress-every-25 /
    never-fatal-on-one-ticker-failure behavior. `only_tickers`/`ticker_overrides` behave
    identically to backfill_historical_ohlcv's -- see that docstring."""
    resolved = _resolve_priceable_tickers(
        conn, funds, tracked_tickers, exchange_suffixes, ticker_overrides
    )
    resolved, unmatched = _apply_only_tickers(resolved, only_tickers, "backfill-dividends")
    if not resolved:
        logger.info("backfill-dividends: no tickers to fetch, nothing to do.")
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
            dividend_source_cls = DIVIDEND_SOURCES[source_name]
        except KeyError:
            raise ValueError(f"Unknown dividend source: {source_name!r}") from None

        dividend_source = dividend_source_cls()
        # See the note in backfill_historical_ohlcv: read under the same identifier the
        # rows are written under.
        floors = get_earliest_dividend_dates(conn, dividend_source.name, tickers)
        for ticker in tickers:
            floor = floors.get(ticker)
            window = _backfill_window(floor, earliest_date, end_date)
            if window is None:
                skipped += 1
                processed += 1
                logger.info(_already_covered_message("backfill-dividends", ticker, floor))
                continue
            fetch_start, fetch_end = window

            try:
                dividends = dividend_source.fetch([ticker], fetch_start, fetch_end)
            except Exception as e:
                failed += 1
                logger.warning(
                    "backfill-dividends: %s failed after retries, skipping: %s", ticker, e
                )
                time.sleep(delay_seconds)
                processed += 1
                continue

            if not dividends.empty:
                total_rows += save_dividends(conn, dividend_source.name, asset_type, dividends)
                logger.info(
                    f"backfill-dividends: {ticker} -> {len(dividends)} rows "
                    f"({fetch_start} to {fetch_end})"
                )
            else:
                logger.info(
                    f"backfill-dividends: {ticker} -> no data ({fetch_start} to {fetch_end})"
                )
            time.sleep(delay_seconds)
            processed += 1
            if processed % 25 == 0:
                logger.info(
                    f"backfill-dividends progress: {processed}/{ticker_count} tickers, "
                    f"{total_rows} rows so far"
                )

    logger.info(
        f"backfill-dividends complete: {ticker_count} tickers "
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
    floor: date | None, earliest_date: date | None, end_date: date
) -> tuple[date, date] | None:
    """The (start, end) one ticker still needs fetched, or None when there's nothing left.

    Two modes, because "how far back have we already looked?" is not something the data itself
    records:

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
