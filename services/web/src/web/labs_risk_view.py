"""What /labs shows of the backtest's risk section (RK-9): the mix's grid of measures by window,
the mix and its assets compared over one window, and the drawdown chart's data island.

Pure, like labs_view.py: the router fetches, this decides what is shown. It sits apart from
labs_view.py because it owns a different thing: how a statistical estimate is worded (its
format, its greyed reason, its flags), where labs_view.py owns which holdings go in the mix.

Spec §10's metadata is shown, not hovered (title tooltips don't show on touch, see
ui-architecture.md, "Glossary"): each window's dates and the returns it used are on its
column, each measure's frequency on its label, a greyed figure's reason in its cell (R9) and
its flags as notes under it.

Under the measures, both tables carry the figures against the benchmark (RK-14, spec §5.6), in
a group the template heads with the benchmark and its first date. An unavailable benchmark's
cells say "Unavailable", and the heading says why once, as on /risk.
"""

import logging
from dataclasses import dataclass
from datetime import date

from web.formatting import delta_class, pct1, ratio, signed_pct1
from web.labs_view import chart_value
from web.portfolio_client.base import (
    BACKTEST_RISK_WINDOWS,
    BacktestRisk,
    BacktestWindowRisk,
    DrawdownSeries,
    RiskFigure,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Measure:
    key: str  # the figure's key in BacktestWindowRisk.figures
    label: str
    frequency: str  # spec §4.3's, which portfolio's single_series layer enforces
    anchor: str  # its glossary entry
    kind: str  # "return" (a signed, colored %), "pct" (a neutral %), "ratio" or "sharpe"


# RK-9's measures, in the table's order.
RISK_MEASURES = (
    Measure("cagr", "CAGR", "daily", "cagr", "return"),
    Measure("volatility", "Volatility", "weekly", "volatility", "pct"),
    # Neutral, not a loss (agreed for RK-9): a drawdown is a distance below a peak, and an
    # oxblood column of every row's drawdown would read as panic, not as a figure.
    Measure("max_drawdown", "Max drawdown", "daily", "max-drawdown", "pct"),
    Measure("sharpe", "Sharpe", "weekly", "sharpe", "sharpe"),
    Measure("sortino", "Sortino", "weekly", "sortino", "ratio"),
    Measure("calmar", "Calmar", "daily", "calmar", "ratio"),
)

# The figures against the benchmark (RK-14), all weekly (spec §5.6). Neutral: a beta or a
# correlation is neither a gain nor a loss, and neither is how far the mix strayed.
RELATIVE_MEASURES = (
    Measure("beta", "Beta", "weekly", "beta", "ratio"),
    Measure("correlation", "Correlation", "weekly", "correlation", "ratio"),
    Measure("tracking_error", "Tracking error", "weekly", "tracking-error", "pct"),
    Measure("information_ratio", "Information ratio", "weekly", "information-ratio", "ratio"),
)
# What a covered window's relative cell says when the benchmark couldn't be used at all.
_UNAVAILABLE = "Unavailable"

# Each flag portfolio can set (risk/types.py's Flag), as a note under the figure. Not every one
# reaches RK-9's measures (nonsync_daily and tail_unstable are VaR's), but a figure shown here
# later shouldn't need this table changed to be worded.
_FLAG_NOTES = {
    "short_window": "under 3 years",
    "data_gaps": "over 2% of days missing",
    "has_suspect_rows": "includes a suspect price",
    "rf_partial": "risk-free rate partly missing",
    "nonsync_daily": "markets close at different times",
    "tail_unstable": "few observations in the tail",
    "survivorship": "today's index members only",
}
# A greyed figure's reason already says it.
_UNNOTED_FLAGS = {"insufficient_history"}


@dataclass(frozen=True)
class RiskCell:
    """A figure as shown: `text` in mono, or None when greyed and `reason` says why (R9)."""

    text: str | None
    css_class: str  # "gain" or "loss" on a CAGR only; "" otherwise
    standard_error: str | None  # "± 0.59", on a Sharpe
    reason: str | None
    notes: tuple[str, ...]


def flag_notes(flags: tuple[str, ...]) -> tuple[str, ...]:
    """The notes for a figure's flags, in the flags' (sorted) order. An unknown flag shows its
    code rather than nothing: it qualifies the figure, and a code is better than silence."""
    notes = []
    for flag in flags:
        if flag in _UNNOTED_FLAGS:
            continue
        note = _FLAG_NOTES.get(flag)
        if note is None:
            logger.warning("labs: unknown risk flag %r, shown as its code.", flag)
            note = flag
        notes.append(note)
    return tuple(notes)


def figure_cell(figure: RiskFigure, measure: Measure) -> RiskCell:
    if figure.value is None:
        reason = figure.reason
        if reason is None:
            logger.warning("labs: greyed %s with no reason from portfolio.", measure.key)
            reason = "not available"
        return RiskCell(None, "", None, reason, ())
    value = figure.value
    if measure.kind == "return":
        text, css_class = signed_pct1(value), delta_class(value, "pct1")
    elif measure.kind == "pct":
        text, css_class = pct1(value), ""
    else:
        text, css_class = ratio(value), ""
    standard_error = None
    if measure.kind == "sharpe" and figure.standard_error is not None:
        standard_error = f"± {ratio(figure.standard_error)}"
    return RiskCell(text, css_class, standard_error, None, flag_notes(figure.flags))


def _returns_used(window: BacktestWindowRisk) -> str | None:
    """How many returns the window's figures rest on: daily from the drawdown, weekly from the
    volatility. Every daily figure in a window shares one series, as every weekly one does."""
    if not window.covered:
        return None
    daily, weekly = window.figures["max_drawdown"], window.figures["volatility"]
    if daily is None or weekly is None:
        return None
    return f"{daily.n_obs} daily · {weekly.n_obs} weekly"


def _cells(window: BacktestWindowRisk) -> tuple[RiskCell, ...] | None:
    if not window.covered:
        return None
    cells = []
    for measure in RISK_MEASURES:
        figure = window.figures[measure.key]
        if figure is None:
            # portfolio sends every figure of a covered window; a null one is still not a 0.
            logger.warning("labs: covered window with no %s figure.", measure.key)
            cells.append(RiskCell(None, "", None, "not available", ()))
        else:
            cells.append(figure_cell(figure, measure))
    return tuple(cells)


def _relative_cells(
    window: BacktestWindowRisk, benchmark_available: bool
) -> tuple[RiskCell, ...] | None:
    if not window.covered:
        return None
    if window.relative is None:
        # portfolio sends them on every covered window; a missing set is still not a 0.
        logger.warning("labs: covered window with no relative figures.")
        return tuple(RiskCell(None, "", None, "not available", ()) for _ in RELATIVE_MEASURES)
    cells = []
    for measure in RELATIVE_MEASURES:
        cell = figure_cell(window.relative[measure.key], measure)
        if cell.text is None and not benchmark_available:
            # Every figure carries "benchmark unavailable: ..."; the heading says it once.
            cell = RiskCell(None, "", None, _UNAVAILABLE, ())
        cells.append(cell)
    return tuple(cells)


@dataclass(frozen=True)
class WindowColumn:
    window: str
    start: date | None
    end: date
    covered: bool
    returns_used: str | None  # None when not covered


@dataclass(frozen=True)
class GridRow:
    measure: Measure
    cells: tuple[RiskCell | None, ...]  # one per window; None where the window isn't covered


@dataclass(frozen=True)
class UncoveredNote:
    windows: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class MixGrid:
    columns: tuple[WindowColumn, ...]
    rows: tuple[GridRow, ...]
    relative_rows: tuple[GridRow, ...]  # against the benchmark, one per RELATIVE_MEASURES
    uncovered: tuple[UncoveredNote, ...]


def build_mix_grid(risk: BacktestRisk) -> MixGrid:
    """The mix's measures (rows) by window (columns). An uncovered window has no cells: its
    reason is the same on every row (one common window, RK-8), so it is said once, under the
    table, for every window that shares it."""
    windows = [risk.mix.windows[w] for w in BACKTEST_RISK_WINDOWS]
    columns = tuple(
        WindowColumn(name, window.start, window.end, window.covered, _returns_used(window))
        for name, window in zip(BACKTEST_RISK_WINDOWS, windows)
    )
    cells = [_cells(window) for window in windows]
    rows = tuple(
        GridRow(measure, tuple(None if c is None else c[i] for c in cells))
        for i, measure in enumerate(RISK_MEASURES)
    )
    relative = [_relative_cells(window, risk.benchmark.available) for window in windows]
    relative_rows = tuple(
        GridRow(measure, tuple(None if c is None else c[i] for c in relative))
        for i, measure in enumerate(RELATIVE_MEASURES)
    )
    uncovered: list[UncoveredNote] = []
    for name, window in zip(BACKTEST_RISK_WINDOWS, windows):
        if window.covered:
            continue
        reason = window.reason or "not enough history"
        same = next((n for n in uncovered if n.reason == reason), None)
        if same is None:
            uncovered.append(UncoveredNote((name,), reason))
        else:
            uncovered[uncovered.index(same)] = UncoveredNote(same.windows + (name,), reason)
    return MixGrid(columns, rows, relative_rows, tuple(uncovered))


@dataclass(frozen=True)
class ComparisonRow:
    ticker: str | None  # None for the mix
    cells: tuple[RiskCell, ...]  # one per measure
    returns_used: str | None
    relative_cells: tuple[RiskCell, ...]  # one per RELATIVE_MEASURES


@dataclass(frozen=True)
class Comparison:
    """The mix and each asset over one window. Every row shares the window (R10), so it is
    covered for all of them or for none, and `reason` then says why."""

    window: str
    start: date | None
    end: date
    covered: bool
    reason: str | None
    rows: tuple[ComparisonRow, ...]  # empty when not covered


def build_comparison(risk: BacktestRisk, window: str) -> Comparison:
    mix = risk.mix.windows[window]
    if not mix.covered:
        return Comparison(window, mix.start, mix.end, False, mix.reason, ())
    rows = []
    for row in (risk.mix, *risk.assets):
        figures = row.windows[window]
        cells = _cells(figures)
        relative = _relative_cells(figures, risk.benchmark.available)
        if cells is None or relative is None:
            # The common window covers every row or none; if it ever doesn't, say so in place.
            logger.warning("labs: %s doesn't cover %s, though the mix does.", row.ticker, window)
            reason = figures.reason or "not enough history"
            cells = tuple(RiskCell(None, "", None, reason, ()) for _ in RISK_MEASURES)
            relative = tuple(RiskCell(None, "", None, reason, ()) for _ in RELATIVE_MEASURES)
        rows.append(ComparisonRow(row.ticker, cells, _returns_used(figures), relative))
    return Comparison(window, mix.start, mix.end, True, None, tuple(rows))


def build_drawdown_payload(drawdown: DrawdownSeries) -> dict:
    """The drawdown chart's data island (static/labs_drawdown_chart.js). A gap date stays None,
    which Chart.js draws as a break in the line."""
    return {
        "period": drawdown.period,
        "dates": [d.isoformat() for d in drawdown.dates],
        "values": [None if v is None else chart_value(v) for v in drawdown.values],
    }
