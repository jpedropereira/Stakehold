"""risk/drawdown.py: max drawdown, its peak, trough and recovery dates, the longest time under
water, and the DD series (spec §5.3), on the growth index with G0 = 1 on the base date.

Dates are consecutive calendar days from DAY_0, so "day k" is k days after the base date.
"""

from datetime import date, timedelta

import pytest
from portfolio.risk.drawdown import drawdown
from portfolio.risk.types import Frequency, ReturnSeries

DAY_0 = date(2026, 3, 1)


def _day(k):
    return DAY_0 + timedelta(days=k)


def _series(values):
    return ReturnSeries(
        Frequency.DAILY, DAY_0, tuple(_day(k) for k in range(1, len(values) + 1)), tuple(values)
    )


def test_a_recovered_drawdown_has_its_recovery_date():
    # G: 1.10 (peak, day 1), 0.88 (trough, day 2), 0.968, 1.1616 (>= 1.10 on day 4).
    result = drawdown(_series([0.10, -0.20, 0.10, 0.20]))

    assert result.max_drawdown == pytest.approx(-0.20)
    assert result.peak_date == _day(1)
    assert result.trough_date == _day(2)
    assert result.recovery_date == _day(4)
    assert result.max_days_under_water == 3


def test_an_unrecovered_drawdown_is_under_water_until_the_last_date():
    # G: 1.10, 0.88, 0.968 -- still below 1.10 on the last day.
    result = drawdown(_series([0.10, -0.20, 0.10]))

    assert result.recovery_date is None
    assert result.max_days_under_water == 2


def test_time_under_water_is_the_longest_episode_not_the_deepest():
    # §5.7's series: under water from day 1 (1.01) until day 5 (1.029612), 4 days, then from
    # day 5 to day 7 without recovering, 2 days. The MDD is in the second episode.
    result = drawdown(_series([0.01, -0.02, 0.015, -0.005, 0.03, -0.04, 0.02]))

    assert result.peak_date == _day(5)
    assert result.max_days_under_water == 4


def test_returning_exactly_to_the_peak_is_a_recovery():
    # 1.25 x 0.80 = 1.00, x 1.25 = 1.25: equal to the peak, which is "G >= peak".
    result = drawdown(_series([0.25, -0.20, 0.25]))

    assert result.recovery_date == _day(3)


def test_a_level_a_rounding_error_below_the_peak_is_a_recovery():
    # The third return lands G one part in 1e14 under 1.10: float noise, not a drawdown.
    result = drawdown(_series([0.10, -0.20, 1.10 * (1 - 1e-14) / 0.88 - 1]))

    assert result.recovery_date == _day(3)


def test_a_series_that_never_falls_has_no_drawdown():
    result = drawdown(_series([0.01, 0.02]))

    assert result.max_drawdown == 0.0
    assert (result.peak_date, result.trough_date, result.recovery_date) == (None, None, None)
    assert result.max_days_under_water == 0


def test_the_peak_can_be_the_base_level():
    # G0 = 1 on day 0; G: 0.90, 0.945. The peak is the level the series started from.
    result = drawdown(_series([-0.10, 0.05]))

    assert result.peak_date == DAY_0
    assert result.trough_date == _day(1)
    assert result.max_drawdown == pytest.approx(-0.10)
    assert result.max_days_under_water == 2


def test_the_dd_series_starts_at_zero_and_is_none_on_a_gap():
    # G: 1.10, (gap: level carried), 0.99. DD on day 3: 0.99 / 1.10 - 1 = -0.10.
    result = drawdown(_series([0.10, None, -0.10]))

    assert [d for d, _ in result.series] == [DAY_0, _day(1), _day(2), _day(3)]
    values = [v for _, v in result.series]
    assert values[:3] == [0.0, 0.0, None]
    assert values[3] == pytest.approx(-0.10)
    assert result.max_drawdown == pytest.approx(-0.10)


def test_an_empty_series_has_only_the_base_point():
    result = drawdown(ReturnSeries(Frequency.DAILY, DAY_0, (), ()))

    assert result.series == ((DAY_0, 0.0),)
    assert result.max_drawdown == 0.0
