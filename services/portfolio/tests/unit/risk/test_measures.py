"""risk/measures.py and risk/drawdown.py at a pinned q: the frequency-agnostic maths behind
spec §5.1-5.4, with §5.7's golden test. No minimums or flags apply at this layer; those are
single_series.py's.

§5.7's values are hand-checked, not regenerated: if the code disagrees, find out which side is
wrong before changing either.
"""

from datetime import date, timedelta

import numpy as np
import pytest
from portfolio.risk.drawdown import drawdown
from portfolio.risk.measures import (
    cagr,
    calmar,
    cumulative_return,
    downside_deviation,
    growth_index,
    sharpe,
    sortino,
    volatility,
)
from portfolio.risk.types import Frequency, ReturnSeries

GOLDEN = [0.01, -0.02, 0.015, -0.005, 0.03, -0.04, 0.02]
GOLDEN_RF = 0.0001
DAY_0 = date(2026, 3, 1)


def _days(n):
    return tuple(DAY_0 + timedelta(days=k) for k in range(1, n + 1))


def _array(values):
    return np.array([np.nan if v is None else v for v in values], dtype=float)


def test_golden_example_section_5_7():
    r = _array(GOLDEN)
    x = r - GOLDEN_RF

    assert growth_index(r) == pytest.approx(
        [1.010000, 0.989800, 1.004647, 0.999624, 1.029612, 0.988428, 1.008197], abs=5e-7
    )
    assert np.std(r, ddof=1) == pytest.approx(0.02461610, abs=5e-9)
    assert volatility(r, 252) == pytest.approx(0.390768, abs=5e-7)
    assert sharpe(x, 252).value == pytest.approx(0.8568, abs=5e-5)
    assert downside_deviation(x, 252) == pytest.approx(0.270867, abs=5e-7)
    assert sortino(x, 252) == pytest.approx(1.2360, abs=5e-5)

    dd = drawdown(ReturnSeries(Frequency.DAILY, DAY_0, _days(7), tuple(GOLDEN)))
    # Peak 1.029612 on day 5, trough 0.988428 on day 6: 0.988428 / 1.029612 - 1 = -4%.
    assert dd.max_drawdown == pytest.approx(-0.04, abs=5e-7)
    assert dd.peak_date == DAY_0 + timedelta(days=5)
    assert dd.trough_date == DAY_0 + timedelta(days=6)
    assert dd.recovery_date is None


def test_cumulative_return_chains_the_growth_index():
    # 1.10 x 0.90 - 1 = -0.01
    assert cumulative_return(_array([0.10, -0.10])) == pytest.approx(-0.01)


def test_a_null_is_excluded_from_the_stats_and_leaves_the_level_where_it_was():
    with_gap = _array([0.01, None, -0.01])
    without = _array([0.01, -0.01])

    assert cumulative_return(with_gap) == pytest.approx(cumulative_return(without))
    # The gap is not a 0% day: a 0 would be a third observation and shrink the stdev.
    assert volatility(with_gap, 52) == pytest.approx(volatility(without, 52))
    assert volatility(with_gap, 52) != pytest.approx(volatility(_array([0.01, 0.0, -0.01]), 52))
    # The level is carried across the gap.
    assert growth_index(with_gap) == pytest.approx([1.01, 1.01, 0.9999])


def test_cagr_annualises_over_calendar_days_of_365_25():
    # 2020-01-01 -> 2024-01-01 is 1,461 days = 4.0 years; 1.2^4 = 2.0736.
    assert cagr(1.0736, date(2020, 1, 1), date(2024, 1, 1)) == pytest.approx(0.2)


def test_sharpe_standard_error_follows_lo_2002():
    # mean 0.02, stdev(ddof=1) 0.0141421, so SR_p = sqrt(2) and SR_p^2 / 2 = 1:
    # SE = sqrt(52) x sqrt((1 + 1) / 2) = sqrt(52). Sharpe = sqrt(2) x sqrt(52).
    result = sharpe(_array([0.01, 0.03]), 52)

    assert result.value == pytest.approx(np.sqrt(2) * np.sqrt(52))
    assert result.standard_error == pytest.approx(np.sqrt(52))


def test_downside_deviation_divides_by_all_observations():
    # Only -0.02 is below 0: sqrt(0.02^2 / 4) = 0.01, x sqrt(52).
    assert downside_deviation(_array([0.01, -0.02, 0.03, 0.0]), 52) == pytest.approx(
        0.01 * np.sqrt(52)
    )


def test_ratios_with_a_zero_denominator_are_undefined_not_infinite():
    flat = _array([0.01, 0.01, 0.01])

    assert sharpe(flat, 52) is None
    assert sortino(flat, 52) is None
    assert calmar(0.05, 0.0) is None


def test_calmar_divides_cagr_by_the_size_of_the_drawdown():
    assert calmar(0.06, -0.20) == pytest.approx(0.3)
