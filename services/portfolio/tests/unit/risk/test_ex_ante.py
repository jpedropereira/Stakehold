"""risk/ex_ante.py: today's mix (spec §6.2-6.3) as RiskFigures, and its constant-weight series.

Minimums (agreed for RK-15): σ_p greys below the volatility minimum, 52 weeks, because it
equals the constant-weight weekly volatility; the per-holding decomposition and the
diversification ratio grey below the covariance minimum, 104 weeks.

Calendar: 2020-01-03 is a Friday. Weekly fixtures are consecutive Fridays after their base;
daily ones are the weekdays after it. Random fixtures use a fixed seed.
"""

from datetime import date, timedelta

import numpy as np
import pytest
from portfolio.risk.ex_ante import constant_weight_series, decomposition_figures
from portfolio.risk.risk_free import RatePoint
from portfolio.risk.single_series import drawdown_figures, weekly_figures
from portfolio.risk.types import Flag, Frequency, ReturnSeries, Window

FRIDAY = date(2020, 1, 3)
ZERO_RATES = [RatePoint(date(1999, 1, 4), 0.0)]


def _weekly(values, base=FRIDAY):
    dates = tuple(base + timedelta(weeks=k) for k in range(1, len(values) + 1))
    return ReturnSeries(Frequency.WEEKLY, base, dates, tuple(values))


def _daily(values, base=FRIDAY, skip=()):
    """Weekdays after `base`, leaving out the dates in `skip` (an exchange closed)."""
    dates, day = [], base
    while len(dates) < len(values):
        day += timedelta(days=1)
        if day.weekday() < 5 and day not in skip:
            dates.append(day)
    return ReturnSeries(Frequency.DAILY, base, tuple(dates), tuple(values))


def _random_weekly(n, seed, scale=0.02):
    return list(np.random.default_rng(seed).normal(0.001, scale, n))


def _window(n_weeks):
    return Window("Max", FRIDAY, FRIDAY + timedelta(weeks=n_weeks))


WEIGHTS = {"A": 0.6, "B": 0.4}


def _pair(n, nulls_a=(), nulls_b=()):
    a, b = _random_weekly(n, 1), _random_weekly(n, 2, scale=0.01)
    for k in nulls_a:
        a[k] = None
    for k in nulls_b:
        b[k] = None
    return {"A": _weekly(a), "B": _weekly(b)}


# --- decomposition_figures -------------------------------------------------------------------


def test_sigma_p_equals_the_constant_weight_weekly_volatility():
    """§6.2's consistency check, with interior NULLs so it covers the listwise drop."""
    weekly = _pair(150, nulls_a=(10, 40), nulls_b=(70,))
    result = decomposition_figures(weekly, WEIGHTS, _window(150))
    mix = constant_weight_series(weekly, WEIGHTS)
    volatility = weekly_figures(mix, _window(150), ZERO_RATES, date(1999, 1, 4)).volatility
    assert result.sigma_p.value == pytest.approx(volatility.value, rel=1e-12)
    assert (result.sigma_p.n_obs, result.sigma_p.n_gaps) == (147, 3)
    assert (volatility.n_obs, volatility.n_gaps) == (147, 3)


def test_figures_match_the_maths_on_the_listwise_returns():
    weekly = _pair(120)
    result = decomposition_figures(weekly, WEIGHTS, _window(120))
    returns = np.array([weekly["A"].values, weekly["B"].values]).T
    cov = np.cov(returns, rowvar=False, ddof=1) * 52
    w = np.array([0.6, 0.4])
    sigma_p = np.sqrt(w @ cov @ w)
    assert result.sigma_p.value == pytest.approx(sigma_p)
    a, b = result.holdings
    assert (a.key, a.weight, b.key, b.weight) == ("A", 0.6, "B", 0.4)
    assert a.volatility == pytest.approx(np.sqrt(cov[0, 0]))
    assert a.mcr == pytest.approx((cov @ w)[0] / sigma_p)
    assert a.cr + b.cr == pytest.approx(sigma_p)
    assert a.pcr + b.pcr == pytest.approx(1.0)
    assert result.diversification_ratio.value == pytest.approx(
        (0.6 * np.sqrt(cov[0, 0]) + 0.4 * np.sqrt(cov[1, 1])) / sigma_p
    )
    assert result.effective_n == pytest.approx(1 / 0.52)


def test_every_figure_carries_the_listwise_metadata():
    result = decomposition_figures(_pair(120), WEIGHTS, _window(120))
    for figure in (result.sigma_p, result.diversification_ratio):
        assert figure.frequency is Frequency.WEEKLY
        assert figure.q == 52
        assert (figure.window_start, figure.window_end) == (FRIDAY, FRIDAY + timedelta(weeks=120))
        assert (figure.n_obs, figure.n_gaps) == (120, 0)
        assert figure.flags == frozenset()


def test_between_52_and_104_weeks_sigma_p_shows_and_the_decomposition_greys():
    result = decomposition_figures(_pair(80), WEIGHTS, _window(80))
    assert result.sigma_p.value is not None
    assert result.diversification_ratio.value is None
    assert result.diversification_ratio.reason == "insufficient history (have 80, need 104)"
    assert Flag.INSUFFICIENT_HISTORY in result.diversification_ratio.flags
    for holding in result.holdings:
        assert (holding.volatility, holding.mcr, holding.cr, holding.pcr) == (None,) * 4
        assert holding.corr_with_portfolio is None
    assert result.effective_n == pytest.approx(1 / 0.52)


def test_at_104_weeks_the_decomposition_shows():
    result = decomposition_figures(_pair(104), WEIGHTS, _window(104))
    assert result.diversification_ratio.value is not None
    assert all(h.pcr is not None for h in result.holdings)


def test_below_52_weeks_everything_greys_but_effective_n():
    result = decomposition_figures(_pair(51), WEIGHTS, _window(51))
    assert result.sigma_p.value is None
    assert result.sigma_p.reason == "insufficient history (have 51, need 52)"
    assert result.diversification_ratio.value is None
    assert result.effective_n == pytest.approx(1 / 0.52)


def test_a_late_holding_shortens_the_window_and_is_named():
    late = FRIDAY + timedelta(weeks=30)
    weekly = {"A": _weekly(_random_weekly(150, 1)), "B": _weekly(_random_weekly(120, 2), late)}
    result = decomposition_figures(weekly, WEIGHTS, _window(150))
    assert result.limited_by == ("B",)
    assert result.sigma_p.window_start == late
    assert result.sigma_p.n_obs == 120


def test_gaps_over_the_budget_flag_every_figure():
    weekly = _pair(120, nulls_a=(1, 2, 3))
    result = decomposition_figures(weekly, WEIGHTS, _window(120))
    assert Flag.DATA_GAPS in result.sigma_p.flags
    assert Flag.DATA_GAPS in result.diversification_ratio.flags


def test_a_suspect_row_flags_every_figure():
    a = _random_weekly(120, 1)
    a[5] = 1.5
    weekly = {"A": _weekly(a), "B": _weekly(_random_weekly(120, 2))}
    result = decomposition_figures(weekly, WEIGHTS, _window(120))
    assert Flag.HAS_SUSPECT_ROWS in result.sigma_p.flags
    assert Flag.HAS_SUSPECT_ROWS in result.diversification_ratio.flags


def test_no_variation_greys_with_a_reason():
    weekly = {"A": _weekly([0.01] * 120), "B": _weekly([0.02] * 120)}
    result = decomposition_figures(weekly, WEIGHTS, _window(120))
    assert result.sigma_p.value is None
    assert result.sigma_p.reason == "no variation in returns"
    assert result.diversification_ratio.reason == "no variation in returns"
    assert all(h.pcr is None for h in result.holdings)


def test_disjoint_histories_grey_everything():
    weekly = {
        "A": _weekly(_random_weekly(60, 1)),
        "B": _weekly(_random_weekly(60, 2), FRIDAY + timedelta(weeks=100)),
    }
    result = decomposition_figures(weekly, WEIGHTS, _window(160))
    assert result.sigma_p.value is None
    assert result.sigma_p.n_obs == 0


def test_a_single_holding_is_its_own_portfolio():
    weekly = {"A": _weekly(_random_weekly(120, 1))}
    result = decomposition_figures(weekly, {"A": 1.0}, _window(120))
    (holding,) = result.holdings
    assert holding.pcr == pytest.approx(1.0)
    assert holding.corr_with_portfolio == pytest.approx(1.0)
    assert result.diversification_ratio.value == pytest.approx(1.0)
    assert result.sigma_p.value == pytest.approx(holding.volatility)


@pytest.mark.parametrize(
    "weights, match",
    [
        ({"A": 0.5, "B": 0.4}, "sum to 1"),
        ({"A": 1.2, "B": -0.2}, "negative"),
        ({"A": 1.0}, "same holdings"),
        ({"A": 0.6, "B": 0.3, "C": 0.1}, "same holdings"),
    ],
)
def test_invalid_weights_are_refused(weights, match):
    with pytest.raises(ValueError, match=match):
        decomposition_figures(_pair(60), weights, _window(60))


# A zero weight (RK-19): the what-if's "before" holds the candidate at 0, so before and after
# share one listwise panel.


def test_a_zero_weight_holding_owes_no_risk_but_has_a_correlation():
    weekly = _pair(120)
    result = decomposition_figures(weekly, {"A": 1.0, "B": 0.0}, _window(120))
    alone = decomposition_figures({"A": weekly["A"]}, {"A": 1.0}, _window(120))
    a, b = result.holdings

    assert (b.weight, b.cr, b.pcr) == (0.0, 0.0, 0.0)
    assert a.pcr == pytest.approx(1.0)
    assert result.sigma_p.value == pytest.approx(alone.sigma_p.value)
    # B's correlation with a portfolio that is all A is B's correlation with A.
    rho = np.corrcoef(_random_weekly(120, 1), _random_weekly(120, 2, scale=0.01))[0, 1]
    assert b.corr_with_portfolio == pytest.approx(rho)
    assert result.effective_n == pytest.approx(1.0)


def test_a_zero_weight_holdings_null_week_still_drops_the_week():
    weekly = _pair(120, nulls_b=(7,))
    result = decomposition_figures(weekly, {"A": 1.0, "B": 0.0}, _window(120))
    alone = decomposition_figures({"A": weekly["A"]}, {"A": 1.0}, _window(120))

    assert (result.sigma_p.n_obs, result.sigma_p.n_gaps) == (119, 1)
    assert (alone.sigma_p.n_obs, alone.sigma_p.n_gaps) == (120, 0)
    mix = constant_weight_series(weekly, {"A": 1.0, "B": 0.0})
    assert mix.values[7] is None
    assert mix.values[6] == pytest.approx(weekly["A"].values[6])


# --- constant_weight_series ------------------------------------------------------------------


def test_weekly_mix_is_the_weighted_sum_with_nulls_propagated():
    weekly = {"A": _weekly([0.01, None, 0.03]), "B": _weekly([0.02, 0.04, None])}
    mix = constant_weight_series(weekly, WEIGHTS)
    assert mix.frequency is Frequency.WEEKLY
    assert mix.base_date == FRIDAY
    assert mix.dates == weekly["A"].dates
    assert mix.values[0] == pytest.approx(0.6 * 0.01 + 0.4 * 0.02)
    assert mix.values[1:] == (None, None)


def test_daily_mix_counts_a_closed_exchange_as_a_flat_day():
    closed = FRIDAY + timedelta(days=4)  # Tuesday
    daily = {
        "A": _daily([0.01, 0.02, 0.03]),
        "B": _daily([0.04, 0.05], skip={closed}),
    }
    mix = constant_weight_series(daily, WEIGHTS)
    monday, tuesday, wednesday = (FRIDAY + timedelta(days=d) for d in (3, 4, 5))
    assert mix.dates == (monday, tuesday, wednesday)
    assert mix.values == pytest.approx(
        [0.6 * 0.01 + 0.4 * 0.04, 0.6 * 0.02, 0.6 * 0.03 + 0.4 * 0.05]
    )


def test_daily_mix_is_a_gap_on_a_day_any_holding_is_null():
    daily = {"A": _daily([0.01, None, 0.03]), "B": _daily([0.04, 0.05, 0.06])}
    mix = constant_weight_series(daily, WEIGHTS)
    assert mix.values[1] is None
    assert mix.values[0] is not None and mix.values[2] is not None


def test_mix_runs_over_the_common_span():
    late = FRIDAY + timedelta(weeks=1)
    weekly = {"A": _weekly([0.01, 0.02, 0.03, 0.04]), "B": _weekly([0.05, 0.06], base=late)}
    mix = constant_weight_series(weekly, WEIGHTS)
    assert mix.base_date == late
    assert mix.dates == (FRIDAY + timedelta(weeks=2), FRIDAY + timedelta(weeks=3))


def test_disjoint_histories_give_an_empty_mix():
    weekly = {"A": _weekly([0.01]), "B": _weekly([0.02], base=FRIDAY + timedelta(weeks=5))}
    mix = constant_weight_series(weekly, WEIGHTS)
    assert mix.dates == ()


def test_mixed_frequencies_are_refused():
    with pytest.raises(ValueError, match="frequency"):
        constant_weight_series({"A": _weekly([0.01]), "B": _daily([0.02])}, WEIGHTS)


def test_the_daily_mix_feeds_the_drawdown_figures():
    daily = {"A": _daily([0.01, -0.03, 0.02]), "B": _daily([0.0, -0.01, 0.01])}
    mix = constant_weight_series(daily, WEIGHTS)
    figures = drawdown_figures(mix, Window("Max", mix.base_date, mix.last_date))
    assert figures.series[2][1] == pytest.approx((1.006 * (1 - 0.022)) / 1.006 - 1)
