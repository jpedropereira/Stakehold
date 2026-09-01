"""Reads and validates configs/ticker_mappings.yaml into an in-memory lookup.

No repository.py: the mapping lives in a YAML file, not a database table -- see
docs/architecture.md's cross-cutting decision on ticker-mapping storage. Nothing here talks to
Postgres.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml
from portfolio.errors import TickerMappingError

_KNOWN_SECTIONS = ("isin", "symbol")


@dataclass(frozen=True)
class TickerMappings:
    isin: dict[str, str]
    symbol: dict[str, dict[str, str]]  # broker -> {raw symbol: canonical_ticker}

    def resolve(self, kind: str, identifier: str, *, broker: str) -> str | None:
        """The canonical_ticker for one reported instrument, or None if unmapped.

        ISIN mappings are global (broker is accepted but ignored); SYMBOL mappings are scoped
        per broker, since a bare symbol is not globally unambiguous. See docs/architecture.md's
        TickerLink key-scoping note.
        """
        if kind == "ISIN":
            return self.isin.get(identifier)
        if kind == "SYMBOL":
            return self.symbol.get(broker, {}).get(identifier)
        raise ValueError(f"unrecognized instrument_identifier_kind: {kind!r}")


def load_ticker_mappings(path: Path) -> TickerMappings:
    if not path.is_file():
        raise TickerMappingError(f"Ticker mappings file not found: {path}")

    with path.open() as f:
        data = yaml.safe_load(f) or {}

    unknown = set(data) - set(_KNOWN_SECTIONS)
    if unknown:
        raise TickerMappingError(f"Unknown top-level section(s): {', '.join(sorted(unknown))}")

    return TickerMappings(
        isin=_parse_isin(data.get("isin") or {}),
        symbol=_parse_symbol(data.get("symbol") or {}),
    )


def _require_non_empty_string(value, *, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise TickerMappingError(f"{context} must be a non-empty string: {value!r}")
    return value


def _parse_isin(entries: dict) -> dict[str, str]:
    isin = {}
    for identifier, ticker in entries.items():
        isin[identifier] = _require_non_empty_string(
            ticker, context=f"isin mapping for {identifier!r}"
        )
    return isin


def _parse_symbol(entries: dict) -> dict[str, dict[str, str]]:
    symbol: dict[str, dict[str, str]] = {}
    for broker, broker_entries in entries.items():
        if not isinstance(broker_entries, dict):
            raise TickerMappingError(
                f"symbol mappings for broker {broker!r} must be nested under that broker key "
                "(symbol: <broker>: <raw symbol>: <ticker>)"
            )
        symbol[broker] = {
            raw_symbol: _require_non_empty_string(
                ticker, context=f"symbol mapping for {broker!r}/{raw_symbol!r}"
            )
            for raw_symbol, ticker in broker_entries.items()
        }
    return symbol
