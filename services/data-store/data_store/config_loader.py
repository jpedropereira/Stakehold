from dataclasses import dataclass
from pathlib import Path

import yaml

_REQUIRED_FIELDS = ("key", "display_name", "provider", "url", "price_source", "enabled")
_TICKER_REQUIRED_FIELDS = ("ticker", "display_name", "asset_type", "price_source", "enabled")
_EXCHANGE_SUFFIX_REQUIRED_FIELDS = ("exchange", "suffix")
_TICKER_OVERRIDE_REQUIRED_FIELDS = ("raw_ticker", "ticker")
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
    """Directly-priced tickers, or [] when none are configured.

    Unlike `load_funds`, an empty or absent `tickers:` section is not an error: this whole file
    is optional (a setup pricing only fund holdings never needs it), so "copied the template
    but haven't filled it in" must behave the same as "never created it" rather than breaking
    every ingest and backfill command. Same optional-section handling as load_ticker_overrides.
    """
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    with path.open() as f:
        data = yaml.safe_load(f) or {}

    entries = data.get("tickers") or []

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


def load_exchange_suffixes(path: Path) -> dict[str, str | None]:
    """{raw exchange string: yfinance suffix, or None if that exchange needs no suffix}.
    Unlike load_funds/load_tracked_tickers this returns a dict, not a list of dataclasses --
    it's fundamentally a lookup table (no per-entry identity/enabled flag), and dict-key
    presence is what lets "unmapped" and "mapped with no suffix" stay distinguishable to
    callers."""
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    with path.open() as f:
        data = yaml.safe_load(f) or {}

    entries = data.get("exchanges") or []
    if not entries:
        raise ConfigError(f"Config file has no exchanges defined: {path}")

    suffixes: dict[str, str | None] = {}
    for entry in entries:
        missing = [field for field in _EXCHANGE_SUFFIX_REQUIRED_FIELDS if field not in entry]
        if missing:
            raise ConfigError(f"Exchange entry missing required field(s): {', '.join(missing)}")

        exchange = entry["exchange"]
        if exchange in suffixes:
            raise ConfigError(f"Duplicate exchange: {exchange}")

        suffix = entry["suffix"]
        if suffix is not None and (not isinstance(suffix, str) or not suffix.startswith(".")):
            raise ConfigError(
                f"Invalid suffix {suffix!r} for exchange {exchange!r}; "
                "must be null or a string starting with '.'"
            )

        suffixes[exchange] = suffix

    return suffixes


def load_ticker_overrides(path: Path) -> dict[str, str]:
    """{raw ticker: final yfinance-ready ticker}, read from the same file as
    load_exchange_suffixes (a different top-level key). For one-off cases plain
    exchange-suffix resolution can't handle: a raw ticker that needs a full rewrite (e.g.
    "BRKB" -> "BRK-B", a share-class ticker the source didn't separate), or one whose source
    exchange tagging is simply wrong (e.g. "HEIA" tagged under a US exchange but actually
    needing "HEIA.AS"). Unlike `exchanges:`, `ticker_overrides:` is optional -- an absent or
    empty section just means none are configured yet, not a config error."""
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    with path.open() as f:
        data = yaml.safe_load(f) or {}

    entries = data.get("ticker_overrides") or []

    overrides: dict[str, str] = {}
    for entry in entries:
        missing = [field for field in _TICKER_OVERRIDE_REQUIRED_FIELDS if field not in entry]
        if missing:
            raise ConfigError(
                f"Ticker override entry missing required field(s): {', '.join(missing)}"
            )

        raw_ticker = entry["raw_ticker"]
        if raw_ticker in overrides:
            raise ConfigError(f"Duplicate ticker override: {raw_ticker}")

        overrides[raw_ticker] = entry["ticker"]

    return overrides
