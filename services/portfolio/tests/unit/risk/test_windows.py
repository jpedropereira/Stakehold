"""risk/windows.py: the standard windows 1Y/3Y/5Y/10Y/Max ending at the latest common date
(spec §4.5), with Max starting where every series has begun (R10)."""

from datetime import date

import pytest
from portfolio.risk.types import Frequency, ReturnSeries, Window
from portfolio.risk.windows import (
    StandardWindow,
    common_span,
    covers,
    earliest_common_start,
    latest_common_date,
    slice_series,
    standard_windows,
    years_before,
)


def _daily(base, points):
    return ReturnSeries(
        Frequency.DAILY, base, tuple(d for d, _ in points), tuple(r for _, r in points)
    )


def test_years_before_clamps_29_february():
    assert years_before(date(2028, 2, 29), 1) == date(2027, 2, 28)
    assert years_before(date(2028, 2, 29), 4) == date(2024, 2, 29)
    assert years_before(date(2026, 10, 16), 10) == date(2016, 10, 16)


def test_standard_windows_end_together_and_max_starts_at_the_given_start():
    end = date(2026, 10, 16)

    windows = standard_windows(end, max_start=date(2019, 7, 29))

    assert [w.label for w in windows] == ["1Y", "3Y", "5Y", "10Y", "Max"]
    assert [w.label for w in windows] == list(StandardWindow)
    assert all(w.end == end for w in windows)
    assert [w.start for w in windows] == [
        date(2025, 10, 16),
        date(2023, 10, 16),
        date(2021, 10, 16),
        date(2016, 10, 16),
        date(2019, 7, 29),
    ]


def test_the_common_end_is_the_earliest_last_date_and_the_common_start_the_latest_base():
    a = _daily(date(2020, 1, 2), [(date(2020, 1, 3), 0.01), (date(2026, 10, 16), 0.01)])
    b = _daily(date(2021, 6, 1), [(date(2021, 6, 2), 0.01), (date(2026, 10, 15), 0.01)])

    assert latest_common_date(a, b) == date(2026, 10, 15)
    assert earliest_common_start(a, b) == date(2021, 6, 1)


def test_common_dates_need_a_series():
    with pytest.raises(ValueError):
        latest_common_date()
    with pytest.raises(ValueError):
        earliest_common_start()
    with pytest.raises(ValueError):
        common_span()


def test_the_common_span_runs_from_the_latest_base_to_the_earliest_last_date():
    a = _daily(date(2020, 1, 2), [(date(2020, 1, 3), 0.01), (date(2026, 10, 16), 0.01)])
    b = _daily(date(2021, 6, 1), [(date(2021, 6, 2), 0.01), (date(2026, 10, 15), 0.01)])

    assert common_span(a, b) == (date(2021, 6, 1), date(2026, 10, 15))


def test_series_that_do_not_overlap_have_no_common_span():
    # a ends in 2020 and b starts in 2021: no Max window, rather than one that ends before it
    # starts.
    a = _daily(date(2020, 1, 2), [(date(2020, 1, 3), 0.01)])
    b = _daily(date(2021, 1, 4), [(date(2021, 1, 5), 0.01)])

    assert common_span(a, b) is None


def test_series_that_only_touch_have_no_common_span():
    # b is based on a's last day, so no return is dated inside both.
    a = _daily(date(2020, 1, 2), [(date(2020, 1, 3), 0.01)])
    b = _daily(date(2020, 1, 3), [(date(2020, 1, 6), 0.01)])

    assert common_span(a, b) is None


def test_covers_needs_the_base_on_or_before_the_start_and_data_through_the_end():
    series = _daily(date(2020, 1, 2), [(date(2020, 1, 3), 0.01), (date(2026, 10, 16), 0.01)])

    assert covers(series, Window("5Y", date(2021, 10, 16), date(2026, 10, 16))) is True
    assert covers(series, Window("Max", date(2020, 1, 2), date(2026, 10, 16))) is True
    # A late starter doesn't cover 10Y.
    assert covers(series, Window("10Y", date(2016, 10, 16), date(2026, 10, 16))) is False
    # Nor a window past its last date.
    assert covers(series, Window("1Y", date(2025, 10, 17), date(2026, 10, 17))) is False


def test_slice_excludes_the_start_includes_the_end_and_rebases():
    series = _daily(
        date(2026, 1, 2),
        [
            (date(2026, 1, 5), 0.01),
            (date(2026, 1, 6), 0.02),
            (date(2026, 1, 7), None),
            (date(2026, 1, 8), 0.04),
            (date(2026, 1, 9), 0.05),
        ],
    )

    sliced = slice_series(series, Window("x", date(2026, 1, 6), date(2026, 1, 8)))

    # The 01-06 return moves the base, so it isn't inside a window based on 01-06.
    assert sliced.base_date == date(2026, 1, 6)
    assert sliced.dates == (date(2026, 1, 7), date(2026, 1, 8))
    assert sliced.values == (None, 0.04)
    assert sliced.frequency is Frequency.DAILY


def test_slice_refuses_a_window_the_series_does_not_cover():
    series = _daily(date(2026, 1, 2), [(date(2026, 1, 5), 0.01)])

    with pytest.raises(ValueError):
        slice_series(series, Window("x", date(2025, 12, 31), date(2026, 1, 5)))
