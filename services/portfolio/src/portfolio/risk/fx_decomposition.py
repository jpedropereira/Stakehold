"""FX decomposition of each non-EUR holding (RK-26, spec §6.4): how much of a holding's EUR risk
is its own market and how much its currency.

- **Four figures per holding:** local-currency volatility, FX volatility (r_fx), corr(r_local,
  r_fx) and EUR volatility. Each comes from RK-5's components (eur_returns/calculator.py) rather
  than a second conversion, compounded into W-FRI weeks like every weekly figure. The spec puts
  it on weekly returns because daily local and FX returns are sampled at different times, which
  biases their correlation toward 0.
- **On the listwise panel** (agreed for RK-26, R12): the same weeks as σ_p and the
  decomposition, so the EUR volatility is the decomposition table's volatility for that holding,
  and the page never shows two volatilities for one holding. A late holding shortens every row,
  as it shortens the decomposition, and the UI's exclude link applies to both. *Rejected:* each
  holding over its own weeks inside the requested window, as a pairwise heatmap cell is. That
  shows a longer history, but its EUR volatility would differ from the table beside it.
- **Why the panel's EUR column is enough.** Per day (1 + l)(1 + f) = 1 + e, and compounding the
  same days keeps it per week. A holding's weekly local return is NULL exactly where its EUR one
  is, so on the panel's weeks every component is known. A component missing a panel week is a
  caller bug, so it raises.
- **Minimums:** each volatility greys below 52 weeks (§4.5), and the correlation below 104,
  correlation's minimum. A constant side (a pegged currency) greys the correlation with "no
  variation in returns", never inf or 0. Every figure is built by `ex_ante.panel_figure`, so it
  carries the panel's window, n_obs, n_gaps and flags like the decomposition's.
- **A EUR holding has no row**: its r_fx is 0, so there is nothing to decompose. The caller
  passes components only for the holdings it wants decomposed.
"""

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
from portfolio.risk.ex_ante import panel_check, panel_figure
from portfolio.risk.history import Measure
from portfolio.risk.listwise import ListwiseReturns
from portfolio.risk.measures import volatility
from portfolio.risk.quality import WEEKLY_Q
from portfolio.risk.types import Frequency, ReturnSeries, RiskFigure


@dataclass(frozen=True)
class FxComponents:
    """A holding's weekly local and FX returns, from the same daily dates as its EUR ones."""

    currency: str
    local_weekly: ReturnSeries
    fx_weekly: ReturnSeries


@dataclass(frozen=True)
class FxDecompositionRow:
    ticker: str
    currency: str
    local_volatility: RiskFigure
    fx_volatility: RiskFigure
    correlation: RiskFigure  # corr(r_local, r_fx)
    eur_volatility: RiskFigure


def fx_decomposition(
    panel: ListwiseReturns, components: Mapping[str, FxComponents]
) -> tuple[FxDecompositionRow, ...]:
    """One row per holding in `components`, in the panel's order. Every key must be in the
    panel."""
    unknown = sorted(set(components) - set(panel.keys))
    if unknown:
        raise ValueError(f"FX components for holdings outside the panel: {', '.join(unknown)}")
    vol_check = panel_check(Measure.VOLATILITY, panel)
    corr_check = panel_check(Measure.CORRELATION, panel)

    rows = []
    for i, key in enumerate(panel.keys):
        if key not in components:
            continue
        parts = components[key]
        local = _on_panel(key, parts.local_weekly, panel)
        fx = _on_panel(key, parts.fx_weekly, panel)
        eur = panel.returns[:, i]

        def vol(r: np.ndarray) -> RiskFigure:
            return panel_figure(panel, vol_check, volatility(r, WEEKLY_Q) if r.size >= 2 else None)

        rows.append(
            FxDecompositionRow(
                ticker=key,
                currency=parts.currency,
                local_volatility=vol(local),
                fx_volatility=vol(fx),
                correlation=panel_figure(panel, corr_check, _correlation(local, fx)),
                eur_volatility=vol(eur),
            )
        )
    return tuple(rows)


def _on_panel(key: str, weekly: ReturnSeries, panel: ListwiseReturns) -> np.ndarray:
    if weekly.frequency is not Frequency.WEEKLY:
        raise ValueError(f"the FX decomposition is weekly (spec §6.4), got {weekly.frequency}")
    by_date = dict(zip(weekly.dates, weekly.values))
    values = [by_date.get(day) for day in panel.dates]
    if any(v is None for v in values):
        raise ValueError(f"{key}'s FX components miss a week of the listwise panel")
    return np.array(values, dtype=float)


def _correlation(x: np.ndarray, y: np.ndarray) -> float | None:
    if x.size < 2 or np.all(x == x[0]) or np.all(y == y[0]):
        return None
    return float(np.corrcoef(x, y)[0, 1])
