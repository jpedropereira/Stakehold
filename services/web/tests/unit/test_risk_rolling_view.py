"""risk_rolling_view (RK-25): which rolling pair /risk asks for, and what each rolling chart is
handed. Pure: the figures are built by hand in portfolio's shapes, so each test reads as the
case it covers."""

from datetime import date

import pytest
from web.portfolio_client.base import RiskFigure, RollingCorrelation, RollingRisk
from web.risk_rolling_view import (
    PORTFOLIO,
    build_correlation_chart,
    build_rolling_charts,
    chosen_pair,
)

GAP = "insufficient history (have 51, need 52)"


def _figure(value, end, *, start=None, standard_error=None, reason=None, n_obs=52):
    end = date.fromisoformat(end)
    return RiskFigure(
        value=value,
        window_start=start or end.replace(year=end.year - 1),
        window_end=end,
        frequency="weekly",
        q=52.0,
        n_obs=n_obs,
        n_gaps=0,
        flags=(),
        reason=reason if value is None else None,
        standard_error=standard_error,
    )


def _rolling(volatility, sharpe):
    return RollingRisk(window="1Y", step="monthly", volatility=volatility, sharpe=sharpe)


def _pair(a="IWDA.AS", b=PORTFOLIO, points=(), reason=None):
    return RollingCorrelation(
        a=a, b=b, window="52W", step="weekly", reason=reason, points=tuple(points)
    )


MIX = ("IWDA.AS", "VWCE.DE", "AAPL")  # heaviest first, as build_default_mix orders it


# --- chosen_pair ---


def test_the_default_pair_is_the_heaviest_holding_against_the_portfolio():
    pair = chosen_pair(None, None, MIX, "VWCE.DE")

    assert (pair.a, pair.b, pair.requested, pair.dropped) == ("IWDA.AS", None, False, None)


@pytest.mark.parametrize(
    "raw_a, raw_b, expected",
    [
        ("AAPL", None, ("AAPL", None)),
        ("AAPL", "", ("AAPL", None)),
        ("AAPL", PORTFOLIO, ("AAPL", None)),
        ("AAPL", "IWDA.AS", ("AAPL", "IWDA.AS")),
        ("AAPL", "VWCE.DE", ("AAPL", "VWCE.DE")),
    ],
)
def test_a_requested_pair_from_the_mix_or_the_benchmark_is_kept(raw_a, raw_b, expected):
    pair = chosen_pair(raw_a, raw_b, MIX, "VWCE.DE")

    assert (pair.a, pair.b, pair.requested, pair.dropped) == (*expected, True, None)


def test_the_benchmark_can_be_a_side_when_it_isnt_held():
    pair = chosen_pair("IWDA.AS", "SPY", ("IWDA.AS",), "SPY")

    assert (pair.a, pair.b) == ("IWDA.AS", "SPY")


@pytest.mark.parametrize(
    "raw_a, raw_b, dropped",
    [
        # Excluded, sold since the link was made, or typed by hand: portfolio would 422 the mix.
        ("TSLA", None, "TSLA vs the portfolio isn't a pair from the measured mix"),
        ("AAPL", "TSLA", "AAPL vs TSLA isn't a pair from the measured mix"),
        ("AAPL", "AAPL", "AAPL vs AAPL isn't a pair from the measured mix"),
        (None, "AAPL", "the portfolio vs AAPL isn't a pair from the measured mix"),
        ("", "AAPL", "the portfolio vs AAPL isn't a pair from the measured mix"),
    ],
)
def test_a_pair_portfolio_would_reject_falls_back_to_the_default(raw_a, raw_b, dropped):
    pair = chosen_pair(raw_a, raw_b, MIX, "VWCE.DE")

    assert (pair.a, pair.b, pair.requested, pair.dropped) == ("IWDA.AS", None, False, dropped)


def test_an_excluded_holding_is_not_a_side():
    # The caller passes the mix as sent, with the exclusions already taken out.
    pair = chosen_pair("IWDA.AS", None, ("VWCE.DE", "AAPL"), "VWCE.DE")

    assert (pair.a, pair.b, pair.requested) == ("VWCE.DE", None, False)


# --- build_rolling_charts ---

VOLATILITY = (
    _figure(0.14, "2025-06-30"),
    _figure(None, "2025-07-31", reason=GAP, n_obs=51),
    _figure(None, "2025-08-29", reason=GAP, n_obs=51),
    _figure(0.18, "2025-09-30"),
    _figure(0.12, "2025-10-16"),
)
SHARPE = (
    _figure(0.8, "2025-06-30", standard_error=0.6),
    _figure(None, "2025-07-31", reason=GAP, n_obs=51),
    _figure(None, "2025-08-29", reason=GAP, n_obs=51),
    _figure(-0.2, "2025-09-30", standard_error=0.55),
    _figure(1.1, "2025-10-16", standard_error=0.62),
)


def test_rolling_is_none_when_portfolio_had_nothing_to_measure():
    assert build_rolling_charts(None, "ex-post") is None


def test_no_point_yet_says_the_history_is_under_a_year():
    charts = build_rolling_charts(_rolling((), ()), "ex-post")

    assert charts.series == ()
    assert charts.empty_reason == "No rolling point yet: the history is under a year."


def test_volatility_then_sharpe_each_with_its_canvas_and_island():
    charts = build_rolling_charts(_rolling(VOLATILITY, SHARPE), "ex-post")

    assert [(s.key, s.label, s.anchor, s.kind) for s in charts.series] == [
        ("volatility", "Volatility", "volatility", "pct1"),
        ("sharpe", "Sharpe", "sharpe", "ratio"),
    ]
    assert [s.canvas_id for s in charts.series] == [
        "risk-ex-post-rolling-volatility",
        "risk-ex-post-rolling-sharpe",
    ]
    assert [s.data_id for s in charts.series] == [
        "risk-ex-post-rolling-volatility-data",
        "risk-ex-post-rolling-sharpe-data",
    ]
    assert charts.empty_reason is None


def test_the_payload_plots_each_point_at_its_window_end_with_gaps_in_place():
    volatility = build_rolling_charts(_rolling(VOLATILITY, SHARPE), "ex-post").series[0]

    assert volatility.payload == {
        "kind": "pct1",
        "label": "Volatility",
        "dates": ["2025-06-30", "2025-07-31", "2025-08-29", "2025-09-30", "2025-10-16"],
        "starts": ["2024-06-30", "2024-07-31", "2024-08-29", "2024-09-30", "2024-10-16"],
        "n_obs": [52, 51, 51, 52, 52],
        "values": [0.14, None, None, 0.18, 0.12],
        "lower": None,
        "upper": None,
    }


def test_sharpe_carries_its_one_standard_error_band():
    sharpe = build_rolling_charts(_rolling(VOLATILITY, SHARPE), "ex-post").series[1]

    assert sharpe.payload["lower"] == pytest.approx([0.2, None, None, -0.75, 0.48])
    assert sharpe.payload["upper"] == pytest.approx([1.4, None, None, 0.35, 1.72])


def test_a_sharpe_with_no_standard_error_has_no_band_at_that_point():
    sharpe = (
        _figure(0.8, "2025-06-30", standard_error=None),
        _figure(0.9, "2025-07-31", standard_error=0.5),
    )
    volatility = (_figure(0.1, "2025-06-30"), _figure(0.1, "2025-07-31"))

    series = build_rolling_charts(_rolling(volatility, sharpe), "ex-ante").series[1]

    assert series.payload["lower"] == pytest.approx([None, 0.4])


def test_the_summary_names_the_latest_lowest_and_highest_windows():
    volatility, sharpe = build_rolling_charts(_rolling(VOLATILITY, SHARPE), "ex-post").series

    assert (volatility.summary.latest.value, volatility.summary.latest.end) == (
        0.12,
        date(2025, 10, 16),
    )
    assert (volatility.summary.low.value, volatility.summary.low.end) == (0.12, date(2025, 10, 16))
    assert (volatility.summary.high.value, volatility.summary.high.end) == (0.18, date(2025, 9, 30))
    assert sharpe.summary.latest.standard_error == 0.62
    assert (sharpe.summary.low.value, sharpe.summary.low.end) == (-0.2, date(2025, 9, 30))


def test_the_latest_is_the_last_measured_point_when_the_last_window_is_a_gap():
    volatility = VOLATILITY[:3]
    sharpe = SHARPE[:3]

    series = build_rolling_charts(_rolling(volatility, sharpe), "ex-post").series[0]

    assert series.summary.latest.end == date(2025, 6, 30)


def test_consecutive_gaps_with_one_reason_are_one_note():
    volatility = build_rolling_charts(_rolling(VOLATILITY, SHARPE), "ex-post").series[0]

    assert [(g.first_end, g.last_end, g.count, g.reason) for g in volatility.gaps] == [
        (date(2025, 7, 31), date(2025, 8, 29), 2, GAP)
    ]


def test_gaps_split_on_a_measured_point_or_a_new_reason():
    other = "no risk-free rate data"
    sharpe = (
        _figure(None, "2025-05-30", reason=GAP),
        _figure(None, "2025-06-30", reason=other),
        _figure(0.5, "2025-07-31", standard_error=0.5),
        _figure(None, "2025-08-29", reason=GAP),
    )
    volatility = tuple(_figure(0.1, f.window_end.isoformat()) for f in sharpe)

    series = build_rolling_charts(_rolling(volatility, sharpe), "ex-post").series[1]

    assert [(g.first_end, g.last_end, g.count, g.reason) for g in series.gaps] == [
        (date(2025, 5, 30), date(2025, 5, 30), 1, GAP),
        (date(2025, 6, 30), date(2025, 6, 30), 1, other),
        (date(2025, 8, 29), date(2025, 8, 29), 1, GAP),
    ]


def test_a_series_greyed_throughout_has_notes_but_no_chart():
    # No risk-free series: every Sharpe is greyed, volatility is still measured.
    sharpe = tuple(
        _figure(None, f.window_end.isoformat(), reason="no risk-free rate data") for f in VOLATILITY
    )

    volatility, sharpe = build_rolling_charts(_rolling(VOLATILITY, sharpe), "ex-post").series

    assert volatility.payload is not None
    assert sharpe.payload is None
    assert sharpe.summary is None
    assert [(g.count, g.reason) for g in sharpe.gaps] == [(5, "no risk-free rate data")]


# --- build_correlation_chart ---

POINTS = (
    _figure(0.9, "2025-09-26"),
    _figure(None, "2025-10-03", reason=GAP, n_obs=51),
    _figure(0.95, "2025-10-10"),
    _figure(0.6, "2025-10-17"),
)


def test_the_correlation_chart_names_its_pair_and_draws_rho():
    pair = chosen_pair(None, None, MIX, "VWCE.DE")

    chart = build_correlation_chart(_pair(points=POINTS), pair, MIX, "VWCE.DE", [])

    assert (chart.a, chart.b_label) == ("IWDA.AS", "the portfolio")
    assert chart.reason is None
    assert chart.series.kind == "ratio"
    assert chart.series.canvas_id == "risk-ex-ante-rolling-correlation"
    assert chart.series.payload["kind"] == "rho"
    assert chart.series.payload["values"] == [0.9, None, 0.95, 0.6]
    assert chart.series.payload["lower"] is None
    assert (chart.series.summary.low.value, chart.series.summary.high.value) == (0.6, 0.95)
    assert [(g.count, g.reason) for g in chart.series.gaps] == [(1, GAP)]


def test_a_pair_against_a_ticker_names_it():
    pair = chosen_pair("AAPL", "VWCE.DE", MIX, "VWCE.DE")

    chart = build_correlation_chart(_pair("AAPL", "VWCE.DE", POINTS), pair, MIX, "VWCE.DE", [])

    assert (chart.a, chart.b_label) == ("AAPL", "VWCE.DE")


def test_a_pair_portfolio_couldnt_measure_shows_its_reason_instead_of_a_chart():
    pair = chosen_pair("AAPL", None, MIX, "VWCE.DE")
    reason = "AAPL left out: no FX series for USD"

    chart = build_correlation_chart(_pair("AAPL", reason=reason), pair, MIX, "VWCE.DE", [])

    assert chart.series is None
    assert chart.reason == reason


def test_no_pair_in_the_response_means_nothing_to_draw():
    pair = chosen_pair(None, None, MIX, "VWCE.DE")

    assert build_correlation_chart(None, pair, MIX, "VWCE.DE", []) is None


def test_the_fallback_says_which_pair_was_dropped():
    pair = chosen_pair("TSLA", None, MIX, "VWCE.DE")

    chart = build_correlation_chart(_pair(points=POINTS), pair, MIX, "VWCE.DE", [])

    assert chart.dropped == "TSLA vs the portfolio isn't a pair from the measured mix"


def test_the_picker_offers_the_mix_and_the_benchmark_once_and_marks_the_pair():
    pair = chosen_pair("AAPL", "IWDA.AS", ("IWDA.AS", "AAPL"), "SPY")

    chart = build_correlation_chart(
        _pair("AAPL", "IWDA.AS", POINTS), pair, ("IWDA.AS", "AAPL"), "SPY", []
    )

    assert [(o.value, o.label, o.selected) for o in chart.options_a] == [
        ("IWDA.AS", "IWDA.AS", False),
        ("AAPL", "AAPL", True),
        ("SPY", "SPY (benchmark)", False),
    ]
    assert [(o.value, o.label, o.selected) for o in chart.options_b] == [
        (PORTFOLIO, "the portfolio", False),
        ("IWDA.AS", "IWDA.AS", True),
        ("AAPL", "AAPL", False),
        ("SPY", "SPY (benchmark)", False),
    ]


def test_a_held_benchmark_is_offered_once():
    pair = chosen_pair(None, None, MIX, "VWCE.DE")

    chart = build_correlation_chart(_pair(points=POINTS), pair, MIX, "VWCE.DE", [])

    assert [o.value for o in chart.options_a] == ["IWDA.AS", "VWCE.DE", "AAPL"]
    assert chart.options_b[0].selected


def test_the_picker_resends_the_rest_of_the_query():
    pair = chosen_pair("AAPL", None, MIX, "VWCE.DE")
    params = [
        ("benchmark", "VWCE.DE"),
        ("corr_a", "AAPL"),
        ("ex_ante_window", "5Y"),
        ("corr_b", "portfolio"),
        ("exclude", "TSLA"),
    ]

    chart = build_correlation_chart(_pair("AAPL", points=POINTS), pair, MIX, "VWCE.DE", params)

    assert chart.kept == (
        ("benchmark", "VWCE.DE"),
        ("ex_ante_window", "5Y"),
        ("exclude", "TSLA"),
    )
