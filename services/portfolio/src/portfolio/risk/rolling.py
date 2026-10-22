"""Rolling windows (RK-24, spec §4.5 and §7.1): how a figure moved through history, rather than
its one value over a standard window. Pure, like the rest of the library. risk/flows.py hands in
ex-post's TWR or ex-ante's constant-weight mix.

- **Rolling 1Y volatility and Sharpe, stepped monthly** (§4.5). Each window ends on the last
  date the series has in a calendar month, and starts one calendar year before that, clamped
  from 29 February as `windows.years_before` does. The last window ends on the series' last
  date, even mid-month, so it is the standard 1Y window and its figure is the 1Y figure. A month
  appears once its window is covered (`windows.covers`): the first point is the first full
  year, never a shorter window in disguise.
- **Each point is `single_series.weekly_figures` over its window**, so a rolling figure is the
  standard figure by construction. That includes the whole weeks, rf, flags and R9 greying. It
  is also why a 1Y point holding one NULL week can be a gap: a year has 52 or 53 whole weeks,
  and the minimum is 52, the same edge the standard 1Y window has. With no risk-free series,
  Sharpe is greyed with `ex_post.NO_RISK_FREE` and volatility is kept.
- **Rolling correlation, 52 weeks stepped weekly** (§7.1). One point per Friday the pair
  shares, from the first one with 52 whole weeks since both series began. Each point is
  `correlation.pair_cell` with the rolling minimum of 52 (`Measure.ROLLING_CORRELATION`, agreed
  for RK-24) rather than §4.5's 104, which would grey every point. Like a pairwise cell, it is
  for display and never feeds portfolio maths (R13).
- **A window below its minimum is a gap, not a point** (RK-24): its figure has no value and has
  R9's reason, and it keeps its place in the sequence, so a chart breaks its line there instead
  of joining across the gap.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from portfolio.risk.correlation import pair_cell
from portfolio.risk.ex_post import NO_RISK_FREE
from portfolio.risk.history import Measure
from portfolio.risk.risk_free import RatePoint
from portfolio.risk.single_series import weekly_figures, weekly_figures_without_rf
from portfolio.risk.types import Frequency, ReturnSeries, RiskFigure, Window
from portfolio.risk.windows import covers, years_before

ROLLING_WINDOW = "1Y"
CORRELATION_WINDOW = "52W"
_CORRELATION_SPAN = timedelta(weeks=52)


@dataclass(frozen=True)
class RollingFigures:
    """One figure per monthly window, in date order, gaps included."""

    volatility: tuple[RiskFigure, ...]
    sharpe: tuple[RiskFigure, ...]


def monthly_windows(series: ReturnSeries) -> tuple[Window, ...]:
    last_in_month: dict[tuple[int, int], date] = {}
    for day in series.dates:
        last_in_month[(day.year, day.month)] = day
    windows = (Window(ROLLING_WINDOW, years_before(end, 1), end) for end in last_in_month.values())
    return tuple(window for window in windows if covers(series, window))


def rolling_weekly(
    weekly: ReturnSeries,
    windows: Sequence[Window],
    *,
    rates: Sequence[RatePoint] | None,
    first_published: date | None,
) -> RollingFigures:
    """Volatility and Sharpe of `weekly` over each window (`monthly_windows` of the same
    series' daily form). `rates` and `first_published` are None together when there is no
    risk-free series."""
    if weekly.frequency is not Frequency.WEEKLY:
        raise ValueError(
            f"rolling volatility and Sharpe need a weekly series, got {weekly.frequency}"
        )
    figures = [
        (
            weekly_figures_without_rf(weekly, window, NO_RISK_FREE)
            if rates is None
            else weekly_figures(weekly, window, rates, first_published)
        )
        for window in windows
    ]
    return RollingFigures(
        volatility=tuple(f.volatility for f in figures),
        sharpe=tuple(f.sharpe for f in figures),
    )


def rolling_correlation(a: ReturnSeries, b: ReturnSeries) -> tuple[RiskFigure, ...]:
    """ρ(a, b) over each 52 weeks ending on a Friday both have, from the first that fits."""
    first_end = max(a.base_date, b.base_date) + _CORRELATION_SPAN
    shared = sorted(d for d in set(a.dates) & set(b.dates) if d >= first_end)
    return tuple(
        pair_cell(
            a,
            b,
            Window(CORRELATION_WINDOW, end - _CORRELATION_SPAN, end),
            Measure.ROLLING_CORRELATION,
        )
        for end in shared
    )
