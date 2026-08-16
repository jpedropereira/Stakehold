from datetime import date

from data_store.config_loader import FundConfig
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


def ingest_daily_ohlcv(conn, fund_config: FundConfig, start_date: date, end_date: date) -> int:
    tickers = _clean_tickers(get_latest_tickers(conn, fund_config.key))
    if not tickers:
        return 0

    try:
        price_source_cls = PRICE_SOURCES[fund_config.price_source]
    except KeyError:
        raise ValueError(f"Unknown price source: {fund_config.price_source!r}") from None

    price_source = price_source_cls()
    prices = price_source.fetch(tickers, start_date, end_date)
    if prices.empty:
        return 0

    # Tickers here always come from etf_holdings, i.e. equity constituents of a fund — not yet
    # wired up for commodity/crypto tickers, which would need their own config/ingestion path.
    return save_prices(conn, price_source.name, "equity", prices)


def _clean_tickers(tickers: list[str]) -> list[str]:
    seen = set()
    cleaned = []
    for ticker in tickers:
        ticker = (ticker or "").strip()
        if ticker and ticker not in seen:
            seen.add(ticker)
            cleaned.append(ticker)
    return cleaned
