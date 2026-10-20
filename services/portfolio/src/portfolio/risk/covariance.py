"""Ex-ante covariance and risk decomposition (spec §6.2-6.3): the maths, at any frequency, with
`q` an explicit parameter as in measures.py.

- **Σ is the sample covariance (ddof = 1) × q** of returns with no gap: the listwise window
  (R12, listwise.py) has already dropped every week on which any holding was NULL. A NaN here is
  a caller bug, not a gap to skip, so it raises.
- **σ_p = √(wᵀΣw).** For each holding, MCR = (Σw)_i / σ_p, CR = w_i × MCR_i (summing to σ_p),
  PCR = CR_i / σ_p (summing to 1, negative for a hedge), and its correlation with the portfolio
  (Σw)_i / (σ_i σ_p). The diversification ratio is Σ w_i σ_i / σ_p.
- **Undefined is None, never inf or 0**: σ_p = 0 has no decomposition at all, and a holding with
  no variation has no correlation with the portfolio. Its contribution is a real 0.

This layer applies no minimum and sets no flag: ex_ante.py does, and turns these numbers into
`RiskFigure`s. §6.3's golden test runs here, on a Σ built from volatilities and a correlation.
"""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Decomposition:
    sigma_p: float
    volatilities: tuple[float, ...]
    mcr: tuple[float, ...]
    cr: tuple[float, ...]
    pcr: tuple[float, ...]
    corr_with_portfolio: tuple[float | None, ...]
    diversification_ratio: float


def covariance_matrix(returns: np.ndarray, q: float) -> np.ndarray:
    """`returns` is T×N, one column per holding."""
    returns = np.asarray(returns, dtype=float)
    if np.isnan(returns).any():
        raise ValueError("a covariance needs listwise returns: no gaps (R12)")
    if returns.shape[0] < 2:
        raise ValueError(f"a covariance needs at least two observations, got {returns.shape[0]}")
    cov = np.atleast_2d(np.cov(returns, rowvar=False, ddof=1)) * q
    # Identical values, tested directly as in measures.sharpe: their float covariance can come
    # out at 1e-18, not 0, because the mean doesn't round-trip.
    flat = np.ptp(returns, axis=0) == 0.0
    cov[flat, :] = 0.0
    cov[:, flat] = 0.0
    return cov


def decompose(weights: np.ndarray, cov: np.ndarray) -> Decomposition | None:
    w = np.asarray(weights, dtype=float)
    cov = np.asarray(cov, dtype=float)
    sigma_w = cov @ w
    variance = float(w @ sigma_w)
    if variance <= 0.0:
        return None
    sigma_p = float(np.sqrt(variance))
    vols = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    mcr = sigma_w / sigma_p
    cr = w * mcr
    return Decomposition(
        sigma_p=sigma_p,
        volatilities=_floats(vols),
        mcr=_floats(mcr),
        cr=_floats(cr),
        pcr=_floats(cr / sigma_p),
        corr_with_portfolio=tuple(
            None if vol == 0.0 else float(s / (vol * sigma_p)) for s, vol in zip(sigma_w, vols)
        ),
        diversification_ratio=float(w @ vols / sigma_p),
    )


def effective_n(weights: np.ndarray) -> float:
    """1 / Σ w_i²: capital concentration, from the weights alone."""
    w = np.asarray(weights, dtype=float)
    return float(1.0 / (w @ w))


def _floats(values: np.ndarray) -> tuple[float, ...]:
    return tuple(float(v) for v in values)
