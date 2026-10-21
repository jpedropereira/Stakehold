"""labs/risk.py tested as a pure function: the backtest's inputs and Labs' growth series in, the
risk table (spec §5.1-5.5 per standard window, for the drifting mix and each asset) and the mix's
drawdown series out.

Hand-checked values are worked out in the comment beside them. Long fixtures are consecutive
weekdays with a fixed, repeating return pattern, so no test depends on randomness.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from portfolio.benchmark.base import Benchmark, BenchmarkUnavailable, build_benchmark
from portfolio.datastore_client.base import FxPoint, PricePoint, TickerInfo
from portfolio.eur_returns.fetch import Fetched
from portfolio.labs.calculator import AssetInput, Period, compute_backtest
from portfolio.labs.risk import (
    LABEL,
    BenchmarkStatus,
    RiskFree,
    RiskFreeMissing,
    RiskFreeStatus,
    compute_backtest_risk,
    risk_free_span,
)
from portfolio.risk.ex_ante import constant_weight_series
from portfolio.risk.relative import relative_figures
from portfolio.risk.risk_free import RatePoint
from portfolio.risk.types import Flag, Frequency, ReturnSeries, Window
from portfolio.risk.windows import StandardWindow

FIRST_PUBLISHED = date(1999, 1, 4)
RISK_FREE = RiskFree(rates=[RatePoint(FIRST_PUBLISHED, 2.0)], first_published=FIRST_PUBLISHED)

D0 = date(2026, 1, 5)  # a Monday
D1 = date(2026, 1, 6)
D2 = date(2026, 1, 7)
D3 = date(2026, 1, 8)

# Ten weekday returns in a fixed cycle: mean +0.04%, enough spread for every measure to exist.
_PATTERN = [
    "0.012",
    "-0.009",
    "0.004",
    "-0.015",
    "0.008",
    "0.003",
    "-0.006",
    "0.011",
    "-0.004",
    "0.0006",
]


def _asset(ticker, weight, points, fx=None):
    """points: (date, simple_return as a string, or None for a gap)."""
    prices = [PricePoint(d, Decimal("100"), None if r is None else Decimal(r)) for d, r in points]
    if fx is not None:
        fx = [FxPoint(d, Decimal(rate)) for d, rate in fx]
    return AssetInput(ticker=ticker, weight=Decimal(weight), prices=prices, fx=fx)


def _weekdays(start, end, skip=()):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5 and day not in skip:
            days.append(day)
        day += timedelta(days=1)
    return days


def _long(ticker, weight, start, end, *, offset=0, gaps=(), skip=(), fx=False):
    """A weekday history from `start` to `end`: NULL on its first day (as data-store's is) and
    on `gaps`, missing on `skip`, otherwise _PATTERN shifted by `offset`. With `fx`, a USD
    asset whose rate drifts in its own cycle."""
    days = _weekdays(start, end, skip)
    points = [(days[0], None)]
    for k, day in enumerate(days[1:]):
        points.append((day, None if day in gaps else _PATTERN[(k + offset) % len(_PATTERN)]))
    rates = None
    if fx:
        rates = [(day, f"{1.10 + 0.002 * ((k * 3) % 7 - 3):.4f}") for k, day in enumerate(days)]
    return _asset(ticker, weight, points, fx=rates)


NOT_TRACKED = BenchmarkUnavailable("BM", "not_tracked", "not tracked")


def _benchmark(asset: AssetInput, ticker="BM") -> Benchmark:
    """A benchmark with `asset`'s prices, as benchmark/flows.py would build it."""
    currency = "EUR" if asset.fx is None else "USD"
    info = TickerInfo(exists=True, has_returns=True, currency=currency, fx_currency=currency)
    return build_benchmark(ticker, Fetched(info, list(asset.prices), asset.fx))


def _run(assets, growth_period=Period.MAX, risk_free=RISK_FREE, benchmark=NOT_TRACKED):
    backtest = compute_backtest(assets, growth_period=growth_period)
    return backtest, compute_backtest_risk(assets, risk_free, backtest.growth, benchmark)


def _row(risk, ticker):
    return next(row for row in risk.assets if row.ticker == ticker)


# --- the drifting mix (R3) ---


def test_the_mix_is_bought_once_and_drifts_unlike_constant_weights():
    # A doubles then halves; B is flat. Bought 50/50 and held: D1 is 0.5 x 2 + 0.5 x 1 = 1.5,
    # D2 is 0.5 x 1 + 0.5 x 1 = 1.0, so the mix returns +50% then -1/3 and ends flat. Constant
    # weights would return +50% then 0.5 x -50% = -25%, ending at +12.5%.
    a = _asset("A", "0.5", [(D0, None), (D1, "1.0"), (D2, "-0.5")])
    b = _asset("B", "0.5", [(D0, None), (D1, "0"), (D2, "0")])

    _, risk = _run([a, b])

    max_window = risk.mix.windows[StandardWindow.MAX]
    assert max_window.cumulative_return.value == pytest.approx(0.0, abs=1e-12)
    # The drawdown series walks the same path: a peak at 1.5, then 1.0 / 1.5 - 1.
    assert risk.drawdown.dates == [D0, D1, D2]
    assert risk.drawdown.values == pytest.approx([0.0, 0.0, -1 / 3])

    returns = {
        t: ReturnSeries(Frequency.DAILY, D0, (D1, D2), values)
        for t, values in (("A", (1.0, -0.5)), ("B", (0.0, 0.0)))
    }
    constant = constant_weight_series(returns, {"A": 0.5, "B": 0.5})
    assert (1 + constant.values[0]) * (1 + constant.values[1]) - 1 == pytest.approx(0.125)


def test_the_mix_and_assets_match_labs_period_returns():
    # No gaps, one calendar, a USD asset: the float risk path and Labs' Decimal levels agree.
    # Labs' 1Y and 5Y start 12 and 60 months before the end, the spec's 1Y and 5Y windows a
    # year and five years before it: the same dates.
    end = date(2026, 6, 30)
    a = _long("A", "0.7", date(2020, 1, 2), end)
    b = _long("B", "0.3", date(2020, 1, 2), end, offset=3, fx=True)

    backtest, risk = _run([a, b])

    for period, window in ((Period.Y1, StandardWindow.Y1), (Period.Y5, StandardWindow.Y5)):
        assert risk.mix.windows[window].start == backtest.mix.periods[period].start
        assert risk.mix.windows[window].cumulative_return.value == pytest.approx(
            float(backtest.mix.periods[period].return_pct), abs=1e-8
        )
        for row in backtest.assets:
            assert _row(risk, row.ticker).windows[window].cumulative_return.value == (
                pytest.approx(float(row.periods[period].return_pct), abs=1e-8)
            )


# --- gaps (R5) ---


def test_a_null_day_is_a_gap_in_the_stats_but_keeps_its_chart_point():
    # B's D2 return was suppressed. Labs bridges it flat for the growth line; the risk path
    # counts the mix's D2 as a gap, and the drawdown series breaks there.
    a = _asset("A", "0.5", [(D0, None), (D1, "0.10"), (D2, "0.10"), (D3, "-0.10")])
    b = _asset("B", "0.5", [(D0, None), (D1, "0"), (D2, None), (D3, "0")])

    backtest, risk = _run([a, b])

    figure = risk.mix.windows[StandardWindow.MAX].cumulative_return
    assert (figure.n_obs, figure.n_gaps) == (2, 1)
    assert D2 in backtest.growth.dates
    assert risk.drawdown.dates == backtest.growth.dates
    assert risk.drawdown.values[2] is None
    # A's own row has no gap.
    assert _row(risk, "A").windows[StandardWindow.MAX].cumulative_return.n_gaps == 0


# --- the common window (R10, agreed for RK-8) ---


def test_a_young_asset_limits_every_row_to_its_history():
    end = date(2026, 6, 30)
    old = _long("OLD", "0.5", date(2014, 1, 2), end)
    young = _long("NEW", "0.5", date(2024, 3, 4), end, offset=5)

    _, risk = _run([old, young])

    assert risk.end_date == end
    assert risk.common_start == date(2024, 3, 4)
    assert risk.limited_by == ["NEW"]
    for row in (risk.mix, _row(risk, "OLD"), _row(risk, "NEW")):
        for label in (StandardWindow.Y3, StandardWindow.Y5, StandardWindow.Y10):
            window = row.windows[label]
            assert window.covered is False
            assert window.reason == "limited by NEW (history from 2024-03-04)"
            assert window.volatility is None and window.cagr is None
        assert row.windows[StandardWindow.Y1].covered is True
        assert row.windows[StandardWindow.MAX].start == date(2024, 3, 4)
        assert row.windows[StandardWindow.MAX].reason is None


def test_histories_that_do_not_overlap_leave_every_window_uncovered():
    a = _long("A", "0.5", date(2019, 1, 2), date(2020, 6, 30))
    b = _long("B", "0.5", date(2021, 1, 4), date(2022, 6, 30))

    _, risk = _run([a, b], growth_period=Period.M1)

    assert risk.common_start is None
    for row in (risk.mix, *risk.assets):
        assert list(row.windows) == list(StandardWindow)
        for window in row.windows.values():
            assert window.covered is False
            assert window.reason == "the assets' histories don't overlap"


def test_every_row_reports_every_standard_window():
    _, risk = _run([_long("A", "1", date(2025, 1, 2), date(2026, 6, 30))])

    assert risk.label == LABEL == "buy & hold (drifting)"
    assert list(risk.mix.windows) == list(StandardWindow)
    assert [row.ticker for row in risk.assets] == ["A"]
    assert risk.mix.ticker is None


# --- frequencies (§4.3) and minimums (R9) ---


def test_volatility_and_ratios_are_weekly_and_the_rest_daily():
    _, risk = _run([_long("A", "1", date(2023, 1, 2), date(2026, 6, 30))])

    window = risk.mix.windows[StandardWindow.Y3]
    for figure in (window.volatility, window.downside_deviation, window.sharpe, window.sortino):
        assert figure.frequency is Frequency.WEEKLY
        assert figure.q == 52
        assert figure.value is not None
    for figure in (window.cumulative_return, window.cagr, window.max_drawdown, window.var_95):
        assert figure.frequency is Frequency.DAILY
    assert window.sharpe.standard_error is not None
    assert window.calmar.value is not None


def test_a_one_asset_mix_measures_the_same_as_the_asset():
    _, risk = _run([_long("A", "1", date(2023, 1, 2), date(2026, 6, 30))])

    mix, asset = risk.mix.windows[StandardWindow.Y3], _row(risk, "A").windows[StandardWindow.Y3]
    for name in ("cumulative_return", "volatility", "sharpe", "max_drawdown", "var_95"):
        assert getattr(mix, name).value == pytest.approx(getattr(asset, name).value, rel=1e-9)
        assert getattr(mix, name).n_obs == getattr(asset, name).n_obs


def test_a_short_common_history_greys_figures_with_the_r9_reason():
    # About eight months of weekdays: under 200 daily returns and 52 weeks.
    _, risk = _run([_long("A", "1", date(2025, 11, 3), date(2026, 6, 30))])

    window = risk.mix.windows[StandardWindow.MAX]
    assert window.covered is True
    assert window.max_drawdown.value is None
    assert window.max_drawdown.reason.startswith("insufficient history (have ")
    assert window.max_drawdown.reason.endswith(", need 200)")
    assert window.sharpe.reason.endswith(", need 52)")
    assert window.cumulative_return.value is not None
    assert risk.mix.windows[StandardWindow.Y1].covered is False


# --- VaR's day mask (R7) and nonsync_daily ---


def test_var_keeps_only_days_every_asset_traded():
    end = date(2026, 6, 30)
    holidays = {date(2025, 12, 25), date(2025, 12, 26), date(2026, 4, 3), date(2026, 4, 6)}
    a = _long("A", "0.5", date(2024, 1, 2), end)
    b = _long("B", "0.5", date(2024, 1, 2), end, offset=2, skip=holidays)

    _, risk = _run([a, b])

    mix_var = risk.mix.windows[StandardWindow.Y1].var_95
    a_var = _row(risk, "A").windows[StandardWindow.Y1].var_95
    b_var = _row(risk, "B").windows[StandardWindow.Y1].var_95
    assert a_var.n_obs == b_var.n_obs + 4
    assert mix_var.n_obs == b_var.n_obs


def test_nonsync_daily_marks_a_mix_or_asset_with_a_non_eur_asset():
    end = date(2026, 6, 30)
    eur = _long("EUR", "0.5", date(2023, 1, 2), end)
    usd = _long("USD", "0.5", date(2023, 1, 2), end, offset=4, fx=True)

    _, mixed = _run([eur, usd])
    _, eur_only = _run([eur, _long("EUR2", "0.5", date(2023, 1, 2), end, offset=4)])

    window = StandardWindow.Y3
    assert Flag.NONSYNC_DAILY in mixed.mix.windows[window].var_95.flags
    assert Flag.NONSYNC_DAILY in _row(mixed, "USD").windows[window].var_95.flags
    assert Flag.NONSYNC_DAILY not in _row(mixed, "EUR").windows[window].var_95.flags
    assert Flag.NONSYNC_DAILY not in eur_only.mix.windows[window].var_95.flags


# --- the risk-free rate ---


def test_without_risk_free_data_the_excess_measures_are_greyed():
    assets = [_long("A", "1", date(2023, 1, 2), date(2026, 6, 30))]

    _, risk = _run(assets, risk_free=RiskFreeMissing("no risk-free rate data"))

    assert risk.risk_free == RiskFreeStatus(False, "no risk-free rate data", None)
    for row in (risk.mix, _row(risk, "A")):
        window = row.windows[StandardWindow.Y3]
        assert window.volatility.value is not None
        for figure in (window.sharpe, window.sortino, window.downside_deviation):
            assert figure.value is None
            assert figure.reason == "no risk-free rate data"


def test_an_available_risk_free_series_reports_its_first_date():
    _, risk = _run([_long("A", "1", date(2025, 1, 2), date(2026, 6, 30))])

    assert risk.risk_free == RiskFreeStatus(True, None, FIRST_PUBLISHED)


def test_a_risk_free_series_with_no_rate_by_the_common_start_greys_rather_than_raises():
    # The series began in 1999 but nothing was fetched on or before 2023-01-02: data-store's
    # rates stopped updating before the window. Forward-filling from nothing would invent a rate.
    stale = RiskFree(rates=[RatePoint(date(2024, 1, 2), 2.0)], first_published=FIRST_PUBLISHED)

    _, risk = _run([_long("A", "1", date(2023, 1, 2), date(2026, 6, 30))], risk_free=stale)

    reason = "no risk-free rate on or before 2023-01-02"
    assert risk.risk_free == RiskFreeStatus(False, reason, FIRST_PUBLISHED)
    window = risk.mix.windows[StandardWindow.Y3]
    assert window.volatility.value is not None
    assert window.sharpe.reason == reason


def test_a_window_before_the_risk_free_series_needs_no_rate_before_it():
    # A history from 1998 starts before €STR/EONIA's first date: R4's rf = 0 there, not an error.
    end = date(2000, 6, 30)
    rates = RiskFree(rates=[RatePoint(FIRST_PUBLISHED, 3.0)], first_published=FIRST_PUBLISHED)

    _, risk = _run([_long("A", "1", date(1998, 1, 2), end)], risk_free=rates)

    assert risk.risk_free.available is True
    assert Flag.RF_PARTIAL in risk.mix.windows[StandardWindow.MAX].sharpe.flags


def test_risk_free_is_fetched_from_just_before_the_common_start_to_the_end():
    end = date(2026, 6, 30)
    backtest = compute_backtest(
        [_long("A", "0.5", date(2014, 1, 2), end), _long("B", "0.5", date(2024, 3, 4), end)],
        growth_period=Period.Y1,
    )

    assert risk_free_span(backtest) == (date(2024, 2, 23), end)


def test_there_is_no_risk_free_span_without_an_overlap():
    backtest = compute_backtest(
        [
            _long("A", "0.5", date(2019, 1, 2), date(2020, 6, 30)),
            _long("B", "0.5", date(2021, 1, 4), date(2022, 6, 30)),
        ],
        growth_period=Period.Y1,
    )

    assert risk_free_span(backtest) is None


# --- the drawdown chart ---


def test_the_drawdown_series_follows_the_requested_growth_period():
    end = date(2026, 6, 30)
    backtest, risk = _run([_long("A", "1", date(2024, 1, 2), end)], growth_period=Period.M6)

    assert risk.drawdown.period is Period.M6
    assert risk.drawdown.dates == backtest.growth.dates
    assert risk.drawdown.values[0] == 0.0
    assert all(v is None or v <= 0 for v in risk.drawdown.values)


def test_there_is_no_drawdown_series_when_no_asset_covers_the_growth_period():
    backtest, risk = _run([_long("A", "1", date(2025, 1, 2), date(2026, 6, 30))], Period.Y5)

    assert backtest.growth.mix is None
    assert risk.drawdown is None


def test_with_nothing_available_there_is_no_risk_section():
    unavailable = _asset("A", "1", [])

    _, risk = _run([unavailable])

    assert risk is None


# --- relative to the benchmark (RK-13, spec §5.6) ---

END = date(2026, 6, 30)


def _window(window_risk, label):
    return Window(label, window_risk.start, window_risk.end)


def test_every_row_is_measured_against_the_benchmark():
    a = _long("A", "0.6", date(2020, 1, 2), END)
    b = _long("B", "0.4", date(2020, 1, 2), END, offset=3, fx=True)
    benchmark = _benchmark(_long("BM", "1", date(2018, 1, 2), END, offset=7))

    _, risk = _run([a, b], benchmark=benchmark)

    for label in (StandardWindow.Y3, StandardWindow.Y5, StandardWindow.MAX):
        for row, asset in ((_row(risk, "A"), a), (_row(risk, "B"), b)):
            window = row.windows[label]
            own = _benchmark(asset, asset.ticker).weekly
            assert window.relative == relative_figures(
                own, benchmark.weekly, _window(window, label)
            )
        mix = risk.mix.windows[label].relative
        assert mix.beta.value is not None
        assert mix.tracking_error.value is not None
        assert mix.beta.frequency is Frequency.WEEKLY


def test_a_mix_of_the_benchmark_alone_tracks_it():
    a = _long("A", "1", date(2020, 1, 2), END)

    _, risk = _run([a], benchmark=_benchmark(a))

    asset = _row(risk, "A").windows[StandardWindow.Y5].relative
    assert asset.beta.value == pytest.approx(1.0)
    assert asset.correlation.value == pytest.approx(1.0)
    assert asset.tracking_error.value == 0.0
    assert asset.information_ratio.reason == "no tracking difference"
    mix = risk.mix.windows[StandardWindow.Y5].relative
    assert mix.beta.value == pytest.approx(1.0, abs=1e-9)
    assert mix.tracking_error.value == pytest.approx(0.0, abs=1e-9)


def test_a_window_the_benchmark_does_not_cover_is_greyed_with_its_first_day():
    # Ten years of assets against a benchmark from 2019-07-29 (VWCE.DE's first day): 10Y is
    # greyed and names that day; nothing measures it against another index.
    assets = [_long("A", "1", date(2015, 1, 2), END)]
    vwce = _benchmark(_long("VWCE.DE", "1", date(2019, 7, 29), END), "VWCE.DE")

    _, risk = _run(assets, benchmark=vwce)

    for row in (risk.mix, _row(risk, "A")):
        greyed = row.windows[StandardWindow.Y10].relative
        for figure in (
            greyed.beta,
            greyed.correlation,
            greyed.tracking_error,
            greyed.information_ratio,
        ):
            assert figure.value is None
            assert figure.reason == "benchmark history too short (from 2019-07-29)"
            assert Flag.INSUFFICIENT_HISTORY in figure.flags
        # Max starts with A, before the benchmark, so it is greyed too.
        assert row.windows[StandardWindow.MAX].relative.beta.reason == (
            "benchmark history too short (from 2019-07-29)"
        )
        assert row.windows[StandardWindow.Y5].relative.beta.value is not None
    assert risk.benchmark == BenchmarkStatus(
        ticker="VWCE.DE",
        available=True,
        reason=None,
        first_date=date(2019, 7, 29),
        growth=None,
        growth_reason="benchmark history too short (from 2019-07-29)",
    )


def test_an_uncovered_row_window_has_no_relative_figures():
    old = _long("OLD", "0.5", date(2014, 1, 2), END)
    young = _long("NEW", "0.5", date(2024, 3, 4), END, offset=5)

    _, risk = _run([old, young], benchmark=_benchmark(old))

    assert risk.mix.windows[StandardWindow.Y5].covered is False
    assert risk.mix.windows[StandardWindow.Y5].relative is None
    assert risk.mix.windows[StandardWindow.Y1].relative is not None


def test_an_unavailable_benchmark_greys_the_relative_figures_and_nothing_else():
    a = _long("A", "1", date(2020, 1, 2), END)

    _, risk = _run([a], benchmark=BenchmarkUnavailable("NOPE", "not_tracked", "not tracked"))

    window = risk.mix.windows[StandardWindow.Y3]
    assert window.relative.beta.value is None
    assert window.relative.beta.reason == "benchmark unavailable: not tracked"
    assert window.volatility.value is not None
    assert risk.benchmark == BenchmarkStatus(
        ticker="NOPE",
        available=False,
        reason="not tracked",
        first_date=None,
        growth=None,
        growth_reason="benchmark unavailable: not tracked",
    )


def test_the_benchmark_line_is_priced_as_labs_prices_an_asset():
    a = _long("A", "0.5", date(2024, 1, 2), END)
    b = _long("B", "0.5", date(2024, 1, 2), END, offset=3, fx=True)
    # A USD benchmark with B's prices: its line must equal B's own line, gaps bridged and FX
    # applied as Labs does.
    benchmark = _benchmark(b)

    backtest, risk = _run([a, b], growth_period=Period.M6, benchmark=benchmark)

    assert risk.benchmark.growth == backtest.growth.assets["B"]
    assert len(risk.benchmark.growth) == len(backtest.growth.dates)
    assert risk.benchmark.growth_reason is None


def test_a_benchmark_starting_after_the_growth_period_has_no_line():
    a = _long("A", "1", date(2024, 1, 2), END)
    late = _benchmark(_long("BM", "1", date(2026, 3, 2), END))

    _, risk = _run([a], growth_period=Period.Y1, benchmark=late)

    assert risk.benchmark.growth is None
    assert risk.benchmark.growth_reason == "benchmark history too short (from 2026-03-02)"
