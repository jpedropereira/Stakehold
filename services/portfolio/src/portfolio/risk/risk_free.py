"""Risk-free returns per period (spec §4.1), the rf_t that excess returns subtract.

- €STR and EONIA are ACT/360. A period [d0, d1) earns Π over its calendar days d of
  (1 + rate_d / 100 / 360) − 1, the exact form rather than §4.1's simple approximation.
- Each day uses the latest rate published on or before it, forward-filling weekends and
  holidays. A rate dated after the day is never used: no look-ahead (§9).
- Days before the series' first published date (1999-01-04) earn 0, and the result is marked
  partial so the figure can carry `rf_partial` (R4). A day after it with no rate on or before
  it is an error: the caller fetched too short a range, and forward-filling from nothing would
  invent a rate.
- Negative rates (2014–2022) are kept as published, never floored at 0.

Boundaries are the series' own dates with its base date first, so one function serves daily and
weekly series: period k runs from boundary k−1 to boundary k.
"""

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

_ACT_360 = 360 * 100  # rate in % a year -> fraction a day
_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class RatePoint:
    """One published rate, annualised %. RK-4 adds data-store's client type; this local one
    keeps the library free of the client."""

    date: date
    rate_pct: float


@dataclass(frozen=True)
class RiskFreeReturns:
    values: tuple[float, ...]
    partial: bool


def risk_free_returns(
    rates: Sequence[RatePoint], first_published: date, boundaries: Sequence[date]
) -> RiskFreeReturns:
    for earlier, later in zip(boundaries, boundaries[1:]):
        if later <= earlier:
            raise ValueError(
                f"risk-free boundaries must strictly increase: {later} after {earlier}"
            )

    ordered = sorted(rates, key=lambda point: point.date)
    rate_dates = [point.date for point in ordered]
    partial = False
    values = []
    for start, end in zip(boundaries, boundaries[1:]):
        growth = 1.0
        day = start
        while day < end:
            if day < first_published:
                partial = True
            else:
                growth *= 1.0 + _rate_on(ordered, rate_dates, day) / _ACT_360
            day += _ONE_DAY
        values.append(growth - 1.0)
    return RiskFreeReturns(tuple(values), partial)


def _rate_on(ordered: Sequence[RatePoint], rate_dates: list[date], day: date) -> float:
    index = bisect_right(rate_dates, day)
    if index == 0:
        raise ValueError(f"no risk-free rate published on or before {day}")
    return ordered[index - 1].rate_pct
