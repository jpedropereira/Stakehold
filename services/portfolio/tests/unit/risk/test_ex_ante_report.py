"""risk/ex_ante_report.py: ex-ante risk of a mix over one requested window (RK-17), from the
holdings' daily EUR returns. The decomposition and the constant-weight series are tested in
test_ex_ante.py, the pairwise matrix in test_correlation.py; this covers how the report picks
their windows.

- The requested window ends on the holdings' latest common date. Max starts at the oldest
  holding's base, so a younger holding shortens it and is named.
- The decomposition's listwise window is the effective one, and the constant-weight figures run
  over it, so their weekly volatility is σ_p exactly (§6.2).
- The pairwise matrix keeps the requested window: each cell has its own overlap (R13).

Calendar: 2015-01-02 is a Friday. Daily fixtures are every weekday after their base, with
returns from a fixed seed.
"""

from datetime import date, timedelta

import numpy as np
import pytest
from portfolio.risk.ex_ante import constant_weight_series
from portfolio.risk.ex_ante_report import NO_OVERLAP, ex_ante_report
from portfolio.risk.ex_post import NO_RISK_FREE
from portfolio.risk.risk_free import RatePoint
from portfolio.risk.rolling import RollingFigures, monthly_windows, rolling_weekly
from portfolio.risk.types import Flag, Frequency, ReturnSeries
from portfolio.risk.weekly import to_weekly
from portfolio.risk.windows import StandardWindow, years_before

FRIDAY = date(2015, 1, 2)
END = date(2021, 6, 30)  # a Wednesday
ZERO_RATES = [RatePoint(date(1999, 1, 4), 0.0)]
EUR = {"A": "EUR", "B": "EUR", "C": "EUR"}


def _daily(base, end, seed, nulls=(), scale=0.01):
    dates, day = [], base
    while day < end:
        day += timedelta(days=1)
        if day.weekday() < 5:
            dates.append(day)
    values = list(np.random.default_rng(seed).normal(0.0003, scale, len(dates)))
    for k, day in enumerate(dates):
        if day in nulls:
            values[k] = None
    return ReturnSeries(Frequency.DAILY, base, tuple(dates), tuple(values))


def _report(daily, weights, label=StandardWindow.Y3, *, currencies=EUR, rates=None, corr=None):
    return ex_ante_report(
        daily,
        weights,
        label,
        currencies=currencies,
        rates=rates,
        first_published=None if rates is None else rates[0].date,
        correlation_daily=daily if corr is None else corr,
    )


def _pair(b_base=FRIDAY, b_end=END, a_nulls=()):
    return {
        "A": _daily(FRIDAY, END, 1, nulls=a_nulls),
        "B": _daily(b_base, b_end, 2, scale=0.02),
    }


WEIGHTS = {"A": 0.6, "B": 0.4}


def test_the_window_ends_on_the_latest_common_date_and_3y_starts_three_years_before():
    end = date(2021, 6, 15)
    report = _report(_pair(b_end=end), WEIGHTS)

    assert report.requested.label == StandardWindow.Y3
    assert report.requested.end == end
    assert report.requested.start == years_before(end, 3)
    assert report.window.end == end
    assert report.limited_by == ()


def test_max_starts_on_the_oldest_base_and_a_younger_holding_shortens_it_and_is_named():
    late = date(2017, 3, 6)  # a Monday: its first whole week ends on Friday 2017-03-10
    report = _report(_pair(b_base=late), WEIGHTS, StandardWindow.MAX)

    assert report.requested.start == FRIDAY
    assert report.limited_by == ("B",)
    assert report.window.start == date(2017, 3, 10)
    decomposition = report.decomposition
    assert decomposition.sigma_p.window_start == report.window.start
    cw = report.constant_weight
    assert cw.returns.cagr.window_start == report.window.start
    assert cw.drawdown.max_drawdown.window_start == report.window.start
    assert cw.weekly.volatility.window_start == report.window.start


def test_the_constant_weight_weekly_volatility_is_sigma_p_on_the_effective_window():
    # A NULL day in A loses its week for both, in the panel and in the constant-weight series.
    report = _report(_pair(b_base=date(2017, 3, 6), a_nulls={date(2019, 5, 8)}), WEIGHTS)

    sigma_p = report.decomposition.sigma_p
    volatility = report.constant_weight.weekly.volatility
    assert sigma_p.value is not None
    assert volatility.value == pytest.approx(sigma_p.value, abs=1e-12)
    assert (volatility.window_start, volatility.window_end) == (
        sigma_p.window_start,
        sigma_p.window_end,
    )
    assert volatility.n_obs == sigma_p.n_obs
    assert volatility.n_gaps == sigma_p.n_gaps == 1


def test_the_correlation_matrix_keeps_the_requested_window_each_cell_over_its_own_overlap():
    late = date(2017, 3, 6)
    report = _report(_pair(b_base=late), WEIGHTS, StandardWindow.MAX)

    a_a = report.correlation.cell("A", "A")
    a_b = report.correlation.cell("A", "B")
    assert a_a.window_start < late  # A's cell isn't shortened by B
    assert a_a.value == pytest.approx(1.0)
    assert a_b.window_start >= late
    assert a_b.value is not None


def test_the_correlation_matrix_takes_the_callers_series_and_keys():
    daily = _pair()
    benchmark = _daily(FRIDAY, END, 3)
    report = _report(daily, WEIGHTS, corr={**daily, "VWCE.DE": benchmark})

    assert report.correlation.keys == ("A", "B", "VWCE.DE")
    assert report.correlation.cell("A", "VWCE.DE").value is not None


def test_on_1y_sigma_p_is_shown_and_the_decomposition_is_greyed_below_104_weeks():
    report = _report(_pair(), WEIGHTS, StandardWindow.Y1)

    decomposition = report.decomposition
    assert decomposition.sigma_p.value is not None
    assert decomposition.diversification_ratio.value is None
    assert "need 104" in decomposition.diversification_ratio.reason
    assert all(h.pcr is None for h in decomposition.holdings)
    assert decomposition.effective_n == pytest.approx(1 / (0.6**2 + 0.4**2))


def test_histories_that_do_not_overlap_grey_the_constant_weight_figures_without_raising():
    daily = {
        "A": _daily(FRIDAY, date(2016, 6, 1), 1),
        "B": _daily(date(2018, 1, 5), END, 2),
    }
    report = _report(daily, WEIGHTS, StandardWindow.MAX)

    assert report.window is None
    assert report.limited_by == ()
    cw = report.constant_weight
    assert cw.returns.cagr.value is None
    assert cw.returns.cagr.reason == NO_OVERLAP
    assert cw.tail.var_95.reason == NO_OVERLAP
    assert report.decomposition.sigma_p.value is None
    assert report.mix_weekly is None
    assert report.rolling == RollingFigures((), ())


# --- rolling (RK-24) --------------------------------------------------------------------------


def test_rolling_runs_on_the_weekly_mix_over_its_whole_history_whatever_the_window():
    late = date(2017, 3, 6)
    daily = _pair(b_base=late)
    weekly = {key: to_weekly(series) for key, series in daily.items()}

    report = _report(daily, WEIGHTS, StandardWindow.Y3, rates=ZERO_RATES)

    assert report.mix_weekly == constant_weight_series(weekly, WEIGHTS)
    expected = rolling_weekly(
        report.mix_weekly,
        monthly_windows(constant_weight_series(daily, WEIGHTS)),
        rates=ZERO_RATES,
        first_published=ZERO_RATES[0].date,
    )
    assert report.rolling == expected
    # From the first full year of the mix, which starts with its youngest holding.
    assert report.rolling.volatility[0].window_start >= late
    assert report.rolling.volatility[0].window_start < years_before(END, 3)
    assert _report(daily, WEIGHTS, StandardWindow.MAX, rates=ZERO_RATES).rolling == expected


def test_the_last_rolling_volatility_is_the_1y_constant_weight_volatility():
    report = _report(_pair(), WEIGHTS, StandardWindow.Y1, rates=ZERO_RATES)

    last = report.rolling.volatility[-1]
    one_year = report.constant_weight.weekly.volatility
    assert (last.window_start, last.window_end) == (one_year.window_start, one_year.window_end)
    assert last.value == pytest.approx(one_year.value)
    assert report.rolling.sharpe[-1].value == pytest.approx(
        report.constant_weight.weekly.sharpe.value
    )


@pytest.mark.parametrize(
    "daily, weights, currencies, nonsync",
    [
        ({"A": _daily(FRIDAY, END, 1)}, {"A": 1.0}, {"A": "EUR"}, False),
        ({"A": _daily(FRIDAY, END, 1)}, {"A": 1.0}, {"A": "USD"}, True),
        (_pair(), WEIGHTS, EUR, True),
    ],
    ids=["one EUR holding", "one USD holding", "two holdings"],
)
def test_var_is_flagged_nonsync_unless_the_mix_is_one_eur_holding(
    daily, weights, currencies, nonsync
):
    report = _report(daily, weights, currencies=currencies)

    var_95 = report.constant_weight.tail.var_95
    assert var_95.value is not None
    assert (Flag.NONSYNC_DAILY in var_95.flags) is nonsync


def test_var_keeps_only_the_days_every_holding_traded():
    a = _daily(FRIDAY, END, 1)
    # B's exchange is shut every Monday: those days are 0% for the mix, and out of VaR's sample.
    b_full = _daily(FRIDAY, END, 2)
    kept = [(d, v) for d, v in zip(b_full.dates, b_full.values) if d.weekday() != 0]
    b = ReturnSeries(Frequency.DAILY, FRIDAY, tuple(d for d, _ in kept), tuple(v for _, v in kept))
    report = _report({"A": a, "B": b}, WEIGHTS)

    cw = report.constant_weight
    assert cw.tail.var_95.n_obs < cw.drawdown.max_drawdown.n_obs


def test_without_a_risk_free_series_the_excess_return_figures_are_greyed_and_volatility_kept():
    without = _report(_pair(), WEIGHTS).constant_weight.weekly
    with_rf = _report(_pair(), WEIGHTS, rates=ZERO_RATES).constant_weight.weekly

    assert without.volatility.value is not None
    assert without.sharpe.value is None
    assert without.sharpe.reason == NO_RISK_FREE
    assert with_rf.sharpe.value is not None
    assert with_rf.sharpe.standard_error is not None


def test_a_null_day_in_one_holding_is_a_gap_in_the_constant_weight_daily_series():
    report = _report(_pair(a_nulls={date(2019, 5, 8)}), WEIGHTS)

    cumulative = report.constant_weight.returns.cumulative_return
    assert cumulative.n_gaps == 1
    assert cumulative.value is not None


def test_weights_that_do_not_sum_to_one_raise():
    with pytest.raises(ValueError, match="sum to 1"):
        _report(_pair(), {"A": 0.6, "B": 0.6})
