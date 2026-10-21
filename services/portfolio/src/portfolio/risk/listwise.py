"""The listwise weekly window (spec R12): the panel every portfolio calculation runs on.

- **Only the weeks on which every holding has a non-NULL return** are kept. A week any holding
  is NULL on, or has no row for, is dropped for all of them and counted as a gap (R8).
- **A late holding shortens the window** (agreed for RK-15, R12 as written): the start is
  clipped to the latest base date among the holdings, and `limited_by` names the holding(s)
  whose start set it, so the UI can offer to exclude it (RK-17, RK-18). The window reported is
  the shortened one. The end is clipped to the earliest last date, as `windows.common_span`.
- **The window first becomes the whole weeks ending inside it** (`weekly.weekly_window`), as
  for every weekly figure (RK-7).
- **R13 is held by type**: ex_ante.py takes a `ListwiseReturns`, never a matrix, so a pairwise
  correlation matrix (RK-16), which needn't be positive semi-definite, can't feed σ_p, risk
  contributions or a what-if.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

import numpy as np
from portfolio.risk.quality import has_suspect_rows
from portfolio.risk.types import Frequency, ReturnSeries, Window
from portfolio.risk.weekly import weekly_window
from portfolio.risk.windows import common_span, slice_series


@dataclass(frozen=True)
class ListwiseReturns:
    """`returns` is T×N, one row per kept week and one column per key, with no NaN."""

    keys: tuple[str, ...]
    window: Window
    dates: tuple[date, ...]
    returns: np.ndarray
    n_gaps: int
    limited_by: tuple[str, ...]
    has_suspect_rows: bool

    @property
    def n_obs(self) -> int:
        return len(self.dates)


def listwise(weekly: Mapping[str, ReturnSeries], window: Window) -> ListwiseReturns:
    if not weekly:
        raise ValueError("a listwise window needs at least one holding")
    for key, series in weekly.items():
        if series.frequency is not Frequency.WEEKLY:
            raise ValueError(
                f"the listwise window is on weekly returns (spec §4.3), got a "
                f"{series.frequency} series for {key}"
            )
    keys = tuple(weekly)
    span = common_span(*weekly.values())
    whole_weeks = weekly_window(window, None if span is None else span[0])
    if whole_weeks is None or span is None:
        return _empty(keys, whole_weeks or window)

    start, end = max(whole_weeks.start, span[0]), min(whole_weeks.end, span[1])
    if start >= end:
        return _empty(keys, whole_weeks)
    limited_by = tuple(
        key
        for key, series in weekly.items()
        if start > whole_weeks.start and series.base_date == start
    )
    shortened = Window(window.label, start, end)
    slices = {key: slice_series(series, shortened) for key, series in weekly.items()}

    by_date = [dict(zip(s.dates, s.values)) for s in slices.values()]
    all_dates = sorted(set().union(*by_date))
    kept_dates, rows = [], []
    for day in all_dates:
        row = [values.get(day) for values in by_date]
        if all(v is not None for v in row):
            kept_dates.append(day)
            rows.append(row)
    return ListwiseReturns(
        keys=keys,
        window=shortened,
        dates=tuple(kept_dates),
        returns=np.array(rows, dtype=float).reshape(len(rows), len(keys)),
        n_gaps=len(all_dates) - len(kept_dates),
        limited_by=limited_by,
        has_suspect_rows=any(has_suspect_rows(s) for s in slices.values()),
    )


def _empty(keys: tuple[str, ...], window: Window) -> ListwiseReturns:
    return ListwiseReturns(keys, window, (), np.empty((0, len(keys))), 0, (), False)
