"""An asset's daily total return as a EUR investor sees it, with its local and FX components
(RK-5, risk spec §4.2). The one place portfolio converts market returns to EUR: Labs builds its
growth lines on it, and the risk path takes its return series from it, so the two can't drift
apart.

No I/O, no datastore_client calls -- the caller fetches the asset's price series and, for a
non-EUR asset, the FX series for its currency, as labs/calculator.py's caller does. This module
never looks at a currency code: an ineligible asset gets a reason code, and the caller, which
knows the currency, words the message.

The rules:

- **Conversion.** fx_t is units of the asset's currency per 1 EUR, as fx_rates stores it, taken
  as the latest rate on or before each date with no age limit. Between the asset's consecutive
  price dates, r_fx = fx_(t-1) / fx_t - 1 and r_eur = (1 + r_local) x (1 + r_fx) - 1. A EUR asset
  has r_fx = 0. A rate that moves on a day the asset didn't trade counts on its next price date.
- **Start.** The anchor is the first price date with a rate on or before it; the series' first
  return is the next price date's. Its own simple_return is ignored: there's no earlier close in
  the series for it to be a return over (data-store's first day is NULL anyway).
- **Gaps.** A NULL simple_return stays None in r_local and r_eur, never 0: in the risk path a gap
  is excluded (spec R5). r_fx is still reported for that day, since the rate is known. Bridging a
  gap as a flat day is Labs' rule, applied in labs/calculator.py, not here.
- **Precision.** Decimal throughout, as the inputs are, so Labs' figures stay exact. The risk
  library works on floats and converts at its own boundary.
"""

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from portfolio.datastore_client.base import FxPoint, PricePoint

NO_HISTORY = "no history yet"
NO_FX_SERIES = "no FX series"

_ONE = Decimal("1")
_ZERO = Decimal("0")


@dataclass(frozen=True)
class FxSeries:
    """Units of a currency per 1 EUR, ascending by date."""

    dates: list[date]
    rates: list[Decimal]

    def on(self, day: date) -> Decimal:
        """The latest rate on or before `day`. Only defined from the first date on: an
        EurReturns' anchor and every later date have one."""
        index = bisect_right(self.dates, day) - 1
        if index < 0:
            raise ValueError(f"No FX rate on or before {day}; the series starts {self.dates[0]}.")
        return self.rates[index]


@dataclass(frozen=True)
class EurReturns:
    anchor: date  # the level-0 day the first return is measured from
    dates: list[date]  # each later price date, ascending
    r_local: list[Decimal | None]  # simple_return; None is a gap
    r_fx: list[Decimal]  # 0 for a EUR asset
    r_eur: list[Decimal | None]  # None exactly where r_local is
    fx: FxSeries | None  # None for a EUR asset


@dataclass(frozen=True)
class Ineligible:
    reason: str  # NO_HISTORY or NO_FX_SERIES


def eur_daily_returns(
    prices: Sequence[PricePoint], fx: Sequence[FxPoint] | None
) -> EurReturns | Ineligible:
    """`prices` ascending by date, only simple_return read. `fx` ascending by date; None for a
    EUR asset, while an empty series means the currency has no rates."""
    if not prices:
        return Ineligible(NO_HISTORY)

    if fx is None:
        series = None
        usable = prices
    else:
        if not fx:
            return Ineligible(NO_FX_SERIES)
        series = FxSeries([point.date for point in fx], [point.rate for point in fx])
        first = bisect_left([point.date for point in prices], series.dates[0])
        if first == len(prices):
            return Ineligible(NO_FX_SERIES)
        usable = prices[first:]

    dates, r_local, r_fx, r_eur = [], [], [], []
    for previous, point in zip(usable, usable[1:]):
        if series is None:
            fx_factor = _ONE
        else:
            fx_factor = series.on(previous.date) / series.on(point.date)
        dates.append(point.date)
        r_local.append(point.simple_return)
        r_fx.append(fx_factor - _ONE if series is not None else _ZERO)
        if point.simple_return is None:
            r_eur.append(None)
        else:
            r_eur.append((_ONE + point.simple_return) * fx_factor - _ONE)
    return EurReturns(
        anchor=usable[0].date, dates=dates, r_local=r_local, r_fx=r_fx, r_eur=r_eur, fx=series
    )
