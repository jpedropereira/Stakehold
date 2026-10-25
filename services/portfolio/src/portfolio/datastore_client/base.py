"""The DataStoreClient Protocol -- portfolio's domain code depends only on this, not on
http_client.py's implementation, so a stub can stand in for tests without a live data-store
instance. See docs/flows.md's cross-service prerequisite section.
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
    # data-store's market_returns.simple_return (price + dividend, split-aware). None is a gap --
    # the ticker's first stored day, or a day data-store judged implausible -- never 0, which is a
    # real flat day. HttpDataStoreClient always sets it; the default only spares callers that
    # don't read it (returns/calculator.py and its fakes).
    simple_return: Decimal | None = None


@dataclass(frozen=True)
class FxPoint:
    date: date
    rate: Decimal


@dataclass(frozen=True)
class RiskFreePoint:
    date: date
    # Annualised %, ACT/360 (risk spec §4.1). Negative rates are kept as published.
    rate: Decimal
    # "estr" or "eonia_adjusted" (EONIA - 0.085) -- data-store's risk_free/splice.py.
    series: str


@dataclass(frozen=True)
class RiskFreeSeries:
    # The first day the whole series covers, whatever the requested window: what a caller
    # applies R4's rf_partial against.
    first_date: date
    # Published days only. Forward-filling weekends and holidays is risk/risk_free.py's job.
    points: list[RiskFreePoint]


@dataclass(frozen=True)
class Regime:
    """One row of data-store's hand-curated `regimes` table -- GET /regimes. The dates are the
    regime's own, unclipped: the caller clips them to the series it measures."""

    regime_id: str
    dimension: str  # business_cycle, drawdown, event, monetary_policy...
    scope: str  # US, EA, GLOBAL, SPX...
    name: str
    start_date: date
    end_date: date | None  # None for an ongoing regime
    known_from: date | None  # served, not applied: the regime view is descriptive (spec §11)
    severity: str | None
    source: str  # a citation (NBER, manual_approx...), not a provider
    notes: str | None


@dataclass(frozen=True)
class RegimeList:
    # The whole table's count, whatever the range: 0 means nothing is loaded, which a caller
    # must word differently from "no regime overlaps this range".
    total_regimes: int
    regimes: list[Regime]


@dataclass(frozen=True)
class EtfFund:
    """One fund in data-store's etfs.yaml -- GET /etf-holdings. `ticker` is the fund's own
    tradeable ticker (IWDA.AS), the only link from a holding to the fund's snapshots."""

    fund_key: str
    display_name: str
    ticker: str | None
    enabled: bool
    latest_as_of_date: date | None  # None when the fund has never been scraped


@dataclass(frozen=True)
class EtfConstituent:
    """One line of a fund's snapshot, as data-store stores it: cash, FX and futures lines
    included, each with its `asset_class`."""

    ticker: str | None  # iShares' raw symbol
    # What data-store prices the line as -- the key a directly held stock matches on. None
    # unless it's an equity line on a mapped exchange.
    yfinance_ticker: str | None
    name: str | None
    sector: str | None
    asset_class: str | None
    location: str | None
    exchange: str | None
    currency: str | None  # the listing's currency, iShares' spelling
    weight_percent: Decimal | None  # as stored: a snapshot can sum to a little over 100


@dataclass(frozen=True)
class EtfHoldings:
    fund_key: str
    ticker: str | None
    as_of_date: date  # the scrape date, not iShares' own as-of date
    constituents: list[EtfConstituent]  # heaviest first


@dataclass(frozen=True)
class TickerInfo:
    exists: bool
    has_returns: bool
    # data-store's onboarding status when the ticker is tracked ("pending", "backfilling",
    # "ready", "failed"); None when it's priced only through a fund's holdings, or not at all.
    status: str | None = None
    # The provider's spelling, verbatim (`GBp` stays `GBp`); None when data-store knows none.
    currency: str | None = None
    # The ISO code fx_rates is keyed by (`GBp` -> `GBP`). Pick an FX pair from this, not from
    # `currency`. None when there's no currency, or it isn't one fx_rates could hold.
    fx_currency: str | None = None


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
        """split_adjusted_close and simple_return for every trading day data-store has for ticker
        in [start, end], ascending by date. An empty list is a legitimate "no data in this
        range"; the ticker itself is assumed to exist -- callers check that with get_ticker_info
        first.
        """
        ...

    def get_fx_rate_series(self, base: str, quote: str, start: date, end: date) -> list[FxPoint]:
        """Every stored EUR->quote rate point in [start, end], ascending by date -- the range
        counterpart to get_fx_rate, for a caller that needs to look up "the latest rate at or
        before" many dates without one HTTP call per date. An empty list means no rate was ever
        tracked for this pair in the window, same as get_fx_rate's None.
        """
        ...

    def get_risk_free_series(self, start: date, end: date) -> RiskFreeSeries | None:
        """The spliced daily rf_eur (€STR, EONIA - 0.085 before 2019-10-01) published in
        [start, end], ascending by date. Returns None when data-store has no risk-free data
        stored at all -- a caller must report that, never read it as rf = 0. A series with no
        points is normal (a weekend, a window before 1999): its first_date still says where
        coverage begins.
        """
        ...

    def get_regimes(self, start: date, end: date) -> RegimeList:
        """Every regime overlapping [start, end], inclusive at both ends (an ongoing regime
        overlaps every range ending on or after its start), ordered by start date, with the
        table's total count."""
        ...

    def list_etf_funds(self) -> list[EtfFund]:
        """Every fund data-store is configured with, enabled or not, with its ticker and
        newest snapshot date."""
        ...

    def get_latest_etf_holdings(self, fund_key: str) -> EtfHoldings | None:
        """Every line of the fund's newest snapshot. None when data-store has no snapshot for
        it (or doesn't know the fund) -- a caller reports that fund as not looked through."""
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
        """Whether data-store has ever ingested ticker at all, whether calculate-returns has run
        for it, and its currency (verbatim and as the FX code), when data-store knows one. Never
        raises for an unknown ticker -- exists=False is itself the answer, not an error -- so a
        caller can block its own rebuild for that ticker loudly rather than finding out from a
        failed price-series call.
        """
        ...
