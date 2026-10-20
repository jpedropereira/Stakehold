"""risk/weekly.py: daily returns compounded into Friday-to-Friday weeks (spec R6).

Calendar used throughout: 2026-01-02, 01-09, 01-16 and 01-23 are Fridays. 2026-04-03 is Good
Friday and 04-06 Easter Monday, both closed on European exchanges.
"""

from datetime import date

import pytest
from portfolio.risk.types import Frequency, ReturnSeries
from portfolio.risk.weekly import to_weekly


def _daily(base, points):
    """points: (date, return, or None for a gap)."""
    return ReturnSeries(
        Frequency.DAILY, base, tuple(d for d, _ in points), tuple(r for _, r in points)
    )


def test_a_week_compounds_its_daily_returns():
    daily = _daily(
        date(2026, 1, 2),
        [
            (date(2026, 1, 5), 0.01),
            (date(2026, 1, 6), -0.02),
            (date(2026, 1, 7), 0.03),
            (date(2026, 1, 8), 0.0),
            (date(2026, 1, 9), 0.01),
        ],
    )

    weekly = to_weekly(daily)

    assert weekly.frequency is Frequency.WEEKLY
    assert weekly.base_date == date(2026, 1, 2)
    assert weekly.dates == (date(2026, 1, 9),)
    # 1.01 x 0.98 x 1.03 x 1.00 x 1.01 - 1 = 1.02968894 - 1
    assert weekly.values[0] == pytest.approx(0.02968894)


def test_a_week_with_a_null_day_is_null():
    daily = _daily(
        date(2026, 1, 2),
        [
            (date(2026, 1, 5), 0.01),
            (date(2026, 1, 7), None),
            (date(2026, 1, 9), 0.01),
            (date(2026, 1, 12), 0.02),
            (date(2026, 1, 16), -0.01),
        ],
    )

    weekly = to_weekly(daily)

    assert weekly.dates == (date(2026, 1, 9), date(2026, 1, 16))
    assert weekly.values[0] is None
    # 1.02 x 0.99 - 1
    assert weekly.values[1] == pytest.approx(0.0098)


def test_a_week_with_no_trades_is_null():
    daily = _daily(date(2026, 1, 2), [(date(2026, 1, 9), 0.01), (date(2026, 1, 23), 0.02)])

    weekly = to_weekly(daily)

    assert weekly.dates == (date(2026, 1, 9), date(2026, 1, 16), date(2026, 1, 23))
    assert weekly.values == (pytest.approx(0.01), None, pytest.approx(0.02))


def test_a_holiday_friday_keeps_its_label():
    daily = _daily(
        date(2026, 3, 27),
        [
            (date(2026, 3, 30), 0.01),
            (date(2026, 3, 31), 0.0),
            (date(2026, 4, 1), 0.0),
            (date(2026, 4, 2), 0.0),
            # 04-03 and 04-06 closed
            (date(2026, 4, 10), 0.02),
        ],
    )

    weekly = to_weekly(daily)

    assert weekly.dates == (date(2026, 4, 3), date(2026, 4, 10))
    assert weekly.values == (pytest.approx(0.01), pytest.approx(0.02))


def test_partial_weeks_at_both_edges_are_dropped():
    # Base on a Wednesday: Thursday and Friday are only part of a week, so the weekly series is
    # based on Friday 01-09 instead. The Monday after the last Friday starts a week not yet over.
    daily = _daily(
        date(2026, 1, 7),
        [
            (date(2026, 1, 8), 0.05),
            (date(2026, 1, 9), 0.05),
            (date(2026, 1, 12), 0.01),
            (date(2026, 1, 16), 0.01),
            (date(2026, 1, 19), 0.03),
        ],
    )

    weekly = to_weekly(daily)

    assert weekly.base_date == date(2026, 1, 9)
    assert weekly.dates == (date(2026, 1, 16),)
    # 1.01 x 1.01 - 1
    assert weekly.values[0] == pytest.approx(0.0201)


def test_a_last_week_ending_on_a_holiday_friday_counts_once_the_friday_has_passed():
    daily = _daily(
        date(2026, 3, 27),
        [(date(2026, 3, 30), 0.01), (date(2026, 4, 2), 0.01)],
    )

    # The series' last day is Thursday 04-02: without knowing Friday has passed, the week may
    # not be over yet.
    assert to_weekly(daily).dates == ()
    # Known complete through Friday 04-03 (a holiday), the week is whole. 1.01 x 1.01 - 1.
    weekly = to_weekly(daily, as_of=date(2026, 4, 3))
    assert weekly.dates == (date(2026, 4, 3),)
    assert weekly.values[0] == pytest.approx(0.0201)


def test_a_weekend_day_belongs_to_the_week_ending_the_next_friday():
    daily = _daily(date(2026, 1, 9), [(date(2026, 1, 10), 0.01), (date(2026, 1, 16), 0.01)])

    weekly = to_weekly(daily)

    assert weekly.dates == (date(2026, 1, 16),)
    assert weekly.values[0] == pytest.approx(0.0201)


def test_a_series_shorter_than_a_week_has_no_weeks():
    daily = _daily(date(2026, 1, 7), [(date(2026, 1, 8), 0.01)])

    weekly = to_weekly(daily)

    assert weekly.dates == ()
    assert weekly.values == ()


def test_only_a_daily_series_can_be_aggregated():
    weekly = ReturnSeries(Frequency.WEEKLY, date(2026, 1, 2), (date(2026, 1, 9),), (0.01,))

    with pytest.raises(ValueError):
        to_weekly(weekly)
