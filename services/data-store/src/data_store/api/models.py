"""Response models for the read API.

Kept separate from routes.py so the response contract -- what portfolio's `DataStoreClient` will
actually consume -- is visible in one place. Numeric fields stay `Decimal`, not `float`: Pydantic
v2 serializes `Decimal` to a JSON string in JSON mode, which preserves the `NUMERIC` column's
exact precision (e.g. `market_returns.split_adjusted_close`'s six decimal places) that a float
round-trip would not. A derived FX rate is not read from a column at all, so `read_flows`
quantizes it to `fx_rates.rate`'s own scale before it gets here -- otherwise a division's
28-digit default context would make an inverse or triangulated pair look more precise than the
stored one it came from.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class PricePoint(BaseModel):
    date: date
    split_adjusted_close: Decimal
    future_split_factor: Decimal
    # Nullable together on a ticker's first stored date, or when calculate-returns flagged the
    # row implausible -- see schema.md. Never coerce these to 0; a flat day is a real 0.
    price_return: Decimal | None
    dividend_return: Decimal | None
    simple_return: Decimal | None
    log_return: Decimal | None
    # raw_market_data.volume. Zero is a real zero-volume day (a holiday row, or a thin listing's
    # quote); NULL is unknown, never zero, as calculate-returns' suppression rule treats it.
    volume: int | None


class PriceSeries(BaseModel):
    ticker: str
    source: str
    points: list[PricePoint]


class FxPoint(BaseModel):
    date: date
    rate: Decimal


class FxSeries(BaseModel):
    base_currency: str
    quote_currency: str
    source: str
    # How this series was produced -- "direct" | "inverse" | "identity" | "triangulated". See
    # read_flows.fx_rate_series for what each means.
    derivation: str
    # The currency triangulated through, only set when derivation == "triangulated".
    via: str | None
    points: list[FxPoint]


class RiskFreePoint(BaseModel):
    date: date
    # Annualised %, ACT/360, as rf_eur defines it (spec §4.1): negative rates kept as-is.
    rate: Decimal
    # "estr" or "eonia_adjusted" (EONIA - 0.085) -- see risk_free/splice.py.
    series: str


class RiskFreeSeries(BaseModel):
    source: str
    # The first day the whole series covers, whatever the requested window -- what a caller
    # applies R4 against. None only when nothing is stored for `source`.
    first_date: date | None
    # Published days only: weekends and holidays have no point, and filling them is the
    # caller's job.
    points: list[RiskFreePoint]


class Regime(BaseModel):
    regime_id: str
    dimension: str
    scope: str
    name: str
    # The regime's own dates, not clipped to the requested range.
    start_date: date
    # None for an ongoing regime.
    end_date: date | None
    # When the regime was identified; served, not applied (spec §11: descriptive use).
    known_from: date | None
    severity: str | None
    # A citation (NBER, CEPR-EABCDC, manual_approx...), not a provider key.
    source: str
    notes: str | None


class RegimeList(BaseModel):
    # Every regime stored, whatever the range: 0 means nothing is loaded, not an empty range.
    total_regimes: int
    regimes: list[Regime]


class EtfHoldingsFund(BaseModel):
    fund_key: str
    display_name: str
    # The fund's own tradeable ticker (etfs.yaml's `ticker`), e.g. IWDA.AS: how a caller gets
    # from a ticker it holds to this fund's holdings. None when the config doesn't set one.
    ticker: str | None
    enabled: bool
    # None when the fund has never been scraped.
    latest_as_of_date: date | None


class EtfHoldingsFundList(BaseModel):
    funds: list[EtfHoldingsFund]


class EtfConstituent(BaseModel):
    # The raw symbol iShares lists, or None for a line with none (cash, some derivatives).
    ticker: str | None
    # What ingest prices this line as. None unless it's an equity line on a mapped exchange.
    yfinance_ticker: str | None
    name: str | None
    sector: str | None
    asset_class: str | None
    location: str | None
    exchange: str | None
    # iShares' "market currency", verbatim: the listing's currency (`GBP` where yfinance says
    # `GBp`), not where the revenue comes from.
    currency: str | None
    # As stored, not normalised: a snapshot can sum to a little over 100.
    weight_percent: Decimal | None


class EtfHoldings(BaseModel):
    fund_key: str
    ticker: str | None
    # The scrape date, not the provider's own as-of date (schema.md's v1 simplification).
    as_of_date: date
    # Every line, heaviest first.
    constituents: list[EtfConstituent]


class TickerList(BaseModel):
    tickers: list[str]


class TickerInfo(BaseModel):
    ticker: str
    exists: bool
    has_returns: bool
    first_date: date | None
    last_date: date | None
    # The ticker's onboarding status ("pending", "backfilling", "ready", "failed") when it is in
    # tracked_tickers; None when it's priced only through a fund's holdings, or not at all.
    status: str | None = None
    # The provider's spelling, verbatim (`GBp` stays `GBp`): from tracked_tickers, else the
    # newest fund holding that resolves to this ticker, else None.
    currency: str | None = None
    # The ISO code fx_rates is keyed by (`GBp` -> `GBP`). Pick an FX pair from this, not from
    # `currency`. None when there's no currency, or it isn't one fx_rates could hold.
    fx_currency: str | None = None


class TickerLookup(BaseModel):
    ticker: str
    # "tracked" (see `status`), "available" (can be added), "unsupported" (an index or a
    # currency pair), "not_found", or "unavailable" (the provider didn't answer; try again).
    outcome: str
    status: str | None
    display_name: str | None
    currency: str | None
    exchange: str | None
    asset_type: str | None


class TrackedTicker(BaseModel):
    ticker: str
    source: str
    display_name: str
    asset_type: str
    currency: str | None
    exchange: str | None
    origin: str
    status: str
    enabled: bool
    attempts: int
    last_error: str | None
    requested_by: UUID | None
    requested_at: datetime
    ready_at: datetime | None


class TrackedTickerAdded(TrackedTicker):
    # False when the ticker was already tracked: the call changed nothing.
    created: bool


class TrackedTickerList(BaseModel):
    tracked_tickers: list[TrackedTicker]


class TrackedTickerCreate(BaseModel):
    # Raw as typed; the flow normalizes and whitelists it. Capped generously here only so an
    # absurd body is refused before it reaches any code.
    ticker: str = Field(min_length=1, max_length=40)
    requested_by: UUID
    # The caller (web, through portfolio) decides who's an admin; admins pass False.
    enforce_limit: bool = True
    source: str = "yfinance"


class TrackedTickerUpdate(BaseModel):
    enabled: bool | None = None
    retry: bool = False
