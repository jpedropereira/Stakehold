"""Correlation on weekly returns (spec §7.1), in the two forms R13 keeps apart.

- **Pairwise, for display.** Each cell is Pearson's ρ over the weeks inside the window that both
  series have a return for, so each cell carries its own `n_obs`, gaps and start date (the base
  of its first overlapping week). A series needn't cover the window: a late-starting asset still
  gets cells over the history it has. Each cell is a `RiskFigure`, greyed below §4.5's 104 weeks
  (R9) or when either side never varies.
- **Listwise, for maths (R12).** `listwise_correlation` takes RK-15's `ListwiseReturns`
  (listwise.py): only the weeks in which every series has a return, the window shortened by a
  late starter and `limited_by` naming it. Σ (covariance.py) runs on the same panel, so R12 has
  one implementation and the listwise ρ always agrees with the Σ beside it.
- **The two never mix.** A pairwise matrix can be internally inconsistent (not positive
  semi-definite), so it must never feed σ_p, risk contributions or the what-if. portfolio runs no
  static type checker, so the separation is by construction plus a runtime check:
  `PairwiseCorrelation` holds only cells, never an array to do maths with, and
  `listwise_correlation` raises `TypeError` on anything but a `ListwiseReturns`.
- **Currency is the caller's.** The same functions take weekly series built from RK-5's `r_eur`
  or its `r_local`; which one, and the label saying so, belong to the caller.

Inputs are weekly series (`weekly.to_weekly`), keyed by ticker in display order. Every weekly
series sits on the same Friday grid, so aligning two is a date intersection. The window becomes
the whole weeks ending inside it first (`weekly.weekly_window`), as for the weekly single-series
measures.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
from portfolio.risk.history import Measure, check_minimum
from portfolio.risk.listwise import ListwiseReturns
from portfolio.risk.quality import WEEKLY_Q, any_suspect, over_gap_budget
from portfolio.risk.types import Flag, Frequency, ReturnSeries, RiskFigure, Window
from portfolio.risk.weekly import weekly_window

_WEEK = timedelta(days=7)
_NO_VARIATION = "no variation in returns"


@dataclass(frozen=True)
class PairwiseCorrelation:
    """For display only (R13). Each cell over its own overlap; there is deliberately no matrix."""

    keys: tuple[str, ...]
    _cells: Mapping[tuple[str, str], RiskFigure] = field(repr=False)

    def cell(self, a: str, b: str) -> RiskFigure:
        return self._cells[(a, b) if (a, b) in self._cells else (b, a)]


@dataclass(frozen=True, eq=False)
class ListwiseCorrelation:
    """`matrix` is None exactly when greyed, and `reason` then says why."""

    keys: tuple[str, ...]
    matrix: np.ndarray | None
    window: Window
    n_obs: int
    n_gaps: int
    limited_by: tuple[str, ...]
    flags: frozenset[Flag]
    reason: str | None = None


def pairwise_correlation(series: Mapping[str, ReturnSeries], window: Window) -> PairwiseCorrelation:
    _require_weekly(series)
    keys = tuple(series)
    whole_weeks = weekly_window(window)
    by_date = {label: _by_date(s) for label, s in series.items()}

    cells = {}
    for i, a in enumerate(keys):
        for b in keys[i:]:
            cells[(a, b)] = _pair_cell(by_date[a], by_date[b], window, whole_weeks)
    return PairwiseCorrelation(keys, cells)


def listwise_correlation(panel: ListwiseReturns) -> ListwiseCorrelation:
    if not isinstance(panel, ListwiseReturns):
        raise TypeError(
            f"portfolio maths takes a ListwiseReturns (R12, R13), not {type(panel).__name__}"
        )
    check = check_minimum(Measure.CORRELATION, panel.n_obs, panel.window.start, panel.window.end)
    reason = check.reason
    if reason is None:
        constant = [
            key for key, column in zip(panel.keys, panel.returns.T) if np.all(column == column[0])
        ]
        if constant:
            reason = f"{_NO_VARIATION} ({', '.join(constant)})"
    matrix = None if reason else _read_only(np.corrcoef(panel.returns, rowvar=False))
    flags = {Flag.DATA_GAPS} if over_gap_budget(panel.n_obs, panel.n_gaps) else set()
    if panel.has_suspect_rows:
        flags.add(Flag.HAS_SUSPECT_ROWS)
    return ListwiseCorrelation(
        keys=panel.keys,
        matrix=matrix,
        window=panel.window,
        n_obs=panel.n_obs,
        n_gaps=panel.n_gaps,
        limited_by=panel.limited_by,
        flags=frozenset(flags) | check.flags,
        reason=reason,
    )


def _pair_cell(
    a: Mapping[date, float | None],
    b: Mapping[date, float | None],
    window: Window,
    whole_weeks: Window | None,
) -> RiskFigure:
    weeks = (
        sorted(d for d in a.keys() & b.keys() if whole_weeks.start < d <= whole_weeks.end)
        if whole_weeks is not None
        else []
    )
    pairs = [(a[d], b[d]) for d in weeks if a[d] is not None and b[d] is not None]
    n_obs, n_gaps = len(pairs), len(weeks) - len(pairs)
    x = np.array([p[0] for p in pairs], dtype=float)
    y = np.array([p[1] for p in pairs], dtype=float)
    start, end = (weeks[0] - _WEEK, weeks[-1]) if weeks else (window.start, window.end)

    check = check_minimum(Measure.CORRELATION, n_obs, start, end)
    value, reason = None, check.reason
    if check.sufficient:
        if np.all(x == x[0]) or np.all(y == y[0]):
            reason = _NO_VARIATION
        else:
            value = float(np.corrcoef(x, y)[0, 1])
    return RiskFigure(
        value=value,
        window_start=start,
        window_end=end,
        frequency=Frequency.WEEKLY,
        q=WEEKLY_Q,
        n_obs=n_obs,
        n_gaps=n_gaps,
        flags=_quality_flags(n_obs, n_gaps, np.column_stack([x, y])) | check.flags,
        reason=reason,
    )


def _quality_flags(n_obs: int, n_gaps: int, returns: np.ndarray) -> frozenset[Flag]:
    flags = set()
    if over_gap_budget(n_obs, n_gaps):
        flags.add(Flag.DATA_GAPS)
    if any_suspect(returns.ravel().tolist()):
        flags.add(Flag.HAS_SUSPECT_ROWS)
    return frozenset(flags)


def _by_date(series: ReturnSeries) -> dict[date, float | None]:
    return dict(zip(series.dates, series.values))


def _require_weekly(series: Mapping[str, ReturnSeries]) -> None:
    for label, s in series.items():
        if s.frequency is not Frequency.WEEKLY:
            raise ValueError(f"correlation needs weekly series; {label} is {s.frequency}")


def _read_only(array: np.ndarray) -> np.ndarray:
    array.setflags(write=False)
    return array
