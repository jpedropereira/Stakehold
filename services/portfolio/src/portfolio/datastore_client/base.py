"""The DataStoreClient Protocol -- portfolio's domain code depends only on this, not on
http_client.py's implementation, so a stub can stand in for tests without a live data-store
instance. See docs/flows.md's cross-service prerequisite section.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class PricePoint:
    date: date
    split_adjusted_close: Decimal


@dataclass(frozen=True)
class FxPoint:
    date: date
    rate: Decimal


@dataclass(frozen=True)
class TickerInfo:
    exists: bool
    has_returns: bool


class DataStoreClient(Protocol):
    def get_fx_rate(self, base: str, quote: str, on_date: date) -> Decimal | None:
        """The exchange rate for on_date, in transactions/base.py's to_eur convention: quote
        units per 1 base unit (so base="EUR" gives exactly the fx_rate a non-EUR
        CanonicalTransaction needs). Falls back to the most recent earlier date with a rate when
        on_date itself has none (weekends/holidays); returns None when no rate is available at
        all -- an untracked currency, or nothing in the lookback window -- rather than raising,
        since "no rate available" is a normal outcome a caller must decide how to handle, not a
        failure of the call itself.
        """
        ...

    def get_price_series(self, ticker: str, start: date, end: date) -> list[PricePoint]:
        """split_adjusted_close for every trading day data-store has for ticker in [start, end],
        ascending by date. An empty list is a legitimate "no data in this range"; the ticker
        itself is assumed to exist -- callers check that with get_ticker_info first.
        """
        ...

    def get_fx_rate_series(self, base: str, quote: str, start: date, end: date) -> list[FxPoint]:
        """Every stored EUR->quote rate point in [start, end], ascending by date -- the range
        counterpart to get_fx_rate, for a caller that needs to look up "the latest rate at or
        before" many dates without one HTTP call per date. An empty list means no rate was ever
        tracked for this pair in the window, same as get_fx_rate's None.
        """
        ...

    def list_tickers(self) -> list[str]:
        """Every ticker data-store has ingested, sorted -- the set manual entry offers to pick
        from."""
        ...

    def get_ticker_info(self, ticker: str) -> TickerInfo:
        """Whether data-store has ever ingested ticker at all, and whether calculate-returns has
        run for it. Never raises for an unknown ticker -- exists=False is itself the answer, not
        an error -- so a caller can block its own rebuild for that ticker loudly rather than
        finding out from a failed price-series call.
        """
        ...
