"""risk/ex_post.py: the §5.1-5.5 figures on the portfolio's daily TWR (RK-11), over the standard
windows. The measures themselves are tested in test_single_series.py; these tests check what
ex_post_figures adds: the windows, greying a window the history doesn't cover (R9), the weekly
series, the VaR day mask and `nonsync_daily`.

Calendar: 2020-01-03 is a Friday. Fixtures are consecutive weekdays after it, alternating +1% and
-1%. Risk-free: 3.6% a year from 1999-01-04.
"""

from datetime import date, timedelta

import pytest
from portfolio.risk.ex_post import ex_post_figures
from portfolio.risk.risk_free import RatePoint
from portfolio.risk.types import Flag, Frequency, ReturnSeries
from portfolio.risk.windows import years_before

BASE = date(2020, 1, 3)
FIRST_PUBLISHED = date(1999, 1, 4)
RATES = [RatePoint(FIRST_PUBLISHED, 3.6)]


def _weekdays(base, n):
    days, day = [], base
    while len(days) < n:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days.append(day)
    return tuple(days)


def _daily(n, base=BASE, skip=()):
    dates = tuple(d for d in _weekdays(base, n) if d not in skip)
    values = tuple(0.01 if k % 2 == 0 else -0.01 for k in range(len(dates)))
    return ReturnSeries(Frequency.DAILY, base, dates, values)


def _held(daily, tickers=("A",)):
    return {d: frozenset(tickers) for d in daily.dates}


def _figures(daily, *, traded=None, held=None, currencies=None, rates=RATES):
    return ex_post_figures(
        daily,
        traded_days=set(daily.dates) if traded is None else traded,
        held=_held(daily) if held is None else held,
        currencies={"A": "EUR"} if currencies is None else currencies,
        rates=rates,
        first_published=None if rates is None else FIRST_PUBLISHED,
    )


def _by_label(windows):
    return {w.window.label: w for w in windows}


def _all(window):
    """Every RiskFigure a window reports."""
    return [
        window.returns.cumulative_return,
        window.returns.cagr,
        window.weekly.volatility,
        window.weekly.downside_deviation,
        window.weekly.sharpe,
        window.weekly.sortino,
        window.drawdown.max_drawdown,
        window.calmar,
        window.tail.var_95,
        window.tail.cvar_95,
        window.tail.var_99,
        window.tail.cvar_99,
    ]


def test_every_standard_window_ends_at_the_last_date_and_max_starts_at_the_base():
    daily = _daily(530)  # a little over two years

    windows = _by_label(_figures(daily))

    assert list(windows) == ["1Y", "3Y", "5Y", "10Y", "Max"]
    assert {w.window.end for w in windows.values()} == {daily.last_date}
    assert windows["Max"].window.start == BASE
    assert windows["1Y"].window.start == years_before(daily.last_date, 1)


def test_a_covered_window_is_measured():
    daily = _daily(530)

    one_year = _by_label(_figures(daily))["1Y"]

    assert one_year.covered
    assert one_year.returns.cagr.value is not None
    assert one_year.weekly.volatility.value is not None
    assert one_year.weekly.volatility.frequency is Frequency.WEEKLY
    assert one_year.weekly.sharpe.value is not None
    assert one_year.drawdown.max_drawdown.value is not None
    assert one_year.tail.var_95.value is not None
    assert one_year.calmar.window_start == one_year.returns.cagr.window_start


def test_a_window_longer_than_the_history_is_greyed_with_where_the_history_starts():
    # R9: shown greyed with its reason, never a number and never a hidden row.
    daily = _daily(530)

    windows = _by_label(_figures(daily))

    for label in ("3Y", "5Y", "10Y"):
        window = windows[label]
        assert not window.covered
        for figure in _all(window):
            assert figure.value is None
            assert figure.reason == "insufficient history (portfolio history from 2020-01-03)"
            assert figure.flags == frozenset({Flag.INSUFFICIENT_HISTORY})
            assert (figure.n_obs, figure.n_gaps) == (0, 0)
            assert (figure.window_start, figure.window_end) == (
                window.window.start,
                daily.last_date,
            )
        assert window.weekly.volatility.frequency is Frequency.WEEKLY
        assert window.drawdown.peak_date is None
        assert window.drawdown.series == ()


def test_weeks_out_of_the_market_are_null_weeks():
    # Two whole weeks with no TWR dates (a full exit and re-entry): R6 makes each a NULL week,
    # so they count as weekly gaps, while the daily series simply has no dates there.
    out = {date(2020, 6, 8) + timedelta(days=k) for k in range(12)}
    daily = _daily(530, skip=out)

    max_window = _by_label(_figures(daily))["Max"]

    assert max_window.weekly.volatility.n_gaps == 2
    assert max_window.drawdown.max_drawdown.n_gaps == 0


def test_var_keeps_only_the_days_every_held_ticker_traded():
    daily = _daily(530)
    not_traded = set(daily.dates[100:110])

    max_window = _by_label(_figures(daily, traded=set(daily.dates) - not_traded))["Max"]

    assert max_window.tail.var_95.n_obs == len(daily.dates) - 10
    assert max_window.drawdown.max_drawdown.n_obs == len(daily.dates)


@pytest.mark.parametrize(
    "tickers, currencies, nonsync",
    [
        (("A",), {"A": "EUR"}, False),
        (("U",), {"U": "USD"}, True),
        (("A", "B"), {"A": "EUR", "B": "EUR"}, True),
    ],
)
def test_var_is_nonsync_unless_the_window_holds_one_eur_ticker(tickers, currencies, nonsync):
    # A USD ticker in EUR mixes the local and FX moves' timings; two tickers mix two
    # exchanges' closes (spec §5.5).
    daily = _daily(530)

    max_window = _by_label(_figures(daily, held=_held(daily, tickers), currencies=currencies))[
        "Max"
    ]

    for figure in (max_window.tail.var_95, max_window.tail.cvar_99):
        assert (Flag.NONSYNC_DAILY in figure.flags) is nonsync


def test_nonsync_is_judged_on_the_tickers_held_inside_each_window():
    # A USD ticker held only in the first months is in Max's window but not in 1Y's.
    daily = _daily(530)
    held = {d: frozenset({"U"} if d < date(2020, 4, 1) else {"A"}) for d in daily.dates}

    windows = _by_label(_figures(daily, held=held, currencies={"A": "EUR", "U": "USD"}))

    assert Flag.NONSYNC_DAILY in windows["Max"].tail.var_95.flags
    assert Flag.NONSYNC_DAILY not in windows["1Y"].tail.var_95.flags


def test_without_risk_free_data_the_rf_measures_are_greyed_and_the_rest_stand():
    daily = _daily(530)

    one_year = _by_label(_figures(daily, rates=None))["1Y"]

    assert one_year.weekly.volatility.value is not None
    assert one_year.weekly.sharpe.reason == "no risk-free rate data"
    assert one_year.weekly.sortino.reason == "no risk-free rate data"
    assert one_year.returns.cagr.value is not None


def test_a_history_under_a_week_has_greyed_weekly_figures_on_its_max_window():
    daily = _daily(3)

    max_window = _by_label(_figures(daily))["Max"]

    assert max_window.covered
    assert max_window.returns.cumulative_return.value is not None
    assert max_window.weekly.volatility.reason == "insufficient history (have 0, need 52)"
