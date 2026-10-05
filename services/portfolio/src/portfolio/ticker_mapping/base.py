"""The in-memory lookup every rebuild resolves instruments through.

Loaded from the ticker_mappings table by repository.load_all -- once per run, so a rebuild
resolves every instrument against one consistent set rather than querying per row.
"""

from dataclasses import dataclass

from portfolio.errors import TickerMappingError

# The kinds a mapping can be saved for. TICKER is an identifier kind too, but resolves to itself.
MAPPED_KINDS = ("ISIN", "SYMBOL")


@dataclass(frozen=True)
class TickerMappings:
    isin: dict[str, str]
    symbol: dict[str, dict[str, str]]  # broker -> {raw symbol: canonical_ticker}

    def resolve(self, kind: str, identifier: str, *, broker: str) -> str | None:
        """The canonical_ticker for one reported instrument, or None if unmapped.

        ISIN mappings are global (broker is accepted but ignored); SYMBOL mappings are scoped
        per broker, since a bare symbol is not globally unambiguous. See docs/architecture.md's
        TickerLink key-scoping note. A TICKER identifier already is the canonical ticker -- it
        was checked against data-store when it was entered -- so it resolves to itself.
        """
        if kind == "TICKER":
            return identifier
        if kind == "ISIN":
            return self.isin.get(identifier)
        if kind == "SYMBOL":
            return self.symbol.get(broker, {}).get(identifier)
        raise TickerMappingError(f"unrecognized instrument_identifier_kind: {kind!r}")
