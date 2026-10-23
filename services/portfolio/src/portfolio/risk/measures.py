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


def sharpe_difference_se(x1: np.ndarray, x2: np.ndarray, q: float) -> float | None:
    """The standard error of SR(x1) - SR(x2), two Sharpes on the same periods (RK-19: a
    what-if's before and after mixes). Jobson and Korkie (1981) with Memmel's (2003)
    correction, i.i.d. like `sharpe`'s SE: with per-period Sharpes SR1, SR2 and the
    correlation ρ of the two series over N periods,
    Var = (2 - 2ρ + (SR1² + SR2² - 2·SR1·SR2·ρ²) / 2) / N, annualised by √q.

    Two highly correlated series have a far smaller SE on their difference than either Sharpe
    has on its own, because their sampling errors mostly cancel. Fat tails and autocorrelation
    widen it, so treat it as a lower bound. Gaps (NaN) must fall on the same periods in both:
    the two are compared period by period."""
    x1, x2 = np.asarray(x1, dtype=float), np.asarray(x2, dtype=float)
    gaps = np.isnan(x1)
    if x1.shape != x2.shape or not np.array_equal(gaps, np.isnan(x2)):
        raise ValueError("a Sharpe difference needs both series on the same periods")
    a, b = x1[~gaps], x2[~gaps]
    # Identical values are tested directly, as in `sharpe`.
    if a.size < 2 or np.ptp(a) == 0.0 or np.ptp(b) == 0.0:
        return None
    sr1 = np.mean(a) / np.std(a, ddof=1)
    sr2 = np.mean(b) / np.std(b, ddof=1)
    rho = np.corrcoef(a, b)[0, 1]
    variance = (2.0 - 2.0 * rho + (sr1**2 + sr2**2 - 2.0 * sr1 * sr2 * rho**2) / 2.0) / a.size
    # Identical series give 0 up to float rounding, which can land just below it.
    return float(np.sqrt(q) * np.sqrt(max(variance, 0.0)))


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
