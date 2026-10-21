"""labs_risk_view (RK-9): what /labs shows of the backtest's risk section -- each figure as a
cell (formatted per spec §10, or greyed with its reason, never 0 or blank), the mix's grid of
measures by window, the mix-and-assets comparison over one window, and the drawdown chart's
data island."""

import logging
from datetime import date

import pytest
from web.labs_risk_view import (
    RISK_MEASURES,
    RiskCell,
    build_comparison,
    build_drawdown_payload,
    build_mix_grid,
    figure_cell,
    flag_notes,
)
from web.portfolio_client.base import (
    BACKTEST_RISK_WINDOWS,
    RISK_FIGURE_KEYS,
    BacktestRisk,
    BacktestRowRisk,
    BacktestWindowRisk,
    DrawdownSeries,
    RiskFigure,
    RiskFreeStatus,
)

MINUS = "−"
END = date(2026, 10, 9)
START = date(2025, 10, 9)


def _figure(value, *, frequency="daily", n_obs=252, flags=(), reason=None, se=None):
    return RiskFigure(
        value=value,
        window_start=START,
        window_end=END,
        frequency=frequency,
        q=252.0 if frequency == "daily" else 52.0,
        n_obs=n_obs,
        n_gaps=0,
        flags=tuple(flags),
        reason=reason,
        standard_error=se,
    )


def _covered(**figures):
    defaults = {
        "cumulative_return": _figure(0.12),
        "cagr": _figure(0.0712, flags=("short_window",)),
        "volatility": _figure(0.1543, frequency="weekly", n_obs=52),
        "downside_deviation": _figure(0.09, frequency="weekly", n_obs=52),
        "sharpe": _figure(0.8649, frequency="weekly", n_obs=52, se=0.5912),
        "sortino": _figure(1.234, frequency="weekly", n_obs=52),
        "calmar": _figure(0.39, flags=("short_window",)),
        "max_drawdown": _figure(-0.184),
        "var_95": _figure(0.015),
        "cvar_95": _figure(0.022),
        "var_99": _figure(0.03),
        "cvar_99": _figure(0.04),
    }
    defaults.update(figures)
    assert set(defaults) == set(RISK_FIGURE_KEYS)
    return BacktestWindowRisk(
        start=START,
        end=END,
        covered=True,
        reason=None,
        figures=defaults,
        peak_date=date(2026, 2, 2),
        trough_date=date(2026, 4, 7),
        recovery_date=None,
        max_days_under_water=249,
    )


def _uncovered(reason="limited by AAPL (history from 2021-11-01)"):
    return BacktestWindowRisk(
        start=None,
        end=END,
        covered=False,
        reason=reason,
        figures={key: None for key in RISK_FIGURE_KEYS},
        peak_date=None,
        trough_date=None,
        recovery_date=None,
        max_days_under_water=None,
    )


def _row(ticker, **windows):
    all_windows = {w: _covered() for w in BACKTEST_RISK_WINDOWS}
    all_windows.update(windows)
    return BacktestRowRisk(ticker=ticker, windows=all_windows)


def _risk(mix=None, assets=None, drawdown=None):
    return BacktestRisk(
        label="buy & hold (drifting)",
        end_date=END,
        common_start=date(2021, 11, 1),
        limited_by=("AAPL",),
        risk_free=RiskFreeStatus(available=True, reason=None, first_date=date(1999, 1, 4)),
        mix=mix or _row(None),
        assets=tuple(assets) if assets is not None else (_row("VWCE.DE"), _row("AAPL")),
        drawdown=drawdown,
    )


# --- measures -----------------------------------------------------------------------------


def test_the_six_measures_in_order_with_their_frequency():
    # RK-9's list, each at spec §4.3's frequency.
    assert [(m.key, m.frequency) for m in RISK_MEASURES] == [
        ("cagr", "daily"),
        ("volatility", "weekly"),
        ("max_drawdown", "daily"),
        ("sharpe", "weekly"),
        ("sortino", "weekly"),
        ("calmar", "daily"),
    ]


def test_every_measure_links_to_a_glossary_entry():
    assert all(m.anchor for m in RISK_MEASURES)


# --- cells ----------------------------------------------------------------------------------


def _measure(key):
    return next(m for m in RISK_MEASURES if m.key == key)


def test_a_cagr_is_a_signed_percentage_colored_as_a_return():
    gain = figure_cell(_figure(0.0712), _measure("cagr"))
    loss = figure_cell(_figure(-0.0712), _measure("cagr"))

    assert (gain.text, gain.css_class) == ("+7.1%", "gain")
    assert (loss.text, loss.css_class) == (f"{MINUS}7.1%", "loss")


def test_a_cagr_that_rounds_to_zero_is_neither_gain_nor_loss():
    cell = figure_cell(_figure(-0.0004), _measure("cagr"))

    assert (cell.text, cell.css_class) == ("0.0%", "")


def test_a_drawdown_is_negative_and_neutral():
    # Agreed for RK-9: a drawdown is a distance below a peak, not a loss figure, so no oxblood.
    cell = figure_cell(_figure(-0.184), _measure("max_drawdown"))

    assert (cell.text, cell.css_class) == (f"{MINUS}18.4%", "")


def test_a_volatility_is_an_unsigned_percentage_to_one_decimal():
    cell = figure_cell(_figure(0.1543, frequency="weekly"), _measure("volatility"))

    assert (cell.text, cell.css_class) == ("15.4%", "")


def test_a_sharpe_has_two_places_and_its_standard_error():
    cell = figure_cell(_figure(0.8649, frequency="weekly", se=0.5912), _measure("sharpe"))

    assert cell == RiskCell(
        text="0.86", css_class="", standard_error="± 0.59", reason=None, notes=()
    )


def test_a_negative_ratio_keeps_its_minus_and_no_color():
    cell = figure_cell(_figure(-0.124, frequency="weekly"), _measure("sortino"))

    assert (cell.text, cell.css_class, cell.standard_error) == (f"{MINUS}0.12", "", None)


def test_a_greyed_figure_shows_its_reason_never_a_number():
    reason = "insufficient history (have 40, need 52)"
    figure = _figure(None, frequency="weekly", n_obs=40, reason=reason)

    cell = figure_cell(figure, _measure("volatility"))

    assert cell == RiskCell(text=None, css_class="", standard_error=None, reason=reason, notes=())


def test_a_greyed_figure_without_a_reason_still_says_something(caplog):
    # portfolio always gives one (R9); if it ever doesn't, the cell is still not blank.
    with caplog.at_level(logging.WARNING, logger="web.labs_risk_view"):
        cell = figure_cell(_figure(None), _measure("calmar"))

    assert cell.text is None
    assert cell.reason == "not available"
    assert "no reason" in caplog.text


def test_a_figures_flags_become_notes():
    cell = figure_cell(_figure(0.39, flags=("data_gaps", "short_window")), _measure("calmar"))

    assert cell.notes == ("over 2% of days missing", "under 3 years")


# --- flags ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flag", "note"),
    [
        ("short_window", "under 3 years"),
        ("data_gaps", "over 2% of days missing"),
        ("has_suspect_rows", "includes a suspect price"),
        ("rf_partial", "risk-free rate partly missing"),
        ("nonsync_daily", "markets close at different times"),
        ("tail_unstable", "few observations in the tail"),
        ("survivorship", "today's index members only"),
    ],
)
def test_each_flag_has_a_note(flag, note):
    assert flag_notes((flag,)) == (note,)


def test_insufficient_history_has_no_note():
    # The greyed figure's reason already says it.
    assert flag_notes(("insufficient_history",)) == ()


def test_an_unknown_flag_is_shown_as_its_code_and_logged(caplog):
    with caplog.at_level(logging.WARNING, logger="web.labs_risk_view"):
        notes = flag_notes(("new_flag",))

    assert notes == ("new_flag",)
    assert "new_flag" in caplog.text


# --- the mix grid ---------------------------------------------------------------------------


def test_the_mix_grid_has_a_column_per_window_and_a_row_per_measure():
    grid = build_mix_grid(_risk())

    assert [c.window for c in grid.columns] == list(BACKTEST_RISK_WINDOWS)
    assert [r.measure.key for r in grid.rows] == [m.key for m in RISK_MEASURES]
    assert all(len(r.cells) == len(BACKTEST_RISK_WINDOWS) for r in grid.rows)


def test_a_grid_column_carries_its_dates_and_the_returns_used():
    column = build_mix_grid(_risk()).columns[0]

    assert (column.start, column.end, column.covered) == (START, END, True)
    # Daily from the drawdown, weekly from the volatility: what §10 asks to show beside each
    # figure, shown once per window since every figure in it shares them.
    assert column.returns_used == "252 daily · 52 weekly"


def test_an_uncovered_window_has_no_cells_and_one_note():
    reason = "limited by AAPL (history from 2021-11-01)"
    mix = _row(None, **{"5Y": _uncovered(reason), "10Y": _uncovered(reason)})

    grid = build_mix_grid(_risk(mix=mix))

    five, ten = BACKTEST_RISK_WINDOWS.index("5Y"), BACKTEST_RISK_WINDOWS.index("10Y")
    assert all(row.cells[five] is None and row.cells[ten] is None for row in grid.rows)
    assert grid.columns[ten].covered is False
    assert grid.columns[ten].returns_used is None
    # One note for the windows that share a reason, not one per cell.
    assert [(n.windows, n.reason) for n in grid.uncovered] == [(("5Y", "10Y"), reason)]


def test_uncovered_windows_with_different_reasons_get_a_note_each():
    mix = _row(None, **{"10Y": _uncovered("limited by AAPL"), "Max": _uncovered("no overlap")})

    grid = build_mix_grid(_risk(mix=mix))

    assert [(n.windows, n.reason) for n in grid.uncovered] == [
        (("10Y",), "limited by AAPL"),
        (("Max",), "no overlap"),
    ]


# --- the comparison -------------------------------------------------------------------------


def test_the_comparison_has_the_mix_then_each_asset():
    comparison = build_comparison(_risk(), "3Y")

    assert comparison.window == "3Y"
    assert [row.ticker for row in comparison.rows] == [None, "VWCE.DE", "AAPL"]
    assert all(len(row.cells) == len(RISK_MEASURES) for row in comparison.rows)
    assert comparison.rows[0].cells[0].text == "+7.1%"


def test_each_comparison_row_has_its_own_returns_used():
    # The window is common, but an asset counts its own trading days.
    asset = _row(
        "AAPL",
        **{
            "1Y": _covered(
                max_drawdown=_figure(-0.2, n_obs=250),
                volatility=_figure(0.2, frequency="weekly", n_obs=52),
            )
        },
    )

    comparison = build_comparison(_risk(assets=[asset]), "1Y")

    assert [row.returns_used for row in comparison.rows] == [
        "252 daily · 52 weekly",
        "250 daily · 52 weekly",
    ]


def test_an_uncovered_comparison_window_has_its_reason_and_no_rows():
    reason = "limited by AAPL (history from 2021-11-01)"
    mix = _row(None, **{"10Y": _uncovered(reason)})
    assets = [_row("VWCE.DE", **{"10Y": _uncovered(reason)})]

    comparison = build_comparison(_risk(mix=mix, assets=assets), "10Y")

    assert comparison.covered is False
    assert comparison.reason == reason
    assert comparison.rows == ()


# --- the drawdown island --------------------------------------------------------------------


def test_the_drawdown_payload_keeps_its_gaps():
    drawdown = DrawdownSeries(
        period="1Y",
        dates=(date(2026, 10, 7), date(2026, 10, 8), date(2026, 10, 9)),
        values=(0.0, None, -0.123456789),
    )

    assert build_drawdown_payload(drawdown) == {
        "period": "1Y",
        "dates": ["2026-10-07", "2026-10-08", "2026-10-09"],
        # Six significant digits, as the growth island's; a gap date stays null, so the chart
        # breaks there.
        "values": [0.0, None, -0.123457],
    }
