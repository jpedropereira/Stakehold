"""risk_view.build_ex_post_table (RK-12): the /risk page's ex-post summary -- one row per measure,
one column per standard window, each figure's metadata in its title, and the flags as markers
with a legend of only those present."""

from dataclasses import replace
from datetime import date

import pytest
from web.portfolio_client.base import (
    RISK_FIGURES,
    RISK_WINDOWS,
    ExcludedHolding,
    ExPostRisk,
    ExPostWindow,
    RiskFigure,
)
from web.risk_view import FLAG_NOTES, build_ex_post_table

END = date(2026, 10, 16)
LABEL = "ex-post · invested assets"
_STARTS = {
    "1Y": date(2025, 10, 16),
    "3Y": date(2023, 10, 16),
    "5Y": date(2021, 10, 16),
    "10Y": date(2016, 10, 16),
    "Max": date(2023, 1, 2),
}
_WEEKLY = {"volatility", "downside_deviation", "sharpe", "sortino"}


def _figure(value=0.1, *, start=date(2025, 10, 16), name="cagr", **changes) -> RiskFigure:
    weekly = name in _WEEKLY
    figure = RiskFigure(
        value=value,
        window_start=start,
        window_end=END,
        frequency="weekly" if weekly else "daily",
        q=52.0 if weekly else 251.4,
        n_obs=52 if weekly else 251,
        n_gaps=0,
        flags=(),
        reason=None,
        standard_error=0.31 if name == "sharpe" else None,
    )
    return replace(figure, **changes)


def _window(label, covered=True, **figures) -> ExPostWindow:
    start = _STARTS[label]
    all_figures = {name: _figure(start=start, name=name) for name in RISK_FIGURES}
    if not covered:
        reason = "insufficient history (portfolio history from 2023-01-02)"
        all_figures = {
            name: _figure(
                None, start=start, name=name, flags=("insufficient_history",), reason=reason
            )
            for name in RISK_FIGURES
        }
    all_figures.update(figures)
    return ExPostWindow(
        label=label,
        start=start,
        end=END,
        covered=covered,
        figures=all_figures,
        peak_date=date(2026, 2, 2) if covered else None,
        trough_date=date(2026, 4, 7) if covered else None,
        recovery_date=None,
        max_days_under_water=256 if covered else None,
    )


def _risk(windows=None, **changes) -> ExPostRisk:
    risk = ExPostRisk(
        label=LABEL,
        base_date=date(2023, 1, 2),
        end_date=END,
        rf_available=True,
        windows=(
            tuple(_window(w, covered=w not in ("5Y", "10Y")) for w in RISK_WINDOWS)
            if windows is None
            else windows
        ),
        excluded=(),
    )
    return replace(risk, **changes)


def _row(table, key):
    return next(row for row in table.rows if row.key == key)


def test_rows_are_the_measures_in_reading_order():
    table = build_ex_post_table(_risk())

    assert [row.key for row in table.rows] == [
        "cumulative_return",
        "cagr",
        "volatility",
        "downside_deviation",
        "max_drawdown",
        "sharpe",
        "sortino",
        "calmar",
        "var_95",
        "cvar_95",
        "var_99",
        "cvar_99",
    ]
    assert sorted(row.key for row in table.rows) == sorted(RISK_FIGURES)


def test_columns_are_the_windows_with_their_dates():
    table = build_ex_post_table(_risk())

    assert [c.label for c in table.columns] == list(RISK_WINDOWS)
    one_y = table.columns[0]
    assert (one_y.start, one_y.end, one_y.covered, one_y.reason) == (
        date(2025, 10, 16),
        END,
        True,
        None,
    )


def test_an_uncovered_window_says_why_once_in_its_header():
    table = build_ex_post_table(_risk())

    five_y = table.columns[2]
    assert five_y.covered is False
    assert five_y.reason == "insufficient history (portfolio history from 2023-01-02)"
    cell = _row(table, "volatility").cells[2]
    assert cell.value is None
    # The column already says why, so its cells don't repeat it twelve times.
    assert cell.reason is None
    # No marker either: the greyed cell already says it.
    assert cell.markers == ()


@pytest.mark.parametrize(
    ("key", "kind"),
    [
        ("cumulative_return", "return"),
        ("cagr", "return"),
        ("volatility", "pct1"),
        ("downside_deviation", "pct1"),
        ("max_drawdown", "pct1"),
        ("sharpe", "ratio"),
        ("sortino", "ratio"),
        ("calmar", "ratio"),
        ("var_95", "pct1"),
        ("cvar_99", "pct1"),
    ],
)
def test_each_row_has_its_format(key, kind):
    """Only returns are coloured as gains or losses; a volatility, a drawdown or a VaR isn't
    one (brand: forest and oxblood are for gains and losses only)."""
    assert _row(build_ex_post_table(_risk()), key).kind == kind


@pytest.mark.parametrize(
    ("key", "frequency"),
    [("cagr", "daily"), ("volatility", "weekly"), ("sharpe", "weekly"), ("var_95", "daily")],
)
def test_each_row_names_its_frequency(key, frequency):
    assert _row(build_ex_post_table(_risk()), key).frequency == frequency


def test_a_rows_frequency_comes_from_any_window():
    windows = tuple(_window(w, covered=False) for w in RISK_WINDOWS)

    assert _row(build_ex_post_table(_risk(windows)), "sortino").frequency == "weekly"


def test_a_cell_carries_its_value_and_metadata_in_its_title():
    cell = _row(build_ex_post_table(_risk()), "volatility").cells[0]

    assert cell.value == 0.1
    assert cell.reason is None
    assert cell.title == (
        "1Y: 2025-10-16 to 2026-10-16 · weekly · 52 obs · 0 gaps · ex-post · invested assets"
    )


def test_a_greyed_figure_in_a_covered_window_shows_its_own_reason():
    one_y = _window(
        "1Y", sharpe=_figure(None, name="sharpe", reason="no risk-free rate data", flags=())
    )
    windows = (one_y,) + tuple(_window(w) for w in RISK_WINDOWS[1:])

    cell = _row(build_ex_post_table(_risk(windows)), "sharpe").cells[0]

    assert cell.value is None
    assert cell.reason == "no risk-free rate data"


def test_sharpe_carries_its_standard_error():
    table = build_ex_post_table(_risk())

    assert _row(table, "sharpe").cells[0].standard_error == 0.31
    assert _row(table, "sortino").cells[0].standard_error is None


def test_the_max_drawdown_cell_carries_its_dates():
    cell = _row(build_ex_post_table(_risk()), "max_drawdown").cells[0]

    assert cell.drawdown is not None
    assert cell.drawdown.peak == date(2026, 2, 2)
    assert cell.drawdown.trough == date(2026, 4, 7)
    assert cell.drawdown.recovery is None  # not recovered
    assert _row(build_ex_post_table(_risk()), "volatility").cells[0].drawdown is None


def test_a_greyed_max_drawdown_has_no_dates():
    assert _row(build_ex_post_table(_risk()), "max_drawdown").cells[2].drawdown is None


def test_flags_become_markers_and_a_legend_of_only_those_present():
    one_y = _window(
        "1Y",
        var_99=_figure(0.04, name="var_99", flags=("nonsync_daily", "tail_unstable")),
        var_95=_figure(0.02, name="var_95", flags=("nonsync_daily",)),
    )
    windows = (one_y,) + tuple(_window(w) for w in RISK_WINDOWS[1:])

    table = build_ex_post_table(_risk(windows))

    assert [note.flag for note in table.legend] == ["nonsync_daily", "tail_unstable"]
    nonsync, tail = table.legend
    assert (nonsync.marker, tail.marker) == ("1", "2")
    assert _row(table, "var_99").cells[0].markers == ("1", "2")
    assert _row(table, "var_95").cells[0].markers == ("1",)
    assert _row(table, "cagr").cells[0].markers == ()


def test_markers_follow_the_legend_order_not_the_cell_order():
    one_y = _window(
        "1Y",
        cagr=_figure(0.1, name="cagr", flags=("short_window",)),
        var_95=_figure(0.02, name="var_95", flags=("data_gaps",)),
    )
    windows = (one_y,) + tuple(_window(w) for w in RISK_WINDOWS[1:])

    table = build_ex_post_table(_risk(windows))

    assert [note.flag for note in table.legend] == ["data_gaps", "short_window"]
    assert _row(table, "cagr").cells[0].markers == ("2",)


def test_no_flags_means_no_legend():
    assert build_ex_post_table(_risk()).legend == ()


def test_insufficient_history_gets_no_marker():
    """A greyed figure says why in its own cell."""
    one_y = _window(
        "1Y",
        var_99=_figure(
            None, name="var_99", flags=("insufficient_history",), reason="have 251, need 500"
        ),
    )
    windows = (one_y,) + tuple(_window(w) for w in RISK_WINDOWS[1:])

    table = build_ex_post_table(_risk(windows))

    assert _row(table, "var_99").cells[0].markers == ()
    assert table.legend == ()


def test_an_unknown_flag_is_still_shown():
    """A flag portfolio adds later isn't dropped silently."""
    one_y = _window("1Y", cagr=_figure(0.1, name="cagr", flags=("brand_new",)))
    windows = (one_y,) + tuple(_window(w) for w in RISK_WINDOWS[1:])

    table = build_ex_post_table(_risk(windows))

    (note,) = table.legend
    assert note.flag == "brand_new"
    assert "brand_new" in note.text
    assert _row(table, "cagr").cells[0].markers == ("1",)


def test_every_flag_portfolio_sets_has_a_note():
    """portfolio's risk.types.Flag, written out: web can't import portfolio."""
    portfolio_flags = {
        "insufficient_history",
        "short_window",
        "has_suspect_rows",
        "rf_partial",
        "survivorship",
        "nonsync_daily",
        "tail_unstable",
        "data_gaps",
    }

    assert portfolio_flags - {"insufficient_history"} == set(FLAG_NOTES)


def test_the_table_carries_the_label_end_date_rf_and_exclusions():
    excluded = (ExcludedHolding("7203.T", "no_fx_rate", "no FX series for JPY"),)

    table = build_ex_post_table(_risk(rf_available=False, excluded=excluded))

    assert table.label == LABEL
    assert table.base_date == date(2023, 1, 2)
    assert table.end_date == END
    assert table.rf_available is False
    assert table.excluded == excluded


def test_nothing_to_measure_is_none():
    empty = _risk((), base_date=None, end_date=None, rf_available=False)

    assert build_ex_post_table(empty) is None
