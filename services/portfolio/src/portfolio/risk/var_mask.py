"""The VaR day mask (spec R7, §5.5): the days in a window on which every included asset's
exchange traded. On the union calendar a closed exchange's day is a carry-forward 0%, and those
zeros crowd the centre of the distribution and shrink the tail quantile, so daily VaR keeps only
the days all assets share. A single asset keeps its own calendar. NULL returns are excluded
separately, by the measure (R5): the exchange traded, but the move was lost.

**An asset traded on a day if data-store has a price row for it** (agreed for RK-6). Known
limitation: data-store keeps rows for some zero-volume holidays (e.g. UK bank holidays, see its
docs/schema.md), so such a day can pass the mask. The fix is to expose `volume` on data-store's
price endpoint and mask on volume > 0 (RK-31, tasks-risk-thread.md).
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
