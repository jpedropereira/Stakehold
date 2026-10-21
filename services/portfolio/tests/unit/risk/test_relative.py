"""risk/relative.py: beta, correlation, tracking error and information ratio against a
benchmark on weekly returns (spec §5.6, RK-13).

Calendar: 2020-01-03 is a Friday, F0. A weekly fixture's week k (from 0) is dated F0 + (k + 1)
weeks. The fixtures are built so every figure is exact by hand, over whole 4-week cycles:
- P: +1%, −1%, +1%, −1%, ... (by week index)
- Q: +1%, +1%, −1%, −1%, ...
Both have mean 0 over a cycle, Σ P·Q = 0 over one, and each has sample variance
v = N · 0.0001 / (N − 1) over N weeks. With the benchmark b = P and the portfolio
p = 1.5·P + Q + c:
- beta = cov(p, b) / var(b) = 1.5
- ρ = 1.5 v / √(v · 3.25 v) = 1.5 / √3.25
- the active return p − b = 0.5·P + Q + c has mean c and variance 1.25 v, so
  TE = √(1.25 v) · √52 and IR = 52 c / TE.
"""

import math
from datetime import date, timedelta

import pytest
from portfolio.risk.relative import (
    BenchmarkReturns,
    RelativeFigures,
    benchmark_covers,
    benchmark_too_short,
    relative_figures,
    relative_greyed,
    relative_to,
)
from portfolio.risk.types import Flag, Frequency, ReturnSeries, Window

F0 = date(2020, 1, 3)
WEEK = timedelta(days=7)
C = 0.001


def _p(k):
    return 0.01 if k % 2 == 0 else -0.01


def _q(k):
    return 0.01 if k % 4 in (0, 1) else -0.01


def _weekly(values, start_week=0):
    base = F0 + start_week * WEEK
    dates = tuple(base + (i + 1) * WEEK for i in range(len(values)))
    return ReturnSeries(Frequency.WEEKLY, base, dates, tuple(values))


def _window(n_weeks, start_week=0):
    return Window("w", F0 + start_week * WEEK, F0 + (start_week + n_weeks) * WEEK)


def _pair(n_weeks):
    benchmark = _weekly([_p(k) for k in range(n_weeks)])
    portfolio = _weekly([1.5 * _p(k) + _q(k) + C for k in range(n_weeks)])
    return portfolio, benchmark


def _figures(result: RelativeFigures):
    return [
        result.beta,
        result.correlation,
        result.tracking_error,
        result.information_ratio,
    ]


def test_each_measure_matches_its_hand_computed_value():
    n = 104
    portfolio, benchmark = _pair(n)
    result = relative_figures(portfolio, benchmark, _window(n))

    v = n * 0.0001 / (n - 1)
    tracking_error = math.sqrt(1.25 * v) * math.sqrt(52)
    assert result.beta.value == pytest.approx(1.5, rel=1e-12)
    assert result.correlation.value == pytest.approx(1.5 / math.sqrt(3.25), rel=1e-12)
    assert result.tracking_error.value == pytest.approx(tracking_error, rel=1e-12)
    assert result.information_ratio.value == pytest.approx(52 * C / tracking_error, rel=1e-12)


def test_every_figure_carries_the_weekly_metadata_of_its_overlap():
    n = 104
    portfolio, benchmark = _pair(n)
    for figure in _figures(relative_figures(portfolio, benchmark, _window(n))):
        assert figure.frequency is Frequency.WEEKLY
        assert figure.q == 52
        assert (figure.n_obs, figure.n_gaps) == (n, 0)
        assert (figure.window_start, figure.window_end) == (F0, F0 + n * WEEK)
        assert figure.flags == frozenset()
        assert figure.reason is None
        assert figure.standard_error is None


def test_beta_and_correlation_need_104_weeks():
    portfolio, benchmark = _pair(103)
    result = relative_figures(portfolio, benchmark, _window(103))

    for figure in (result.beta, result.correlation):
        assert figure.value is None
        assert figure.reason == "insufficient history (have 103, need 104)"
        assert Flag.INSUFFICIENT_HISTORY in figure.flags
    assert result.tracking_error.value is not None
    assert result.information_ratio.value is not None


def test_tracking_error_and_information_ratio_need_52_weeks():
    portfolio, benchmark = _pair(51)
    result = relative_figures(portfolio, benchmark, _window(51))

    for figure in _figures(result):
        assert figure.value is None
    for figure in (result.tracking_error, result.information_ratio):
        assert figure.reason == "insufficient history (have 51, need 52)"

    portfolio, benchmark = _pair(52)
    result = relative_figures(portfolio, benchmark, _window(52))
    assert result.tracking_error.value is not None
    assert result.information_ratio.value is not None


def test_a_constant_benchmark_greys_beta_and_correlation_not_inf():
    n = 104
    benchmark = _weekly([0.002] * n)
    portfolio = _weekly([_p(k) for k in range(n)])
    result = relative_figures(portfolio, benchmark, _window(n))

    for figure in (result.beta, result.correlation):
        assert figure.value is None
        assert figure.reason == "no variation in benchmark returns"
    assert result.tracking_error.value is not None


def test_a_constant_portfolio_greys_correlation_and_has_zero_beta():
    n = 104
    benchmark = _weekly([_p(k) for k in range(n)])
    portfolio = _weekly([0.002] * n)
    result = relative_figures(portfolio, benchmark, _window(n))

    assert result.correlation.value is None
    assert result.correlation.reason == "no variation in returns"
    assert result.beta.value == 0.0


def test_identical_series_have_zero_tracking_error_and_no_information_ratio():
    n = 104
    series = _weekly([_p(k) + _q(k) for k in range(n)])
    result = relative_figures(series, series, _window(n))

    assert result.tracking_error.value == 0.0
    assert result.information_ratio.value is None
    assert result.information_ratio.reason == "no tracking difference"
    assert result.beta.value == pytest.approx(1.0)


def test_a_null_week_on_either_side_is_excluded_and_counted():
    n = 108
    portfolio, benchmark = _pair(n)
    portfolio = _weekly([None if k == 10 else v for k, v in enumerate(portfolio.values)])
    benchmark = _weekly([None if k == 20 else v for k, v in enumerate(benchmark.values)])
    for figure in _figures(relative_figures(portfolio, benchmark, _window(n))):
        assert (figure.n_obs, figure.n_gaps) == (n - 2, 2)


def test_a_daily_series_is_refused():
    daily = ReturnSeries(Frequency.DAILY, F0, (F0 + timedelta(days=1),), (0.01,))
    weekly = _weekly([0.01])
    with pytest.raises(ValueError, match="weekly"):
        relative_figures(daily, weekly, _window(1))
    with pytest.raises(ValueError, match="weekly"):
        relative_figures(weekly, daily, _window(1))


def test_greyed_figures_carry_the_reason_and_the_window():
    window = Window("10Y", date(2016, 10, 21), date(2026, 10, 21))
    result = relative_greyed(window, "benchmark history too short (from 2019-07-29)")

    for figure in _figures(result):
        assert figure.value is None
        assert figure.reason == "benchmark history too short (from 2019-07-29)"
        assert figure.flags == frozenset({Flag.INSUFFICIENT_HISTORY})
        assert (figure.window_start, figure.window_end) == (window.start, window.end)
        assert (figure.n_obs, figure.n_gaps) == (0, 0)
        assert figure.frequency is Frequency.WEEKLY
        assert figure.q == 52


def test_the_benchmark_covers_a_window_starting_on_or_after_its_first_day():
    first = date(2019, 7, 29)
    assert benchmark_covers(first, Window("w", first, date(2026, 10, 21)))
    assert benchmark_covers(first, Window("w", date(2021, 10, 21), date(2026, 10, 21)))
    assert not benchmark_covers(first, Window("w", date(2016, 10, 21), date(2026, 10, 21)))


def test_too_short_names_the_benchmark_s_first_day():
    assert benchmark_too_short(date(2019, 7, 29)) == "benchmark history too short (from 2019-07-29)"


def test_relative_to_measures_a_covered_window_and_greys_the_rest():
    n = 104
    portfolio, benchmark = _pair(n)
    window = _window(n)

    covering = BenchmarkReturns(F0, benchmark)
    assert relative_to(portfolio, window, covering) == relative_figures(
        portfolio, benchmark, window
    )

    late = BenchmarkReturns(F0 + WEEK, benchmark)
    assert relative_to(portfolio, window, late) == relative_greyed(
        window, "benchmark history too short (from 2020-01-10)"
    )

    assert relative_to(portfolio, window, "benchmark unavailable: not tracked") == (
        relative_greyed(window, "benchmark unavailable: not tracked")
    )
