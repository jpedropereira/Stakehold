"""The /risk page's ex-post summary table (RK-12): one row per measure, one column per standard
window.

Pure, like labs_view.py: the router fetches, this decides what is shown, so the template only
lays it out. Spec §10's rules land here:
- every figure has its window, frequency, n_obs and the ex-post label: the column header shows
  the window, the row its frequency, and the cell's `title` the rest;
- a greyed figure shows its reason (R9), never a 0 or a blank; in a window the history doesn't
  cover, the column says why once, so its twelve cells don't repeat it;
- each flag a figure carries is a numbered marker, explained once in a legend under the table.

Under the measures, a group of figures against the benchmark (RK-14, spec §5.6), which names
the benchmark and its first date in its heading. When portfolio couldn't measure against it
at all (not tracked, no FX series, data-store down), the heading says why once and each of its
cells says "Unavailable", as an uncovered window's say "Not covered".

The values stay floats: the template formats them (`signed_pct1`, `pct1`, `ratio`) by the row's
kind.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date

from web.portfolio_client.base import (
    BenchmarkStatus,
    ExcludedHolding,
    ExPostRisk,
    ExPostWindow,
    RiskFigure,
)

logger = logging.getLogger(__name__)

# (response name, label, glossary anchor, kind), shared with the ex-ante summary
# (risk_ex_ante_view.py). The kind picks the format: "return" is a signed percentage to 1
# decimal coloured as a gain or a loss, "pct1" a percentage to 1 decimal and "ratio" 2 decimals
# (spec §10), both in plain ink: a volatility, a drawdown or a VaR isn't a gain or a loss.
SUMMARY_ROWS = (
    ("cumulative_return", "Cumulative return", "cumulative-return", "return"),
    ("cagr", "CAGR", "cagr", "return"),
    ("volatility", "Volatility", "volatility", "pct1"),
    ("downside_deviation", "Downside deviation", "downside-deviation", "pct1"),
    ("max_drawdown", "Max drawdown", "max-drawdown", "pct1"),
    ("sharpe", "Sharpe ratio", "sharpe", "ratio"),
    ("sortino", "Sortino ratio", "sortino", "ratio"),
    ("calmar", "Calmar ratio", "calmar", "ratio"),
    ("var_95", "1-day VaR 95%", "var", "pct1"),
    ("cvar_95", "1-day CVaR 95%", "cvar", "pct1"),
    ("var_99", "1-day VaR 99%", "var", "pct1"),
    ("cvar_99", "1-day CVaR 99%", "cvar", "pct1"),
)

# The figures against the benchmark, in the same shape. All weekly; none is a gain or a loss.
_RELATIVE_ROWS = (
    ("beta", "Beta", "beta", "ratio"),
    ("correlation", "Correlation", "correlation", "ratio"),
    ("tracking_error", "Tracking error", "tracking-error", "pct1"),
    ("information_ratio", "Information ratio", "information-ratio", "ratio"),
)
# What a covered window's relative cell says when the benchmark couldn't be used at all.
_UNAVAILABLE = "Unavailable"

# What each flag means, in the legend's order. insufficient_history has no note: the figure is
# greyed and its cell already says why.
FLAG_NOTES = {
    "data_gaps": (
        "More than 2% of the returns in this window are missing, so the figure rests on fewer "
        "days than the window spans."
    ),
    "has_suspect_rows": (
        "The window includes a one-day move of over 100% that the price data kept as real. "
        "Drawdown and tail figures are sensitive to it."
    ),
    "nonsync_daily": (
        "Your holdings trade on different markets or in different currencies, whose closes "
        "aren't at the same time, so a 1-day figure understates the loss."
    ),
    "rf_partial": (
        "No risk-free rate exists before 1999-01-04, so the excess returns use 0% for that part "
        "of the window."
    ),
    "short_window": "Under 3 years of history, so this figure can move a lot as time passes.",
    # Set only on a what-if candidate's "after" figures (RK-19): spec R14's warning.
    "survivorship": (
        "The candidate is priced only through a fund's holdings, so its history comes from "
        "today's index members and overstates what an investor could have achieved."
    ),
    "tail_unstable": (
        "Fewer than 1,000 days, so the 99% figures rest on only a handful of the worst days."
    ),
}
_NO_MARKER = frozenset({"insufficient_history"})


@dataclass(frozen=True)
class FlagNote:
    flag: str
    marker: str  # "1", "2", ...: its place in the legend
    text: str


@dataclass(frozen=True)
class DrawdownDates:
    peak: date
    trough: date
    recovery: date | None  # None: not recovered by the window's end


@dataclass(frozen=True)
class Cell:
    value: float | None
    reason: str | None  # why it's greyed; None in an uncovered window, whose header says why
    title: str  # window, frequency, n_obs, gaps, label (spec §10)
    markers: tuple[str, ...]
    standard_error: float | None  # Sharpe only
    drawdown: DrawdownDates | None  # max drawdown only, when it has a value


@dataclass(frozen=True)
class Row:
    key: str
    label: str
    anchor: str  # its /glossary entry
    frequency: str  # "daily" or "weekly"
    kind: str  # "return", "pct1" or "ratio"
    cells: tuple[Cell, ...]  # one per column


@dataclass(frozen=True)
class Column:
    label: str
    start: date
    end: date
    covered: bool
    reason: str | None  # why it isn't covered


@dataclass(frozen=True)
class ExPostTable:
    label: str
    base_date: date  # the TWR's first level: where the measured history starts
    end_date: date
    rf_available: bool
    columns: tuple[Column, ...]
    rows: tuple[Row, ...]
    benchmark: BenchmarkStatus
    relative_rows: tuple[Row, ...]  # against `benchmark`, one per RELATIVE_FIGURE_KEYS
    legend: tuple[FlagNote, ...]
    excluded: tuple[ExcludedHolding, ...]


def build_ex_post_table(risk: ExPostRisk) -> ExPostTable | None:
    """The summary table, or None when there's nothing to measure (no windows)."""
    if not risk.windows:
        return None
    legend = _legend(risk.windows)
    markers = {note.flag: note.marker for note in legend}
    columns = tuple(_column(window) for window in risk.windows)
    rows = tuple(
        Row(
            key=key,
            label=label,
            anchor=anchor,
            frequency=risk.windows[0].figures[key].frequency,
            kind=kind,
            cells=tuple(_cell(window, key, risk.label, markers) for window in risk.windows),
        )
        for key, label, anchor, kind in SUMMARY_ROWS
    )
    against = f"{risk.label} · against {risk.benchmark.ticker}"
    relative_rows = tuple(
        Row(
            key=key,
            label=label,
            anchor=anchor,
            frequency=risk.windows[0].relative[key].frequency,
            kind=kind,
            cells=tuple(
                _relative_cell(window, key, against, markers, risk.benchmark.available)
                for window in risk.windows
            ),
        )
        for key, label, anchor, kind in _RELATIVE_ROWS
    )
    return ExPostTable(
        label=risk.label,
        base_date=risk.base_date,
        end_date=risk.end_date,
        rf_available=risk.rf_available,
        columns=columns,
        rows=rows,
        benchmark=risk.benchmark,
        relative_rows=relative_rows,
        legend=legend,
        excluded=risk.excluded,
    )


def _legend(windows: tuple[ExPostWindow, ...]) -> tuple[FlagNote, ...]:
    return legend_for(
        figure
        for window in windows
        if window.covered
        for figure in (*window.figures.values(), *window.relative.values())
    )


def legend_for(figures: Iterable[RiskFigure]) -> tuple[FlagNote, ...]:
    """A numbered note for each flag `figures` carry, in FLAG_NOTES' order: the ex-post table's
    legend, and the ex-ante section's (risk_ex_ante_view.py)."""
    present = {flag for figure in figures for flag in figure.flags if flag not in _NO_MARKER}
    unknown = sorted(present - set(FLAG_NOTES))
    if unknown:
        # A flag portfolio added after this table: shown by name rather than dropped.
        logger.warning("risk: no note for flag(s) %s; showing them by name.", ", ".join(unknown))
    ordered = [flag for flag in FLAG_NOTES if flag in present] + unknown
    return tuple(
        FlagNote(flag, str(i), FLAG_NOTES.get(flag, f"Flagged: {flag}."))
        for i, flag in enumerate(ordered, start=1)
    )


def _column(window: ExPostWindow) -> Column:
    # Every figure of an uncovered window carries the same reason; any one says it.
    reason = None if window.covered else next(iter(window.figures.values())).reason
    return Column(window.label, window.start, window.end, window.covered, reason)


def _cell(window: ExPostWindow, key: str, label: str, markers: dict[str, str]) -> Cell:
    figure = window.figures[key]
    drawdown = None
    if key == "max_drawdown" and figure.value is not None and window.peak_date is not None:
        drawdown = DrawdownDates(window.peak_date, window.trough_date, window.recovery_date)
    return _figure_cell(window, figure, label, markers, drawdown=drawdown)


def _relative_cell(
    window: ExPostWindow, key: str, label: str, markers: dict[str, str], available: bool
) -> Cell:
    cell = _figure_cell(window, window.relative[key], label, markers)
    if not available and window.covered and cell.value is None:
        # Every figure carries the same "benchmark unavailable: ..."; the heading says it once.
        return replace(cell, reason=_UNAVAILABLE)
    return cell


def _figure_cell(
    window: ExPostWindow,
    figure: RiskFigure,
    label: str,
    markers: dict[str, str],
    drawdown: DrawdownDates | None = None,
) -> Cell:
    return figure_cell(
        figure, window.label, label, markers, covered=window.covered, drawdown=drawdown
    )


def figure_cell(
    figure: RiskFigure,
    window_label: str,
    label: str,
    markers: dict[str, str],
    *,
    covered: bool = True,
    drawdown: DrawdownDates | None = None,
) -> Cell:
    """One figure's cell, in a window `covered` or not. `label` ends its title: the section's
    ex-post or ex-ante label (spec §10, R2)."""
    return Cell(
        value=figure.value,
        reason=figure.reason if covered else None,
        title=figure_title(window_label, figure, label),
        markers=(
            tuple(sorted({markers[f] for f in figure.flags if f in markers}, key=int))
            if covered
            else ()
        ),
        standard_error=figure.standard_error,
        drawdown=drawdown,
    )


def figure_title(window_label: str, figure: RiskFigure, label: str) -> str:
    """A figure's window, frequency, n_obs, gaps and label, for its `title` (spec §10)."""
    gaps = "1 gap" if figure.n_gaps == 1 else f"{figure.n_gaps} gaps"
    return (
        f"{window_label}: {figure.window_start.isoformat()} to {figure.window_end.isoformat()}"
        f" · {figure.frequency} · {figure.n_obs} obs · {gaps} · {label}"
    )
