"""risk/risk_free.py: the risk-free return over each period, compounded ACT/360 over calendar
days with the latest published rate forward-filled (spec §4.1), and R4's rf = 0 before the
series begins.

Rates are annualised % as the ECB publishes them. 3.6% / 100 / 360 = 0.0001 a day and
7.2% = 0.0002 a day keep the products checkable by hand.
"""

from datetime import date

import pytest
from portfolio.risk.risk_free import RatePoint, risk_free_returns

FIRST_PUBLISHED = date(1999, 1, 4)


def test_a_weekend_compounds_fridays_rate_for_three_days():
    rates = [RatePoint(date(2026, 1, 9), 3.6), RatePoint(date(2026, 1, 12), 7.2)]

    result = risk_free_returns(
        rates, FIRST_PUBLISHED, [date(2026, 1, 9), date(2026, 1, 12), date(2026, 1, 13)]
    )

    # Fri 01-09 -> Mon 01-12: Fri, Sat and Sun at Friday's rate, 1.0001^3 - 1.
    # Mon 01-12 -> Tue 01-13: Monday at its own rate, 0.0002.
    assert result.values == (pytest.approx(0.000300030001), pytest.approx(0.0002))
    assert result.partial is False


def test_a_rate_published_mid_period_applies_from_its_date():
    rates = [RatePoint(date(2026, 1, 9), 3.6), RatePoint(date(2026, 1, 14), 7.2)]

    result = risk_free_returns(rates, FIRST_PUBLISHED, [date(2026, 1, 9), date(2026, 1, 16)])

    # 01-09..01-13 (5 days) at 0.0001, 01-14 and 01-15 at 0.0002.
    assert result.values == (pytest.approx(1.0001**5 * 1.0002**2 - 1),)


def test_a_rate_dated_after_a_day_is_never_used_for_it():
    # The 36% print on 01-10 is outside [01-09, 01-10): using it would be look-ahead.
    rates = [RatePoint(date(2026, 1, 9), 3.6), RatePoint(date(2026, 1, 10), 36.0)]

    result = risk_free_returns(rates, FIRST_PUBLISHED, [date(2026, 1, 9), date(2026, 1, 10)])

    assert result.values == (pytest.approx(0.0001),)


def test_a_negative_rate_is_kept_negative():
    # -0.36% a year is -0.00001 a day: 0.99999^2 - 1.
    rates = [RatePoint(date(2020, 3, 2), -0.36)]

    result = risk_free_returns(rates, FIRST_PUBLISHED, [date(2020, 3, 2), date(2020, 3, 4)])

    assert result.values == (pytest.approx(0.99999**2 - 1),)
    assert result.values[0] < 0


def test_days_before_the_series_begins_earn_zero_and_mark_it_partial():
    rates = [RatePoint(FIRST_PUBLISHED, 3.6)]

    result = risk_free_returns(rates, FIRST_PUBLISHED, [date(1998, 12, 31), date(1999, 1, 6)])

    # 1998-12-31 .. 1999-01-03 earn 0 (R4); 01-04 and 01-05 at 0.0001: 1.0001^2 - 1.
    assert result.values == (pytest.approx(0.00020001),)
    assert result.partial is True


def test_a_period_entirely_before_the_series_earns_zero():
    result = risk_free_returns([], FIRST_PUBLISHED, [date(1998, 1, 2), date(1998, 1, 9)])

    assert result.values == (0.0,)
    assert result.partial is True


def test_a_day_after_the_series_begins_with_no_rate_on_or_before_it_is_an_error():
    # The caller fetched too short a range: forward-filling from nothing would invent a rate.
    rates = [RatePoint(date(2026, 1, 12), 3.6)]

    with pytest.raises(ValueError):
        risk_free_returns(rates, FIRST_PUBLISHED, [date(2026, 1, 9), date(2026, 1, 13)])


def test_boundaries_must_strictly_increase():
    rates = [RatePoint(date(2026, 1, 9), 3.6)]

    with pytest.raises(ValueError):
        risk_free_returns(rates, FIRST_PUBLISHED, [date(2026, 1, 12), date(2026, 1, 12)])


def test_fewer_than_two_boundaries_give_no_periods():
    result = risk_free_returns([], FIRST_PUBLISHED, [date(2026, 1, 9)])

    assert result.values == ()
    assert result.partial is False
