"""The DataStoreClient Protocol -- portfolio's domain code depends only on this, not on
http_client.py's implementation, so a stub can stand in for tests without a live data-store
instance. See docs/architecture.md's cross-service prerequisite section.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol
from uuid import UUID


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
    # data-store's onboarding status when the ticker is tracked ("pending", "backfilling",
    # "ready", "failed"); None when it's priced only through a fund's holdings, or not at all.
    status: str | None = None


@dataclass(frozen=True)
class TickerLookup:
    """What adding a ticker would do -- see data-store's GET /tickers/{ticker}/lookup."""

    ticker: str
    # "tracked" (see status), "available", "unsupported", "not_found" or "unavailable".
    outcome: str
    status: str | None
    display_name: str | None
    currency: str | None
    exchange: str | None
    asset_type: str | None


@dataclass(frozen=True)
class TrackedTicker:
    """One row of data-store's tracked_tickers, as its API reports it. Timestamps stay ISO
    strings: portfolio only passes them on, it never computes with them."""

    ticker: str
    display_name: str
    asset_type: str
    currency: str | None
    exchange: str | None
    origin: str
    status: str
    enabled: bool
    attempts: int
    last_error: str | None
    requested_by: str | None
    requested_at: str
    ready_at: str | None


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

    def lookup_ticker(self, ticker: str) -> TickerLookup:
        """What adding `ticker` would do. Raises TickerRequestRefused for a symbol that can't
        be one."""
        ...

    def add_tracked_ticker(
        self, ticker: str, *, requested_by: UUID, enforce_limit: bool
    ) -> tuple[TrackedTicker, bool]:
        """Add a ticker for data-store to onboard. Returns the row and whether this call added
        it. Raises TickerRequestRefused when data-store refuses (not found, can't be held, over
        the limit, provider down)."""
        ...

    def list_tracked_tickers(
        self,
        *,
        requested_by: UUID | None = None,
        statuses: list[str] | None = None,
        tickers: list[str] | None = None,
    ) -> list[TrackedTicker]: ...

    def update_tracked_ticker(
        self, ticker: str, *, enabled: bool | None, retry: bool
    ) -> TrackedTicker:
        """An admin's change. Raises TickerRequestRefused for an unknown ticker (404) or a change
        data-store won't make (409)."""
        ...

    def get_ticker_info(self, ticker: str) -> TickerInfo:
        """Whether data-store has ever ingested ticker at all, and whether calculate-returns has
        run for it. Never raises for an unknown ticker -- exists=False is itself the answer, not
        an error -- so a caller can block its own rebuild for that ticker loudly rather than
        finding out from a failed price-series call.
        """
        ...
