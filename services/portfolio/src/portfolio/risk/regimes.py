"""The regime view (RK-28, spec §11 phase 5): for each regime overlapping a daily series, the
series' and the benchmark's cumulative return and max drawdown inside it. Descriptive, so
`known_from` isn't applied.

- **A regime is measured as a `Window` from its start date to its end date**: returns dated
  after the start and on or before the end, from the level on the start. A drawdown regime
  starts on its peak close, so this is its peak-to-trough; a calendar regime (an NBER month)
  starts from the close on its first day, and a start on a closed day takes the last close.
- **Clipped to the series and flagged.** A regime the series only partly covers is measured on
  the overlap and is `partial`; one with no end runs to the series' last date and is
  `ongoing`. A regime with no return dated inside the series -- data-store's overlap is
  inclusive, so one can end on the series' base date -- is dropped.
- **The benchmark is measured on the same clipped window**, and greyed when it doesn't cover it
  (§5.6's wording) or couldn't be fetched (the caller's reason). It is never replaced.

Pure: the caller fetches the regimes and joins their names and dimensions back on
`regime_id`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from portfolio.risk.relative import benchmark_too_short
from portfolio.risk.single_series import RegimeFigures, regime_figures
from portfolio.risk.types import Flag, Frequency, ReturnSeries, RiskFigure, Window
from portfolio.risk.windows import covers


@dataclass(frozen=True)
class RegimePeriod:
    regime_id: str
    start: date
    end: date | None  # None while the regime is ongoing

    def __post_init__(self):
        if self.end is not None and self.end < self.start:
            raise ValueError(f"regime {self.regime_id} ends {self.end}, before its start")


@dataclass(frozen=True)
class RegimeRow:
    regime_id: str
    measured: Window  # the regime clipped to the series
    partial: bool  # the series covers only part of the regime
    ongoing: bool  # the regime has no end date
    portfolio: RegimeFigures
    benchmark: RegimeFigures


def regime_rows(
    series: ReturnSeries, periods: Sequence[RegimePeriod], benchmark: ReturnSeries | str
) -> tuple[RegimeRow, ...]:
    """One row per period with a return dated inside `series`, in the periods' order.
    `benchmark` is its daily series, or why there is none."""
    rows = []
    for period in periods:
        end = series.last_date if period.end is None else period.end
        start, clipped_end = max(period.start, series.base_date), min(end, series.last_date)
        if start >= clipped_end:
            continue
        window = Window(period.regime_id, start, clipped_end)
        rows.append(
            RegimeRow(
                regime_id=period.regime_id,
                measured=window,
                partial=(window.start, window.end) != (period.start, end),
                ongoing=period.end is None,
                portfolio=regime_figures(series, window),
                benchmark=_benchmark(benchmark, window),
            )
        )
    return tuple(rows)


def _benchmark(benchmark: ReturnSeries | str, window: Window) -> RegimeFigures:
    if isinstance(benchmark, str):
        return _greyed(window, benchmark)
    if covers(benchmark, window):
        return regime_figures(benchmark, window)
    if benchmark.base_date > window.start:
        return _greyed(window, benchmark_too_short(benchmark.base_date))
    return _greyed(window, f"benchmark history too short (to {benchmark.last_date.isoformat()})")


def _greyed(window: Window, reason: str) -> RegimeFigures:
    figure = RiskFigure(
        value=None,
        window_start=window.start,
        window_end=window.end,
        frequency=Frequency.DAILY,
        q=0.0,  # a daily q is empirical and undefined without observations
        n_obs=0,
        n_gaps=0,
        flags=frozenset({Flag.INSUFFICIENT_HISTORY}),
        reason=reason,
    )
    return RegimeFigures(figure, figure)
