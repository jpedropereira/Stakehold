"""The /risk page's regime tables (RK-28, spec §11 phase 5): for each market regime overlapping
a section's history, how that series and the benchmark did inside it, as cumulative return and
max drawdown.

Pure, like the other risk views: the router fetches, this decides what is shown. Each section
gets its own table, ex-post's on the TWR and ex-ante's on today's mix, so R2's two labels never
share one. Agreed for RK-28:
- **Every overlapping regime is shown, grouped by dimension** (drawdowns, recessions, events,
  monetary policy, ...), each group in the order its first regime starts, its regimes by start
  date as portfolio sends them. An unknown dimension is shown by its own name, not dropped. The
  template closes each group by default, with its count in the summary.
- **A partial or ongoing regime says so.** The row keeps the regime's own dates and adds the
  window it was measured on, the regime clipped to the series.
- **A short regime's drawdown is shown, flagged**: `short_regime`, a numbered marker with its
  note in the legend, like the ex-post table's flags.
- **The benchmark is greyed as on the ex-post table.** When it couldn't be used at all, the
  heading says why once and each of its cells says "Unavailable". When it doesn't cover a
  regime, the cell shows its own reason.

The values stay floats: the template formats a return as a gain or a loss and a drawdown in
ink, as risk_view's SUMMARY_ROWS do.
"""

from dataclasses import dataclass, replace
from datetime import date

from web.portfolio_client.base import BenchmarkStatus, RegimeRow, RegimeView
from web.risk_view import Cell, FlagNote, figure_cell, legend_for

# How portfolio's dimensions are headed. Plain words, the page's voice.
DIMENSION_LABELS = {
    "drawdown": "Drawdowns",
    "business_cycle": "Recessions",
    "event": "Events",
    "monetary_policy": "Monetary policy",
    "inflation": "Inflation",
    "currency": "Currency",
    "commodity": "Commodities",
}
_NO_REGIMES_LOADED = "no regimes loaded"
_UNAVAILABLE_PREFIX = "regimes unavailable"
_UNAVAILABLE = "Unavailable"


@dataclass(frozen=True)
class RegimeLine:
    regime_id: str
    name: str
    scope: str
    severity: str | None
    start: date
    end: date | None  # None while the regime is ongoing
    measured_start: date
    measured_end: date
    partial: bool
    ongoing: bool
    # The series' cumulative return and max drawdown, then the benchmark's.
    cells: tuple[Cell, Cell, Cell, Cell]


@dataclass(frozen=True)
class RegimeGroup:
    key: str
    label: str
    lines: tuple[RegimeLine, ...]


@dataclass(frozen=True)
class RegimeSection:
    label: str  # the section's ex-post or ex-ante label, which ends every cell's title
    benchmark: BenchmarkStatus
    groups: tuple[RegimeGroup, ...]
    legend: tuple[FlagNote, ...]
    count: int
    empty_reason: str | None  # shown in place of the groups when there are none
    reason: str | None  # why there are none, for the log: portfolio's, or "none overlapping"


def build_regime_section(
    view: RegimeView | None, label: str, benchmark: BenchmarkStatus
) -> RegimeSection | None:
    """The section's regime table, or None when portfolio sent no view (nothing measured)."""
    if view is None:
        return None
    if not view.rows:
        reason = view.reason if not view.available else "none overlapping"
        return RegimeSection(label, benchmark, (), (), 0, _empty_reason(view), reason)

    legend = legend_for(
        figure
        for row in view.rows
        for side in (row.portfolio, row.benchmark)
        for figure in (side.cumulative_return, side.max_drawdown)
    )
    markers = {note.flag: note.marker for note in legend}
    against = f"{label} · against {benchmark.ticker}"
    by_dimension: dict[str, list[RegimeLine]] = {}
    for row in view.rows:
        by_dimension.setdefault(row.dimension, []).append(
            _line(row, label, against, markers, benchmark.available)
        )
    groups = tuple(
        RegimeGroup(
            key, DIMENSION_LABELS.get(key, key.replace("_", " ").capitalize()), tuple(lines)
        )
        for key, lines in by_dimension.items()
    )
    return RegimeSection(label, benchmark, groups, legend, len(view.rows), None, None)


def _line(
    row: RegimeRow, label: str, against: str, markers: dict[str, str], available: bool
) -> RegimeLine:
    def benchmark_cell(figure) -> Cell:
        cell = figure_cell(figure, row.name, against, markers)
        if not available and cell.value is None:
            # Every figure carries the same "benchmark unavailable: ..."; the heading says it.
            return replace(cell, reason=_UNAVAILABLE)
        return cell

    return RegimeLine(
        regime_id=row.regime_id,
        name=row.name,
        scope=row.scope,
        severity=row.severity,
        start=row.start_date,
        end=row.end_date,
        measured_start=row.measured_start,
        measured_end=row.measured_end,
        partial=row.partial,
        ongoing=row.ongoing,
        cells=(
            figure_cell(row.portfolio.cumulative_return, row.name, label, markers),
            figure_cell(row.portfolio.max_drawdown, row.name, label, markers),
            benchmark_cell(row.benchmark.cumulative_return),
            benchmark_cell(row.benchmark.max_drawdown),
        ),
    )


def _empty_reason(view: RegimeView) -> str:
    if view.available:
        return "No market regime overlaps this history."
    if view.reason == _NO_REGIMES_LOADED:
        return "No market regimes are loaded yet, so there is nothing to compare."
    if view.reason and view.reason.startswith(_UNAVAILABLE_PREFIX):
        return "Couldn't load the market regimes. Try again later."
    return f"No regime view: {view.reason}."
