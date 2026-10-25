"""Minimum history per measure (spec §4.5) and the frequency each is computed at (§4.3).

Below its minimum a measure is greyed with "insufficient history (have N, need M)" (R9), never
shown as a number. Most minimums count non-NULL observations at the measure's frequency. CAGR
and Calmar need a calendar span of one year instead, and carry `short_window` under three.
VaR 99% carries `tail_unstable` under 1,000 observations, where its CVaR averages fewer than 10
points.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from portfolio.risk.types import Flag, Frequency
from portfolio.risk.windows import years_before


class Measure(StrEnum):
    VOLATILITY = "volatility"
    DOWNSIDE_DEVIATION = "downside_deviation"
    SHARPE = "sharpe"
    SORTINO = "sortino"
    MAX_DRAWDOWN = "max_drawdown"
    CAGR = "cagr"
    CALMAR = "calmar"
    VAR_95 = "var_95"
    VAR_99 = "var_99"
    CORRELATION = "correlation"
    BETA = "beta"
    COVARIANCE = "covariance"
    TRACKING_ERROR = "tracking_error"
    INFORMATION_RATIO = "information_ratio"
    ROLLING_CORRELATION = "rolling_correlation"
    REGIME = "regime"


@dataclass(frozen=True)
class _Minimum:
    frequency: Frequency
    min_obs: int | None = None
    min_years: int | None = None


_MINIMUMS = {
    Measure.VOLATILITY: _Minimum(Frequency.WEEKLY, min_obs=52),
    Measure.DOWNSIDE_DEVIATION: _Minimum(Frequency.WEEKLY, min_obs=52),
    Measure.SHARPE: _Minimum(Frequency.WEEKLY, min_obs=52),
    Measure.SORTINO: _Minimum(Frequency.WEEKLY, min_obs=52),
    Measure.MAX_DRAWDOWN: _Minimum(Frequency.DAILY, min_obs=200),
    Measure.CAGR: _Minimum(Frequency.DAILY, min_years=1),
    Measure.CALMAR: _Minimum(Frequency.DAILY, min_years=1),
    Measure.VAR_95: _Minimum(Frequency.DAILY, min_obs=250),
    Measure.VAR_99: _Minimum(Frequency.DAILY, min_obs=500),
    Measure.CORRELATION: _Minimum(Frequency.WEEKLY, min_obs=104),
    Measure.BETA: _Minimum(Frequency.WEEKLY, min_obs=104),
    Measure.COVARIANCE: _Minimum(Frequency.WEEKLY, min_obs=104),
    # §4.5 doesn't list these two. Agreed for RK-13: tracking error is the volatility of the
    # active return and the information ratio its Sharpe, so they take those minimums.
    Measure.TRACKING_ERROR: _Minimum(Frequency.WEEKLY, min_obs=52),
    Measure.INFORMATION_RATIO: _Minimum(Frequency.WEEKLY, min_obs=52),
    # Agreed for RK-24: §7.1's rolling correlation runs over 52 weekly obs, which §4.5's 104
    # would grey at every point. It is for display only and never feeds portfolio maths (R13).
    Measure.ROLLING_CORRELATION: _Minimum(Frequency.WEEKLY, min_obs=52),
    # Agreed for RK-28: a regime's cumulative return and max drawdown are descriptive (§11), and
    # §4.5's 200 obs for a drawdown would grey most regimes (a crash lasts weeks). One return is
    # enough; the drawdown carries `short_regime` under 200 instead (single_series.py).
    Measure.REGIME: _Minimum(Frequency.DAILY, min_obs=1),
}

_SHORT_WINDOW_YEARS = 3
_TAIL_STABLE_OBS = 1000


@dataclass(frozen=True)
class HistoryCheck:
    reason: str | None
    flags: frozenset[Flag]

    @property
    def sufficient(self) -> bool:
        return self.reason is None


def frequency_of(measure: Measure) -> Frequency:
    return _MINIMUMS[measure].frequency


def check_minimum(measure: Measure, n_obs: int, start: date, end: date) -> HistoryCheck:
    """`n_obs` counts non-NULL observations at the measure's frequency; `start` and `end` are
    the window's, used by the span-based measures."""
    minimum = _MINIMUMS[measure]
    if minimum.min_years is not None:
        if start > years_before(end, minimum.min_years):
            return _insufficient(f"have {(end - start).days} days, need {minimum.min_years} year")
        short = start > years_before(end, _SHORT_WINDOW_YEARS)
        return HistoryCheck(None, frozenset({Flag.SHORT_WINDOW} if short else ()))

    if n_obs < minimum.min_obs:
        return _insufficient(f"have {n_obs}, need {minimum.min_obs}")
    unstable = measure is Measure.VAR_99 and n_obs < _TAIL_STABLE_OBS
    return HistoryCheck(None, frozenset({Flag.TAIL_UNSTABLE} if unstable else ()))


def _insufficient(detail: str) -> HistoryCheck:
    return HistoryCheck(f"insufficient history ({detail})", frozenset({Flag.INSUFFICIENT_HISTORY}))
