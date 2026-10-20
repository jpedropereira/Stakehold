"""1-day historical VaR and CVaR (spec §5.5), as positive loss fractions.

- **VaR(α)** is minus the (1 − α) empirical quantile of the returns, by **linear interpolation
  between order statistics**: numpy's `method="linear"`, Hyndman & Fan's type 7, which puts the
  p-quantile at 0-based position (n − 1) × p of the sorted sample. It is the common default
  (numpy, R, Excel's PERCENTILE.INC), so a reader can reproduce a figure in a spreadsheet.
- **CVaR(α)** is minus the mean of the returns at or below that quantile.
- A gap (NaN) is dropped, never a 0% day (R5).

Frequency-agnostic like measures.py. The day mask (R7), the minimums and the `nonsync_daily`
and `tail_unstable` flags are single_series.py's.
"""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Tail:
    var: float
    cvar: float


def var_cvar(r: np.ndarray, alpha: float) -> Tail:
    values = np.asarray(r, dtype=float)
    observed = values[~np.isnan(values)]
    quantile = np.quantile(observed, 1.0 - alpha, method="linear")
    return Tail(var=float(-quantile), cvar=float(-np.mean(observed[observed <= quantile])))
