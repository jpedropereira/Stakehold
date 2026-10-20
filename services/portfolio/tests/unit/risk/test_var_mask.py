"""risk/var_mask.py: the days daily VaR may use -- those on which every included asset's
exchange traded (spec R7, §5.5), so carry-forward zeros don't crowd the distribution."""

from datetime import date

import pytest
from portfolio.risk.types import Window
from portfolio.risk.var_mask import var_days

WEEK = Window("x", date(2026, 1, 2), date(2026, 1, 9))
A = {date(2026, 1, d) for d in (5, 6, 7, 8, 9)}


def test_a_day_one_exchange_was_closed_is_dropped():
    b = {date(2026, 1, d) for d in (5, 7, 8, 9)}  # closed 01-06

    assert var_days([A, b], WEEK) == (
        date(2026, 1, 5),
        date(2026, 1, 7),
        date(2026, 1, 8),
        date(2026, 1, 9),
    )


def test_a_single_asset_keeps_its_own_calendar():
    assert var_days([A], WEEK) == tuple(sorted(A))


def test_the_window_excludes_its_start_and_includes_its_end():
    window = Window("x", date(2026, 1, 5), date(2026, 1, 8))

    assert var_days([A], window) == (date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8))


def test_there_must_be_at_least_one_asset():
    with pytest.raises(ValueError):
        var_days([], WEEK)
