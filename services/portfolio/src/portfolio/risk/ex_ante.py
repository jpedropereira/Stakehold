"""Ex-ante risk of today's mix (spec §6.2-6.3): the reporting layer over covariance.py, and the
constant-weight series the single-series measures run on.

- **Weights** are today's EUR market-value shares, keyed like the return series: each above 0,
  summing to 1, the same keys as the series. The caller (RK-17) builds and normalises them;
  anything else raises.
- **The decomposition runs on the listwise weekly window** (R12, listwise.py), which a late
  holding shortens; `limited_by` names it. Every figure carries that window, q = 52, the kept
  weeks as `n_obs` and the dropped ones as `n_gaps`, R8's `data_gaps` and `has_suspect_rows`.
- **Two minimums** (agreed for RK-15). σ_p greys below 52 weeks, the volatility minimum,
  because on the listwise window it equals the constant-weight series' weekly volatility
  exactly (§6.2's consistency check), and the page must never grey one while showing the
  other. The per-holding decomposition (volatility, MCR, CR, PCR, correlation with the
  portfolio) and the diversification ratio grey below 104 weeks, the covariance minimum,
  because they rest on cross-correlations. The holdings table shares the diversification
  ratio's window, flags and reason: its values are None exactly when that figure is greyed.
  The effective N comes from the weights alone and never greys.
- **The constant-weight series** r_p,t = Σ w_i r_i,t is "today's mix" through history, at the
  frequency of its inputs, over their common span. Daily feeds drawdown and VaR, weekly feeds
  volatility, Sharpe and Sortino, through single_series.py's existing layer. A holding's NULL
  makes the mix's period NULL (R5). A daily date a holding has no row for is a closed exchange
  on the union calendar and counts as 0% (R7), which is fine for drawdown; VaR masks such days
  out with `var_mask.var_days`. A weekly date a holding has no row for is a lost week, NULL,
  as listwise.py treats it.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
from portfolio.risk.covariance import covariance_matrix, decompose, effective_n
from portfolio.risk.history import HistoryCheck, Measure, check_minimum
from portfolio.risk.listwise import ListwiseReturns, listwise
from portfolio.risk.quality import WEEKLY_Q, over_gap_budget
from portfolio.risk.types import Flag, Frequency, ReturnSeries, RiskFigure, Window
from portfolio.risk.windows import common_span

_NO_VARIATION = "no variation in returns"
_WEIGHT_TOLERANCE = 1e-9


@dataclass(frozen=True)
class HoldingRisk:
    key: str
    weight: float
    volatility: float | None
    mcr: float | None
    cr: float | None
    pcr: float | None
    corr_with_portfolio: float | None


@dataclass(frozen=True)
class ExAnteDecomposition:
    sigma_p: RiskFigure
    diversification_ratio: RiskFigure
    effective_n: float
    holdings: tuple[HoldingRisk, ...]
    limited_by: tuple[str, ...]


def decomposition_figures(
    weekly: Mapping[str, ReturnSeries], weights: Mapping[str, float], window: Window
) -> ExAnteDecomposition:
    _check_weights(weekly, weights)
    panel = listwise(weekly, window)
    w = np.array([weights[key] for key in panel.keys], dtype=float)
    result = decompose(w, covariance_matrix(panel.returns, WEEKLY_Q)) if panel.n_obs >= 2 else None

    vol_check = _check(Measure.VOLATILITY, panel)
    cov_check = _check(Measure.COVARIANCE, panel)
    sigma_p = _figure(panel, vol_check, None if result is None else result.sigma_p)
    ratio = _figure(panel, cov_check, None if result is None else result.diversification_ratio)

    shown = cov_check.sufficient and result is not None
    holdings = tuple(
        HoldingRisk(
            key=key,
            weight=weights[key],
            volatility=result.volatilities[i] if shown else None,
            mcr=result.mcr[i] if shown else None,
            cr=result.cr[i] if shown else None,
            pcr=result.pcr[i] if shown else None,
            corr_with_portfolio=result.corr_with_portfolio[i] if shown else None,
        )
        for i, key in enumerate(panel.keys)
    )
    return ExAnteDecomposition(sigma_p, ratio, effective_n(w), holdings, panel.limited_by)


def constant_weight_series(
    series: Mapping[str, ReturnSeries], weights: Mapping[str, float]
) -> ReturnSeries:
    _check_weights(series, weights)
    frequencies = {s.frequency for s in series.values()}
    if len(frequencies) != 1:
        raise ValueError(
            f"a constant-weight mix needs every holding at one frequency, got "
            f"{sorted(frequencies)}"
        )
    frequency = frequencies.pop()
    span = common_span(*series.values())
    if span is None:
        base = max(s.base_date for s in series.values())
        return ReturnSeries(frequency, base, (), ())

    start, end = span
    # A missing row is a closed exchange (0%) on a daily calendar, a lost week (NULL) on a
    # weekly one.
    missing = 0.0 if frequency is Frequency.DAILY else None
    by_date = {key: dict(zip(s.dates, s.values)) for key, s in series.items()}
    dates = sorted({d for values in by_date.values() for d in values if start < d <= end})
    mixed: list[float | None] = []
    for day in dates:
        row = [(weights[key], values.get(day, missing)) for key, values in by_date.items()]
        if any(r is None for _, r in row):
            mixed.append(None)
        else:
            mixed.append(math.fsum(w * r for w, r in row))
    return ReturnSeries(frequency, start, tuple(dates), tuple(mixed))


def _check_weights(series: Mapping[str, ReturnSeries], weights: Mapping[str, float]) -> None:
    if set(weights) != set(series):
        raise ValueError(
            f"weights and returns must name the same holdings: weights {sorted(weights)}, "
            f"returns {sorted(series)}"
        )
    for key, weight in weights.items():
        if not weight > 0:
            raise ValueError(f"every weight must be positive, got {weight} for {key}")
    total = math.fsum(weights.values())
    if abs(total - 1.0) > _WEIGHT_TOLERANCE:
        raise ValueError(f"weights must sum to 1, got {total}")


def _check(measure: Measure, panel: ListwiseReturns) -> HistoryCheck:
    return check_minimum(measure, panel.n_obs, panel.window.start, panel.window.end)


def _figure(panel: ListwiseReturns, check: HistoryCheck, value: float | None) -> RiskFigure:
    flags = set(check.flags)
    if over_gap_budget(panel.n_obs, panel.n_gaps):
        flags.add(Flag.DATA_GAPS)
    if panel.has_suspect_rows:
        flags.add(Flag.HAS_SUSPECT_ROWS)
    if not check.sufficient:
        value, reason = None, check.reason
    else:
        reason = _NO_VARIATION if value is None else None
    return RiskFigure(
        value=value,
        window_start=panel.window.start,
        window_end=panel.window.end,
        frequency=Frequency.WEEKLY,
        q=WEEKLY_Q,
        n_obs=panel.n_obs,
        n_gaps=panel.n_gaps,
        flags=frozenset(flags),
        reason=reason,
    )
