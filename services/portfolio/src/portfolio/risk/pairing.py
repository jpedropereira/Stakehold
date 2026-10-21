"""Two weekly series paired on the weeks both have inside a window: the overlap a pairwise
correlation cell (RK-16, spec §7.1) and the relative measures (RK-13, §5.6) run on.

- **The window becomes the whole weeks ending inside it first** (`weekly.weekly_window`), as
  for every weekly figure (RK-7). The week the window starts in counts, since it ends inside
  it, so a one-year window holds its 52 weeks.
- **The weeks are those both series have a row for inside it.** Every weekly series sits on the
  same Friday grid, so that is a date intersection. Neither series needs to cover the window: a
  late starter pairs over the history it has.
- **A NULL on either side is a gap** (R5, R6), dropped from both and counted against R8's budget.
- **The span is the shared weeks'**: from the base of the first (the Friday before it) to the
  last, gaps included. With no shared week it is the window as given.
- `data_gaps` and `has_suspect_rows` are set here, from the pair's own weeks. Minimums and
  undefined values are the caller's: each measure has its own.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from portfolio.risk.quality import any_suspect, over_gap_budget
from portfolio.risk.types import Flag, Frequency, ReturnSeries, Window
from portfolio.risk.weekly import weekly_window

_WEEK = timedelta(days=7)


@dataclass(frozen=True, eq=False)
class PairedWeeks:
    """`x` and `y` are the two series' returns on the weeks both have a value for, in date
    order, with no NaN."""

    x: np.ndarray
    y: np.ndarray
    start: date
    end: date
    n_obs: int
    n_gaps: int
    flags: frozenset[Flag]


def paired_weeks(a: ReturnSeries, b: ReturnSeries, window: Window) -> PairedWeeks:
    for series in (a, b):
        if series.frequency is not Frequency.WEEKLY:
            raise ValueError(f"pairing needs weekly series, got {series.frequency}")
    whole_weeks = weekly_window(window)
    a_by_date, b_by_date = dict(zip(a.dates, a.values)), dict(zip(b.dates, b.values))
    weeks = (
        sorted(
            d
            for d in a_by_date.keys() & b_by_date.keys()
            if whole_weeks.start < d <= whole_weeks.end
        )
        if whole_weeks is not None
        else []
    )
    pairs = [
        (a_by_date[d], b_by_date[d])
        for d in weeks
        if a_by_date[d] is not None and b_by_date[d] is not None
    ]
    n_obs, n_gaps = len(pairs), len(weeks) - len(pairs)
    x = np.array([p[0] for p in pairs], dtype=float)
    y = np.array([p[1] for p in pairs], dtype=float)
    start, end = (weeks[0] - _WEEK, weeks[-1]) if weeks else (window.start, window.end)

    flags = set()
    if over_gap_budget(n_obs, n_gaps):
        flags.add(Flag.DATA_GAPS)
    if any_suspect([*x.tolist(), *y.tolist()]):
        flags.add(Flag.HAS_SUSPECT_ROWS)
    return PairedWeeks(x, y, start, end, n_obs, n_gaps, frozenset(flags))
