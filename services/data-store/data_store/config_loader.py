from dataclasses import dataclass
from pathlib import Path

import yaml

_REQUIRED_FIELDS = ("key", "display_name", "provider", "url", "price_source", "enabled")


class ConfigError(Exception):
    """Raised when config/etfs.yaml is missing, malformed, or invalid."""


@dataclass(frozen=True)
class FundConfig:
    key: str
    display_name: str
    provider: str
    url: str
    isin: str | None
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
