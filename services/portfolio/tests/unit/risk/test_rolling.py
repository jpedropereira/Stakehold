"""risk/rolling.py: the rolling views of spec §4.5 and §7.1 (RK-24). Rolling 1Y volatility and
Sharpe are stepped monthly, and rolling 52-week correlation weekly. A window below its minimum is
a gap (a greyed figure with R9's reason), never a point with a made-up value and never dropped.

Calendar: 2020-01-03 is a Friday. Daily fixtures are consecutive weekdays after their base date.
Risk-free: 3.6% a year from 1999-01-04, as in test_single_series.py. Weekly correlation
fixtures use two patterns whose ρ is exact by hand over any 52 consecutive weeks: P alternates
±1% and Q runs +,+,−,− at 1%, so P·Q sums to 0 over every 4 weeks and ρ(P, Q) = 0.
"""

from datetime import date, timedelta

import pytest
from portfolio.risk.ex_post import NO_RISK_FREE
from portfolio.risk.risk_free import RatePoint
from portfolio.risk.rolling import (
    CORRELATION_WINDOW,
    monthly_windows,
    rolling_correlation,
    rolling_weekly,
)
from portfolio.risk.single_series import weekly_figures
from portfolio.risk.types import Flag, Frequency, ReturnSeries, Window
from portfolio.risk.weekly import to_weekly
from portfolio.risk.windows import standard_windows

FRIDAY = date(2020, 1, 3)
FIRST_PUBLISHED = date(1999, 1, 4)
RATES = [RatePoint(FIRST_PUBLISHED, 3.6)]
WEEK = timedelta(days=7)


def _weekdays_until(base, end):
    days, day = [], base
    while day < end:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days.append(day)
    return tuple(days)


def _daily(base, end, value=lambda k: 0.01 if k % 2 == 0 else -0.012, nulls=()):
    dates = _weekdays_until(base, end)
    values = tuple(None if d in nulls else value(k) for k, d in enumerate(dates))
    return ReturnSeries(Frequency.DAILY, base, dates, values)


def _varied(k):
    """Daily returns whose weekly compounds vary, so volatility and Sharpe are defined."""
    return [0.011, -0.007, 0.004, -0.013, 0.006, 0.002, -0.003][k % 7]


# --- monthly windows -------------------------------------------------------------------------


def test_monthly_windows_end_on_each_months_last_date_once_a_year_is_covered():
    daily = _daily(FRIDAY, date(2021, 3, 17))  # ends on a Wednesday, mid-month

    windows = monthly_windows(daily)

    # December 2020's window would start on 2019-12-31, before the base date: not covered.
    assert windows == (
        Window("1Y", date(2020, 1, 29), date(2021, 1, 29)),
        Window("1Y", date(2020, 2, 26), date(2021, 2, 26)),
        Window("1Y", date(2020, 3, 17), date(2021, 3, 17)),
    )


def test_the_last_monthly_window_is_the_standard_1y_window():
    daily = _daily(FRIDAY, date(2023, 6, 14))

    assert monthly_windows(daily)[-1] == standard_windows(daily.last_date, daily.base_date)[0]


def test_a_monthly_window_ending_on_29_february_starts_on_the_28th():
    daily = _daily(date(2023, 1, 2), date(2024, 2, 29))

    assert monthly_windows(daily)[-1] == Window("1Y", date(2023, 2, 28), date(2024, 2, 29))


def test_a_history_under_a_year_has_no_monthly_windows():
    daily = _daily(FRIDAY, date(2020, 12, 30))

    assert monthly_windows(daily) == ()
    rolling = rolling_weekly(to_weekly(daily), (), rates=RATES, first_published=FIRST_PUBLISHED)
    assert rolling.volatility == () and rolling.sharpe == ()


# --- rolling volatility and Sharpe ------------------------------------------------------------


def test_each_rolling_point_is_the_weekly_figure_over_its_window():
    daily = _daily(FRIDAY, date(2023, 3, 1), value=_varied)
    weekly = to_weekly(daily)
    windows = monthly_windows(daily)

    rolling = rolling_weekly(weekly, windows, rates=RATES, first_published=FIRST_PUBLISHED)

    assert len(rolling.volatility) == len(rolling.sharpe) == len(windows) == 27
    for window, volatility, sharpe in zip(windows, rolling.volatility, rolling.sharpe):
        expected = weekly_figures(weekly, window, RATES, FIRST_PUBLISHED)
        assert volatility == expected.volatility
        assert sharpe == expected.sharpe
        assert volatility.value is not None and sharpe.value is not None
        assert sharpe.standard_error is not None


def test_a_window_with_null_weeks_is_a_gap_and_its_neighbours_stay_measured():
    # Two NULL days in consecutive weeks: every 1Y window holding both has at most 51 weeks.
    nulls = {date(2021, 5, 4), date(2021, 5, 11)}
    daily = _daily(FRIDAY, date(2023, 3, 1), value=_varied, nulls=nulls)

    rolling = rolling_weekly(
        to_weekly(daily), monthly_windows(daily), rates=RATES, first_published=FIRST_PUBLISHED
    )

    gaps = [f for f in rolling.volatility if f.value is None]
    measured = [f for f in rolling.volatility if f.value is not None]
    assert gaps and measured
    for figure in gaps:
        assert figure.n_obs < 52
        assert figure.reason == f"insufficient history (have {figure.n_obs}, need 52)"
        assert Flag.INSUFFICIENT_HISTORY in figure.flags
        assert figure.window_start < date(2021, 5, 4) and figure.window_end > date(2021, 5, 11)
    assert all(f.n_obs >= 52 for f in measured)
    # The first windows end before the NULLs, the last ones start after them.
    assert rolling.volatility[0].value is not None
    assert rolling.volatility[-1].value is not None


def test_without_a_risk_free_rate_sharpe_is_greyed_and_volatility_kept():
    daily = _daily(FRIDAY, date(2022, 3, 1), value=_varied)

    rolling = rolling_weekly(
        to_weekly(daily), monthly_windows(daily), rates=None, first_published=None
    )

    assert rolling.volatility and all(f.value is not None for f in rolling.volatility)
    assert all(f.value is None and f.reason == NO_RISK_FREE for f in rolling.sharpe)


def test_rolling_weekly_refuses_a_daily_series():
    daily = _daily(FRIDAY, date(2021, 3, 1))

    with pytest.raises(ValueError, match="weekly"):
        rolling_weekly(daily, monthly_windows(daily), rates=RATES, first_published=FIRST_PUBLISHED)


# --- rolling correlation ----------------------------------------------------------------------


def _p(k):
    return 0.01 if k % 2 == 0 else -0.01


def _q(k):
    return 0.01 if k % 4 in (0, 1) else -0.01


def _weekly(start_week, values):
    """A weekly series whose first return is week `start_week`, ending on F0 + (week + 1)·7."""
    base = FRIDAY + start_week * WEEK
    dates = tuple(base + (i + 1) * WEEK for i in range(len(values)))
    return ReturnSeries(Frequency.WEEKLY, base, dates, tuple(values))


def _pattern(fn, start_week, end_week, scale=1.0, shift=0.0):
    return _weekly(start_week, [scale * fn(k) + shift for k in range(start_week, end_week)])


def test_rolling_correlation_steps_weekly_over_52_weeks():
    a = _pattern(_p, 0, 120)

    points = rolling_correlation(a, _pattern(_p, 0, 120, scale=2.0, shift=0.001))

    # The first window that fits 52 weeks after the base ends on week 51's Friday.
    assert [p.window_end for p in points] == list(a.dates[51:])
    assert all(p.window_start == p.window_end - 52 * WEEK for p in points)
    assert all(p.n_obs == 52 and p.frequency is Frequency.WEEKLY for p in points)
    assert all(p.value == pytest.approx(1.0) for p in points)
    assert CORRELATION_WINDOW == "52W"


def test_rolling_correlation_of_orthogonal_and_opposite_patterns():
    a = _pattern(_p, 0, 80)

    assert all(
        p.value == pytest.approx(0.0, abs=1e-12)
        for p in rolling_correlation(a, _pattern(_q, 0, 80))
    )
    assert all(
        p.value == pytest.approx(-1.0) for p in rolling_correlation(a, _pattern(_p, 0, 80, -1.0))
    )


def test_a_null_week_makes_its_windows_gaps_at_51_and_the_rest_measured_at_52():
    a = _pattern(_p, 0, 110)
    values = [_q(k) for k in range(110)]
    values[70] = None
    b = _weekly(0, values)

    points = rolling_correlation(a, b)

    null_friday = b.dates[70]
    for point in points:
        if point.window_start < null_friday <= point.window_end:
            assert point.value is None
            assert (point.n_obs, point.n_gaps) == (51, 1)
            assert point.reason == "insufficient history (have 51, need 52)"
        else:
            assert point.n_obs == 52
            assert point.value == pytest.approx(0.0, abs=1e-12)


def test_a_late_starter_starts_the_rolling_correlation_at_the_shared_span():
    a = _pattern(_p, 0, 120)
    b = _pattern(_q, 20, 120)

    points = rolling_correlation(a, b)

    assert points[0].window_start == b.base_date
    assert points[0].window_end == b.base_date + 52 * WEEK
    assert len(points) == 120 - 20 - 51


def test_a_constant_series_is_greyed_not_inf_or_zero():
    points = rolling_correlation(_pattern(_p, 0, 60), _weekly(0, [0.01] * 60))

    assert points and all(p.value is None for p in points)
    assert all(p.reason == "no variation in returns" for p in points)


def test_a_pair_sharing_under_52_weeks_has_no_points():
    assert rolling_correlation(_pattern(_p, 0, 60), _pattern(_q, 10, 61)) == ()
    assert rolling_correlation(_pattern(_p, 0, 40), _pattern(_q, 50, 120)) == ()
