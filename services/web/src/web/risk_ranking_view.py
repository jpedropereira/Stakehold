"""The /risk page's candidate ranking (RK-29): every tracked ticker outside the holdings, scored
as a what-if candidate at one weight, from portfolio's POST /risk/what-if/ranking (spec §7.2).

Pure, like risk_what_if_view.py: the router fetches, this decides what is sent and shown. Two
parts:
- the form (`parse_ranking`): a GET form with one field, the weight in % of the mix after the
  change (5% until it's sent), and the order, `?rank_by=`, set by the table's links. Only an
  unsent form sends nothing, because the ranking costs about two data-store calls per tracked
  ticker: it runs when asked, not on every /risk view. On a 1Y ex-ante window nothing is sent
  and the form says why: the diversification ratio needs 104 weeks, so portfolio would refuse it;
- the section (`build_ranking_section`): portfolio's order kept and numbered, each candidate's
  Δσ_p, Δ diversification ratio, share of risk, correlation with the mix, and ΔSharpe with its
  s.e. ΔSharpe is shown but is never an order, because the best of many noisy Sharpe deltas is
  mostly luck. Then the greyed rows, unnumbered, the candidates that weren't ranked and why,
  and the tracked tickers past the cap.

The window and exclusions are the ex-ante section's (`?ex_ante_window=`, `?exclude=`), so every
candidate joins the mix that section measures.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

from web.labs_mix import parse_weight
from web.portfolio_client.base import (
    CandidateRanking,
    RankedCandidate,
    RankingWindow,
    UnrankedCandidate,
    WhatIfRow,
)
from web.risk_ex_ante_view import LeftOut
from web.risk_view import Cell, FlagNote, figure_cell, legend_for

RANK_KEYS = ("sigma_p", "diversification_ratio")
DEFAULT_WEIGHT = "5"
WINDOW_NOTE = (
    "Ranking needs an ex-ante window of 3Y or more: the diversification ratio needs two years "
    "of weekly returns. Pick a longer window above."
)
_ANCHOR = "risk-ranking"
_HUNDRED = Decimal(100)
_WEIGHT_RANGE = "Enter a weight above 0% and below 100%."
# portfolio's reasons for having nothing to rank, in the page's words. Another is shown as sent.
_NOTHING = {
    "no tracked ticker outside the mix to rank": (
        "Every ticker tracked is already one of your holdings."
    ),
    "no holding could be included": "None of your holdings could be measured.",
    "no candidate could be measured": "None of the tracked tickers could be measured.",
}


@dataclass(frozen=True)
class RankingRequest:
    weight: Decimal  # a fraction of the mix after the change, as portfolio takes it
    rank_by: str


@dataclass(frozen=True)
class RankingForm:
    weight: str  # as typed, or DEFAULT_WEIGHT before the form is sent
    rank_by: str
    error: str | None
    window_note: str | None  # why nothing is sent on this ex-ante window
    request: RankingRequest | None  # None when unsent, with an error, or on 1Y


@dataclass(frozen=True)
class DeltaCell:
    """A change, with the after figure's cell for its title, greyed reason and markers."""

    delta: float | None
    delta_se: float | None  # ΔSharpe's 1 s.e.
    cell: Cell


@dataclass(frozen=True)
class RankingLine:
    rank: int | None  # None on a greyed row
    ticker: str
    name: str
    history_start: date
    n_weeks: int
    sigma_p: DeltaCell
    diversification_ratio: DeltaCell
    share_of_risk: Cell  # after, the candidate's
    correlation: Cell  # with the mix it would join
    sharpe: DeltaCell


@dataclass(frozen=True)
class RankingSection:
    label: str
    rf_available: bool
    weight: float
    rank_by: str
    cap: int
    universe: int
    over_cap: tuple[str, ...]
    window: RankingWindow | None
    lines: tuple[RankingLine, ...]
    greyed: tuple[RankingLine, ...]
    unranked: tuple[UnrankedCandidate, ...]
    legend: tuple[FlagNote, ...]
    sort_links: dict[str, str]  # by RANK_KEYS key
    nothing: str | None  # why there's nothing to rank
    left_out: tuple[LeftOut, ...]  # holdings portfolio couldn't measure, and why


def parse_ranking(weight: str | None, rank_by: str | None, window: str) -> RankingForm:
    """The form as sent, its error, and the request to make when it has none. No weight at all
    is the unsent form: 5% shown, nothing sent."""
    rank_by = rank_by if rank_by in RANK_KEYS else RANK_KEYS[0]
    if weight is None:
        note = WINDOW_NOTE if window == "1Y" else None
        return RankingForm(DEFAULT_WEIGHT, rank_by, None, note, None)
    value = parse_weight(weight)
    error = None
    if not weight.strip():
        error = "Enter a weight."
    elif value is None or value >= _HUNDRED:
        error = _WEIGHT_RANGE
    if window == "1Y":
        return RankingForm(weight, rank_by, error, WINDOW_NOTE, None)
    request = None if error else RankingRequest(value / _HUNDRED, rank_by)
    return RankingForm(weight, rank_by, error, None, request)


def ranking_url(params: Sequence[tuple[str, str]], rank_by: str) -> str:
    """/risk with this page's query and `rank_by` replaced, back at the ranking."""
    query = [(k, v) for k, v in params if k != "rank_by"]
    query.append(("rank_by", rank_by))
    return "/risk?" + urlencode(query) + "#" + _ANCHOR


def build_ranking_section(
    ranking: CandidateRanking, params: Sequence[tuple[str, str]]
) -> RankingSection:
    common = dict(
        label=ranking.label,
        rf_available=ranking.rf_available,
        weight=ranking.weight,
        rank_by=ranking.rank_by,
        cap=ranking.cap,
        universe=ranking.universe,
        over_cap=ranking.over_cap,
        window=ranking.window,
        unranked=ranking.unranked,
        sort_links={key: ranking_url(params, key) for key in RANK_KEYS},
        left_out=tuple(
            LeftOut(s.ticker, s.reason or s.status)
            for s in ranking.statuses
            if s.status not in {"included", "excluded"}
        ),
    )
    if ranking.nothing is not None or ranking.window is None:
        reason = ranking.nothing or "nothing to rank"
        nothing = _NOTHING.get(reason, reason[:1].upper() + reason[1:] + ".")
        return RankingSection(**common, lines=(), greyed=(), legend=(), nothing=nothing)

    scored = (*(ranking.ranked or ()), *(ranking.greyed or ()))
    legend = legend_for(
        figure
        for candidate in scored
        for row in candidate.rows.values()
        for figure in (row.before, row.after)
        if figure is not None
    )
    markers = {note.flag: note.marker for note in legend}
    window_label = ranking.window.label

    def line(candidate: RankedCandidate, rank: int | None) -> RankingLine:
        rows = candidate.rows

        def cell(row: WhatIfRow) -> Cell:
            return figure_cell(row.after, window_label, ranking.label, markers)

        def change(row: WhatIfRow) -> DeltaCell:
            return DeltaCell(row.delta, row.delta_se if row.delta is not None else None, cell(row))

        return RankingLine(
            rank=rank,
            ticker=candidate.ticker,
            name=candidate.name,
            history_start=candidate.history_start,
            n_weeks=candidate.n_weeks,
            sigma_p=change(rows["sigma_p"]),
            diversification_ratio=change(rows["diversification_ratio"]),
            share_of_risk=cell(rows["candidate_pcr"]),
            correlation=cell(rows["candidate_correlation"]),
            sharpe=change(rows["sharpe"]),
        )

    return RankingSection(
        **common,
        lines=tuple(line(c, i) for i, c in enumerate(ranking.ranked or (), start=1)),
        greyed=tuple(line(c, None) for c in ranking.greyed or ()),
        legend=legend,
        nothing=None,
    )
