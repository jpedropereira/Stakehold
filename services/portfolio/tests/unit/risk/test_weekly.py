"""risk/weekly.py: daily returns compounded into Friday-to-Friday weeks (spec R6).

Calendar used throughout: 2026-01-02, 01-09, 01-16 and 01-23 are Fridays. 2026-04-03 is Good
Friday and 04-06 Easter Monday, both closed on European exchanges.
"""

from datetime import date, timedelta

import pytest
from portfolio.risk.types import Frequency, ReturnSeries, Window
from portfolio.risk.weekly import to_weekly, weekly_window
from portfolio.risk.windows import years_before


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


# --- weekly_window: the weeks ending inside a window ---


def test_a_window_keeps_the_weeks_that_end_inside_it():
    """Tuesday 01-06 to Thursday 01-22: the weeks ending Friday 01-09 and 01-16. The first
    starts on Friday 01-02, before the window does; the week ending 01-23 hasn't ended."""
    whole_weeks = weekly_window(Window("t", date(2026, 1, 6), date(2026, 1, 22)))

    assert whole_weeks == Window("t", date(2026, 1, 2), date(2026, 1, 16))


def test_a_window_on_fridays_is_unchanged():
    window = Window("t", date(2026, 1, 2), date(2026, 1, 23))

    assert weekly_window(window) == window


@pytest.mark.parametrize("offset", range(7))
def test_a_one_year_window_holds_at_least_52_weeks_whatever_day_it_ends(offset):
    """Spec §4.5's weekly minimum is 52 weeks, "1 year". Snapping both edges inward left a
    one-year window 51 weeks on most days, so its weekly figures were greyed."""
    end = date(2026, 10, 16) + timedelta(days=offset)

    whole_weeks = weekly_window(Window("1Y", years_before(end, 1), end))

    assert (whole_weeks.end - whole_weeks.start).days // 7 in (52, 53)


def test_a_window_in_which_no_week_ends_has_none():
    """Tuesday 01-13 to Thursday 01-15: the week ending Friday 01-16 hasn't ended."""
    assert weekly_window(Window("t", date(2026, 1, 13), date(2026, 1, 15))) is None


def test_the_week_a_window_starts_in_is_skipped_when_the_data_starts_after_it():
    """A Max window starts on the series' first day, inside a week the weekly series dropped
    as partial: its weeks start at the next Friday, as before."""
    window = Window("Max", date(2026, 1, 6), date(2026, 1, 23))

    assert weekly_window(window, available_from=date(2026, 1, 9)) == Window(
        "Max", date(2026, 1, 9), date(2026, 1, 23)
    )


def test_the_week_a_window_starts_in_is_kept_when_the_data_has_it():
    window = Window("1Y", date(2026, 1, 6), date(2026, 1, 23))

    assert weekly_window(window, available_from=date(2025, 6, 6)).start == date(2026, 1, 2)
    assert weekly_window(window, available_from=date(2026, 1, 2)).start == date(2026, 1, 2)


def test_data_starting_after_the_first_whole_week_is_left_for_the_caller_to_clamp():
    """A holding starting mid-window shortens it, and the caller names it as the limit (R12)."""
    window = Window("1Y", date(2026, 1, 6), date(2026, 1, 30))

    assert weekly_window(window, available_from=date(2026, 1, 16)).start == date(2026, 1, 2)
