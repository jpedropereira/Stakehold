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
    start_date: date,
    end_date: date,
) -> int:
    """Prices every fund's holdings plus every tracked ticker in one pass. Tickers are
    resolved into a single deduped map first (not fetched per-source) so a ticker that's both
    a fund holding and an explicitly tracked ticker — or held by more than one fund — is only
    fetched once, not once per source."""
    resolved = _resolve_priceable_tickers(conn, funds, tracked_tickers)
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
    earliest_date: date,
    end_date: date,
    delay_seconds: float,
) -> int:
    """For each priceable ticker (same resolution as ingest_daily_ohlcv), fetches and stores
    only the still-missing stretch of history: earliest_date up to (existing earliest stored
    date - 1 day), or up to end_date if no rows exist yet. One ticker at a time, sleeping
    delay_seconds after each live fetch (success or failure) — never after a skip. A ticker
    that fails after YFinancePriceSource's internal retries is logged and skipped, not fatal
    to the run. Re-running is cheap: a ticker that already succeeded now has a smaller (or
    empty) remaining gap computed fresh from daily_ohlcv itself — no separate checkpoint is
    needed, the table is the checkpoint."""
    resolved = _resolve_priceable_tickers(conn, funds, tracked_tickers)
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
    conn, funds: list[FundConfig], tracked_tickers: list[TickerConfig]
) -> dict[str, tuple[str, str]]:
    """{ticker: (asset_type, price_source)}. Holdings-derived tickers default to
    ("equity", fund.price_source); explicit tracked_tickers entries win for tickers that
    appear in both."""
    resolved: dict[str, tuple[str, str]] = {}
    for fund in funds:
        for ticker in _clean_tickers(get_latest_tickers(conn, fund.key)):
            resolved.setdefault(ticker, ("equity", fund.price_source))
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


def _clean_tickers(tickers: list[str]) -> list[str]:
    seen = set()
    cleaned = []
    for ticker in tickers:
        ticker = (ticker or "").strip()
        if ticker and ticker not in seen:
            seen.add(ticker)
            cleaned.append(ticker)
    return cleaned
