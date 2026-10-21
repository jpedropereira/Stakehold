"""risk/single_series.py: the reporting layer over the measures. It applies §4.3's frequency
rule (a daily series can't reach a weekly measure), slices to the window, and turns each value
into a RiskFigure with §4.5's minimums (R9), R8's gap budget and §9's flags.

Calendar: 2020-01-03 is a Friday. Daily fixtures are consecutive weekdays after their base
date, alternating +1% and -1% unless a test needs otherwise. Risk-free: 3.6% a year from 1999-01-04
is 0.0001 a day ACT/360, so a Friday-to-Friday week earns 1.0001^7 - 1.
"""

from datetime import date, timedelta

import numpy as np
import pytest
from portfolio.risk.risk_free import RatePoint
from portfolio.risk.single_series import (
    calmar_figure,
    drawdown_figures,
    return_figures,
    tail_figures,
    weekly_figures,
)
from portfolio.risk.types import Flag, Frequency, ReturnSeries, RiskFigure, Window
from portfolio.risk.weekly import to_weekly
from portfolio.risk.windows import years_before

FRIDAY = date(2020, 1, 3)
FIRST_PUBLISHED = date(1999, 1, 4)
RATES = [RatePoint(FIRST_PUBLISHED, 3.6)]
ZERO_RATES = [RatePoint(FIRST_PUBLISHED, 0.0)]
RF_WEEK = 1.0001**7 - 1


def _weekdays(base, n):
    days, day = [], base
    while len(days) < n:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days.append(day)
    return tuple(days)


def _alternating(n):
    return [0.01 if k % 2 == 0 else -0.01 for k in range(n)]


def _daily(values, base=FRIDAY):
    return ReturnSeries(Frequency.DAILY, base, _weekdays(base, len(values)), tuple(values))


def _weekly(values, base=FRIDAY):
    dates = tuple(base + timedelta(weeks=k) for k in range(1, len(values) + 1))
    return ReturnSeries(Frequency.WEEKLY, base, dates, tuple(values))


def _whole(series):
    return Window("Max", series.base_date, series.last_date)


def _figure(value, reason=None, flags=(), start=FRIDAY, end=date(2023, 1, 3)):
    return RiskFigure(value, start, end, Frequency.DAILY, 252.0, 800, 0, frozenset(flags), reason)


# --- §4.3's frequency rule ---


@pytest.mark.parametrize(
    "call",
    [
        lambda s: return_figures(s, _whole(s)),
        lambda s: drawdown_figures(s, _whole(s)),
        lambda s: tail_figures(s, _whole(s), [set(s.dates)], nonsync=False),
    ],
)
def test_a_daily_measure_refuses_a_weekly_series(call):
    with pytest.raises(ValueError, match="daily"):
        call(_weekly(_alternating(60)))


def test_a_weekly_measure_refuses_a_daily_series():
    daily = _daily(_alternating(300))

    with pytest.raises(ValueError, match="weekly"):
        weekly_figures(daily, _whole(daily), RATES, FIRST_PUBLISHED)


# --- §5.1: cumulative return and CAGR ---


def test_cumulative_return_carries_its_window_and_counts():
    daily = _daily([0.10, None, -0.10])
    window = _whole(daily)

    figure = return_figures(daily, window).cumulative_return

    # 1.10 x 0.90 - 1, the gap excluded.
    assert figure.value == pytest.approx(-0.01)
    assert (figure.window_start, figure.window_end) == (window.start, window.end)
    assert figure.frequency is Frequency.DAILY
    assert (figure.n_obs, figure.n_gaps) == (2, 1)
    # 3 days over 2020-01-03 -> 2020-01-08, 5 calendar days: 3 / (5 / 365.25).
    assert figure.q == pytest.approx(3 / (5 / 365.25))


def test_a_window_of_only_gaps_has_no_cumulative_return():
    figure = return_figures(_daily([None]), _whole(_daily([None]))).cumulative_return

    assert figure.value is None
    assert figure.reason == "insufficient history (have 0, need 1)"
    assert Flag.INSUFFICIENT_HISTORY in figure.flags


def _span_window(daily, years, extra_days=0):
    end = daily.last_date
    return Window("t", years_before(end, years) + timedelta(days=extra_days), end)


def test_cagr_under_one_year_is_greyed_and_the_cumulative_return_stands():
    daily = _daily(_alternating(900))
    window = _span_window(daily, 1, extra_days=1)

    figures = return_figures(daily, window)

    assert figures.cagr.value is None
    # One year back from the end, plus a day: 364 days, no 29 February between.
    assert figures.cagr.reason == "insufficient history (have 364 days, need 1 year)"
    assert figures.cumulative_return.value is not None


def test_cagr_at_one_year_is_shown_and_flagged_short():
    daily = _daily(_alternating(900))

    cagr = return_figures(daily, _span_window(daily, 1)).cagr

    assert cagr.value is not None
    assert Flag.SHORT_WINDOW in cagr.flags


def test_cagr_at_three_years_is_not_short():
    daily = _daily(_alternating(900))

    cagr = return_figures(daily, _span_window(daily, 3)).cagr

    assert cagr.value is not None
    assert Flag.SHORT_WINDOW not in cagr.flags


# --- R8 and §4.3's suspect rows ---


@pytest.mark.parametrize("n_gaps, flagged", [(2, False), (3, True)])
def test_gaps_above_two_percent_raise_data_gaps(n_gaps, flagged):
    # 2 gaps in 100 expected is exactly 2%; 3 in 100 is over.
    daily = _daily(_alternating(100 - n_gaps) + [None] * n_gaps)

    figure = return_figures(daily, _whole(daily)).cumulative_return

    assert (Flag.DATA_GAPS in figure.flags) is flagged


def test_a_kept_move_above_100_percent_is_flagged_suspect():
    daily = _daily([0.01, 1.5, -0.01])

    figure = return_figures(daily, _whole(daily)).cumulative_return

    assert Flag.HAS_SUSPECT_ROWS in figure.flags


# --- §5.3: drawdown ---


def test_drawdown_below_200_daily_obs_is_greyed_but_keeps_its_series():
    daily = _daily(_alternating(199))

    figures = drawdown_figures(daily, _whole(daily))

    assert figures.max_drawdown.value is None
    assert figures.max_drawdown.reason == "insufficient history (have 199, need 200)"
    assert figures.peak_date is None
    assert figures.max_days_under_water is None
    # The chart still gets every point, base included.
    assert len(figures.series) == 200


def test_drawdown_at_200_daily_obs_is_shown():
    daily = _daily(_alternating(200))

    figures = drawdown_figures(daily, _whole(daily))

    # Each +1%/-1% pair multiplies the level by 0.9999, so the first day's 1.01 is never
    # regained and the trough is the last day, after 100 pairs: 0.9999^100 / 1.01 - 1.
    assert figures.max_drawdown.value == pytest.approx(0.9999**100 / 1.01 - 1)
    assert figures.trough_date == daily.dates[-1]
    assert figures.recovery_date is None
    assert figures.peak_date == daily.dates[0]
    assert figures.max_days_under_water is not None


# --- §5.2 and §5.4: weekly measures ---


@pytest.mark.parametrize("n_weeks, shown", [(51, False), (52, True), (53, True)])
def test_weekly_measures_need_52_weeks(n_weeks, shown):
    weekly = _weekly(_alternating(n_weeks))

    figures = weekly_figures(weekly, _whole(weekly), RATES, FIRST_PUBLISHED)

    for figure in (figures.volatility, figures.downside_deviation, figures.sharpe, figures.sortino):
        assert (figure.value is not None) is shown
        assert figure.frequency is Frequency.WEEKLY
        assert figure.q == 52
        if not shown:
            assert figure.reason == f"insufficient history (have {n_weeks}, need 52)"


def test_weekly_volatility_annualises_with_52():
    weekly = _weekly(_alternating(52))

    volatility = weekly_figures(weekly, _whole(weekly), RATES, FIRST_PUBLISHED).volatility

    # 26 of +1% and 26 of -1%: mean 0, sample variance 52 x 0.0001 / 51.
    assert volatility.value == pytest.approx(0.01 * np.sqrt(52 / 51) * np.sqrt(52))


def test_sharpe_carries_its_standard_error():
    weekly = _weekly([0.01, 0.03] * 26)

    sharpe = weekly_figures(weekly, _whole(weekly), RATES, FIRST_PUBLISHED).sharpe

    assert sharpe.standard_error is not None
    assert sharpe.standard_error > 0


def test_the_weekly_window_snaps_to_whole_weeks_and_rf_spans_each_week():
    # Each week returns exactly its risk-free return plus +-1%, so the mean excess return is 0
    # and so is the Sharpe -- unless rf were taken over a different span than the week's.
    daily_base = FRIDAY
    # 61 weeks; the first is outside the snapped window, leaving 30 of each sign.
    weekly_values = [RF_WEEK + e for e in _alternating(61)]
    weekly = _weekly(weekly_values, base=daily_base)
    window = Window("t", date(2020, 1, 7), weekly.last_date + timedelta(days=5))
    # Tuesday 01-07 snaps forward to Friday 01-10; the end snaps back to the last Friday.
    expected_start, expected_end = date(2020, 1, 10), weekly.last_date

    figures = weekly_figures(weekly, window, RATES, FIRST_PUBLISHED)

    assert (figures.sharpe.window_start, figures.sharpe.window_end) == (
        expected_start,
        expected_end,
    )
    assert figures.sharpe.n_obs == 60
    assert figures.sharpe.value == pytest.approx(0.0, abs=1e-9)


def test_a_window_inside_one_week_has_no_weekly_figures():
    weekly = _weekly(_alternating(60))
    window = Window("t", date(2020, 1, 14), date(2020, 1, 16))

    figures = weekly_figures(weekly, window, RATES, FIRST_PUBLISHED)

    assert figures.volatility.value is None
    assert figures.volatility.reason == "insufficient history (have 0, need 52)"


def test_a_window_before_1999_marks_the_rf_measures_partial():
    weekly = _weekly(_alternating(60), base=date(1998, 1, 2))

    figures = weekly_figures(weekly, _whole(weekly), RATES, FIRST_PUBLISHED)

    for figure in (figures.downside_deviation, figures.sharpe, figures.sortino):
        assert Flag.RF_PARTIAL in figure.flags
    assert Flag.RF_PARTIAL not in figures.volatility.flags


def test_flat_excess_returns_grey_the_ratios_with_a_reason():
    weekly = _weekly([0.001] * 52)

    figures = weekly_figures(weekly, _whole(weekly), ZERO_RATES, FIRST_PUBLISHED)

    assert figures.volatility.value == 0.0
    assert figures.sharpe.value is None
    assert figures.sharpe.reason == "no variation in excess returns"
    assert figures.sortino.value is None
    assert figures.sortino.reason == "no returns below the risk-free rate"


def test_weekly_figures_from_to_weekly_line_up_with_its_fridays():
    daily = _daily(_alternating(400))
    weekly = to_weekly(daily)
    window = Window("t", daily.base_date, daily.last_date)

    figures = weekly_figures(weekly, window, RATES, FIRST_PUBLISHED)

    assert figures.volatility.window_start.weekday() == 4
    assert figures.volatility.window_end.weekday() == 4
    assert figures.volatility.value is not None


def test_without_risk_free_data_the_rf_measures_are_greyed_and_volatility_stands():
    # No risk-free series (none stored, or data-store unreachable): excess returns can't be
    # formed, so downside deviation, Sharpe and Sortino are greyed -- never computed on rf = 0.
    weekly = _weekly(_alternating(52))
    with_rf = weekly_figures(weekly, _whole(weekly), RATES, FIRST_PUBLISHED)

    figures = weekly_figures(weekly, _whole(weekly), None, None)

    assert figures.volatility == with_rf.volatility
    for figure in (figures.downside_deviation, figures.sharpe, figures.sortino):
        assert figure.value is None
        assert figure.reason == "no risk-free rate data"
        assert figure.standard_error is None
        assert figure.n_obs == 52
        assert Flag.RF_PARTIAL not in figure.flags


def test_without_risk_free_data_insufficient_history_still_wins():
    weekly = _weekly(_alternating(51))

    figures = weekly_figures(weekly, _whole(weekly), None, None)

    for figure in (figures.volatility, figures.downside_deviation, figures.sharpe, figures.sortino):
        assert figure.reason == "insufficient history (have 51, need 52)"
        assert Flag.INSUFFICIENT_HISTORY in figure.flags


@pytest.mark.parametrize("rates, first", [(None, FIRST_PUBLISHED), (RATES, None)])
def test_rates_and_their_first_date_are_absent_together(rates, first):
    weekly = _weekly(_alternating(52))

    with pytest.raises(ValueError, match="together"):
        weekly_figures(weekly, _whole(weekly), rates, first)


# --- §5.4: Calmar ---


def test_calmar_divides_cagr_by_the_drawdown_and_keeps_short_window():
    figure = calmar_figure(_figure(0.06, flags={Flag.SHORT_WINDOW}), _figure(-0.20))

    assert figure.value == pytest.approx(0.3)
    assert Flag.SHORT_WINDOW in figure.flags


def test_calmar_takes_the_reason_of_a_greyed_cagr():
    cagr = _figure(None, "insufficient history (have 200 days, need 1 year)")

    figure = calmar_figure(cagr, _figure(-0.20))

    assert figure.value is None
    assert figure.reason == cagr.reason


def test_calmar_takes_the_reason_of_a_greyed_drawdown():
    mdd = _figure(None, "insufficient history (have 199, need 200)")

    figure = calmar_figure(_figure(0.06), mdd)

    assert figure.value is None
    assert figure.reason == mdd.reason


def test_calmar_without_a_drawdown_is_greyed_with_a_reason():
    figure = calmar_figure(_figure(0.06), _figure(0.0))

    assert figure.value is None
    assert figure.reason == "no drawdown in window"


def test_calmar_refuses_figures_from_different_windows():
    with pytest.raises(ValueError, match="window"):
        calmar_figure(_figure(0.06), _figure(-0.2, start=date(2021, 1, 4)))


# --- §5.5: tail risk ---


def _tail(n, nonsync=False):
    daily = _daily(_alternating(n))
    return tail_figures(daily, _whole(daily), [set(daily.dates)], nonsync=nonsync)


@pytest.mark.parametrize("n_obs, shown", [(249, False), (250, True)])
def test_var_95_needs_250_daily_obs(n_obs, shown):
    figures = _tail(n_obs)

    assert (figures.var_95.value is not None) is shown
    assert (figures.cvar_95.value is not None) is shown


@pytest.mark.parametrize("n_obs, shown", [(499, False), (500, True)])
def test_var_99_needs_500_daily_obs(n_obs, shown):
    figures = _tail(n_obs)

    assert (figures.var_99.value is not None) is shown
    assert (figures.cvar_99.value is not None) is shown


@pytest.mark.parametrize("n_obs, unstable", [(999, True), (1000, False)])
def test_var_99_is_tail_unstable_below_1000_obs(n_obs, unstable):
    figures = _tail(n_obs)

    assert (Flag.TAIL_UNSTABLE in figures.var_99.flags) is unstable
    assert (Flag.TAIL_UNSTABLE in figures.cvar_99.flags) is unstable
    assert Flag.TAIL_UNSTABLE not in figures.var_95.flags


@pytest.mark.parametrize("nonsync", [True, False])
def test_nonsync_daily_is_the_callers_call(nonsync):
    figures = _tail(300, nonsync=nonsync)

    for figure in (figures.var_95, figures.cvar_95, figures.var_99, figures.cvar_99):
        assert (Flag.NONSYNC_DAILY in figure.flags) is nonsync


def test_var_keeps_only_days_every_asset_traded_and_counts_nulls_on_them():
    daily = _daily(_alternating(300) + [None])
    other_exchange = set(daily.dates) - set(daily.dates[:10])

    figures = tail_figures(daily, _whole(daily), [set(daily.dates), other_exchange], nonsync=True)

    # 10 days the other exchange was closed are dropped; the NULL day is shared, so a gap.
    assert (figures.var_95.n_obs, figures.var_95.n_gaps) == (290, 1)
