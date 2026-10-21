"""risk/history.py: the minimum history behind each measure (spec §4.5) and R9's greying
reason. Every minimum is checked one below, at, and one above its boundary."""

from datetime import date

import pytest
from portfolio.risk.history import Measure, check_minimum, frequency_of
from portfolio.risk.types import Flag, Frequency

END = date(2026, 3, 15)
# Far enough back that a span never limits an observation-count measure.
LONG_AGO = date(2000, 1, 3)

_OBS_MINIMUMS = [
    (Measure.VOLATILITY, 52),
    (Measure.DOWNSIDE_DEVIATION, 52),
    (Measure.SHARPE, 52),
    (Measure.SORTINO, 52),
    (Measure.MAX_DRAWDOWN, 200),
    (Measure.VAR_95, 250),
    (Measure.VAR_99, 500),
    (Measure.CORRELATION, 104),
    (Measure.BETA, 104),
    (Measure.COVARIANCE, 104),
    # Agreed for RK-13: tracking error is the active return's volatility and the information
    # ratio its Sharpe, so they take those minimums, not beta's.
    (Measure.TRACKING_ERROR, 52),
    (Measure.INFORMATION_RATIO, 52),
]


@pytest.mark.parametrize("measure, minimum", _OBS_MINIMUMS)
def test_one_below_the_minimum_is_insufficient(measure, minimum):
    check = check_minimum(measure, minimum - 1, LONG_AGO, END)

    assert check.reason == f"insufficient history (have {minimum - 1}, need {minimum})"
    assert check.flags == frozenset({Flag.INSUFFICIENT_HISTORY})
    assert check.sufficient is False


@pytest.mark.parametrize("measure, minimum", _OBS_MINIMUMS)
@pytest.mark.parametrize("extra", [0, 1])
def test_at_or_above_the_minimum_is_sufficient(measure, minimum, extra):
    check = check_minimum(measure, minimum + extra, LONG_AGO, END)

    assert check.reason is None
    assert Flag.INSUFFICIENT_HISTORY not in check.flags
    assert check.sufficient is True


def test_the_reason_reads_as_r9_words_it():
    check = check_minimum(Measure.VOLATILITY, 51, LONG_AGO, END)

    assert check.reason == "insufficient history (have 51, need 52)"


@pytest.mark.parametrize(
    "n_obs, flags",
    [
        (500, frozenset({Flag.TAIL_UNSTABLE})),
        (999, frozenset({Flag.TAIL_UNSTABLE})),
        (1000, frozenset()),
    ],
)
def test_var_99_is_tail_unstable_below_1000_observations(n_obs, flags):
    assert check_minimum(Measure.VAR_99, n_obs, LONG_AGO, END).flags == flags


def test_var_95_is_never_tail_unstable():
    assert check_minimum(Measure.VAR_95, 250, LONG_AGO, END).flags == frozenset()


@pytest.mark.parametrize("measure", [Measure.CAGR, Measure.CALMAR])
def test_a_span_under_one_year_is_insufficient(measure):
    # 2025-03-16 -> 2026-03-15 is 364 days, a day short of a calendar year.
    check = check_minimum(measure, 250, date(2025, 3, 16), END)

    assert check.reason == "insufficient history (have 364 days, need 1 year)"
    assert check.flags == frozenset({Flag.INSUFFICIENT_HISTORY})


@pytest.mark.parametrize("measure", [Measure.CAGR, Measure.CALMAR])
@pytest.mark.parametrize(
    "start, flags",
    [
        (date(2025, 3, 15), frozenset({Flag.SHORT_WINDOW})),  # exactly 1 year
        (date(2023, 3, 16), frozenset({Flag.SHORT_WINDOW})),  # a day under 3 years
        (date(2023, 3, 15), frozenset()),  # exactly 3 years
    ],
)
def test_a_span_under_three_years_is_a_short_window(measure, start, flags):
    check = check_minimum(measure, 250, start, END)

    assert check.reason is None
    assert check.flags == flags


@pytest.mark.parametrize(
    "measure, frequency",
    [
        (Measure.VOLATILITY, Frequency.WEEKLY),
        (Measure.DOWNSIDE_DEVIATION, Frequency.WEEKLY),
        (Measure.SHARPE, Frequency.WEEKLY),
        (Measure.SORTINO, Frequency.WEEKLY),
        (Measure.CORRELATION, Frequency.WEEKLY),
        (Measure.BETA, Frequency.WEEKLY),
        (Measure.COVARIANCE, Frequency.WEEKLY),
        (Measure.TRACKING_ERROR, Frequency.WEEKLY),
        (Measure.INFORMATION_RATIO, Frequency.WEEKLY),
        (Measure.MAX_DRAWDOWN, Frequency.DAILY),
        (Measure.CAGR, Frequency.DAILY),
        (Measure.CALMAR, Frequency.DAILY),
        (Measure.VAR_95, Frequency.DAILY),
        (Measure.VAR_99, Frequency.DAILY),
    ],
)
def test_each_measure_has_the_frequency_spec_4_3_gives_it(measure, frequency):
    assert frequency_of(measure) is frequency
