"""The backtest's risk table (RK-8): spec §5.1-5.5 per standard window for the buy-and-hold mix
and for each asset, plus the mix's drawdown series for the growth chart. The rules are in
docs/domain/risk.md, cited by section and rule number.

Pure, like labs/calculator.py: the caller fetches the risk-free series and hands it in. This is
the bridge between Labs and the risk library: risk/ imports nothing from portfolio, so the code
that turns Labs' series into `ReturnSeries` lives here. The measures themselves are
risk/single_series.py's.

The rules, as agreed for RK-8 (tasks-risk-thread.md):

- **Windows** are the spec's 1Y, 3Y, 5Y, 10Y and Max (§4.5), not Labs' periods, so periods under
  a year never get risk figures. They all end on Labs' end date, the last day every asset has.
- **One common window for every row (R10).** Max starts on the latest first day among the
  assets, and `limited_by` names whose. A window starting before that is uncovered on every row,
  the mix's and each asset's, with "limited by X (history from <date>)" and no figures. An asset
  with ten years of history therefore shows no 10Y beside a two-year-old one: the table compares
  like with like, and Labs' period table still shows each asset's own returns. Showing each row
  over whatever it covers was the alternative; it was rejected because one table would then mix
  rows over different windows. A consequence is that every covered window holds every asset, so
  the mix here needs neither LB-9's re-weighting nor Max's rebalancing as assets join.
- **The mix drifts (R3).** It is bought at its weights on each window's start and held:
  V(t) = Σ wᵢ · Lᵢ(t) / Lᵢ(start), on the union calendar, with Lᵢ the asset's EUR level exactly
  as Labs prices it (native index over FX, each on or before the date). Its daily return is
  V(t) / V(t−1) − 1. A day an asset didn't trade carries its level forward, a 0% move (R7), which
  VaR's day mask removes. The levels are converted to floats once and weighted with numpy:
  per-day Decimal sums over five windows and up to 50 assets would cost seconds, and float
  rounding is far below a risk figure's sampling error. A unit test checks that the 1Y and 5Y
  cumulative returns match Labs' Decimal ones.
- **Gaps (R5).** The mix's return on a day any asset's return is NULL is a gap, None, while the
  levels stay bridged as Labs builds them. An asset's own row is its r_eur from eur_returns/
  (spec stream 1) on its own calendar, NULLs kept.
- **Frequencies (§4.3).** Cumulative return, CAGR, drawdown and VaR are daily. Volatility,
  downside deviation, Sharpe and Sortino are weekly, from the same daily series compounded to
  W-FRI weeks (R6). The mix's weekly series is re-based with its daily one, on the Friday after
  the window starts.
- **VaR (§5.5)** uses the days every asset has a price row (R7, var_mask.py), and carries
  `nonsync_daily` when the row includes a non-EUR asset. `TickerInfo` has no exchange, so the
  rule is by currency: EUR-listed assets close at about the same time, and anything else is
  FX-converted at a time that matches neither close.
- **No risk-free data** (none stored, or the fetch failed) greys downside deviation, Sharpe and
  Sortino with the caller's reason. They are never computed against rf = 0 (RK-4).
- **The drawdown chart** is the mix's only, over the requested growth period, on Labs' own growth
  line, so it lines up date for date with the chart above it, including LB-9's members and Max's
  joins. Its points are None on the dates a member's return is NULL, so the line breaks where a
  move was lost. Measuring it on the gap-excluded returns instead would let it drift away from
  the growth line after each gap.
"""

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from portfolio.labs.calculator import (
    AssetInput,
    AssetSeries,
    BacktestResult,
    GrowthSeries,
    Period,
    Unavailable,
    build_series,
)
from portfolio.risk.risk_free import RatePoint
from portfolio.risk.single_series import (
    calmar_figure,
    drawdown_figures,
    return_figures,
    tail_figures,
    weekly_figures,
    weekly_figures_without_rf,
)
from portfolio.risk.types import Frequency, ReturnSeries, RiskFigure, Window
from portfolio.risk.weekly import to_weekly
from portfolio.risk.windows import StandardWindow, standard_windows, years_before

LABEL = "buy & hold (drifting)"
NO_OVERLAP = "the assets' histories don't overlap"

# A risk-free return starting on a date uses the rate published on or before it, so the fetch
# starts early enough to find one across a long holiday closure.
_RF_LOOKBACK = timedelta(days=10)
_YEARS = {StandardWindow.Y1: 1, StandardWindow.Y3: 3, StandardWindow.Y5: 5, StandardWindow.Y10: 10}


@dataclass(frozen=True)
class RiskFree:
    rates: Sequence[RatePoint]
    first_published: date  # the series' own first date, for R4's rf_partial


@dataclass(frozen=True)
class RiskFreeMissing:
    reason: str  # what greys the excess-return figures


@dataclass(frozen=True)
class WindowRisk:
    """One window of one row. Uncovered: `reason` says why, and every figure is None. Covered:
    each figure is a RiskFigure, greyed with its own reason where it must be (R9)."""

    start: date | None  # None only for Max when the histories don't overlap
    end: date
    covered: bool
    reason: str | None = None
    cumulative_return: RiskFigure | None = None
    cagr: RiskFigure | None = None
    volatility: RiskFigure | None = None
    downside_deviation: RiskFigure | None = None
    sharpe: RiskFigure | None = None
    sortino: RiskFigure | None = None
    calmar: RiskFigure | None = None
    max_drawdown: RiskFigure | None = None
    peak_date: date | None = None
    trough_date: date | None = None
    recovery_date: date | None = None
    max_days_under_water: int | None = None
    var_95: RiskFigure | None = None
    cvar_95: RiskFigure | None = None
    var_99: RiskFigure | None = None
    cvar_99: RiskFigure | None = None


@dataclass(frozen=True)
class RowRisk:
    ticker: str | None  # None for the mix
    windows: dict[StandardWindow, WindowRisk]


@dataclass(frozen=True)
class DrawdownSeries:
    period: Period
    dates: list[date]  # the growth series' own dates
    values: list[float | None]  # None on a member's gap date


@dataclass(frozen=True)
class BacktestRisk:
    label: str
    end_date: date
    common_start: date | None  # None when the histories don't overlap
    limited_by: list[str]  # the asset(s) whose first day is the common start
    risk_free_reason: str | None  # None when the risk-free series was available
    risk_free_first_date: date | None
    mix: RowRisk
    assets: list[RowRisk]  # available assets, in input order
    drawdown: DrawdownSeries | None  # None when the mix has no growth line


def risk_free_span(result: BacktestResult) -> tuple[date, date] | None:
    """The range of risk-free rates compute_backtest_risk needs for `result`: from just before
    the common start to the end. None when no window can be covered."""
    if result.end_date is None or not result.assets:
        return None
    common_start = max(row.periods[Period.MAX].start for row in result.assets)
    if common_start >= result.end_date:
        return None
    return common_start - _RF_LOOKBACK, result.end_date


def compute_backtest_risk(
    assets: Sequence[AssetInput],
    risk_free: RiskFree | RiskFreeMissing,
    growth: GrowthSeries | None,
) -> BacktestRisk | None:
    """The risk table for the assets compute_backtest was given, and the drawdown of its
    `growth` series. None when no asset is available, as compute_backtest has no figures then."""
    series = [s for s in map(build_series, assets) if not isinstance(s, Unavailable)]
    if not series:
        return None

    total = sum(s.weight for s in series)
    weights = {s.ticker: float(s.weight / total) for s in series}
    trading = {a.ticker: frozenset(p.date for p in a.prices) for a in assets}
    end = min(s.dates[-1] for s in series)
    common_start = max(s.dates[0] for s in series)
    limited_by = [s.ticker for s in series if s.dates[0] == common_start]
    overlap = common_start < end
    windows = _windows(end, common_start if overlap else None)
    if overlap:
        uncovered = f"limited by {', '.join(limited_by)} (history from {common_start.isoformat()})"
    else:
        uncovered = NO_OVERLAP

    def row(ticker, daily_for, days, nonsync):
        """daily_for(window) gives the row's daily series for one window."""
        out = {}
        for label, start, window in windows:
            if window is None or window.start < common_start:
                out[label] = WindowRisk(start=start, end=end, covered=False, reason=uncovered)
            else:
                out[label] = _measure(daily_for(window), window, risk_free, days, nonsync)
        return RowRisk(ticker=ticker, windows=out)

    levels = {s.ticker: _EurLevels(s) for s in series}
    mix = row(
        None,
        lambda window: _mix_daily(series, levels, weights, window),
        [trading[s.ticker] for s in series],
        any(s.fx is not None for s in series),
    )
    asset_rows = []
    for s in series:
        daily = _asset_daily(s)
        asset_rows.append(
            row(s.ticker, lambda _window, d=daily: d, [trading[s.ticker]], s.fx is not None)
        )

    return BacktestRisk(
        label=LABEL,
        end_date=end,
        common_start=common_start if overlap else None,
        limited_by=limited_by if overlap else [],
        risk_free_reason=risk_free.reason if isinstance(risk_free, RiskFreeMissing) else None,
        risk_free_first_date=(
            risk_free.first_published if isinstance(risk_free, RiskFree) else None
        ),
        mix=mix,
        assets=asset_rows,
        drawdown=_drawdown_series(growth, series),
    )


def _windows(
    end: date, common_start: date | None
) -> list[tuple[StandardWindow, date | None, Window | None]]:
    """(label, start, window) for every standard window. The window is None only when the
    histories don't overlap, so there is no Max to build."""
    if common_start is not None:
        return [(w.label, w.start, w) for w in standard_windows(end, common_start)]
    return [
        (label, None if label is StandardWindow.MAX else years_before(end, _YEARS[label]), None)
        for label in StandardWindow
    ]


def _measure(
    daily: ReturnSeries,
    window: Window,
    risk_free: RiskFree | RiskFreeMissing,
    trading_days: Sequence[AbstractSet[date]],
    nonsync: bool,
) -> WindowRisk:
    weekly = to_weekly(daily)
    returns = return_figures(daily, window)
    drawdown = drawdown_figures(daily, window)
    if isinstance(risk_free, RiskFree):
        ratios = weekly_figures(weekly, window, risk_free.rates, risk_free.first_published)
    else:
        ratios = weekly_figures_without_rf(weekly, window, risk_free.reason)
    tail = tail_figures(daily, window, trading_days, nonsync)
    return WindowRisk(
        start=window.start,
        end=window.end,
        covered=True,
        cumulative_return=returns.cumulative_return,
        cagr=returns.cagr,
        volatility=ratios.volatility,
        downside_deviation=ratios.downside_deviation,
        sharpe=ratios.sharpe,
        sortino=ratios.sortino,
        calmar=calmar_figure(returns.cagr, drawdown.max_drawdown),
        max_drawdown=drawdown.max_drawdown,
        peak_date=drawdown.peak_date,
        trough_date=drawdown.trough_date,
        recovery_date=drawdown.recovery_date,
        max_days_under_water=drawdown.max_days_under_water,
        var_95=tail.var_95,
        cvar_95=tail.cvar_95,
        var_99=tail.var_99,
        cvar_99=tail.cvar_99,
    )


class _EurLevels:
    """An asset's EUR level on any date from its anchor on, as floats: its native index and its
    FX rate, each the latest on or before the date, as labs/calculator.py prices it."""

    def __init__(self, s: AssetSeries):
        self._days = np.array([d.toordinal() for d in s.dates])
        self._native = np.array([float(v) for v in s.native])
        if s.fx is None:
            self._fx_days = self._fx = None
        else:
            self._fx_days = np.array([d.toordinal() for d in s.fx.dates])
            self._fx = np.array([float(v) for v in s.fx.rates])

    def on(self, days: np.ndarray) -> np.ndarray:
        native = self._native[np.searchsorted(self._days, days, side="right") - 1]
        if self._fx is None:
            return native
        return native / self._fx[np.searchsorted(self._fx_days, days, side="right") - 1]


def _mix_daily(
    series: Sequence[AssetSeries],
    levels: Mapping[str, _EurLevels],
    weights: Mapping[str, float],
    window: Window,
) -> ReturnSeries:
    """The mix bought on the window's start and held, as daily returns on the union calendar."""
    days = sorted({d for s in series for d in s.dates if window.start < d <= window.end})
    points = np.array([window.start.toordinal(), *(d.toordinal() for d in days)])
    value = np.zeros(len(points))
    for s in series:
        level = levels[s.ticker].on(points)
        value += weights[s.ticker] * level / level[0]
    returns = value[1:] / value[:-1] - 1.0
    gaps = {d for s in series for d in s.gap_dates}
    values = tuple(None if d in gaps else float(r) for d, r in zip(days, returns))
    return ReturnSeries(Frequency.DAILY, window.start, tuple(days), values)


def _asset_daily(s: AssetSeries) -> ReturnSeries:
    """The asset's own r_eur, NULLs kept: spec stream 1."""
    r = s.returns
    return ReturnSeries(
        Frequency.DAILY,
        r.anchor,
        tuple(r.dates),
        tuple(None if v is None else float(v) for v in r.r_eur),
    )


def _drawdown_series(
    growth: GrowthSeries | None, series: Sequence[AssetSeries]
) -> DrawdownSeries | None:
    if growth is None or growth.mix is None:
        return None
    members = {ticker for ticker, line in growth.assets.items() if line is not None}
    gaps = {d for s in series if s.ticker in members for d in s.gap_dates}
    levels = [1.0 + float(g) for g in growth.mix]
    bridged = ReturnSeries(
        Frequency.DAILY,
        growth.dates[0],
        tuple(growth.dates[1:]),
        tuple(after / before - 1.0 for before, after in zip(levels, levels[1:])),
    )
    window = Window(growth.period, growth.dates[0], growth.dates[-1])
    points = drawdown_figures(bridged, window).series
    return DrawdownSeries(
        period=growth.period,
        dates=[d for d, _ in points],
        values=[None if d in gaps else v for d, v in points],
    )
