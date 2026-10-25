"""The VaR day mask (spec R7, §5.5): the days in a window on which every included asset's
exchange traded. On the union calendar a closed exchange's day is a carry-forward 0%, and those
zeros crowd the centre of the distribution and shrink the tail quantile, so daily VaR keeps only
the days all assets share. A single asset keeps its own calendar. NULL returns are excluded
separately, by the measure (R5): the exchange traded, but the move was lost.

**An asset traded on a day if data-store has a price row for it that isn't a stale one**: zero
volume and no move (RK-31). data-store keeps such rows on some exchange holidays, and a row alone
would let their 0% pass the mask. The rule is the caller's, applied once in
`eur_returns.traded_dates`, since this library never sees prices or volume; it passes one set of
trading days per asset.
"""

from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from datetime import date

from portfolio.risk.types import Window


def var_days(trading_days: Sequence[AbstractSet[date]], window: Window) -> tuple[date, ...]:
    if not trading_days:
        raise ValueError("the VaR day mask needs at least one asset's trading days")
    shared = set.intersection(*(set(days) for days in trading_days))
    return tuple(sorted(day for day in shared if window.start < day <= window.end))
