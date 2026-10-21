"""Measures relative to a benchmark (spec §5.6, RK-13): beta, correlation, tracking error and
information ratio, on weekly EUR returns.

- **One overlap for all four**: `pairing.paired_weeks`, the weeks inside the window (shrunk to
  whole weeks) on which both the series and the benchmark have a return. A NULL on either side
  is a gap (R5), counted against R8's budget. Every figure reports that overlap as its window.
- **The formulas.** beta = cov(r_p, r_b) / var(r_b); ρ is Pearson's; tracking error =
  stdev(r_p − r_b) × √52; information ratio = mean(r_p − r_b) × 52 / tracking error. Sample
  statistics (ddof = 1), as everywhere in §5.
- **Minimums.** Beta and correlation take §4.5's 104 weeks. Tracking error and the information
  ratio take 52 (agreed for RK-13; §4.5 doesn't list them): tracking error is the volatility of
  the active return and the information ratio its Sharpe, so they take volatility's and
  Sharpe's minimum, as σ_p does in RK-15.
- **Undefined is greyed, never inf or 0.** A benchmark that never varies greys beta and
  correlation; a series that never varies greys correlation (its beta is a real 0). Identical
  series have a tracking error of exactly 0, a real value, and no information ratio. "Never
  varies" is tested as identical values, not a zero stdev, which floats can miss
  (`measures.sharpe`).
- **Coverage**: the benchmark covers a window when its first day is on or before the window's
  start (`benchmark_covers`). `relative_to` applies it: a window it doesn't cover is greyed
  with `benchmark_too_short`'s reason, and **never measured against another index** (§5.6: VWCE
  and IWDA track different indices, so swapping by window would make each window's beta answer
  a different question). A benchmark whose data ends before the window does is measured on the
  overlap, which its figures' windows show.
"""

from dataclasses import dataclass
from datetime import date

import numpy as np
from portfolio.risk.history import Measure, check_minimum
from portfolio.risk.pairing import PairedWeeks, paired_weeks
from portfolio.risk.quality import WEEKLY_Q
from portfolio.risk.types import Flag, Frequency, ReturnSeries, RiskFigure, Window

_NO_BENCHMARK_VARIATION = "no variation in benchmark returns"
_NO_VARIATION = "no variation in returns"
_NO_TRACKING_DIFFERENCE = "no tracking difference"


@dataclass(frozen=True)
class BenchmarkReturns:
    """The benchmark as the library takes it: its first day and its weekly EUR returns. The
    caller builds it (benchmark/), since this package imports nothing from portfolio."""

    first_date: date
    weekly: ReturnSeries


@dataclass(frozen=True)
class RelativeFigures:
    beta: RiskFigure
    correlation: RiskFigure
    tracking_error: RiskFigure
    information_ratio: RiskFigure


def relative_figures(
    weekly: ReturnSeries, benchmark: ReturnSeries, window: Window
) -> RelativeFigures:
    pair = paired_weeks(weekly, benchmark, window)
    x, y = pair.x, pair.y
    active = x - y
    tracking_error = 0.0 if _constant(active) else float(np.std(active, ddof=1) * np.sqrt(WEEKLY_Q))

    def beta():
        if _constant(y):
            return None, _NO_BENCHMARK_VARIATION
        covariance = 0.0 if _constant(x) else float(np.cov(x, y, ddof=1)[0, 1])
        return covariance / float(np.var(y, ddof=1)), None

    def correlation():
        if _constant(y):
            return None, _NO_BENCHMARK_VARIATION
        if _constant(x):
            return None, _NO_VARIATION
        return float(np.corrcoef(x, y)[0, 1]), None

    def information_ratio():
        if tracking_error == 0.0:
            return None, _NO_TRACKING_DIFFERENCE
        return float(np.mean(active) * WEEKLY_Q / tracking_error), None

    return RelativeFigures(
        beta=_figure(pair, Measure.BETA, beta),
        correlation=_figure(pair, Measure.CORRELATION, correlation),
        tracking_error=_figure(pair, Measure.TRACKING_ERROR, lambda: (tracking_error, None)),
        information_ratio=_figure(pair, Measure.INFORMATION_RATIO, information_ratio),
    )


def relative_to(
    weekly: ReturnSeries, window: Window, benchmark: BenchmarkReturns | str
) -> RelativeFigures:
    """`weekly` against `benchmark` over `window`, greyed when the benchmark doesn't cover it.
    A str is the reason there is no benchmark, which greys every figure."""
    if isinstance(benchmark, str):
        return relative_greyed(window, benchmark)
    if not benchmark_covers(benchmark.first_date, window):
        return relative_greyed(window, benchmark_too_short(benchmark.first_date))
    return relative_figures(weekly, benchmark.weekly, window)


def relative_greyed(window: Window, reason: str) -> RelativeFigures:
    """All four greyed over `window` with `reason`: a window the benchmark doesn't cover, a
    benchmark that couldn't be fetched, or a window the series itself doesn't cover."""
    figure = RiskFigure(
        value=None,
        window_start=window.start,
        window_end=window.end,
        frequency=Frequency.WEEKLY,
        q=WEEKLY_Q,
        n_obs=0,
        n_gaps=0,
        flags=frozenset({Flag.INSUFFICIENT_HISTORY}),
        reason=reason,
    )
    return RelativeFigures(figure, figure, figure, figure)


def benchmark_covers(first_date: date, window: Window) -> bool:
    return first_date <= window.start


def benchmark_too_short(first_date: date) -> str:
    return f"benchmark history too short (from {first_date.isoformat()})"


def _figure(pair: PairedWeeks, measure: Measure, compute) -> RiskFigure:
    """`compute()` gives (value, None), or (None, reason) when the value is undefined. It runs
    only when the overlap meets the measure's minimum."""
    check = check_minimum(measure, pair.n_obs, pair.start, pair.end)
    value, reason = (None, check.reason) if not check.sufficient else compute()
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


def _constant(values: np.ndarray) -> bool:
    return values.size == 0 or np.ptp(values) == 0.0
