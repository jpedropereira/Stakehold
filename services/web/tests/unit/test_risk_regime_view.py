"""risk_regime_view (RK-28): what /risk's regime tables show. Pure: the regimes are built by hand
in portfolio's shapes, so each test reads as the case it covers."""

from datetime import date

from web.portfolio_client.base import (
    BenchmarkStatus,
    RegimeFigures,
    RegimeRow,
    RegimeView,
    RiskFigure,
)
from web.risk_regime_view import build_regime_section

LABEL = "ex-post · invested assets"
VWCE = BenchmarkStatus("VWCE.DE", True, None, date(2019, 7, 29))
NOT_TRACKED = BenchmarkStatus("NOPE", False, "not tracked", None)


def _figure(value, start, end, *, flags=(), reason=None, n_obs=33):
    return RiskFigure(
        value=value,
        window_start=start,
        window_end=end,
        frequency="daily",
        q=252.0,
        n_obs=n_obs,
        n_gaps=0,
        flags=tuple(flags),
        reason=reason if value is None else None,
        standard_error=None,
    )


def _row(
    regime_id,
    dimension,
    start,
    end,
    *,
    measured_start=None,
    measured_end=None,
    benchmark=(-0.12, -0.16),
    benchmark_reason=None,
    flags=("short_regime",),
):
    m_start = measured_start or start
    m_end = measured_end or end or date(2026, 10, 16)

    def figures(cumulative, drawdown, reason=None):
        return RegimeFigures(
            _figure(cumulative, m_start, m_end, reason=reason),
            _figure(drawdown, m_start, m_end, flags=flags, reason=reason),
        )

    return RegimeRow(
        regime_id=regime_id,
        dimension=dimension,
        scope="SPX",
        name=regime_id.replace("_", " ").title(),
        severity="severe",
        source="manual",
        start_date=start,
        end_date=end,
        measured_start=m_start,
        measured_end=m_end,
        partial=(m_start, m_end) != (start, end or m_end),
        ongoing=end is None,
        portfolio=figures(-0.1, -0.14),
        benchmark=figures(*benchmark, reason=benchmark_reason),
    )


ROWS = (
    _row("covid_crash", "drawdown", date(2020, 2, 19), date(2020, 3, 23)),
    _row("ukraine_war", "event", date(2022, 2, 24), None, measured_start=date(2023, 1, 2)),
    _row("tariff_selloff", "drawdown", date(2025, 2, 19), date(2025, 4, 8)),
    _row("ecb_cut", "monetary_policy", date(2024, 6, 12), None),
)


def _view(rows=ROWS, **changes):
    return RegimeView(
        **{"available": True, "reason": None, "total_loaded": 94, "rows": rows, **changes}
    )


def test_regimes_are_grouped_by_dimension_in_the_order_each_first_starts():
    section = build_regime_section(_view(), LABEL, VWCE)

    assert [(g.key, g.label) for g in section.groups] == [
        ("drawdown", "Drawdowns"),
        ("event", "Events"),
        ("monetary_policy", "Monetary policy"),
    ]
    assert [line.regime_id for line in section.groups[0].lines] == [
        "covid_crash",
        "tariff_selloff",
    ]
    assert section.count == 4
    assert section.empty_reason is None


def test_an_unknown_dimension_is_shown_by_its_own_name_not_dropped():
    rows = (_row("odd", "sentiment_shift", date(2021, 1, 4), date(2021, 2, 1)),)

    section = build_regime_section(_view(rows), LABEL, VWCE)

    assert [(g.key, g.label) for g in section.groups] == [("sentiment_shift", "Sentiment shift")]


def test_each_line_has_four_cells_with_their_window_and_label_in_the_title():
    line = build_regime_section(_view(), LABEL, VWCE).groups[0].lines[0]

    portfolio_return, portfolio_mdd, benchmark_return, benchmark_mdd = line.cells
    assert (portfolio_return.value, portfolio_mdd.value) == (-0.1, -0.14)
    assert (benchmark_return.value, benchmark_mdd.value) == (-0.12, -0.16)
    assert portfolio_return.title == (
        "Covid Crash: 2020-02-19 to 2020-03-23 · daily · 33 obs · 0 gaps · ex-post · invested "
        "assets"
    )
    assert benchmark_mdd.title.endswith("· ex-post · invested assets · against VWCE.DE")


def test_short_regime_is_a_numbered_marker_with_its_note():
    section = build_regime_section(_view(), LABEL, VWCE)

    (note,) = section.legend
    assert (note.flag, note.marker) == ("short_regime", "1")
    assert "200" in note.text and "historical" in note.text
    line = section.groups[0].lines[0]
    assert line.cells[1].markers == ("1",)
    assert line.cells[0].markers == ()


def test_partial_and_ongoing_regimes_say_where_they_were_measured():
    lines = {
        line.regime_id: line
        for group in build_regime_section(_view(), LABEL, VWCE).groups
        for line in group.lines
    }

    war, crash = lines["ukraine_war"], lines["covid_crash"]
    assert (war.partial, war.ongoing, war.measured_start) == (True, True, date(2023, 1, 2))
    assert (crash.partial, crash.ongoing) == (False, False)


def test_an_unavailable_benchmark_says_why_once_and_each_cell_unavailable():
    rows = (
        _row(
            "covid_crash",
            "drawdown",
            date(2020, 2, 19),
            date(2020, 3, 23),
            benchmark=(None, None),
            benchmark_reason="benchmark unavailable: not tracked",
        ),
    )

    section = build_regime_section(_view(rows), LABEL, NOT_TRACKED)

    cells = section.groups[0].lines[0].cells
    assert [cell.reason for cell in cells[2:]] == ["Unavailable", "Unavailable"]
    assert section.benchmark == NOT_TRACKED


def test_a_benchmark_too_short_for_a_regime_keeps_its_own_reason():
    reason = "benchmark history too short (from 2019-07-29)"
    rows = (
        _row(
            "gfc",
            "drawdown",
            date(2007, 10, 9),
            date(2009, 3, 9),
            benchmark=(None, None),
            benchmark_reason=reason,
        ),
    )

    cells = build_regime_section(_view(rows), LABEL, VWCE).groups[0].lines[0].cells

    assert [cell.reason for cell in cells[2:]] == [reason, reason]


def test_no_regimes_loaded_is_said_plainly():
    section = build_regime_section(
        _view((), available=False, reason="no regimes loaded", total_loaded=0), LABEL, VWCE
    )

    assert section.groups == ()
    assert (
        section.empty_reason == "No market regimes are loaded yet, so there is nothing to compare."
    )


def test_a_failed_fetch_says_the_regimes_couldnt_load():
    section = build_regime_section(
        _view(
            (),
            available=False,
            reason="regimes unavailable: data-store call failed",
            total_loaded=None,
        ),
        LABEL,
        VWCE,
    )

    assert section.empty_reason == "Couldn't load the market regimes. Try again later."


def test_a_view_with_no_overlapping_regime_says_so():
    section = build_regime_section(_view(()), LABEL, VWCE)

    assert section.empty_reason == "No market regime overlaps this history."


def test_another_reason_is_shown_as_portfolio_gave_it():
    section = build_regime_section(
        _view((), available=False, reason="no overlap", total_loaded=None), LABEL, VWCE
    )

    assert section.empty_reason == "No regime view: no overlap."


def test_no_view_means_no_section():
    assert build_regime_section(None, LABEL, VWCE) is None
