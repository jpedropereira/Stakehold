"""The TickerLookupSource Protocol: asking a provider whether a symbol exists before a user can
add it, and what it is."""

from dataclasses import dataclass
from typing import Protocol

# The provider has prices for the symbol, and it's something a portfolio can hold.
OUTCOME_FOUND = "found"
# The provider has the symbol, but it's not something a portfolio can hold (an index, a
# currency pair). Kept apart from not-found so the user is told *why* it can't be added.
OUTCOME_UNSUPPORTED = "unsupported"
# The provider doesn't know the symbol, or has no prices for it (typically a delisting).
OUTCOME_NOT_FOUND = "not_found"
# The provider didn't answer: throttled, timed out, or unreachable. Says nothing about the
# symbol, so it must never be reported as "doesn't exist".
OUTCOME_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class LookupResult:
    outcome: str
    # The provider's canonical spelling when found or unsupported; otherwise what was asked.
    ticker: str
    display_name: str | None
    currency: str | None
    exchange: str | None
    # One of config_loader.VALID_ASSET_TYPES when found; None otherwise.
    asset_type: str | None


class TickerLookupSource(Protocol):
    name: str

    def lookup(self, ticker: str) -> LookupResult:
        """Classify one symbol. Raises only for a programming error -- every provider answer,
        including "didn't answer", is an outcome."""
        ...
