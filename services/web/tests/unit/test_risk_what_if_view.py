"""risk_what_if_view (RK-20): the what-if form's checks, and the before/after section built from
portfolio's WhatIf (RK-19, spec §7.2)."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from web.labs_view import DefaultMix
from web.portfolio_client.base import (
    BacktestAssetStatus,
    RiskFigure,
    WhatIf,
    WhatIfCandidate,
    WhatIfFunding,
    WhatIfRow,
    WhatIfThreshold,
    WhatIfWindow,
)
from web.risk_view import FLAG_NOTES
from web.risk_what_if_view import (
    WhatIfRequest,
    build_what_if_section,
    parse_what_if,
)

MIX = DefaultMix(
    assets=(("IWDA.AS", Decimal("0.6")), ("AAPL", Decimal("0.3")), ("NVCR", Decimal("0.1"))),
    unpriced=("XYZ",),
    unweighted=(),
    dropped=(),
)


def _parse(candidate="EGLN.L", weight="10", funding="pro_rata", excluded=()):
    return parse_what_if(candidate, weight, funding, MIX, list(excluded))


# --- parse_what_if ----------------------------------------------------------------------


def test_no_candidate_is_an_empty_form_with_no_request():
    form = parse_what_if(None, None, None, MIX, [])

    assert form.request is None
    assert form.errors == {}
    assert (form.ticker, form.weight, form.funding) == ("", "", "pro_rata")


def test_funding_offers_pro_rata_then_each_holding_left_in():
    form = _parse(excluded=["NVCR"])

    assert form.funding_options == (
        ("pro_rata", "Pro-rata from every holding"),
        ("replace:IWDA.AS", "Replace IWDA.AS"),
        ("replace:AAPL", "Replace AAPL"),
    )


def test_a_valid_form_is_a_request_with_the_weight_as_a_fraction():
    form = _parse(candidate="  EGLN.L ", weight="7,5%", funding="replace:AAPL")

    assert form.errors == {}
    assert form.request == WhatIfRequest("EGLN.L", Decimal("0.075"), "replace:AAPL")
    # Shown back as typed, trimmed.
    assert (form.ticker, form.weight) == ("EGLN.L", "7,5%")


@pytest.mark.parametrize(
    ("candidate", "message"),
    [
        ("", "Enter a ticker."),
        ("   ", "Enter a ticker."),
        ("X" * 21, "A ticker is at most 20 characters."),
        ("aapl", "You already hold AAPL, so it can't be added to the mix."),
        # An excluded holding is still in the mix portfolio is sent.
        ("NVCR", "You already hold NVCR, so it can't be added to the mix."),
    ],
)
def test_ticker_errors(candidate, message):
    form = _parse(candidate=candidate, excluded=["NVCR"])

    assert form.errors == {"ticker": message}
    assert form.request is None


def test_a_holding_left_out_of_the_mix_can_be_a_candidate():
    # XYZ has no price yet, so it isn't in the mix portfolio is sent.
    assert _parse(candidate="XYZ").request is not None


@pytest.mark.parametrize(
    ("weight", "message"),
    [
        ("", "Enter a weight."),
        ("abc", "Enter a weight above 0% and below 100%."),
        ("0", "Enter a weight above 0% and below 100%."),
        ("-5", "Enter a weight above 0% and below 100%."),
        ("100", "Enter a weight above 0% and below 100%."),
        ("150%", "Enter a weight above 0% and below 100%."),
    ],
)
def test_weight_errors(weight, message):
    form = _parse(weight=weight)

    assert form.errors == {"weight": message}
    assert form.request is None


@pytest.mark.parametrize(
    ("funding", "message"),
    [
        # Chosen before NVCR was excluded: not silently changed to pro-rata.
        ("replace:NVCR", "NVCR is excluded above, so it can't fund EGLN.L. Choose again."),
        ("replace:MSFT", "MSFT isn't one of your holdings. Choose again."),
        ("cash", "Choose how to fund EGLN.L."),
        ("", "Choose how to fund EGLN.L."),
    ],
)
def test_funding_errors(funding, message):
    form = _parse(funding=funding, excluded=["NVCR"])

    assert form.errors == {"funding": message}
    assert form.request is None


def test_every_error_at_once():
    form = parse_what_if("", "", "replace:MSFT", MIX, [])

    assert set(form.errors) == {"ticker", "weight", "funding"}
    assert form.errors["funding"] == "MSFT isn't one of your holdings. Choose again."


def test_a_submit_without_funding_is_pro_rata():
    # The select always sends a value; a hand-written URL may not.
    assert _parse(funding=None).request.funding == "pro_rata"


# --- build_what_if_section --------------------------------------------------------------


def _figure(value=0.1, frequency="weekly", flags=(), reason=None, standard_error=None):
    return RiskFigure(
        value=value,
        window_start=date(2023, 10, 20),
        window_end=date(2026, 10, 16),
        frequency=frequency,
        q=52.0,
        n_obs=156,
        n_gaps=0,
        flags=tuple(flags),
        reason=reason,
        standard_error=standard_error,
    )


def _row(before, after, delta, delta_se=None, frequency="weekly", flags=()):
    return WhatIfRow(
        before=None if before is None else _figure(before, frequency),
        after=_figure(after, frequency, flags),
        delta=delta,
        delta_se=delta_se,
    )


def _rows(flags=()):
    return {
        "sigma_p": _row(0.14, 0.138, -0.002, flags=flags),
        "diversification_ratio": _row(1.16, 1.25, 0.09, flags=flags),
        "candidate_pcr": _row(None, 0.03, None, flags=flags),
        "candidate_correlation": _row(None, 0.12, None, flags=flags),
        "sharpe": _row(0.8, 0.88, 0.08, delta_se=0.1, flags=flags),
        "max_drawdown": _row(-0.2, -0.19, 0.01, frequency="daily", flags=flags),
    }


WINDOW = WhatIfWindow(
    label="3Y",
    requested_start=date(2023, 10, 16),
    start=date(2023, 10, 20),
    end=date(2026, 10, 16),
    limited_by=(),
    shortened=False,
    start_without_candidate=date(2023, 10, 20),
    end_without_candidate=date(2026, 10, 16),
)

THRESHOLD = WhatIfThreshold(
    applicable=True,
    reason=None,
    candidate_sharpe=_figure(0.5, standard_error=0.58),
    correlation=0.12,
    portfolio_sharpe=0.8,
    threshold=0.096,
    improves=True,
)


def _what_if(**changes):
    return replace(
        WhatIf(
            label="ex-ante · constant weights",
            rf_available=True,
            statuses=(
                BacktestAssetStatus("IWDA.AS", "included", None),
                BacktestAssetStatus("AAPL", "included", None),
                BacktestAssetStatus("NVCR", "no_history", "no price history"),
            ),
            candidate=WhatIfCandidate(
                ticker="EGLN.L",
                status="included",
                reason=None,
                requested_weight=0.1,
                weight=0.1,
                capped=False,
                survivorship=False,
            ),
            funding=WhatIfFunding("pro_rata", None, None),
            window=WINDOW,
            rows=_rows(),
            threshold=THRESHOLD,
        ),
        **changes,
    )


def test_rows_lead_with_risk_and_name_the_candidate():
    section = build_what_if_section(_what_if())

    assert [(line.key, line.label, line.anchor, line.kind) for line in section.lines] == [
        ("sigma_p", "Volatility", "volatility", "pct1"),
        ("diversification_ratio", "Diversification ratio", "diversification-ratio", "ratio"),
        ("candidate_pcr", "EGLN.L's share of risk", "share-of-risk", "pct1"),
        ("candidate_correlation", "EGLN.L's correlation with the mix", "correlation", "ratio"),
        ("sharpe", "Sharpe ratio", "sharpe", "ratio"),
        ("max_drawdown", "Max drawdown", "max-drawdown", "pct1"),
    ]
    assert section.nothing is None


def test_a_row_has_before_after_and_delta_cells():
    sigma, _, pcr, _, sharpe, drawdown = build_what_if_section(_what_if()).lines

    assert (sigma.before.value, sigma.after.value, sigma.delta) == (0.14, 0.138, -0.002)
    assert sigma.frequency == "weekly"
    assert sigma.after.title == (
        "3Y: 2023-10-20 to 2026-10-16 · weekly · 156 obs · 0 gaps · ex-ante · constant weights"
    )
    # The candidate's own rows have no before, so no delta.
    assert (pcr.before, pcr.delta) == (None, None)
    assert (sharpe.delta, sharpe.delta_se) == (0.08, 0.1)
    assert drawdown.frequency == "daily"


def test_a_greyed_figure_keeps_its_reason():
    rows = _rows()
    rows["diversification_ratio"] = WhatIfRow(
        before=_figure(None, reason="insufficient history (52 of 104 weeks)"),
        after=_figure(None, reason="insufficient history (52 of 104 weeks)"),
        delta=None,
        delta_se=None,
    )

    line = build_what_if_section(_what_if(rows=rows)).lines[1]

    assert line.before.reason == "insufficient history (52 of 104 weeks)"
    assert line.after.value is None
    assert line.delta is None


def test_survivorship_marks_the_after_figures_and_the_legend():
    what_if = _what_if(
        candidate=replace(_what_if().candidate, survivorship=True),
        rows=_rows(flags=("survivorship",)),
        threshold=replace(
            THRESHOLD, candidate_sharpe=_figure(0.5, flags=("survivorship",), standard_error=0.58)
        ),
    )

    section = build_what_if_section(what_if)

    assert section.survivorship is True
    assert [(note.flag, note.marker) for note in section.legend] == [("survivorship", "1")]
    sigma = section.lines[0]
    assert (sigma.before.markers, sigma.after.markers) == ((), ("1",))
    assert section.threshold_cell.markers == ("1",)


def test_the_survivorship_note_describes_a_candidate_priced_through_a_fund():
    # Only the what-if sets the flag (RK-19): R14's warning, not a tracked/untracked note.
    assert "today's index members" in FLAG_NOTES["survivorship"]


def test_pro_rata_threshold_is_kept_with_its_sharpe_cell():
    section = build_what_if_section(_what_if())

    assert section.threshold == THRESHOLD
    assert section.threshold_cell.value == 0.5
    assert section.threshold_cell.standard_error == 0.58


def test_replace_has_no_threshold_cell_and_keeps_the_reason():
    threshold = WhatIfThreshold(
        False, "shown under pro-rata funding only", None, None, None, None, None
    )

    section = build_what_if_section(
        _what_if(funding=WhatIfFunding("replace", "AAPL", None), threshold=threshold)
    )

    assert section.threshold.reason == "shown under pro-rata funding only"
    assert section.threshold_cell is None


def test_capped_candidate_weight_is_kept():
    candidate = replace(_what_if().candidate, weight=0.05, capped=True)

    section = build_what_if_section(
        _what_if(candidate=candidate, funding=WhatIfFunding("replace", "AAPL", None))
    )

    assert (section.candidate.requested_weight, section.candidate.weight) == (0.1, 0.05)
    assert section.candidate.capped is True


def test_holdings_portfolio_left_out_are_listed():
    section = build_what_if_section(_what_if())

    assert [(h.ticker, h.reason) for h in section.left_out] == [("NVCR", "no price history")]


@pytest.mark.parametrize(
    ("changes", "nothing"),
    [
        (
            {
                "candidate": WhatIfCandidate(
                    "0700.HK", "no_fx_rate", "no FX series for HKD", 0.1, None, None, False
                )
            },
            "0700.HK can't be measured: no FX series for HKD.",
        ),
        (
            {
                "funding": WhatIfFunding(
                    "replace",
                    "NVCR",
                    "NVCR, the holding to replace, can't be measured: no price history",
                )
            },
            "NVCR, the holding to replace, can't be measured: no price history.",
        ),
        (
            {"statuses": (BacktestAssetStatus("IWDA.AS", "failed", "couldn't reach data-store"),)},
            "None of your holdings could be measured.",
        ),
    ],
)
def test_nothing_to_compare_says_why(changes, nothing):
    what_if = _what_if(window=None, rows=None, threshold=None, **changes)

    section = build_what_if_section(what_if)

    assert section.nothing == nothing
    assert section.lines == ()
    assert section.threshold_cell is None
    assert section.legend == ()
