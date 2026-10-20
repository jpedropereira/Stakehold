"""Standard windows (spec §4.5): 1Y, 3Y, 5Y, 10Y and Max, all ending at the latest common date.

- A year window starts N calendar years before the end, 29 February clamped to the 28th, as
  Labs clamps its month periods.
- Max starts at the latest base date among the series compared (R10's common window), so a
  single series' Max is its whole history.
- A window covers a series when the series' base is on or before the start and its data runs
  through the end. Whether an uncovered window is greyed is the caller's call (R9).
- Slicing measures from the level on the window's start: returns dated after the start and on
  or before the end.
"""

import calendar
from datetime import date
from enum import StrEnum

from portfolio.risk.types import ReturnSeries, Window


class StandardWindow(StrEnum):
    Y1 = "1Y"
    Y3 = "3Y"
    Y5 = "5Y"
    Y10 = "10Y"
    MAX = "Max"


_YEARS = {
    StandardWindow.Y1: 1,
    StandardWindow.Y3: 3,
    StandardWindow.Y5: 5,
    StandardWindow.Y10: 10,
}


def years_before(day: date, years: int) -> date:
    year = day.year - years
    return date(year, day.month, min(day.day, calendar.monthrange(year, day.month)[1]))


def latest_common_date(*series: ReturnSeries) -> date:
    if not series:
        raise ValueError("a common end date needs at least one series")
    return min(s.last_date for s in series)


def earliest_common_start(*series: ReturnSeries) -> date:
    if not series:
        raise ValueError("a common start date needs at least one series")
    return max(s.base_date for s in series)


def standard_windows(end: date, max_start: date) -> tuple[Window, ...]:
    return tuple(
        Window(
            label,
            max_start if label is StandardWindow.MAX else years_before(end, _YEARS[label]),
            end,
        )
        for label in StandardWindow
    )


def covers(series: ReturnSeries, window: Window) -> bool:
    return series.base_date <= window.start and series.last_date >= window.end


def slice_series(series: ReturnSeries, window: Window) -> ReturnSeries:
    if not covers(series, window):
        raise ValueError(
            f"series {series.base_date}..{series.last_date} doesn't cover window "
            f"{window.label} {window.start}..{window.end}"
        )
    kept = [
        (day, value)
        for day, value in zip(series.dates, series.values)
        if window.start < day <= window.end
    ]
    return ReturnSeries(
        series.frequency,
        window.start,
        tuple(day for day, _ in kept),
        tuple(value for _, value in kept),
    )
