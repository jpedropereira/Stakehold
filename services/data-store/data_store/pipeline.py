from datetime import date

from data_store.config_loader import FundConfig, TickerConfig
from data_store.holdings.parser import fetch_holdings_csv
from data_store.holdings.repository import (
    get_latest_tickers,
    normalize_holdings,
    save_holdings,
)
from data_store.holdings.scraper import get_holdings_csv_url
from data_store.prices.repository import save_prices
from data_store.prices.yfinance_source import YFinancePriceSource

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
