"""The /risk page's ex-ante section (RK-18): today's mix held at constant weights through the
window, labelled "ex-ante · constant weights" beside the ex-post summary (R2).

Pure, like risk_view.py: the router fetches, this decides what is shown. Five parts:
- the summary: the constant-weight figures over the measured window, with σ_p as their
  volatility (they are equal by §6.2's check, so the page shows one), the diversification ratio
  and the effective number of holdings. Cells reuse risk_view's: title metadata, greyed reasons,
  flag markers and the legend;
- the decomposition (§6.3): weight beside share of risk and their ratio. A holding whose share
  of risk is at least 1.5 times its weight *and* 5 points above it is named in a note: spec
  §6.3's headline insight ("10% of the money, 25% of the risk"). The 5 points keep a 1% holding
  at 2% of the risk out of it. A hedge (a negative share of risk) is named too;
- the € tail figures (§5.5): the constant-weight 1-day VaR on the market value of the holdings
  portfolio measured. Not the whole portfolio's: a holding left out isn't in the mix the
  percentage describes;
- the heatmap (§7.1, R13): pairwise correlations mirrored into squares, each with a bucket of
  an ink ramp on ρ from 0 to 1 (`bucket`). ρ at or below 0 is unfilled: dark means "moves
  together", and a negative ρ is a diversifier, not a darker version of a positive one. The
  printed number carries the sign. Forest and oxblood are for gains and losses, and gold is the
  accent, so neither colours a correlation;
- the FX decomposition (§6.4, RK-26): for each non-EUR holding, its local, currency and EUR
  volatilities and the correlation of local and currency returns, on the decomposition's weeks.
  All four are in ink, since none is a gain or a loss. Their flags share the summary's legend.
  There's no derived "currency share of risk": the EUR variance has a correlation and a cross
  term, so no split into shares adds up (agreed).

The window (`?ex_ante_window=`, 3Y by default, §7.1) and the exclusions (`?exclude=`, R12) are
the query string: the section's links change one and keep the rest.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlencode

from web.labs_view import MAX_BACKTEST_ASSETS, DefaultMix
from web.portfolio_client.base import (
    RISK_WINDOWS,
    BenchmarkStatus,
    ExAnteHolding,
    ExAnteRisk,
    ExAnteWindow,
    FxDecompositionRow,
    Holding,
    RiskFigure,
)
from web.risk_view import (
    SUMMARY_ROWS,
    Cell,
    Column,
    DrawdownDates,
    FlagNote,
    Row,
    figure_cell,
    figure_title,
    legend_for,
)

DEFAULT_EX_ANTE_WINDOW = "3Y"  # spec §7.1's default
_ANCHOR = "risk-ex-ante"

# The headline rule (spec §6.3): a share of risk at least this many times the weight, and at
# least this far above it. The tolerance keeps a share exactly on the line from missing it to
# float rounding (0.10 × 1.5 is 0.15000000000000002).
OUTSIZED_RATIO = 1.5
OUTSIZED_POINTS = 0.05
_TOLERANCE = 1e-9

# The heatmap's bucket edges: an ink ramp on ρ, unfilled below the first.
BUCKET_EDGES = (0.2, 0.4, 0.6, 0.8)

# The 1-day tail figures shown in euros, with their odds in words (spec §5.5).
_TAILS = (("95", "var_95", "1 in 20"), ("99", "var_99", "1 in 100"))

_DIVERSIFICATION_ROW = (
    "diversification_ratio",
    "Diversification ratio",
    "diversification-ratio",
    "ratio",
)


@dataclass(frozen=True)
class Named:
    """A holding a note names: its weight and share of risk, both fractions."""

    ticker: str
    weight: float
    pcr: float


@dataclass(frozen=True)
class DecompositionRow:
    ticker: str
    weight: float
    pcr: float | None
    risk_to_weight: float | None
    volatility: float | None
    cr: float | None
    corr_with_portfolio: float | None
    outsized: bool
    hedge: bool


@dataclass(frozen=True)
class Decomposition:
    rows: tuple[DecompositionRow, ...]
    reason: str | None  # why every figure is greyed (under 104 weeks), said once
    title: str  # its window, n_obs and label, as a summary cell's
    outsized: tuple[Named, ...]
    hedges: tuple[Named, ...]


@dataclass(frozen=True)
class TailEuro:
    level: str  # "95" or "99"
    odds: str  # "1 in 20"
    pct: float | None
    euros: Decimal | None
    reason: str | None  # why there's no figure


@dataclass(frozen=True)
class HeatCell:
    value: float | None
    bucket: int | None  # 0 (unfilled) to 4; None when greyed
    title: str  # the pair, its own window and n_obs (R13), and a greyed cell's reason
    diagonal: bool

    @property
    def css_class(self) -> str:
        return "corr-greyed" if self.bucket is None else f"corr-{self.bucket}"


@dataclass(frozen=True)
class Heatmap:
    keys: tuple[str, ...]
    benchmark_key: str | None
    rows: tuple[tuple[HeatCell, ...], ...]  # one per key, each one cell per key

    @property
    def any_greyed(self) -> bool:
        return any(cell.bucket is None for row in self.rows for cell in row)


@dataclass(frozen=True)
class FxColumn:
    key: str  # FxDecompositionRow's field
    label: str
    note: str  # under the label, as a summary row's frequency
    kind: str  # "pct1" or "ratio", as risk_view.Row's


# Spec §6.4's four figures, in its order: the holding's own market, its currency, how the two
# moved together, and what a EUR investor got.
FX_COLUMNS = (
    FxColumn("local_volatility", "Local volatility", "in its own currency", "pct1"),
    FxColumn("fx_volatility", "Currency volatility", "against the euro", "pct1"),
    FxColumn("correlation", "Correlation", "local and currency", "ratio"),
    FxColumn("eur_volatility", "Euro volatility", "what you held", "pct1"),
)


@dataclass(frozen=True)
class FxRow:
    ticker: str
    currency: str
    cells: tuple[Cell, ...]  # one per FX_COLUMNS entry


@dataclass(frozen=True)
class FxDecomposition:
    rows: tuple[FxRow, ...]  # empty when every included holding is in EUR
    columns: tuple[FxColumn, ...] = FX_COLUMNS


@dataclass(frozen=True)
class LeftOut:
    ticker: str
    reason: str


@dataclass(frozen=True)
class ExAnteSection:
    label: str
    rf_available: bool
    benchmark: BenchmarkStatus
    window: ExAnteWindow | None  # None when nothing could be included
    column: Column | None
    summary_rows: tuple[Row, ...]
    legend: tuple[FlagNote, ...]
    effective_n: float | None
    limited_by: tuple[str, ...]
    decomposition: Decomposition | None
    measured_value: Decimal  # the included holdings' market value, the € figures' base
    tail_euros: tuple[TailEuro, ...]
    heatmap: Heatmap | None
    fx_decomposition: FxDecomposition | None  # None when nothing could be measured
    excluded: tuple[str, ...]  # left out by request, each with an "include it again" link
    left_out: tuple[LeftOut, ...]  # left out for any other reason
    window_links: dict[str, str]
    exclude_links: tuple[tuple[str, str], ...]  # (limiting ticker, URL excluding it)
    include_links: tuple[tuple[str, str], ...]  # (excluded ticker, URL including it again)


def chosen_ex_ante_window(raw: str | None) -> str:
    """`raw` if it is a standard window, exactly as portfolio spells it; otherwise 3Y."""
    return raw if raw in RISK_WINDOWS else DEFAULT_EX_ANTE_WINDOW


def ex_ante_url(
    params: Sequence[tuple[str, str]],
    *,
    window: str | None = None,
    exclude: Sequence[str] | None = None,
) -> str:
    """/risk/ex-ante with this page's query, `ex_ante_window` and/or `exclude` replaced, back
    at the section."""
    replaced = {"ex_ante_window"} if window is not None else set()
    if exclude is not None:
        replaced.add("exclude")
    query = [(k, v) for k, v in params if k not in replaced]
    if window is not None:
        query.append(("ex_ante_window", window))
    query.extend(("exclude", ticker) for ticker in exclude or ())
    return "/risk/ex-ante?" + urlencode(query) + "#" + _ANCHOR


def bucket(value: float | None) -> int | None:
    """The heatmap square's shade: 0 (unfilled) below ρ = 0.2, negative ρ included, up to 4
    from 0.8. None for a greyed cell."""
    if value is None:
        return None
    return sum(1 for edge in BUCKET_EDGES if value >= edge)


def build_ex_ante_section(
    risk: ExAnteRisk,
    mix: DefaultMix,
    holdings: Sequence[Holding],
    params: Sequence[tuple[str, str]],
) -> ExAnteSection:
    excluded = tuple(s.ticker for s in risk.statuses if s.status == "excluded")
    included = {s.ticker for s in risk.statuses if s.status == "included"}
    measured_value = sum(
        (
            h.market_value_eur
            for h in holdings
            if h.canonical_ticker in included and h.market_value_eur is not None
        ),
        Decimal(0),
    )
    window = risk.window
    limited_by = window.limited_by if window else ()
    common = dict(
        label=risk.label,
        rf_available=risk.rf_available,
        benchmark=risk.benchmark,
        window=window,
        effective_n=risk.effective_n,
        limited_by=limited_by,
        measured_value=measured_value,
        excluded=excluded,
        left_out=_left_out(risk, mix),
        window_links={w: ex_ante_url(params, window=w) for w in RISK_WINDOWS},
        exclude_links=tuple(
            (ticker, ex_ante_url(params, exclude=(*excluded, ticker)))
            for ticker in limited_by
            if ticker not in excluded
        ),
        include_links=tuple(
            (ticker, ex_ante_url(params, exclude=tuple(t for t in excluded if t != ticker)))
            for ticker in excluded
        ),
    )
    if window is None or risk.constant_weight is None:
        return ExAnteSection(
            **common,
            column=None,
            summary_rows=(),
            legend=(),
            decomposition=None,
            tail_euros=(),
            heatmap=None,
            fx_decomposition=None,
        )
    figures = {
        **risk.constant_weight.figures,
        "volatility": risk.sigma_p,
        "diversification_ratio": risk.diversification_ratio,
    }
    covered = window.start is not None
    column = Column(
        label=window.label,
        start=window.start or window.requested_start,
        end=window.end,
        covered=covered,
        reason=None if covered else risk.sigma_p.reason,
    )
    fx_rows = risk.fx_decomposition or ()
    fx_figures = [getattr(row, column.key) for row in fx_rows for column in FX_COLUMNS]
    legend = legend_for([*figures.values(), *fx_figures]) if covered else ()
    markers = {note.flag: note.marker for note in legend}
    return ExAnteSection(
        **common,
        column=column,
        summary_rows=_summary_rows(risk, figures, column, markers),
        legend=legend,
        decomposition=_decomposition(risk),
        tail_euros=tuple(
            _tail(level, odds, figures[key], measured_value) for level, key, odds in _TAILS
        ),
        heatmap=None if risk.correlation is None else _heatmap(risk),
        fx_decomposition=(
            None
            if risk.fx_decomposition is None
            else FxDecomposition(
                tuple(_fx_row(row, window.label, risk.label, markers) for row in fx_rows)
            )
        ),
    )


def _summary_rows(
    risk: ExAnteRisk, figures: dict[str, RiskFigure], column: Column, markers: dict[str, str]
) -> tuple[Row, ...]:
    rows = []
    for spec in SUMMARY_ROWS:
        rows.append(_row(spec, figures, column, risk.label, markers, risk))
        if spec[0] == "volatility":
            rows.append(_row(_DIVERSIFICATION_ROW, figures, column, risk.label, markers, risk))
    return tuple(rows)


def _row(spec, figures, column: Column, label: str, markers, risk: ExAnteRisk) -> Row:
    key, name, anchor, kind = spec
    figure = figures[key]
    drawdown = None
    series = risk.constant_weight
    if key == "max_drawdown" and figure.value is not None and series.peak_date is not None:
        drawdown = DrawdownDates(series.peak_date, series.trough_date, series.recovery_date)
    cell: Cell = figure_cell(
        figure, column.label, label, markers, covered=column.covered, drawdown=drawdown
    )
    return Row(key, name, anchor, figure.frequency, kind, (cell,))


def _decomposition(risk: ExAnteRisk) -> Decomposition | None:
    ratio = risk.diversification_ratio
    if risk.holdings is None or ratio is None:
        return None
    rows = tuple(_decomposition_row(h) for h in risk.holdings)
    return Decomposition(
        rows=rows,
        reason=ratio.reason if ratio.value is None else None,
        title=figure_title(risk.window.label, ratio, risk.label),
        outsized=tuple(Named(r.ticker, r.weight, r.pcr) for r in rows if r.outsized),
        hedges=tuple(Named(r.ticker, r.weight, r.pcr) for r in rows if r.hedge),
    )


def _decomposition_row(holding: ExAnteHolding) -> DecompositionRow:
    pcr, weight = holding.pcr, holding.weight
    outsized = (
        pcr is not None
        and pcr >= OUTSIZED_RATIO * weight - _TOLERANCE
        and pcr - weight >= OUTSIZED_POINTS - _TOLERANCE
    )
    return DecompositionRow(
        ticker=holding.ticker,
        weight=weight,
        pcr=pcr,
        # portfolio rejects a weight of 0 or less, so the ratio is always defined.
        risk_to_weight=None if pcr is None else pcr / weight,
        volatility=holding.volatility,
        cr=holding.cr,
        corr_with_portfolio=holding.corr_with_portfolio,
        outsized=outsized,
        hedge=pcr is not None and pcr < 0,
    )


def _fx_row(
    row: FxDecompositionRow, window_label: str, label: str, markers: dict[str, str]
) -> FxRow:
    return FxRow(
        ticker=row.ticker,
        currency=row.currency,
        cells=tuple(
            figure_cell(getattr(row, column.key), window_label, label, markers)
            for column in FX_COLUMNS
        ),
    )


def _tail(level: str, odds: str, figure: RiskFigure, base: Decimal) -> TailEuro:
    if figure.value is None:
        return TailEuro(level, odds, None, None, figure.reason)
    if base <= 0:
        return TailEuro(level, odds, figure.value, None, "no market value for these holdings yet")
    euros = (base * Decimal(str(figure.value))).quantize(Decimal("0.01"))
    return TailEuro(level, odds, figure.value, euros, None)


def _heatmap(risk: ExAnteRisk) -> Heatmap:
    matrix = risk.correlation
    figures = {}
    for cell in matrix.cells:
        figures[cell.a, cell.b] = figures[cell.b, cell.a] = cell.figure
    rows = tuple(tuple(_heat_cell(a, b, figures[a, b]) for b in matrix.keys) for a in matrix.keys)
    return Heatmap(keys=matrix.keys, benchmark_key=matrix.benchmark_key, rows=rows)


def _heat_cell(a: str, b: str, figure: RiskFigure) -> HeatCell:
    title = figure_title(f"{a} and {b}", figure, "pairwise, for display only")
    if figure.value is None:
        title += f" · {figure.reason}"
    return HeatCell(figure.value, bucket(figure.value), title, a == b)


def _left_out(risk: ExAnteRisk, mix: DefaultMix) -> tuple[LeftOut, ...]:
    """Every holding the figures don't cover and not by request: portfolio's statuses, then the
    default mix's own (labs_view.build_default_mix), which never reach portfolio."""
    return (
        *(
            LeftOut(s.ticker, s.reason or s.status)
            for s in risk.statuses
            if s.status not in {"included", "excluded"}
        ),
        *mix_left_out(mix),
    )


def mix_left_out(mix: DefaultMix) -> tuple[LeftOut, ...]:
    """The holdings the default mix leaves out before anything reaches portfolio, and why: this
    section's and the concentration section's (risk_concentration_view.py)."""
    return (
        *(LeftOut(t, "no price yet") for t in mix.unpriced),
        *(LeftOut(t, "worth nothing at today's price") for t in mix.unweighted),
        *(LeftOut(t, f"over the {MAX_BACKTEST_ASSETS}-holding limit") for t in mix.dropped),
    )
