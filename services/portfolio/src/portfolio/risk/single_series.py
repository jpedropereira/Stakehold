"""The reporting layer over the single-series measures (spec §5.1-5.5): it turns measures.py,
drawdown.py and tail.py's numbers into `RiskFigure`s.

- **§4.3's frequency rule is enforced here.** Each function takes the series at the frequency its
  measures use and raises on the other one, so a caller can't feed a daily series to the
  volatility by accident. Cumulative return, CAGR, drawdown and VaR are daily; volatility,
  downside deviation, Sharpe and Sortino weekly. The measures below this layer are
  frequency-agnostic, which is what lets §5.7's golden test pin daily data at q = 252.
- **Each takes an unsliced series and a `Window`** and slices it, so callers loop over the
  standard windows. The series must cover the window (`windows.covers`); greying a window it
  doesn't cover is the caller's call (R9).
- **A weekly window is the whole weeks ending inside it** (`weekly.weekly_window`), and the
  figure reports that window. Its first week starts on the Friday on or before the window's
  start, when the weekly series has that week, so a one-year window holds its 52 weeks. The
  weekly series has no partial weeks at its edges (RK-6), so a daily window's end is usually
  after its last Friday. Risk-free returns run Friday to Friday, the span each week covers.
- **Every figure carries** its window, frequency, q, `n_obs` and `n_gaps` from its own slice,
  R8's `data_gaps`, and `has_suspect_rows` when a kept >100% move is inside it. Below its §4.5
  minimum it is greyed with R9's reason. An undefined ratio (no variation, no downside, no
  drawdown) is greyed with a reason too, never inf or 0.
- `nonsync_daily` is the caller's to say: the library doesn't know which markets or currencies
  a series mixes.
"""

from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import date

import numpy as np
from portfolio.risk.drawdown import drawdown
from portfolio.risk.history import HistoryCheck, Measure, check_minimum, frequency_of
from portfolio.risk.measures import (
    cagr,
    calmar,
    cumulative_return,
    downside_deviation,
    sharpe,
    sortino,
    volatility,
)
from portfolio.risk.quality import (
    WEEKLY_Q,
    gap_stats,
    has_suspect_rows,
    over_gap_budget,
    periods_per_year,
)
from portfolio.risk.risk_free import RatePoint, risk_free_returns
from portfolio.risk.tail import var_cvar
from portfolio.risk.types import (
    Flag,
    Frequency,
    ReturnSeries,
    RiskFigure,
    Window,
    to_array,
)
from portfolio.risk.var_mask import var_days
from portfolio.risk.weekly import weekly_window
from portfolio.risk.windows import slice_series

_NO_VARIATION = "no variation in excess returns"
_NO_DOWNSIDE = "no returns below the risk-free rate"
_NO_DRAWDOWN = "no drawdown in window"


@dataclass(frozen=True)
class ReturnFigures:
    cumulative_return: RiskFigure
    cagr: RiskFigure


@dataclass(frozen=True)
class DrawdownFigures:
    """The dates and time under water are None when the max drawdown is greyed. The DD series
    is always there, base date first, for a chart over any period."""

    max_drawdown: RiskFigure
    peak_date: date | None
    trough_date: date | None
    recovery_date: date | None
    max_days_under_water: int | None
    series: tuple[tuple[date, float | None], ...]


@dataclass(frozen=True)
class RegimeFigures:
    cumulative_return: RiskFigure
    max_drawdown: RiskFigure


@dataclass(frozen=True)
class WeeklyFigures:
    volatility: RiskFigure
    downside_deviation: RiskFigure
    sharpe: RiskFigure
    sortino: RiskFigure


@dataclass(frozen=True)
class TailFigures:
    var_95: RiskFigure
    cvar_95: RiskFigure
    var_99: RiskFigure
    cvar_99: RiskFigure


@dataclass(frozen=True)
class _Slice:
    series: ReturnSeries
    window: Window
    q: float
    n_obs: int
    n_gaps: int
    flags: frozenset[Flag]

    def figure(
        self,
        value: float | None,
        check: HistoryCheck,
        reason: str | None = None,
        flags: AbstractSet[Flag] = frozenset(),
        standard_error: float | None = None,
    ) -> RiskFigure:
        """`reason` greys a figure whose history is sufficient but whose value is undefined."""
        if not check.sufficient:
            value, reason, standard_error = None, check.reason, None
        elif value is None and reason is None:
            raise ValueError("a figure without a value needs a reason (R9)")
        return RiskFigure(
            value=value,
            window_start=self.window.start,
            window_end=self.window.end,
            frequency=self.series.frequency,
            q=self.q,
            n_obs=self.n_obs,
            n_gaps=self.n_gaps,
            flags=self.flags | check.flags | flags,
            reason=reason if value is None else None,
            standard_error=standard_error,
        )


def return_figures(daily: ReturnSeries, window: Window) -> ReturnFigures:
    """Cumulative return, and CAGR from one year (§5.1): under a year only the cumulative
    return is shown, and CAGR carries `short_window` under three."""
    _require(daily, Measure.CAGR)
    part = _slice(daily, window)
    if part.n_obs == 0:
        nothing = HistoryCheck(
            "insufficient history (have 0, need 1)", frozenset({Flag.INSUFFICIENT_HISTORY})
        )
        return ReturnFigures(part.figure(None, nothing), part.figure(None, nothing))

    cumulative = cumulative_return(to_array(part.series))
    return ReturnFigures(
        cumulative_return=part.figure(cumulative, HistoryCheck(None, frozenset())),
        cagr=part.figure(cagr(cumulative, window.start, window.end), _check(Measure.CAGR, part)),
    )


def drawdown_figures(daily: ReturnSeries, window: Window) -> DrawdownFigures:
    _require(daily, Measure.MAX_DRAWDOWN)
    part = _slice(daily, window)
    result = drawdown(part.series)
    figure = part.figure(result.max_drawdown, _check(Measure.MAX_DRAWDOWN, part))
    if figure.value is None:
        return DrawdownFigures(figure, None, None, None, None, result.series)
    return DrawdownFigures(
        figure,
        result.peak_date,
        result.trough_date,
        result.recovery_date,
        result.max_days_under_water,
        result.series,
    )


def regime_figures(daily: ReturnSeries, window: Window) -> RegimeFigures:
    """Cumulative return and max drawdown inside one regime (RK-28), which the regime view
    describes rather than estimates (§11): no §4.5 minimum beyond one return, and the drawdown
    flagged `short_regime` below its usual 200 obs, since it is then a single short path. No
    drawdown in the window is a real 0, as in `drawdown_figures`."""
    _require(daily, Measure.REGIME)
    part = _slice(daily, window)
    check = _check(Measure.REGIME, part)
    if not check.sufficient:
        return RegimeFigures(part.figure(None, check), part.figure(None, check))
    short = frozenset(
        {Flag.SHORT_REGIME} if not _check(Measure.MAX_DRAWDOWN, part).sufficient else ()
    )
    return RegimeFigures(
        cumulative_return=part.figure(cumulative_return(to_array(part.series)), check),
        max_drawdown=part.figure(drawdown(part.series).max_drawdown, check, flags=short),
    )


def weekly_figures(
    weekly: ReturnSeries, window: Window, rates: Sequence[RatePoint], first_published: date
) -> WeeklyFigures:
    """Volatility, downside deviation, Sharpe ± SE and Sortino (§5.2, §5.4) on excess returns
    over the ACT/360 risk-free rate (§4.1). `rf_partial` marks a window starting before the
    rate series does (R4)."""
    part = _weekly_slice(weekly, window)
    r = to_array(part.series)
    x, rf_flags = _excess(part, rates, first_published)

    vol_check = _check(Measure.VOLATILITY, part)
    if not vol_check.sufficient:
        # Every weekly measure has the same minimum; don't compute on too few points.
        return WeeklyFigures(
            part.figure(None, vol_check),
            part.figure(None, _check(Measure.DOWNSIDE_DEVIATION, part), flags=rf_flags),
            part.figure(None, _check(Measure.SHARPE, part), flags=rf_flags),
            part.figure(None, _check(Measure.SORTINO, part), flags=rf_flags),
        )

    ratio = sharpe(x, part.q)
    sortino_value = sortino(x, part.q)
    return WeeklyFigures(
        volatility=part.figure(volatility(r, part.q), vol_check),
        downside_deviation=part.figure(
            downside_deviation(x, part.q), _check(Measure.DOWNSIDE_DEVIATION, part), flags=rf_flags
        ),
        sharpe=part.figure(
            None if ratio is None else ratio.value,
            _check(Measure.SHARPE, part),
            reason=_NO_VARIATION,
            flags=rf_flags,
            standard_error=None if ratio is None else ratio.standard_error,
        ),
        sortino=part.figure(
            sortino_value, _check(Measure.SORTINO, part), reason=_NO_DOWNSIDE, flags=rf_flags
        ),
    )


def weekly_excess(
    weekly: ReturnSeries, window: Window, rates: Sequence[RatePoint], first_published: date
) -> np.ndarray:
    """The excess returns `weekly_figures` measures over `window`, NaN on a NULL week. The
    what-if (RK-19) compares two mixes' Sharpes week by week on these."""
    return _excess(_weekly_slice(weekly, window), rates, first_published)[0]


def _excess(
    part: _Slice, rates: Sequence[RatePoint], first_published: date
) -> tuple[np.ndarray, frozenset[Flag]]:
    r = to_array(part.series)
    if not part.series.dates:
        return r, frozenset()
    rf = risk_free_returns(rates, first_published, (part.series.base_date, *part.series.dates))
    return r - rf.values, frozenset({Flag.RF_PARTIAL} if rf.partial else ())


def weekly_figures_without_rf(weekly: ReturnSeries, window: Window, reason: str) -> WeeklyFigures:
    """`weekly_figures` when there is no risk-free series to subtract (none stored, or the
    fetch failed): volatility is shown, since it doesn't use rf, and downside deviation, Sharpe
    and Sortino are greyed with `reason`. Computing them against rf = 0 instead would show a
    number that means something else (RK-4). Below 52 weeks every figure takes R9's reason."""
    part = _weekly_slice(weekly, window)
    vol_check = _check(Measure.VOLATILITY, part)
    volatility_value = volatility(to_array(part.series), part.q) if vol_check.sufficient else None
    return WeeklyFigures(
        volatility=part.figure(volatility_value, vol_check),
        downside_deviation=part.figure(
            None, _check(Measure.DOWNSIDE_DEVIATION, part), reason=reason
        ),
        sharpe=part.figure(None, _check(Measure.SHARPE, part), reason=reason),
        sortino=part.figure(None, _check(Measure.SORTINO, part), reason=reason),
    )


def calmar_figure(cagr_figure: RiskFigure, max_drawdown: RiskFigure) -> RiskFigure:
    """CAGR / |MDD| over one window (§5.4), from `return_figures` and `drawdown_figures`.
    Calmar's own minimum (one year, `short_window` under three) is CAGR's, so a greyed CAGR or
    drawdown greys it with that input's reason."""
    if (cagr_figure.window_start, cagr_figure.window_end) != (
        max_drawdown.window_start,
        max_drawdown.window_end,
    ):
        raise ValueError(
            f"Calmar needs CAGR and max drawdown over one window: CAGR "
            f"{cagr_figure.window_start}..{cagr_figure.window_end}, drawdown "
            f"{max_drawdown.window_start}..{max_drawdown.window_end}"
        )
    value = None
    if cagr_figure.value is None:
        reason = cagr_figure.reason
    elif max_drawdown.value is None:
        reason = max_drawdown.reason
    else:
        value = calmar(cagr_figure.value, max_drawdown.value)
        reason = None if value is not None else _NO_DRAWDOWN
    return RiskFigure(
        value=value,
        window_start=cagr_figure.window_start,
        window_end=cagr_figure.window_end,
        frequency=Frequency.DAILY,
        q=cagr_figure.q,
        n_obs=cagr_figure.n_obs,
        n_gaps=cagr_figure.n_gaps,
        flags=cagr_figure.flags | max_drawdown.flags,
        reason=reason,
    )


def tail_figures(
    daily: ReturnSeries,
    window: Window,
    trading_days: Sequence[AbstractSet[date]],
    nonsync: bool,
) -> TailFigures:
    """1-day VaR and CVaR at 95% and 99% (§5.5), on the days every included asset traded (R7,
    `var_mask.var_days`): one set of trading days per asset, a single asset passing its own.
    `nonsync` is True for a series mixing markets or currencies, including a non-EUR asset in
    EUR, and sets `nonsync_daily` (§4.3)."""
    _require(daily, Measure.VAR_95)
    sliced = _slice(daily, window)
    mask = set(var_days(trading_days, window))
    kept = [(d, v) for d, v in zip(sliced.series.dates, sliced.series.values) if d in mask]
    masked = ReturnSeries(
        Frequency.DAILY, window.start, tuple(d for d, _ in kept), tuple(v for _, v in kept)
    )
    part = _with_quality(masked, window, sliced.q)
    nonsync_flags = frozenset({Flag.NONSYNC_DAILY} if nonsync else ())
    r = to_array(masked)

    def at(alpha: float, measure: Measure) -> tuple[RiskFigure, RiskFigure]:
        check = _check(measure, part)
        tail = var_cvar(r, alpha) if check.sufficient else None
        return (
            part.figure(None if tail is None else tail.var, check, flags=nonsync_flags),
            part.figure(None if tail is None else tail.cvar, check, flags=nonsync_flags),
        )

    var_95, cvar_95 = at(0.95, Measure.VAR_95)
    var_99, cvar_99 = at(0.99, Measure.VAR_99)
    return TailFigures(var_95, cvar_95, var_99, cvar_99)


def _require(series: ReturnSeries, measure: Measure) -> None:
    expected = frequency_of(measure)
    if series.frequency is not expected:
        raise ValueError(
            f"{measure} is computed on a {expected} series (spec §4.3), got a "
            f"{series.frequency} one"
        )


def _weekly_slice(weekly: ReturnSeries, window: Window) -> _Slice:
    _require(weekly, Measure.VOLATILITY)
    whole_weeks = weekly_window(window, weekly.base_date)
    if whole_weeks is None:
        return _with_quality(ReturnSeries(Frequency.WEEKLY, window.start, (), ()), window, WEEKLY_Q)
    return _slice(weekly, whole_weeks)


def _slice(series: ReturnSeries, window: Window) -> _Slice:
    sliced = slice_series(series, window)
    # A daily q is empirical and undefined without an observation; such a figure is greyed.
    empty_daily = not sliced.dates and sliced.frequency is Frequency.DAILY
    q = 0.0 if empty_daily else periods_per_year(sliced)
    return _with_quality(sliced, window, q)


def _with_quality(series: ReturnSeries, window: Window, q: float) -> _Slice:
    n_obs, n_gaps = gap_stats(series)
    flags = set()
    if over_gap_budget(n_obs, n_gaps):
        flags.add(Flag.DATA_GAPS)
    if has_suspect_rows(series):
        flags.add(Flag.HAS_SUSPECT_ROWS)
    return _Slice(series, window, q, n_obs, n_gaps, frozenset(flags))


def _check(measure: Measure, part: _Slice) -> HistoryCheck:
    return check_minimum(measure, part.n_obs, part.window.start, part.window.end)
