"""Drawdown (spec §5.3) on the growth index with G0 = 1 on the series' base date.

- DD_t = G_t / peak_t − 1. Max drawdown is its minimum, ≤ 0; the trough is the first date it
  is reached, and the peak is the date the running maximum before it was set (the base date
  when the series falls from the start).
- Recovery is the first date after the trough with G back at or above the peak. "At" allows
  a relative 1e-12, so a level that returns to the peak through float rounding counts.
- Max time under water is the longest span, in calendar days, between a peak and its recovery
  -- or the last date, if unrecovered -- across **every** episode, not only the deepest one.
- A gap (R5) is excluded: the level carries across it, which chains the same as a flat day,
  and its DD point is None so a chart can break the line rather than draw a move that was lost.

Frequency-agnostic like measures.py; the frequency rule and the 200-observation minimum are
single_series.py's.
"""

from dataclasses import dataclass
from datetime import date

from portfolio.risk.types import ReturnSeries

_RECOVERY_TOLERANCE = 1e-12


@dataclass(frozen=True)
class Drawdown:
    max_drawdown: float
    peak_date: date | None
    trough_date: date | None
    recovery_date: date | None
    max_days_under_water: int
    series: tuple[tuple[date, float | None], ...]


def drawdown(series: ReturnSeries) -> Drawdown:
    level = peak = 1.0
    peak_date = series.base_date
    under_water = False
    longest = 0
    max_dd = 0.0
    mdd_peak_date = trough_date = recovery_date = None
    points: list[tuple[date, float | None]] = [(series.base_date, 0.0)]

    for day, value in zip(series.dates, series.values):
        if value is None:
            points.append((day, None))
            continue
        level *= 1.0 + value
        if level >= peak * (1.0 - _RECOVERY_TOLERANCE):
            if under_water:
                longest = max(longest, (day - peak_date).days)
                if mdd_peak_date == peak_date and recovery_date is None:
                    recovery_date = day
                under_water = False
            peak = max(peak, level)
            peak_date = day
            points.append((day, 0.0))
            continue
        under_water = True
        dd = level / peak - 1.0
        points.append((day, dd))
        if dd < max_dd:
            max_dd, trough_date, mdd_peak_date, recovery_date = dd, day, peak_date, None

    if under_water:
        longest = max(longest, (series.last_date - peak_date).days)
    return Drawdown(max_dd, mdd_peak_date, trough_date, recovery_date, longest, tuple(points))
