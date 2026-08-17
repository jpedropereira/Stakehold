import logging
import time
from datetime import date

from data_store.config_loader import FundConfig, TickerConfig
from data_store.holdings.parser import fetch_holdings_csv
from data_store.holdings.repository import (
    get_latest_tickers,
    normalize_holdings,
    save_holdings,
)
from data_store.holdings.scraper import get_holdings_csv_url
from data_store.prices.repository import get_earliest_price_dates, save_prices
from data_store.prices.yfinance_source import YFinancePriceSource

logger = logging.getLogger(__name__)

# A new fund provider or price source is a new registry entry here, not a branch in the
# functions below.
SCRAPERS = {
    "ishares": get_holdings_csv_url,
}

PRICE_SOURCES = {
    "yfinance": YFinancePriceSource,
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
) -> int:
    """Prices every fund's holdings plus every tracked ticker in one pass. Tickers are
    resolved into a single deduped map first (not fetched per-source) so a ticker that's both
    a fund holding and an explicitly tracked ticker — or held by more than one fund — is only
    fetched once, not once per source. `only_tickers`, when given, restricts the run to exactly
    those final (post-suffix-resolution) tickers -- e.g. "0700.HK", not the raw "0700" -- so it
    can target holdings-derived tickers too, not just tracked_tickers entries. `ticker_overrides`
    is forwarded to ticker resolution -- see _resolve_yfinance_ticker."""
    resolved = _resolve_priceable_tickers(
        conn, funds, tracked_tickers, exchange_suffixes, ticker_overrides
    )
    if only_tickers is not None:
        resolved = {t: v for t, v in resolved.items() if t in only_tickers}
    if not resolved:
        return 0

    total = 0
    for (price_source_name, asset_type), tickers in _group_by_source_and_type(resolved).items():
        try:
            price_source_cls = PRICE_SOURCES[price_source_name]
        except KeyError:
            raise ValueError(f"Unknown price source: {price_source_name!r}") from None

        price_source = price_source_cls()
        prices = price_source.fetch(tickers, start_date, end_date)
        if prices.empty:
            continue

        total += save_prices(conn, price_source.name, asset_type, prices)

    return total


def backfill_historical_ohlcv(
    conn,
    funds: list[FundConfig],
    tracked_tickers: list[TickerConfig],
    exchange_suffixes: dict[str, str | None],
    earliest_date: date,
    end_date: date,
    delay_seconds: float,
    only_tickers: set[str] | None = None,
    ticker_overrides: dict[str, str] | None = None,
) -> int:
    """For each priceable ticker (same resolution as ingest_daily_ohlcv), fetches and stores
    only the still-missing stretch of history: earliest_date up to (existing earliest stored
    date - 1 day), or up to end_date if no rows exist yet. One ticker at a time, sleeping
    delay_seconds after each live fetch (success or failure) — never after a skip. A ticker
    that fails after YFinancePriceSource's internal retries is logged and skipped, not fatal
    to the run. Re-running is cheap: a ticker that already succeeded now has a smaller (or
    empty) remaining gap computed fresh from daily_ohlcv itself — no separate checkpoint is
    needed, the table is the checkpoint. `only_tickers`, when given, restricts the run to
    exactly those final (post-suffix-resolution) tickers -- e.g. "0700.HK", not the raw "0700"
    -- so it can target holdings-derived tickers too, not just tracked_tickers entries.
    `ticker_overrides` is forwarded to ticker resolution -- see _resolve_yfinance_ticker."""
    # yfinance logs an ERROR-level "Failed download"/"Data doesn't exist" message on every
    # empty result via its own 'yfinance' logger — which, here, is the expected outcome for
    # every ticker once requested back past its actual listing date. Left alone that noise
    # drowns out our own per-ticker progress lines below, so it's silenced for this run only
    # (not touched for the daily flow, where an empty result is more likely to mean something
    # actually needs attention, e.g. a delisted/mistyped ticker).
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    resolved = _resolve_priceable_tickers(
        conn, funds, tracked_tickers, exchange_suffixes, ticker_overrides
    )
    if only_tickers is not None:
        resolved = {t: v for t, v in resolved.items() if t in only_tickers}
    if not resolved:
        return 0

    total_rows = 0
    processed = skipped = failed = 0
    ticker_count = len(resolved)
    for (price_source_name, asset_type), tickers in _group_by_source_and_type(resolved).items():
        try:
            price_source_cls = PRICE_SOURCES[price_source_name]
        except KeyError:
            raise ValueError(f"Unknown price source: {price_source_name!r}") from None

        price_source = price_source_cls()
        floors = get_earliest_price_dates(conn, price_source_name, tickers)
        for ticker in tickers:
            floor = floors.get(ticker)
            # fetch()'s end is exclusive (confirmed against real yfinance), so passing floor
            # itself as the end already stops the day before it — no -1 day adjustment needed,
            # and one would silently drop the trading day immediately before floor.
            fetch_end = floor if floor else end_date
            if fetch_end <= earliest_date:
                skipped += 1
                processed += 1
                print(f"backfill: {ticker} already covered back to {floor}, skipping", flush=True)
                continue

            try:
                prices = price_source.fetch([ticker], earliest_date, fetch_end)
            except Exception as e:
                failed += 1
                logger.warning("backfill: %s failed after retries, skipping: %s", ticker, e)
                time.sleep(delay_seconds)
                processed += 1
                continue

            if not prices.empty:
                total_rows += save_prices(conn, price_source.name, asset_type, prices)
                print(
                    f"backfill: {ticker} -> {len(prices)} rows ({earliest_date} to {fetch_end})",
                    flush=True,
                )
            else:
                print(f"backfill: {ticker} -> no data ({earliest_date} to {fetch_end})", flush=True)
            time.sleep(delay_seconds)
            processed += 1
            if processed % 25 == 0:
                print(
                    f"backfill progress: {processed}/{ticker_count} tickers, "
                    f"{total_rows} rows so far",
                    flush=True,
                )

    print(
        f"backfill complete: {ticker_count} tickers "
        f"({skipped} already covered, {failed} failed), {total_rows} rows written",
        flush=True,
    )
    return total_rows


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
            if not ticker:
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
