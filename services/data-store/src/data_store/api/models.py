"""Response models for the read API.

Kept separate from routes.py so the response contract -- what portfolio's `DataStoreClient` will
actually consume -- is visible in one place. Numeric fields stay `Decimal`, not `float`: Pydantic
v2 serializes `Decimal` to a JSON string in JSON mode, which preserves the `NUMERIC` column's
exact precision (e.g. `market_returns.split_adjusted_close`'s four decimal places) that a float
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
