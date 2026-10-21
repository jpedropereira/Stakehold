"""The /risk page's ex-post summary table (RK-12): one row per measure, one column per standard
window.

Pure, like labs_view.py: the router fetches, this decides what is shown, so the template only
lays it out. Spec §10's rules land here:
- every figure has its window, frequency, n_obs and the ex-post label: the column header shows
  the window, the row its frequency, and the cell's `title` the rest;
- a greyed figure shows its reason (R9), never a 0 or a blank; in a window the history doesn't
  cover, the column says why once, so its twelve cells don't repeat it;
- each flag a figure carries is a numbered marker, explained once in a legend under the table.

The values stay floats: the template formats them (`pct`, `pct1`, `ratio`) by the row's kind.
"""

import logging
from dataclasses import dataclass
from datetime import date

from web.portfolio_client.base import (
    ExcludedHolding,
    ExPostRisk,
    ExPostWindow,
    RiskFigure,
)

logger = logging.getLogger(__name__)

# (response name, label, glossary anchor, kind). The kind picks the format: "return" is a signed
# percentage coloured as a gain or a loss, "pct1" a percentage to 1 decimal and "ratio" 2
# decimals (spec §10), both in plain ink: a volatility, a drawdown or a VaR isn't a gain or a
# loss.
_ROWS = (
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
    "survivorship": (
        "This asset isn't tracked, so its history may leave out funds that closed along the way."
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
        for key, label, anchor, kind in _ROWS
    )
    return ExPostTable(
        label=risk.label,
        base_date=risk.base_date,
        end_date=risk.end_date,
        rf_available=risk.rf_available,
        columns=columns,
        rows=rows,
        legend=legend,
        excluded=risk.excluded,
    )


def _legend(windows: tuple[ExPostWindow, ...]) -> tuple[FlagNote, ...]:
    present = {
        flag
        for window in windows
        if window.covered
        for figure in window.figures.values()
        for flag in figure.flags
        if flag not in _NO_MARKER
    }
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
    return Cell(
        value=figure.value,
        reason=figure.reason if window.covered else None,
        title=_title(window.label, figure, label),
        markers=(
            tuple(sorted({markers[f] for f in figure.flags if f in markers}, key=int))
            if window.covered
            else ()
        ),
        standard_error=figure.standard_error,
        drawdown=drawdown,
    )


def _title(window_label: str, figure: RiskFigure, label: str) -> str:
    gaps = "1 gap" if figure.n_gaps == 1 else f"{figure.n_gaps} gaps"
    return (
        f"{window_label}: {figure.window_start.isoformat()} to {figure.window_end.isoformat()}"
        f" · {figure.frequency} · {figure.n_obs} obs · {gaps} · {label}"
    )
