"""Single-series measures at any frequency (spec §5.1, §5.2, §5.4): the maths, with `q` an
explicit parameter so tests can pin it (§4.4; §5.7's golden test runs daily data at q = 252).

This layer applies no minimum, sets no flag and doesn't check the frequency: §4.3's rule that
volatility and the ratios are weekly is single_series.py's job. Inputs are float arrays with NaN
for a gap (R5), and each measure drops the gaps -- a NaN is never a 0% day.

- Standard deviations are sample ones (ddof = 1), as §5 says; downside deviation divides by N,
  all observations, not just the ones below target.
- A ratio whose denominator is 0 (no variation, no downside, no drawdown) is undefined and
  returns None, never inf: the caller greys it with a reason.
"""

from dataclasses import dataclass
from datetime import date

import numpy as np

_DAYS_PER_YEAR = 365.25


@dataclass(frozen=True)
class SharpeRatio:
    value: float
    standard_error: float


def growth_index(r: np.ndarray) -> np.ndarray:
    """G1..GN with G0 = 1 implied. A gap carries the previous level: excluding a return chains
    the same as a flat day for the level, though not for any statistic."""
    return np.cumprod(1.0 + np.nan_to_num(np.asarray(r, dtype=float), nan=0.0))


def cumulative_return(r: np.ndarray) -> float:
    return float(np.prod(1.0 + _observed(r)) - 1.0)


def cagr(cumulative: float, start: date, end: date) -> float:
    years = (end - start).days / _DAYS_PER_YEAR
    return float((1.0 + cumulative) ** (1.0 / years) - 1.0)


def volatility(r: np.ndarray, q: float) -> float:
    return float(np.std(_observed(r), ddof=1) * np.sqrt(q))


def downside_deviation(x: np.ndarray, q: float) -> float:
    """Target = the risk-free rate, so `x` is excess returns."""
    shortfall = np.minimum(_observed(x), 0.0)
    return float(np.sqrt(np.mean(shortfall**2)) * np.sqrt(q))


def sharpe(x: np.ndarray, q: float) -> SharpeRatio | None:
    """With Lo (2002)'s i.i.d. standard error, SE = √q × √((1 + SR_p²/2) / N), where SR_p is
    the per-period Sharpe. Fat tails and autocorrelation widen the true SE: treat it as a
    lower bound."""
    observed = _observed(x)
    # Identical values, tested directly: their float stdev can come out at 1e-18, not 0,
    # because the mean doesn't round-trip, and would make a Sharpe of 1e15.
    if observed.size < 2 or np.ptp(observed) == 0.0:
        return None
    per_period = np.mean(observed) / np.std(observed, ddof=1)
    return SharpeRatio(
        value=float(per_period * np.sqrt(q)),
        standard_error=float(np.sqrt(q) * np.sqrt((1.0 + per_period**2 / 2.0) / observed.size)),
    )


def sortino(x: np.ndarray, q: float) -> float | None:
    downside = downside_deviation(x, q)
    if downside == 0.0:
        return None
    return float(np.mean(_observed(x)) * q / downside)


def calmar(cagr_value: float, max_drawdown: float) -> float | None:
    if max_drawdown == 0.0:
        return None
    return cagr_value / abs(max_drawdown)


def _observed(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    return values[~np.isnan(values)]
