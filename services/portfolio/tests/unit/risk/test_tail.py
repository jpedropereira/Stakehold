"""risk/tail.py: 1-day historical VaR and CVaR (spec §5.5), as positive loss fractions.

The sample is -10% .. +10% in 1% steps, 21 returns, handed in shuffled. Linear interpolation
between order statistics (numpy's "linear", Hyndman-Fan type 7) puts the p-quantile at
position h = (n - 1) x p in the sorted sample, 0-based.
"""

import numpy as np
import pytest
from portfolio.risk.tail import var_cvar

_SAMPLE = np.array([k / 100 for k in range(-10, 11)])
SHUFFLED = np.random.default_rng(7).permutation(_SAMPLE)


def test_var_95_lands_on_an_order_statistic():
    # h = 20 x 0.05 = 1.0: the second-smallest return, -9%. CVaR: mean of -10% and -9%.
    result = var_cvar(SHUFFLED, 0.95)

    assert result.var == pytest.approx(0.09)
    assert result.cvar == pytest.approx(0.095)


def test_var_99_interpolates_between_order_statistics():
    # h = 20 x 0.01 = 0.2: -10% + 0.2 x 1% = -9.8%. Only -10% is at or below it.
    result = var_cvar(SHUFFLED, 0.99)

    assert result.var == pytest.approx(0.098)
    assert result.cvar == pytest.approx(0.10)


def test_cvar_includes_returns_equal_to_the_quantile():
    # 21 returns: -10% once, -5% four times, +1% sixteen times. h ~ 1.0 falls between two -5%s,
    # so the quantile is exactly -5%. CVaR: (-10% - 4 x 5%) / 5 = -6%. Strictly below: -10%.
    result = var_cvar(np.array([-0.10] + [-0.05] * 4 + [0.01] * 16), 0.95)

    assert result.var == pytest.approx(0.05)
    assert result.cvar == pytest.approx(0.06)


def test_nulls_are_dropped_not_counted_as_flat_days():
    with_gaps = np.concatenate([SHUFFLED, [np.nan, np.nan]])

    assert var_cvar(with_gaps, 0.95) == var_cvar(SHUFFLED, 0.95)
