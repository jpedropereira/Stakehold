"""risk_ranking_view.py (RK-29): the ranking's GET form (`?rank_weight=`, `?rank_by=`) and the
section built from portfolio's CandidateRanking -- portfolio's order kept, each candidate's
deltas with the after figure's metadata, ΔSharpe's s.e., the greyed and unranked lists, the
cap, the order links, and why there's nothing to rank."""

from datetime import date
from decimal import Decimal

from web.portfolio_client.base import (
    RANKING_ROW_KEYS,
    BacktestAssetStatus,
    CandidateRanking,
    RankedCandidate,
    RankingWindow,
    RiskFigure,
    UnrankedCandidate,
    WhatIfRow,
)
from web.risk_ranking_view import (
    WINDOW_NOTE,
    build_ranking_section,
    parse_ranking,
    ranking_url,
)

LABEL = "ex-ante · constant weights"
START, END = date(2023, 10, 13), date(2026, 10, 16)


def _figure(value, flags=(), reason=None, se=None):
    return RiskFigure(value, START, END, "weekly", 52.0, 156, 0, flags, reason, se)


def _row(before, after, delta, se=None, **figure):
    return WhatIfRow(
        None if before is None else _figure(before, **figure), _figure(after, **figure), delta, se
    )


def _ranked(ticker, d_sigma, d_ratio=0.05, **rows):
    defaults = {
        "sigma_p": _row(0.14, 0.14 + d_sigma, d_sigma),
        "diversification_ratio": _row(1.2, 1.2 + d_ratio, d_ratio),
        "candidate_pcr": _row(None, 0.03, None),
        "candidate_correlation": _row(None, 0.12, None),
        "sharpe": _row(0.8, 0.82, 0.02, se=0.04),
    }
    merged = defaults | rows
    return RankedCandidate(
        ticker, f"{ticker} fund", date(2015, 1, 2), 610, {k: merged[k] for k in RANKING_ROW_KEYS}
    )


def _ranking(**changes):
    fields = dict(
        label=LABEL,
        rf_available=True,
        statuses=(
            BacktestAssetStatus("IWDA.AS", "included", None),
            BacktestAssetStatus("7203.T", "no_fx_rate", "no FX series for JPY"),
        ),
        nothing=None,
        universe=4,
        cap=50,
        over_cap=(),
        weight=0.05,
        rank_by="sigma_p",
        window=RankingWindow("3Y", date(2023, 10, 16), START, END),
        ranked=(_ranked("EGLN.L", -0.004), _ranked("IUSN.DE", 0.002)),
        greyed=(),
        unranked=(
            UnrankedCandidate(
                "LATE.DE",
                "Late fund",
                "short_history",
                "insufficient history (have 80, need 156)",
                date(2025, 3, 7),
                80,
            ),
        ),
    )
    return CandidateRanking(**(fields | changes))


PARAMS = [("benchmark", "IWDA.AS"), ("rank_weight", "5"), ("rank_by", "sigma_p")]


# --- the form ------------------------------------------------------------------------------------


def test_an_unsent_form_shows_five_percent_and_asks_for_nothing():
    form = parse_ranking(None, None, "3Y")
    assert (form.weight, form.rank_by, form.error, form.window_note) == ("5", "sigma_p", None, None)
    assert form.request is None


def test_a_valid_weight_is_sent_as_a_fraction():
    form = parse_ranking("7,5%", "diversification_ratio", "5Y")
    assert form.error is None
    assert (form.request.weight, form.request.rank_by) == (
        Decimal("0.075"),
        "diversification_ratio",
    )


def test_an_unknown_order_falls_back_to_volatility():
    form = parse_ranking("5", "sharpe", "3Y")
    assert form.rank_by == "sigma_p"
    assert form.request.rank_by == "sigma_p"


def test_a_blank_weight_is_an_error():
    form = parse_ranking(" ", None, "3Y")
    assert form.error == "Enter a weight."
    assert form.request is None


def test_a_weight_outside_zero_to_a_hundred_is_an_error():
    for text in ("abc", "0", "100", "-5"):
        form = parse_ranking(text, None, "3Y")
        assert form.error == "Enter a weight above 0% and below 100%.", text
        assert form.request is None


def test_one_year_sends_nothing_and_says_why():
    form = parse_ranking("5", None, "1Y")
    assert form.window_note == WINDOW_NOTE
    assert form.request is None
    assert "3Y" in WINDOW_NOTE


# --- the section ---------------------------------------------------------------------------------


def test_the_ranking_keeps_portfolios_order_and_numbers_it():
    section = build_ranking_section(_ranking(), PARAMS)

    assert [(line.rank, line.ticker, line.name) for line in section.lines] == [
        (1, "EGLN.L", "EGLN.L fund"),
        (2, "IUSN.DE", "IUSN.DE fund"),
    ]
    first = section.lines[0]
    assert (first.history_start, first.n_weeks) == (date(2015, 1, 2), 610)
    assert first.sigma_p.delta == -0.004
    assert first.diversification_ratio.delta == 0.05
    assert (first.sharpe.delta, first.sharpe.delta_se) == (0.02, 0.04)
    assert first.sigma_p.delta_se is None
    assert first.share_of_risk.value == 0.03
    assert first.correlation.value == 0.12
    assert "156 obs" in first.sigma_p.cell.title
    assert LABEL in first.sigma_p.cell.title


def test_the_section_carries_the_request_and_counts():
    section = build_ranking_section(_ranking(over_cap=("ZPRV.DE",), universe=51), PARAMS)
    assert (section.weight, section.rank_by, section.cap) == (0.05, "sigma_p", 50)
    assert section.over_cap == ("ZPRV.DE",)
    assert section.window.start == START
    assert section.nothing is None


def test_a_greyed_key_shows_its_reason_and_the_row_is_unnumbered():
    reason = "insufficient history (have 96, need 104)"
    gappy = _ranked(
        "GAPPY.L",
        0.001,
        diversification_ratio=WhatIfRow(
            _figure(None, reason=reason, flags=("insufficient_history",)),
            _figure(None, reason=reason, flags=("insufficient_history",)),
            None,
            None,
        ),
    )
    section = build_ranking_section(_ranking(greyed=(gappy,)), PARAMS)

    (line,) = section.greyed
    assert line.rank is None
    assert line.diversification_ratio.delta is None
    assert line.diversification_ratio.cell.reason == reason


def test_flags_get_a_legend_and_markers():
    flagged = _ranked("EGLN.L", -0.004, sigma_p=_row(0.14, 0.136, -0.004, flags=("data_gaps",)))
    section = build_ranking_section(_ranking(ranked=(flagged,)), PARAMS)

    assert [note.flag for note in section.legend] == ["data_gaps"]
    assert section.lines[0].sigma_p.cell.markers == ("1",)


def test_unranked_candidates_and_left_out_holdings_are_listed():
    section = build_ranking_section(_ranking(), PARAMS)

    (late,) = section.unranked
    assert (late.ticker, late.name, late.reason) == (
        "LATE.DE",
        "Late fund",
        "insufficient history (have 80, need 156)",
    )
    assert [(h.ticker, h.reason) for h in section.left_out] == [("7203.T", "no FX series for JPY")]


def test_the_order_links_keep_the_query_and_change_only_the_order():
    section = build_ranking_section(_ranking(), PARAMS)

    assert section.sort_links == {
        "sigma_p": "/risk/what-if?benchmark=IWDA.AS&rank_weight=5&rank_by=sigma_p#risk-ranking",
        "diversification_ratio": (
            "/risk/what-if?benchmark=IWDA.AS&rank_weight=5"
            "&rank_by=diversification_ratio#risk-ranking"
        ),
    }
    assert ranking_url([("exclude", "A"), ("exclude", "B")], "sigma_p") == (
        "/risk/what-if?exclude=A&exclude=B&rank_by=sigma_p#risk-ranking"
    )


def test_nothing_to_rank_says_why_in_the_pages_words():
    cases = {
        "no tracked ticker outside the mix to rank": (
            "Every ticker tracked is already one of your holdings."
        ),
        "no holding could be included": "None of your holdings could be measured.",
        "no candidate could be measured": "None of the tracked tickers could be measured.",
        "something new": "Something new.",
    }
    for reason, text in cases.items():
        ranking = _ranking(nothing=reason, window=None, ranked=None, greyed=None)
        section = build_ranking_section(ranking, PARAMS)
        assert section.nothing == text
        assert (section.lines, section.greyed) == ((), ())
