"""Response models for the read API.

Kept separate from routes.py so the response contract -- what portfolio's `DataStoreClient` will
actually consume -- is visible in one place. Numeric fields stay `Decimal`, not `float`: Pydantic
v2 serializes `Decimal` to a JSON string in JSON mode, which preserves the `NUMERIC` column's
exact precision (e.g. `market_returns.split_adjusted_close`'s four decimal places) that a float
round-trip would not.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel


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


class TickerInfo(BaseModel):
    ticker: str
    exists: bool
    has_returns: bool
    first_date: date | None
    last_date: date | None
