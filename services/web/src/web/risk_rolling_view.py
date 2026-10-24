"""The /risk page's rolling charts (RK-25): how a figure moved through history, from RK-24's
rolling figures, rather than its one value over a standard window.

Pure, like the other risk views: the router fetches, this decides what is shown, and
static/risk_rolling_chart.js only draws the payload it is handed. Agreed for RK-25:
- **Each section draws its own**: ex-post's rolling 1Y volatility and Sharpe on the TWR, and
  ex-ante's on the constant-weight mix, with the rolling correlation beside them. A section that
  fails takes only its own charts with it, and R2's two labels never share a chart.
- **Volatility and Sharpe are two small charts**, since their units differ, and Sharpe carries
  a soft ± 1 s.e. band from each point's standard error, so its noise shows.
- **The default pair is the heaviest holding against the portfolio** (spec §7.1's "one asset
  against the portfolio"), always defined when anything is measured. `?corr_a=&corr_b=` picks
  another; a pair portfolio would reject (a ticker excluded, sold, or typed by hand) falls back
  to the default, so a stale link never costs the whole ex-ante section a 422.

Each point is plotted at its window's end, and a gap (a greyed figure, R9) stays in place as a
null, so the line breaks there. The canvas can't show a gap's reason, so consecutive gaps with
one reason become a note under the chart. A series greyed throughout has its notes and no chart.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from web.portfolio_client.base import RiskFigure, RollingCorrelation, RollingRisk

# How portfolio names the constant-weight mix as a pair's side, and how /risk's `corr_b` does.
PORTFOLIO = "portfolio"
PAIR_PARAMS = ("corr_a", "corr_b")
NO_POINT_YET = "No rolling point yet: the history is under a year."

# (response name, label, glossary anchor, kind): the kind is risk_view.SUMMARY_ROWS', "pct1" a
# percentage to 1 decimal and "ratio" 2 decimals, both in plain ink.
_ROLLING_SERIES = (
    ("volatility", "Volatility", "volatility", "pct1"),
    ("sharpe", "Sharpe", "sharpe", "ratio"),
)


@dataclass(frozen=True)
class Pair:
    """The rolling pair to ask portfolio for. `b` None is the portfolio. `requested` when it came
    from the query; `dropped` says which requested pair was replaced by the default, and why."""

    a: str
    b: str | None
    requested: bool
    dropped: str | None


@dataclass(frozen=True)
class Point:
    value: float
    end: date
    standard_error: float | None


@dataclass(frozen=True)
class Summary:
    """The chart in words, for a screen reader and for anyone without JS: the latest measured
    window, and the lowest and highest."""

    latest: Point
    low: Point
    high: Point


@dataclass(frozen=True)
class GapNote:
    """Consecutive greyed windows with one reason."""

    first_end: date
    last_end: date
    count: int
    reason: str


@dataclass(frozen=True)
class RollingSeries:
    key: str
    label: str
    anchor: str  # its /glossary entry
    kind: str  # "pct1" or "ratio", as the summary's figures are formatted
    canvas_id: str
    data_id: str
    payload: dict | None  # the chart's data island; None when every point is greyed
    summary: Summary | None
    gaps: tuple[GapNote, ...]


@dataclass(frozen=True)
class RollingCharts:
    """One section's rolling volatility and Sharpe. `series` is empty, with `empty_reason`, when
    the history is under a year."""

    series: tuple[RollingSeries, ...]
    empty_reason: str | None


@dataclass(frozen=True)
class Option:
    value: str
    label: str
    selected: bool


@dataclass(frozen=True)
class CorrelationChart:
    a: str
    b_label: str  # "the portfolio" or the ticker
    series: RollingSeries | None  # None when portfolio couldn't measure the pair: see `reason`
    reason: str | None
    dropped: str | None  # a requested pair replaced by the default
    options_a: tuple[Option, ...]
    options_b: tuple[Option, ...]
    kept: tuple[tuple[str, str], ...]  # the rest of the query, re-sent by the picker


def _side(ticker: str | None) -> str:
    return "the portfolio" if ticker is None else ticker


def chosen_pair(
    raw_a: str | None, raw_b: str | None, tickers: Sequence[str], benchmark: str
) -> Pair:
    """The pair from `?corr_a=&corr_b=`, if portfolio would accept it, else the default.

    `tickers` is the mix as sent, heaviest first, with any exclusions already taken out: a side
    must be one of them or the benchmark (portfolio's own rule), and the two must differ. A
    missing or "portfolio" `corr_b` is the portfolio.
    """
    default = Pair(a=tickers[0], b=None, requested=False, dropped=None)
    a = raw_a or None
    b = None if raw_b in (None, "", PORTFOLIO) else raw_b
    if a is None and b is None:
        return default
    allowed = {*tickers, benchmark}
    if a in allowed and (b is None or b in allowed) and a != b:
        return Pair(a=a, b=b, requested=True, dropped=None)
    dropped = f"{_side(a)} vs {_side(b)} isn't a pair from the measured mix"
    return Pair(a=default.a, b=default.b, requested=False, dropped=dropped)


def _gaps(figures: Sequence[RiskFigure]) -> tuple[GapNote, ...]:
    notes: list[GapNote] = []
    previous_greyed = False
    for figure in figures:
        if figure.value is not None:
            previous_greyed = False
            continue
        reason = figure.reason or "not measured"
        if previous_greyed and notes[-1].reason == reason:
            last = notes[-1]
            notes[-1] = GapNote(last.first_end, figure.window_end, last.count + 1, reason)
        else:
            notes.append(GapNote(figure.window_end, figure.window_end, 1, reason))
        previous_greyed = True
    return tuple(notes)


def _summary(figures: Sequence[RiskFigure]) -> Summary | None:
    points = [
        Point(f.value, f.window_end, f.standard_error) for f in figures if f.value is not None
    ]
    if not points:
        return None
    return Summary(
        latest=points[-1],
        low=min(points, key=lambda p: p.value),
        high=max(points, key=lambda p: p.value),
    )


def _band(figures: Sequence[RiskFigure], sign: int) -> list[float | None]:
    return [
        (None if f.value is None or f.standard_error is None else f.value + sign * f.standard_error)
        for f in figures
    ]


def _series(
    figures: Sequence[RiskFigure],
    *,
    key: str,
    label: str,
    anchor: str,
    kind: str,
    chart_kind: str,
    canvas_id: str,
    band: bool,
) -> RollingSeries:
    summary = _summary(figures)
    payload = None
    if summary is not None:
        payload = {
            "kind": chart_kind,
            "label": label,
            "dates": [f.window_end.isoformat() for f in figures],
            "starts": [f.window_start.isoformat() for f in figures],
            "n_obs": [f.n_obs for f in figures],
            "values": [f.value for f in figures],
            "lower": _band(figures, -1) if band else None,
            "upper": _band(figures, 1) if band else None,
        }
    return RollingSeries(
        key=key,
        label=label,
        anchor=anchor,
        kind=kind,
        canvas_id=canvas_id,
        data_id=f"{canvas_id}-data",
        payload=payload,
        summary=summary,
        gaps=_gaps(figures),
    )


def build_rolling_charts(rolling: RollingRisk | None, section: str) -> RollingCharts | None:
    """`section` ("ex-post" or "ex-ante") keeps each canvas's id unique on the page. None when
    portfolio had nothing to measure, so the section draws no rolling block at all."""
    if rolling is None:
        return None
    if not rolling.volatility:
        return RollingCharts(series=(), empty_reason=NO_POINT_YET)
    series = tuple(
        _series(
            getattr(rolling, key),
            key=key,
            label=label,
            anchor=anchor,
            kind=kind,
            chart_kind=kind,
            canvas_id=f"risk-{section}-rolling-{key}",
            band=key == "sharpe",
        )
        for key, label, anchor, kind in _ROLLING_SERIES
    )
    return RollingCharts(series=series, empty_reason=None)


def _options(choices: Sequence[str], benchmark: str, selected: str) -> tuple[Option, ...]:
    values = list(dict.fromkeys([*choices, benchmark]))
    return tuple(
        Option(
            value=value,
            label=f"{value} (benchmark)" if value == benchmark and value not in choices else value,
            selected=value == selected,
        )
        for value in values
    )


def build_correlation_chart(
    rolling: RollingCorrelation | None,
    pair: Pair,
    tickers: Sequence[str],
    benchmark: str,
    params: Sequence[tuple[str, str]],
) -> CorrelationChart | None:
    """The rolling ρ of `pair`, with the picker that changes it. `tickers` is the mix as sent.
    None when portfolio returned no pair (nothing was included)."""
    if rolling is None:
        return None
    series = None
    if rolling.reason is None:
        series = _series(
            rolling.points,
            key="correlation",
            label="Correlation",
            anchor="rolling-correlation",
            kind="ratio",
            chart_kind="rho",
            canvas_id="risk-ex-ante-rolling-correlation",
            band=False,
        )
    b_selected = PORTFOLIO if pair.b is None else pair.b
    return CorrelationChart(
        a=pair.a,
        b_label=_side(pair.b),
        series=series,
        reason=rolling.reason,
        dropped=pair.dropped,
        options_a=_options(tickers, benchmark, pair.a),
        options_b=(
            Option(PORTFOLIO, "the portfolio", pair.b is None),
            *_options(tickers, benchmark, b_selected),
        ),
        kept=tuple((k, v) for k, v in params if k not in PAIR_PARAMS),
    )
