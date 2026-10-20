"""risk/quality.py: gap accounting and the 2% budget (R8), the suspect-row check (§4.3), and
periods per year (§4.4)."""

from datetime import date, timedelta

import pytest
from portfolio.risk.quality import (
    WEEKLY_Q,
    empirical_q,
    gap_stats,
    has_suspect_rows,
    over_gap_budget,
    periods_per_year,
)
from portfolio.risk.types import Frequency, ReturnSeries

BASE = date(2026, 1, 2)


def _daily(values, base=BASE):
    dates = tuple(base + timedelta(days=k + 1) for k in range(len(values)))
    return ReturnSeries(Frequency.DAILY, base, dates, tuple(values))


def test_gap_stats_counts_values_and_nulls():
    assert gap_stats(_daily([0.01, None, 0.02, None, 0.0])) == (3, 2)


def test_gap_stats_of_an_empty_series():
    assert gap_stats(_daily([])) == (0, 0)


@pytest.mark.parametrize(
    "n_obs, n_gaps, over",
    [
        (98, 2, False),  # exactly 2%: within budget
        (97, 3, True),  # 3%
        (4900, 100, False),  # exactly 2% of 5,000
        (4899, 101, True),  # 2.02%
        (0, 0, False),  # nothing expected, nothing missing
    ],
)
def test_the_gap_budget_is_two_percent_of_expected_observations(n_obs, n_gaps, over):
    assert over_gap_budget(n_obs, n_gaps) is over


@pytest.mark.parametrize(
    "values, suspect",
    [
        ([0.01, 1.0], False),  # exactly +100% is not over the threshold
        ([0.01, 1.0001], True),
        ([0.01, 1.5, None], True),
        ([None, None], False),  # a NULL is a gap, never a suspect value
    ],
)
def test_a_return_over_100_percent_is_a_suspect_row(values, suspect):
    assert has_suspect_rows(_daily(values)) is suspect


def test_empirical_q_on_a_union_calendar():
    # 261 observations (one of them a NULL day, which still counts as a day of the calendar)
    # spanning 2024-01-01 -> 2025-01-01, 366 calendar days:
    # 261 / (366 / 365.25) = 95330.25 / 366 = 260.4651639...
    # More than one exchange's ~252, as a union of calendars gives.
    base = date(2024, 1, 1)
    dates = tuple(base + timedelta(days=k) for k in range(106, 367))
    values = (None,) + (0.001,) * 260
    series = ReturnSeries(Frequency.DAILY, base, dates, values)

    assert dates[-1] == date(2025, 1, 1)
    assert empirical_q(series) == pytest.approx(260.4651639)


def test_empirical_q_needs_at_least_one_observation():
    with pytest.raises(ValueError):
        empirical_q(_daily([]))


def test_periods_per_year_is_52_for_weekly_and_empirical_for_daily():
    weekly = ReturnSeries(Frequency.WEEKLY, BASE, (date(2026, 1, 9),), (0.01,))
    daily = _daily([0.01] * 10)

    assert WEEKLY_Q == 52
    assert periods_per_year(weekly) == 52
    assert periods_per_year(daily) == pytest.approx(empirical_q(daily))
