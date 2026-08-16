from dataclasses import dataclass
from pathlib import Path

import yaml

_REQUIRED_FIELDS = ("key", "display_name", "provider", "url", "price_source", "enabled")
_TICKER_REQUIRED_FIELDS = ("ticker", "display_name", "asset_type", "price_source", "enabled")
VALID_ASSET_TYPES = {"equity", "etf", "commodity", "crypto", "etc"}


class ConfigError(Exception):
    """Raised when a config file is missing, malformed, or invalid."""


@dataclass(frozen=True)
class FundConfig:
    key: str
    display_name: str
    provider: str
    url: str
    isin: str | None
    price_source: str
    enabled: bool


@dataclass(frozen=True)
class TickerConfig:
    ticker: str
    display_name: str
    asset_type: str
    price_source: str
    enabled: bool


def load_funds(path: Path) -> list[FundConfig]:
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    with path.open() as f:
        data = yaml.safe_load(f) or {}

    entries = data.get("funds") or []
    if not entries:
        raise ConfigError(f"Config file has no funds defined: {path}")

    funds = []
    seen_keys = set()
    for entry in entries:
        missing = [field for field in _REQUIRED_FIELDS if field not in entry]
        if missing:
            raise ConfigError(f"Fund entry missing required field(s): {', '.join(missing)}")

        key = entry["key"]
        if key in seen_keys:
            raise ConfigError(f"Duplicate fund key: {key}")
        seen_keys.add(key)

        funds.append(
            FundConfig(
                key=key,
                display_name=entry["display_name"],
                provider=entry["provider"],
                url=entry["url"],
                isin=entry.get("isin"),
                price_source=entry["price_source"],
                enabled=entry["enabled"],
            )
        )

    return funds


def load_tracked_tickers(path: Path) -> list[TickerConfig]:
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    with path.open() as f:
        data = yaml.safe_load(f) or {}

    entries = data.get("tickers") or []
    if not entries:
        raise ConfigError(f"Config file has no tickers defined: {path}")

    tickers = []
    seen_tickers = set()
    for entry in entries:
        missing = [field for field in _TICKER_REQUIRED_FIELDS if field not in entry]
        if missing:
            raise ConfigError(f"Ticker entry missing required field(s): {', '.join(missing)}")

        ticker = entry["ticker"]
        if ticker in seen_tickers:
            raise ConfigError(f"Duplicate ticker: {ticker}")
        seen_tickers.add(ticker)

        asset_type = entry["asset_type"]
        if asset_type not in VALID_ASSET_TYPES:
            raise ConfigError(
                f"Invalid asset_type {asset_type!r} for ticker {ticker!r}; "
                f"must be one of {sorted(VALID_ASSET_TYPES)}"
            )

        tickers.append(
            TickerConfig(
                ticker=ticker,
                display_name=entry["display_name"],
                asset_type=asset_type,
                price_source=entry["price_source"],
                enabled=entry["enabled"],
            )
        )

    return tickers
