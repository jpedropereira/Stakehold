import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_REQUIRED_FIELDS = ("key", "display_name", "provider", "url", "price_source", "enabled")
_TICKER_REQUIRED_FIELDS = ("ticker", "display_name", "asset_type", "price_source", "enabled")
_EXCHANGE_SUFFIX_REQUIRED_FIELDS = ("exchange", "suffix")
_TICKER_OVERRIDE_REQUIRED_FIELDS = ("raw_ticker", "ticker")
_CURRENCY_REQUIRED_FIELDS = ("code", "enabled")
VALID_ASSET_TYPES = {"equity", "etf", "commodity", "crypto", "etc"}
_CURRENCY_CODE_RE = re.compile(r"^[A-Z]{3}$")


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
class TickerResolution:
    """Everything needed to turn a raw scraped ticker into a provider-ready one.

    The two live in the same file under different top-level keys and are always used together
    -- an override short-circuits suffix resolution, so a caller holding one without the other
    would resolve tickers wrongly. Bundling them makes that pairing structural rather than a
    convention every call site has to remember.
    """

    exchange_suffixes: dict[str, str | None]
    ticker_overrides: dict[str, str]


@dataclass(frozen=True)
class TickerConfig:
    ticker: str
    display_name: str
    asset_type: str
    price_source: str
    enabled: bool


@dataclass(frozen=True)
class CurrencyConfig:
    code: str
    enabled: bool


@dataclass(frozen=True)
class CurrenciesConfig:
    base_currency: str
    currencies: list[CurrencyConfig]


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


def load_currencies(path: Path) -> CurrenciesConfig:
    """The supported-currency list plus which one every other currency is tracked against.

    Unlike `load_tracked_tickers`, this file is required, not optional: there's no sensible
    "not configured yet" state for the currency list an FX ingest needs to run at all, the same
    reasoning that makes `load_funds` required rather than optional.
    """
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    with path.open() as f:
        data = yaml.safe_load(f) or {}

    if "base_currency" not in data:
        raise ConfigError(f"Config file has no base_currency defined: {path}")
    base_currency = data["base_currency"]

    entries = data.get("currencies") or []
    if not entries:
        raise ConfigError(f"Config file has no currencies defined: {path}")

    currencies = []
    seen_codes = set()
    for entry in entries:
        missing = [field for field in _CURRENCY_REQUIRED_FIELDS if field not in entry]
        if missing:
            raise ConfigError(f"Currency entry missing required field(s): {', '.join(missing)}")

        code = entry["code"]
        if not _CURRENCY_CODE_RE.match(code):
            raise ConfigError(f"Invalid currency code {code!r}; must be 3 uppercase letters")
        if code in seen_codes:
            raise ConfigError(f"Duplicate currency code: {code}")
        seen_codes.add(code)

        currencies.append(CurrencyConfig(code=code, enabled=entry["enabled"]))

    base_entry = next((c for c in currencies if c.code == base_currency), None)
    if base_entry is None:
        raise ConfigError(f"base_currency {base_currency!r} is not one of the configured codes")
    if not base_entry.enabled:
        raise ConfigError(f"base_currency {base_currency!r} is not enabled")

    return CurrenciesConfig(base_currency=base_currency, currencies=currencies)


def load_ticker_resolution(path: Path) -> TickerResolution:
    """Both ticker-resolution tables, from a single parse of a single file.

    One read rather than two: the tables are always used together, so parsing once means they
    cannot be read from different states of the file, and a command stops paying to parse the
    same YAML twice.
    """
    data = _read_yaml_mapping(path)
    return TickerResolution(
        exchange_suffixes=_parse_exchange_suffixes(data, path),
        ticker_overrides=_parse_ticker_overrides(data),
    )


def _read_yaml_mapping(path: Path) -> dict:
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    with path.open() as f:
        return yaml.safe_load(f) or {}


def _parse_exchange_suffixes(data: dict, path: Path) -> dict[str, str | None]:
    """{raw exchange string: yfinance suffix, or None if that exchange needs no suffix}.
    A dict rather than a list of dataclasses because it's fundamentally a lookup table (no
    per-entry identity or enabled flag), and dict-key presence is what lets "unmapped" and
    "mapped with no suffix" stay distinguishable to callers."""
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


def _parse_ticker_overrides(data: dict) -> dict[str, str]:
    """{raw ticker: final yfinance-ready ticker}, from the same file's `ticker_overrides:`
    section. For one-off cases plain
    exchange-suffix resolution can't handle: a raw ticker that needs a full rewrite (e.g.
    "BRKB" -> "BRK-B", a share-class ticker the source didn't separate), or one whose source
    exchange tagging is simply wrong (e.g. "HEIA" tagged under a US exchange but actually
    needing "HEIA.AS"). Unlike `exchanges:`, `ticker_overrides:` is optional -- an absent or
    empty section just means none are configured yet, not a config error."""
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
