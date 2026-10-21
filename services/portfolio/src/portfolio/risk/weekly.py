"""Weekly aggregation (spec R6): daily returns compounded into weeks ending on Friday (W-FRI).

- A week's return is Π(1 + r_d) − 1 over its days, each day belonging to the week of the Friday
  on or after it (a weekend day joins the next week).
- Any NULL day makes the week NULL, and so does a week with no rows at all: the move is lost,
  not zero (R5).
- A week is labelled by its Friday even when Friday was a holiday.
- **Partial weeks at the edges are dropped** (agreed for RK-6). The weekly series is based on
  the first Friday on or after the daily base date and ends at the last complete week, so every
  week runs Friday to Friday and two assets' weeks line up for correlation. Keeping the partial
  edges (pandas' resample default) was the alternative; those weeks would cover different spans
  per asset.
- A week is complete once its Friday is on or before `as_of`, the date the caller knows the data
  runs through. It defaults to the series' last date, so a series ending on a Thursday drops its
  last week unless the caller says Friday has passed (a holiday Friday).
- `weekly_window` turns a measurement window into the whole weeks ending inside it (RK-7), so a
  weekly measure's window starts and ends on Fridays the weekly series has.
"""

from datetime import date, timedelta

import numpy as np
from portfolio.risk.types import Frequency, ReturnSeries, Window

_FRIDAY = 4
_WEEK = timedelta(days=7)


def to_weekly(daily: ReturnSeries, as_of: date | None = None) -> ReturnSeries:
    if daily.frequency is not Frequency.DAILY:
        raise ValueError(f"to_weekly needs a daily series, got {daily.frequency}")

    first_friday = _friday_on_or_after(daily.base_date)
    last_friday = _friday_on_or_before(as_of or daily.last_date)

    weeks: dict[date, list[float | None]] = {}
    for day, value in zip(daily.dates, daily.values):
        friday = _friday_on_or_after(day)
        if first_friday < friday <= last_friday:
            weeks.setdefault(friday, []).append(value)

    dates: list[date] = []
    values: list[float | None] = []
    friday = first_friday + _WEEK
    while friday <= last_friday:
        dates.append(friday)
        values.append(_compound(weeks.get(friday)))
        friday += _WEEK
    return ReturnSeries(Frequency.WEEKLY, first_friday, tuple(dates), tuple(values))


def weekly_window(window: Window, available_from: date | None = None) -> Window | None:
    """The window's weeks: those ending (on their Friday) inside it, up to the Friday on or
    before its end. The first is the week the window starts in, which began on the Friday on
    or before its start, up to six days before the window does.

    Snapping the start forward to the next Friday instead lost that week on most days: a
    one-year window then held 51 weeks, under §4.5's 52-week minimum, so its weekly figures
    were greyed. The week is taken only when the weekly data has it: when it starts after that
    Friday (`available_from`, the caller's weekly base), but no later than the next one, the
    window starts on the next Friday, as a Max window starting on the series' own first day
    does. A later `available_from` is left for the caller to clamp, so a holding that starts
    mid-window still shows as limiting it.

    Without the end's snap a window ending on a weekday other than Friday would end after the
    weekly series does. None when no week ends inside it."""
    before, after = _friday_on_or_before(window.start), _friday_on_or_after(window.start)
    start = after if available_from is not None and before < available_from <= after else before
    end = _friday_on_or_before(window.end)
    return Window(window.label, start, end) if start < end else None


def _compound(days: list[float | None] | None) -> float | None:
    if not days or any(v is None for v in days):
        return None
    return float(np.prod(1.0 + np.array(days, dtype=float)) - 1.0)


def _friday_on_or_after(day: date) -> date:
    return day + timedelta(days=(_FRIDAY - day.weekday()) % 7)


def _friday_on_or_before(day: date) -> date:
    return day - timedelta(days=(day.weekday() - _FRIDAY) % 7)
