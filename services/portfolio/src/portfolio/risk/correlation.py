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

Inputs are weekly series (`weekly.to_weekly`), keyed by ticker in display order. A pairwise
cell's overlap is `pairing.paired_weeks`, shared with the relative measures (RK-13): the whole
weeks ending inside the window (`weekly.weekly_window`), as for the weekly single-series
measures, then the weeks both series have.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
from portfolio.risk.history import Measure, check_minimum
from portfolio.risk.listwise import ListwiseReturns
from portfolio.risk.pairing import paired_weeks
from portfolio.risk.quality import WEEKLY_Q, over_gap_budget
from portfolio.risk.types import Flag, Frequency, ReturnSeries, RiskFigure, Window

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
    cells = {}
    for i, a in enumerate(keys):
        for b in keys[i:]:
            cells[(a, b)] = _pair_cell(series[a], series[b], window)
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


def _pair_cell(a: ReturnSeries, b: ReturnSeries, window: Window) -> RiskFigure:
    pair = paired_weeks(a, b, window)
    check = check_minimum(Measure.CORRELATION, pair.n_obs, pair.start, pair.end)
    value, reason = None, check.reason
    if check.sufficient:
        if np.all(pair.x == pair.x[0]) or np.all(pair.y == pair.y[0]):
            reason = _NO_VARIATION
        else:
            value = float(np.corrcoef(pair.x, pair.y)[0, 1])
    return RiskFigure(
        value=value,
        window_start=pair.start,
        window_end=pair.end,
        frequency=Frequency.WEEKLY,
        q=WEEKLY_Q,
        n_obs=pair.n_obs,
        n_gaps=pair.n_gaps,
        flags=pair.flags | check.flags,
        reason=reason,
    )


def _require_weekly(series: Mapping[str, ReturnSeries]) -> None:
    for label, s in series.items():
        if s.frequency is not Frequency.WEEKLY:
            raise ValueError(f"correlation needs weekly series; {label} is {s.frequency}")


def _read_only(array: np.ndarray) -> np.ndarray:
    array.setflags(write=False)
    return array
