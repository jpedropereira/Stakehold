"""risk_ex_ante_view (RK-18): what /risk's ex-ante section shows -- the constant-weight summary,
the decomposition with weight beside risk and the holdings whose risk outweighs their money,
the € tail figures on the measured holdings' value, the heatmap's squares and their buckets,
and the window, exclude and include links."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from urllib.parse import parse_qsl, urlsplit

import pytest
from web.labs_view import DefaultMix
from web.portfolio_client.base import (
    RISK_FIGURE_KEYS,
    BacktestAssetStatus,
    BenchmarkStatus,
    CorrelationCell,
    CorrelationMatrix,
    ExAnteHolding,
    ExAnteRisk,
    ExAnteWindow,
    Holding,
    RiskFigure,
    SeriesRisk,
)
from web.risk_ex_ante_view import (
    DEFAULT_EX_ANTE_WINDOW,
    bucket,
    build_ex_ante_section,
    chosen_ex_ante_window,
    ex_ante_url,
)

LABEL = "ex-ante · constant weights"
START = date(2023, 10, 20)
END = date(2026, 10, 16)


def _figure(value=0.1, frequency="weekly", **changes):
    fields = dict(
        value=value,
        window_start=START,
        window_end=END,
        frequency=frequency,
        q=52.0 if frequency == "weekly" else 252.0,
        n_obs=156,
        n_gaps=0,
        flags=(),
        reason=None,
        standard_error=None,
    )
    return RiskFigure(**{**fields, **changes})


def _holding_row(ticker, weight, pcr, volatility=0.2):
    return ExAnteHolding(ticker, weight, volatility, 0.19, pcr * 0.137, pcr, 0.9)


def _matrix(keys, value=0.5, benchmark_key=None):
    return CorrelationMatrix(
        keys=tuple(keys),
        benchmark_key=benchmark_key,
        cells=tuple(
            CorrelationCell(a, b, _figure(1.0 if a == b else value))
            for i, a in enumerate(keys)
            for b in keys[i:]
        ),
    )


def _risk(**changes):
    rows = (_holding_row("IWDA.AS", 0.6, 0.84), _holding_row("AAPL", 0.4, 0.16))
    figures = {
        key: _figure(
            0.02,
            frequency=(
                "weekly"
                if key in {"volatility", "downside_deviation", "sharpe", "sortino"}
                else "daily"
            ),
        )
        for key in RISK_FIGURE_KEYS
    }
    defaults = dict(
        label=LABEL,
        currency="eur",
        rf_available=True,
        window=ExAnteWindow("3Y", date(2023, 10, 16), START, END, ()),
        benchmark=BenchmarkStatus("VWCE.DE", True, None, date(2019, 7, 29)),
        statuses=(
            BacktestAssetStatus("IWDA.AS", "included", None),
            BacktestAssetStatus("AAPL", "included", None),
        ),
        sigma_p=_figure(0.137),
        diversification_ratio=_figure(1.16),
        effective_n=1.92,
        holdings=rows,
        constant_weight=SeriesRisk(figures, date(2024, 2, 2), date(2024, 4, 7), None, 256),
        correlation=_matrix(["IWDA.AS", "AAPL", "VWCE.DE"], benchmark_key="VWCE.DE"),
    )
    defaults.update(changes)
    return ExAnteRisk(**defaults)


def _held(ticker, market_value):
    return Holding(
        canonical_ticker=ticker,
        quantity=Decimal("1"),
        average_cost_eur=None,
        cost_basis_eur=Decimal("1"),
        native_currency="EUR",
        mixed_currency=False,
        average_cost_native=None,
        market_value_native=None,
        priced=market_value is not None,
        price_date=END if market_value is not None else None,
        market_value_eur=None if market_value is None else Decimal(market_value),
        unrealized_return_eur=None,
        unrealized_return_pct=None,
        dividends_received_eur=None,
        total_return_eur=None,
        total_return_pct=None,
        accounts=(),
    )


HOLDINGS = (_held("IWDA.AS", "6000"), _held("AAPL", "4000"), _held("7203.T", "1000"))
MIX = DefaultMix(
    assets=(("IWDA.AS", Decimal("0.6")), ("AAPL", Decimal("0.4"))),
    unpriced=(),
    unweighted=(),
    dropped=(),
)


def _section(risk=None, holdings=HOLDINGS, mix=MIX, params=()):
    return build_ex_ante_section(risk or _risk(), mix, holdings, params)


# --- the summary ---


def test_the_summary_uses_sigma_p_as_the_volatility_and_adds_the_diversification_ratio():
    section = _section()

    keys = [row.key for row in section.summary_rows]
    assert keys[:3] == ["cumulative_return", "cagr", "volatility"]
    assert "diversification_ratio" in keys
    volatility = next(row for row in section.summary_rows if row.key == "volatility")
    # σ_p equals the constant-weight weekly volatility (§6.2); the page shows one of them.
    assert volatility.cells[0].value == 0.137
    assert keys.count("volatility") == 1
    assert section.effective_n == 1.92


def test_every_summary_cell_carries_its_metadata_and_the_ex_ante_label():
    section = _section()

    cell = section.summary_rows[0].cells[0]
    assert cell.title == (
        "3Y: 2023-10-20 to 2026-10-16 · daily · 156 obs · 0 gaps · ex-ante · constant weights"
    )


def test_a_greyed_figure_keeps_its_reason():
    risk = _risk()
    figures = dict(risk.constant_weight.figures)
    figures["var_99"] = _figure(None, "daily", reason="too few days")
    risk = replace(risk, constant_weight=replace(risk.constant_weight, figures=figures))

    row = next(r for r in _section(risk).summary_rows if r.key == "var_99")

    assert row.cells[0].value is None
    assert row.cells[0].reason == "too few days"


def test_flags_get_numbered_markers_and_a_legend():
    risk = _risk(sigma_p=_figure(0.137, flags=("short_window",)))

    section = _section(risk)

    assert [note.flag for note in section.legend] == ["short_window"]
    volatility = next(row for row in section.summary_rows if row.key == "volatility")
    assert volatility.cells[0].markers == ("1",)


def test_the_window_column_is_the_measured_window():
    risk = _risk(window=ExAnteWindow("Max", date(2009, 9, 25), date(2015, 3, 6), END, ("AAPL",)))

    section = _section(risk)

    assert section.column.label == "Max"
    assert section.column.start == date(2015, 3, 6)
    assert section.limited_by == ("AAPL",)


# --- the decomposition ---


def test_the_decomposition_puts_weight_beside_risk_and_their_ratio():
    rows = _section().decomposition.rows

    assert [(r.ticker, r.weight, r.pcr) for r in rows] == [
        ("IWDA.AS", 0.6, 0.84),
        ("AAPL", 0.4, 0.16),
    ]
    assert rows[0].risk_to_weight == pytest.approx(1.4)
    assert rows[1].risk_to_weight == pytest.approx(0.4)


def test_a_holding_with_10pct_weight_and_25pct_of_the_risk_is_named():
    risk = _risk(
        holdings=(_holding_row("IWDA.AS", 0.9, 0.75), _holding_row("TSLA", 0.1, 0.25)),
    )

    decomposition = _section(risk).decomposition

    assert [(n.ticker, n.weight, n.pcr) for n in decomposition.outsized] == [("TSLA", 0.1, 0.25)]
    assert [r.outsized for r in decomposition.rows] == [False, True]


@pytest.mark.parametrize(
    "weight, pcr, named",
    [
        (0.10, 0.15, True),  # exactly 1.5x and 5 pp
        (0.10, 0.1499, False),  # just under 1.5x
        (0.02, 0.06, False),  # 3x, but only 4 pp: a small holding
        (0.30, 0.349, False),  # 4.9 pp
        (0.30, 0.45, True),
    ],
)
def test_the_outsized_rule_needs_both_1_5x_and_5_points(weight, pcr, named):
    risk = _risk(
        holdings=(_holding_row("A", weight, pcr), _holding_row("B", 1 - weight, 1 - pcr)),
    )

    outsized = [n.ticker for n in _section(risk).decomposition.outsized]

    assert ("A" in outsized) is named


def test_a_hedge_is_named_and_not_outsized():
    risk = _risk(
        holdings=(_holding_row("IWDA.AS", 0.8, 1.05), _holding_row("TLT", 0.2, -0.05)),
    )

    decomposition = _section(risk).decomposition

    assert [n.ticker for n in decomposition.hedges] == ["TLT"]
    assert "TLT" not in [n.ticker for n in decomposition.outsized]


def test_no_note_when_risk_follows_weight():
    decomposition = _section().decomposition

    assert decomposition.outsized == ()
    assert decomposition.hedges == ()


def test_a_greyed_decomposition_says_why_once_and_keeps_the_weights():
    reason = "insufficient history (52 of 104 weeks)"
    risk = _risk(
        diversification_ratio=_figure(None, reason=reason),
        holdings=(
            ExAnteHolding("IWDA.AS", 0.6, None, None, None, None, None),
            ExAnteHolding("AAPL", 0.4, None, None, None, None, None),
        ),
    )

    decomposition = _section(risk).decomposition

    assert decomposition.reason == reason
    assert [r.weight for r in decomposition.rows] == [0.6, 0.4]
    assert all(r.pcr is None and r.risk_to_weight is None for r in decomposition.rows)
    assert decomposition.outsized == ()


# --- the € tail figures ---


def test_the_euro_var_applies_to_the_measured_holdings_value_only():
    risk = _risk(
        statuses=(
            BacktestAssetStatus("IWDA.AS", "included", None),
            BacktestAssetStatus("AAPL", "included", None),
            BacktestAssetStatus("7203.T", "no_fx_rate", "no FX series for JPY"),
        ),
    )

    section = _section(risk)

    # 7203.T isn't in the mix the percentage describes, so its €1,000 isn't in the base.
    assert section.measured_value == Decimal("10000")
    tail = {t.level: t for t in section.tail_euros}
    assert tail["95"].odds == "1 in 20"
    assert tail["95"].euros == Decimal("200.00")
    assert tail["99"].odds == "1 in 100"
    assert tail["99"].reason is None


def test_a_greyed_var_has_no_euro_figure_but_its_reason():
    risk = _risk()
    figures = dict(risk.constant_weight.figures)
    figures["var_99"] = _figure(None, "daily", reason="too few days")
    risk = replace(risk, constant_weight=replace(risk.constant_weight, figures=figures))

    tail = {t.level: t for t in _section(risk).tail_euros}

    assert tail["99"].euros is None
    assert tail["99"].reason == "too few days"


def test_an_unpriced_included_holding_adds_nothing_to_the_base():
    holdings = (_held("IWDA.AS", "6000"), _held("AAPL", None))

    assert _section(holdings=holdings).measured_value == Decimal("6000")


# --- the heatmap ---


@pytest.mark.parametrize(
    "value, expected",
    [
        (-0.9, 0),
        (0.0, 0),
        (0.1999, 0),
        (0.2, 1),
        (0.3999, 1),
        (0.4, 2),
        (0.6, 3),
        (0.7999, 3),
        (0.8, 4),
        (1.0, 4),
        (None, None),
    ],
)
def test_bucket_is_an_ink_ramp_on_positive_correlation(value, expected):
    assert bucket(value) == expected


def test_the_heatmap_mirrors_the_upper_triangle():
    risk = _risk(
        correlation=CorrelationMatrix(
            keys=("A", "B", "C"),
            benchmark_key="C",
            cells=(
                CorrelationCell("A", "A", _figure(1.0)),
                CorrelationCell("A", "B", _figure(0.3)),
                CorrelationCell("A", "C", _figure(-0.2)),
                CorrelationCell("B", "B", _figure(1.0)),
                CorrelationCell("B", "C", _figure(0.85)),
                CorrelationCell("C", "C", _figure(1.0)),
            ),
        )
    )

    heatmap = _section(risk).heatmap

    values = [[cell.value for cell in row] for row in heatmap.rows]
    assert values == [[1.0, 0.3, -0.2], [0.3, 1.0, 0.85], [-0.2, 0.85, 1.0]]
    assert heatmap.rows[1][2].bucket == 4
    assert heatmap.rows[2][0].bucket == 0
    assert heatmap.benchmark_key == "C"
    assert heatmap.rows[0][1].title.startswith("A and B: 2023-10-20 to 2026-10-16 · weekly")


def test_a_greyed_heatmap_cell_has_no_bucket_and_says_why_in_its_title():
    risk = _risk(
        correlation=CorrelationMatrix(
            keys=("A", "B"),
            benchmark_key=None,
            cells=(
                CorrelationCell("A", "A", _figure(1.0)),
                CorrelationCell("A", "B", _figure(None, reason="no variation in returns")),
                CorrelationCell("B", "B", _figure(1.0)),
            ),
        )
    )

    cell = _section(risk).heatmap.rows[1][0]

    assert cell.value is None
    assert cell.bucket is None
    assert "no variation in returns" in cell.title


# --- statuses and links ---


def test_left_out_holdings_are_listed_with_their_reason_and_the_mixes_own():
    risk = _risk(
        statuses=(
            BacktestAssetStatus("IWDA.AS", "included", None),
            BacktestAssetStatus("AAPL", "excluded", "excluded by request"),
            BacktestAssetStatus("7203.T", "no_fx_rate", "no FX series for JPY"),
        ),
    )
    mix = replace(MIX, unpriced=("NEWCO.DE",), dropped=("TINY.L",))

    section = _section(risk, mix=mix)

    assert section.excluded == ("AAPL",)
    assert [(h.ticker, h.reason) for h in section.left_out] == [
        ("7203.T", "no FX series for JPY"),
        ("NEWCO.DE", "no price yet"),
        ("TINY.L", "over the 50-holding limit"),
    ]


def test_nothing_included_has_no_figures_but_still_lists_why():
    risk = _risk(
        window=None,
        statuses=(BacktestAssetStatus("7203.T", "no_fx_rate", "no FX series for JPY"),),
        sigma_p=None,
        diversification_ratio=None,
        effective_n=None,
        holdings=None,
        constant_weight=None,
        correlation=None,
    )

    section = _section(risk)

    assert section.summary_rows == ()
    assert section.decomposition is None
    assert section.heatmap is None
    assert section.tail_euros == ()
    assert [h.ticker for h in section.left_out] == ["7203.T"]


def _query(url):
    parts = urlsplit(url)
    return parts.path, parse_qsl(parts.query), parts.fragment


def test_ex_ante_url_changes_only_the_window_and_keeps_the_rest():
    params = [("benchmark", "IWDA.AS"), ("ex_ante_window", "5Y"), ("exclude", "AAPL")]

    path, query, fragment = _query(ex_ante_url(params, window="Max"))

    assert path == "/risk"
    assert query == [("benchmark", "IWDA.AS"), ("exclude", "AAPL"), ("ex_ante_window", "Max")]
    assert fragment == "risk-ex-ante"


def test_ex_ante_url_replaces_the_exclusions():
    params = [("benchmark", "IWDA.AS"), ("exclude", "AAPL"), ("ex_ante_window", "5Y")]

    _, query, _ = _query(ex_ante_url(params, exclude=("AAPL", "NEWCO.DE")))

    assert query == [
        ("benchmark", "IWDA.AS"),
        ("ex_ante_window", "5Y"),
        ("exclude", "AAPL"),
        ("exclude", "NEWCO.DE"),
    ]


def test_the_section_links_exclude_each_limiting_holding_and_include_each_excluded_one():
    risk = _risk(
        window=ExAnteWindow("Max", date(2009, 9, 25), date(2015, 3, 6), END, ("NEWCO.DE",)),
        statuses=(
            BacktestAssetStatus("IWDA.AS", "included", None),
            BacktestAssetStatus("NEWCO.DE", "included", None),
            BacktestAssetStatus("AAPL", "excluded", "excluded by request"),
        ),
    )
    params = [("exclude", "AAPL"), ("ex_ante_window", "Max")]

    section = _section(risk, params=params)

    ((ticker, url),) = section.exclude_links
    assert ticker == "NEWCO.DE"
    assert ("exclude", "AAPL") in _query(url)[1] and ("exclude", "NEWCO.DE") in _query(url)[1]
    ((ticker, url),) = section.include_links
    assert ticker == "AAPL"
    assert [v for k, v in _query(url)[1] if k == "exclude"] == []
    assert list(section.window_links) == ["1Y", "3Y", "5Y", "10Y", "Max"]
    assert ("ex_ante_window", "5Y") in _query(section.window_links["5Y"])[1]


@pytest.mark.parametrize(
    "raw, expected",
    [(None, "3Y"), ("5Y", "5Y"), ("Max", "Max"), ("max", "3Y"), ("2Y", "3Y"), ("", "3Y")],
)
def test_chosen_ex_ante_window(raw, expected):
    assert DEFAULT_EX_ANTE_WINDOW == "3Y"
    assert chosen_ex_ante_window(raw) == expected
