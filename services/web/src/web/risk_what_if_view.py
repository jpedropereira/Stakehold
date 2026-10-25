"""The /risk page's what-if section (RK-20): today's mix before and after adding a candidate,
from portfolio's POST /risk/what-if (RK-19, spec §7.2).

Pure, like risk_ex_ante_view.py: the router fetches, this decides what is sent and shown. Two
parts:
- the form (`parse_what_if`): a GET form, so it works without JS, as LB-7's mix form does. The
  ticker, the weight in % of the mix after the change, and the funding: pro-rata or replacing
  one holding. Only what portfolio would 422 is caught here: a missing or over-long ticker, a
  holding as candidate, a weight outside (0%, 100%), a funding holding that isn't in the mix
  sent. A ticker portfolio can't measure is still sent: it answers 200 and says why, and a
  ticker priced only through a fund's holdings is a valid candidate (R14), so the tracked list
  isn't checked. A funding choice gone stale (its holding excluded since) is an error, not a
  fallback to pro-rata: that would answer a question the user didn't ask;
- the section (`build_what_if_section`): the before/after table in portfolio's order, the risk
  deltas first and ΔSharpe with its s.e. after them (§7.2), its cells risk_view's (title
  metadata, greyed reasons, flag markers and the legend). Then the threshold, or why it isn't
  shown, and why there's nothing to compare when there isn't.

The window and exclusions are the ex-ante section's (`?ex_ante_window=`, `?exclude=`), so
"before" is the mix that section measures.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from web.benchmark_param import MAX_TICKER_LENGTH
from web.labs_mix import parse_weight
from web.labs_view import DefaultMix
from web.portfolio_client.base import (
    WHAT_IF_ROW_KEYS,
    WhatIf,
    WhatIfCandidate,
    WhatIfFunding,
    WhatIfThreshold,
    WhatIfWindow,
)
from web.risk_ex_ante_view import LeftOut
from web.risk_view import Cell, FlagNote, figure_cell, legend_for

PRO_RATA = "pro_rata"
_REPLACE = "replace:"
_HUNDRED = Decimal(100)
_WEIGHT_RANGE = "Enter a weight above 0% and below 100%."

# (label, glossary anchor, kind) per row, in WHAT_IF_ROW_KEYS' order. "{c}" is the candidate.
# The kind picks the format, as in risk_view.SUMMARY_ROWS; none is coloured as a gain or loss.
_LINES = {
    "sigma_p": ("Volatility", "volatility", "pct1"),
    "diversification_ratio": ("Diversification ratio", "diversification-ratio", "ratio"),
    "candidate_pcr": ("{c}'s share of risk", "share-of-risk", "pct1"),
    "candidate_correlation": ("{c}'s correlation with the mix", "correlation", "ratio"),
    "sharpe": ("Sharpe ratio", "sharpe", "ratio"),
    "max_drawdown": ("Max drawdown", "max-drawdown", "pct1"),
}


@dataclass(frozen=True)
class WhatIfRequest:
    ticker: str
    weight: Decimal  # a fraction of the mix after the change, as portfolio takes it
    funding: str  # "pro_rata" or "replace:<ticker>"


@dataclass(frozen=True)
class WhatIfForm:
    ticker: str  # as typed, trimmed
    weight: str  # as typed
    funding: str
    funding_options: tuple[tuple[str, str], ...]  # (value, label)
    errors: dict[str, str]  # by field: "ticker", "weight", "funding"
    request: WhatIfRequest | None  # None when the form is empty or has an error


@dataclass(frozen=True)
class WhatIfLine:
    key: str
    label: str
    anchor: str  # its /glossary entry
    frequency: str  # "daily" or "weekly"
    kind: str  # "pct1" or "ratio"
    before: Cell | None  # None on the candidate's own rows
    after: Cell
    delta: float | None
    delta_se: float | None  # ΔSharpe's 1 s.e.


@dataclass(frozen=True)
class WhatIfSection:
    label: str
    rf_available: bool
    candidate: WhatIfCandidate
    funding: WhatIfFunding
    survivorship: bool
    window: WhatIfWindow | None
    lines: tuple[WhatIfLine, ...]
    legend: tuple[FlagNote, ...]
    threshold: WhatIfThreshold | None
    threshold_cell: Cell | None  # SR_c, when the threshold is applicable
    nothing: str | None  # why there's nothing to compare
    left_out: tuple[LeftOut, ...]  # holdings portfolio couldn't measure, and why


def parse_what_if(
    candidate: str | None,
    weight: str | None,
    funding: str | None,
    mix: DefaultMix,
    excluded: Sequence[str],
) -> WhatIfForm:
    """The form as typed, its errors, and the request to send when it has none. No candidate
    field at all is the empty form: nothing to send, nothing to complain about."""
    held = [ticker for ticker, _ in mix.assets]
    funders = [ticker for ticker in held if ticker not in excluded]
    options = (
        (PRO_RATA, "Pro-rata from every holding"),
        *((f"{_REPLACE}{ticker}", f"Replace {ticker}") for ticker in funders),
    )
    ticker, weight_text = (candidate or "").strip(), weight or ""
    funding = PRO_RATA if funding is None else funding
    if candidate is None:
        return WhatIfForm("", weight_text, funding, options, {}, None)
    errors = {}
    if not ticker:
        errors["ticker"] = "Enter a ticker."
    elif len(ticker) > MAX_TICKER_LENGTH:
        errors["ticker"] = f"A ticker is at most {MAX_TICKER_LENGTH} characters."
    else:
        holding = next((t for t in held if t.casefold() == ticker.casefold()), None)
        if holding is not None:
            errors["ticker"] = f"You already hold {holding}, so it can't be added to the mix."
    value = parse_weight(weight_text)
    if not weight_text.strip():
        errors["weight"] = "Enter a weight."
    elif value is None or value >= _HUNDRED:
        errors["weight"] = _WEIGHT_RANGE
    funding_error = _funding_error(funding, ticker or "it", funders, excluded)
    if funding_error:
        errors["funding"] = funding_error
    request = None if errors else WhatIfRequest(ticker, value / _HUNDRED, funding)
    return WhatIfForm(ticker, weight_text, funding, options, errors, request)


def _funding_error(
    funding: str, candidate: str, funders: Sequence[str], excluded: Sequence[str]
) -> str | None:
    if funding == PRO_RATA:
        return None
    replaced = funding.removeprefix(_REPLACE) if funding.startswith(_REPLACE) else ""
    if not replaced:
        return f"Choose how to fund {candidate}."
    if replaced in funders:
        return None
    if replaced in excluded:
        return f"{replaced} is excluded above, so it can't fund {candidate}. Choose again."
    return f"{replaced} isn't one of your holdings. Choose again."


def build_what_if_section(what_if: WhatIf) -> WhatIfSection:
    candidate = what_if.candidate
    common = dict(
        label=what_if.label,
        rf_available=what_if.rf_available,
        candidate=candidate,
        funding=what_if.funding,
        survivorship=candidate.survivorship,
        window=what_if.window,
        threshold=what_if.threshold,
        left_out=tuple(
            LeftOut(s.ticker, s.reason or s.status)
            for s in what_if.statuses
            if s.status not in {"included", "excluded"}
        ),
    )
    if what_if.rows is None or what_if.window is None:
        return WhatIfSection(
            **common, lines=(), legend=(), threshold_cell=None, nothing=_nothing(what_if)
        )
    window_label = what_if.window.label
    threshold = what_if.threshold
    sharpe_c = threshold.candidate_sharpe if threshold and threshold.applicable else None
    figures = [
        figure
        for row in what_if.rows.values()
        for figure in (row.before, row.after)
        if figure is not None
    ]
    legend = legend_for([*figures, *([sharpe_c] if sharpe_c else [])])
    markers = {note.flag: note.marker for note in legend}
    lines = []
    for key in WHAT_IF_ROW_KEYS:
        row = what_if.rows[key]
        label, anchor, kind = _LINES[key]
        lines.append(
            WhatIfLine(
                key=key,
                label=label.format(c=candidate.ticker),
                anchor=anchor,
                frequency=row.after.frequency,
                kind=kind,
                before=(
                    None
                    if row.before is None
                    else figure_cell(row.before, window_label, what_if.label, markers)
                ),
                after=figure_cell(row.after, window_label, what_if.label, markers),
                delta=row.delta,
                delta_se=row.delta_se,
            )
        )
    return WhatIfSection(
        **common,
        lines=tuple(lines),
        legend=legend,
        threshold_cell=(
            None
            if sharpe_c is None
            else figure_cell(sharpe_c, window_label, what_if.label, markers)
        ),
        nothing=None,
    )


def _nothing(what_if: WhatIf) -> str:
    """Why portfolio had nothing to compare (RK-19): the candidate, the holding to replace, or
    every holding couldn't be measured. In that order, as portfolio checks them."""
    candidate = what_if.candidate
    if candidate.status != "included":
        return f"{candidate.ticker} can't be measured: {candidate.reason or candidate.status}."
    if what_if.funding.reason:
        return f"{what_if.funding.reason}."
    return "None of your holdings could be measured."
