"""GET /risk (RK-12): the risk page, whose first section is the ex-post summary -- every window's
figures for every measure, their metadata, flags and greyed reasons, the holdings left out, and
the empty and error states. A failing section shows its own notice; the page still renders.

Like test_labs_page.py, it carries its own minimal fakes: only the calls this one page makes.
"""

import logging
import re
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from web import settings as settings_module
from web.api.app import create_app
from web.api.deps import get_auth_client, get_portfolio_client
from web.auth_client.base import AuthResult, AuthUser
from web.auth_client.errors import SessionInvalid
from web.portfolio_client.base import (
    RELATIVE_FIGURE_KEYS,
    RISK_FIGURE_KEYS,
    RISK_WINDOWS,
    BacktestAssetStatus,
    BenchmarkStatus,
    CorrelationCell,
    CorrelationMatrix,
    ExAnteHolding,
    ExAnteRisk,
    ExAnteWindow,
    ExcludedHolding,
    ExPostRisk,
    ExPostWindow,
    FxDecompositionRow,
    Holding,
    Holdings,
    LookThrough,
    RiskFigure,
    SeriesRisk,
)
from web.portfolio_client.errors import PortfolioUnavailable
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"
END = date(2026, 10, 16)
LABEL = "ex-post · invested assets"
UNCOVERED = "insufficient history (portfolio history from 2023-01-02)"
_STARTS = {
    "1Y": date(2025, 10, 16),
    "3Y": date(2023, 10, 16),
    "5Y": date(2021, 10, 16),
    "10Y": date(2016, 10, 16),
    "Max": date(2023, 1, 2),
}
_WEEKLY = {"volatility", "downside_deviation", "sharpe", "sortino", *RELATIVE_FIGURE_KEYS}
BENCHMARK = BenchmarkStatus(
    ticker="VWCE.DE", available=True, reason=None, first_date=date(2019, 7, 29)
)
# One value per figure, each distinct, so a test can find a figure by its printed value.
_VALUES = {
    "cumulative_return": 0.1234,
    "cagr": -0.0456,
    "volatility": 0.1531,
    "downside_deviation": 0.1012,
    "max_drawdown": -0.2345,
    "sharpe": 0.8412,
    "sortino": 1.2049,
    "calmar": 0.4311,
    "var_95": 0.0213,
    "cvar_95": 0.0318,
    "var_99": 0.0377,
    "beta": 0.9321,
    "correlation": 0.8765,
    "tracking_error": 0.0432,
    "information_ratio": -0.2178,
    "cvar_99": 0.0482,
}


EX_ANTE_LABEL = "ex-ante · constant weights"
EX_ANTE_START = date(2023, 10, 20)


def _ex_ante_figure(value, frequency="weekly", **changes) -> RiskFigure:
    figure = RiskFigure(
        value=value,
        window_start=EX_ANTE_START,
        window_end=END,
        frequency=frequency,
        q=52.0 if frequency == "weekly" else 251.4,
        n_obs=156 if frequency == "weekly" else 753,
        n_gaps=0,
        flags=(),
        reason=None,
        standard_error=None,
    )
    return replace(figure, **changes)


def _held(ticker, market_value) -> Holding:
    return Holding(
        canonical_ticker=ticker,
        quantity=Decimal("1"),
        average_cost_eur=None,
        cost_basis_eur=Decimal("1"),
        native_currency="EUR",
        mixed_currency=False,
        average_cost_native=None,
        market_value_native=None,
        priced=True,
        price_date=END,
        market_value_eur=Decimal(market_value),
        unrealized_return_eur=None,
        unrealized_return_pct=None,
        dividends_received_eur=None,
        total_return_eur=None,
        total_return_pct=None,
        accounts=(),
    )


# 70/20/10 by market value: build_default_mix sends these weights, largest first.
_HOLDINGS = (_held("IWDA.AS", "7000"), _held("TSLA", "1000"), _held("AAPL", "2000"))
_EX_ANTE_KEYS = ("IWDA.AS", "AAPL", "TSLA", "VWCE.DE")
# One distinct ρ per pair, so a test can find a square by its printed value.
_RHO = {
    ("IWDA.AS", "AAPL"): 0.6512,
    ("IWDA.AS", "TSLA"): 0.4123,
    ("IWDA.AS", "VWCE.DE"): 0.9734,
    ("AAPL", "TSLA"): 0.3311,
    ("AAPL", "VWCE.DE"): 0.6201,
    ("TSLA", "VWCE.DE"): -0.1234,
}


# AAPL and TSLA are USD: one distinct figure each, so a test can find them in the table.
_FX_ROWS = (
    FxDecompositionRow(
        "AAPL",
        "USD",
        _ex_ante_figure(0.2611),
        _ex_ante_figure(0.0812),
        _ex_ante_figure(-0.1873),
        _ex_ante_figure(0.25),
    ),
    FxDecompositionRow(
        "TSLA",
        "USD",
        _ex_ante_figure(0.5734),
        _ex_ante_figure(0.0812),
        _ex_ante_figure(-0.0921),
        _ex_ante_figure(0.55),
    ),
)


def _ex_ante(**changes) -> ExAnteRisk:
    figures = {
        name: _ex_ante_figure(_VALUES[name] / 2, "weekly" if name in _WEEKLY else "daily")
        for name in RISK_FIGURE_KEYS
    }
    risk = ExAnteRisk(
        label=EX_ANTE_LABEL,
        currency="eur",
        rf_available=True,
        window=ExAnteWindow("3Y", date(2023, 10, 16), EX_ANTE_START, END, ()),
        benchmark=BENCHMARK,
        statuses=tuple(
            BacktestAssetStatus(t, "included", None) for t in ("IWDA.AS", "AAPL", "TSLA")
        ),
        sigma_p=_ex_ante_figure(0.1418),
        diversification_ratio=_ex_ante_figure(1.2345),
        effective_n=1.8519,
        holdings=(
            ExAnteHolding("IWDA.AS", 0.7, 0.15, 0.13, 0.091, 0.6418, 0.95),
            ExAnteHolding("AAPL", 0.2, 0.25, 0.14, 0.028, 0.1975, 0.71),
            # 10% of the money, 16% of the risk: spec §6.3's headline case.
            ExAnteHolding("TSLA", 0.1, 0.55, 0.23, 0.023, 0.1607, 0.52),
        ),
        constant_weight=SeriesRisk(figures, date(2024, 2, 2), date(2024, 4, 7), None, 256),
        correlation=CorrelationMatrix(
            keys=_EX_ANTE_KEYS,
            benchmark_key="VWCE.DE",
            cells=tuple(
                CorrelationCell(a, b, _ex_ante_figure(1.0 if a == b else _RHO[a, b]))
                for i, a in enumerate(_EX_ANTE_KEYS)
                for b in _EX_ANTE_KEYS[i:]
            ),
        ),
        fx_decomposition=_FX_ROWS,
    )
    return replace(risk, **changes)


class FakeAuthClient:
    def __init__(self):
        self.validate_result: AuthResult | None = None

    async def validate_session(self, *, cookie_header):
        if self.validate_result is None:
            raise SessionInvalid("no session")
        return self.validate_result


class FakePortfolioClient:
    def __init__(self):
        self.ex_post: ExPostRisk = _risk()
        self.ex_post_error: Exception | None = None
        self.calls: list[tuple] = []
        self.tickers = ["IWDA.AS", "VWCE.DE"]
        self.tickers_error: Exception | None = None
        self.holdings = Holdings(holdings=_HOLDINGS, totals=None)
        self.holdings_error: Exception | None = None
        self.ex_ante: ExAnteRisk = _ex_ante()
        self.ex_ante_error: Exception | None = None

    async def list_tickers(self):
        if self.tickers_error:
            raise self.tickers_error
        return self.tickers

    async def ex_post_risk(self, *, user_id, benchmark):
        self.calls.append(("ex_post_risk", user_id, benchmark))
        if self.ex_post_error:
            raise self.ex_post_error
        return self.ex_post

    async def list_holdings(self, *, user_id):
        self.calls.append(("list_holdings", user_id))
        if self.holdings_error:
            raise self.holdings_error
        return self.holdings

    async def ex_ante_risk(self, *, assets, window, benchmark, exclude, rolling_pair=None):
        self.calls.append(("ex_ante_risk", tuple(assets), window, benchmark, tuple(exclude)))
        if self.ex_ante_error:
            raise self.ex_ante_error
        return self.ex_ante

    async def look_through(self, *, assets):
        # The concentration section's own tests are test_risk_concentration_page.py.
        return LookThrough(assets=(), exposures=None)

    async def what_if(self, *, assets, candidate, funding, window, exclude):
        # The what-if section's own tests are test_risk_what_if_page.py.
        self.calls.append(("what_if", candidate))
        raise PortfolioUnavailable("not under test here")

    async def rank_candidates(self, *, assets, weight, window, exclude, rank_by):
        # The ranking's own tests are test_risk_ranking_page.py.
        self.calls.append(("rank_candidates", weight))
        raise PortfolioUnavailable("not under test here")


def _settings() -> Settings:
    return Settings(
        auth_api_url="http://auth.internal",
        internal_secret="test-secret",  # pragma: allowlist secret
        portfolio_api_url="http://portfolio.internal",
        portfolio_internal_secret="test-portfolio-secret",  # pragma: allowlist secret
        brand_dir=settings_module._DEFAULT_BRAND_DIR,
        cookie_secure=False,
        api_host="127.0.0.1",
        api_port=8000,
        http_timeout_seconds=5.0,
    )


@pytest.fixture
def client():
    fake_auth, fake_portfolio = FakeAuthClient(), FakePortfolioClient()
    app = create_app(_settings())
    app.dependency_overrides[get_auth_client] = lambda: fake_auth
    app.dependency_overrides[get_portfolio_client] = lambda: fake_portfolio
    with TestClient(app) as test_client:
        yield test_client, fake_auth, fake_portfolio


def _logged_in(test_client, fake_auth):
    user = AuthUser(user_id="u1", email="ann@example.com", display_name="Ann")
    fake_auth.validate_result = AuthResult(user=user, set_cookie=SESSION_COOKIE)
    test_client.cookies.set("stakehold_session", "tok")


def _figure(name, start, value=None, **changes) -> RiskFigure:
    weekly = name in _WEEKLY
    figure = RiskFigure(
        value=_VALUES[name] if value is None else value,
        window_start=start,
        window_end=END,
        frequency="weekly" if weekly else "daily",
        q=52.0 if weekly else 251.4,
        n_obs=52 if weekly else 251,
        n_gaps=0,
        flags=(),
        reason=None,
        standard_error=0.3123 if name == "sharpe" else None,
    )
    return replace(figure, **changes)


def _greyed(name, start, reason, flags=("insufficient_history",)) -> RiskFigure:
    return replace(_figure(name, start), value=None, reason=reason, flags=flags)


def _window(label, *, covered=True, relative=None, **figures) -> ExPostWindow:
    start = _STARTS[label]
    if covered:
        all_figures = {name: _figure(name, start) for name in RISK_FIGURE_KEYS}
        all_relative = {name: _figure(name, start) for name in RELATIVE_FIGURE_KEYS}
    else:
        all_figures = {name: _greyed(name, start, UNCOVERED) for name in RISK_FIGURE_KEYS}
        all_relative = {name: _greyed(name, start, UNCOVERED) for name in RELATIVE_FIGURE_KEYS}
    all_figures.update(figures)
    all_relative.update(relative or {})
    return ExPostWindow(
        label=label,
        start=start,
        end=END,
        covered=covered,
        figures=all_figures,
        peak_date=date(2026, 2, 2) if covered else None,
        trough_date=date(2026, 4, 7) if covered else None,
        recovery_date=None,
        max_days_under_water=256 if covered else None,
        relative=all_relative,
    )


def _risk(*, one_y=None, **changes) -> ExPostRisk:
    windows = [_window(w, covered=w not in ("5Y", "10Y")) for w in RISK_WINDOWS]
    if one_y is not None:
        windows[0] = one_y
    risk = ExPostRisk(
        label=LABEL,
        base_date=date(2023, 1, 2),
        end_date=END,
        rf_available=True,
        benchmark=BENCHMARK,
        windows=tuple(windows),
        excluded=(),
    )
    return replace(risk, **changes)


def _ex_post_calls(fake_portfolio):
    return [call for call in fake_portfolio.calls if call[0] == "ex_post_risk"]


def _page(test_client):
    response = test_client.get("/risk")
    assert response.status_code == 200
    return response.text


def _table(html):
    return re.search(r'<table class="[^"]*risk-table.*?</table>', html, re.S).group(0)


def _rows(html):
    """{row label text: row html} for the summary table's body rows."""
    body = re.search(r"<tbody.*?</tbody>", _table(html), re.S).group(0)
    rows = re.findall(r"<tr role=\"row\".*?</tr>", body, re.S)
    return {re.sub(r"<[^>]+>", "", re.search(r"<th.*?</th>", r, re.S).group(0)): r for r in rows}


def _row(html, label):
    return next(row for text, row in _rows(html).items() if text.strip().startswith(label))


def _cells(row_html):
    return re.findall(r'<td class="num"[^>]*>.*?</td>', row_html, re.S)


# --- access and nav ---------------------------------------------------------------------------


def test_anonymous_visitor_is_sent_to_login(client):
    test_client, _, _ = client

    response = test_client.get("/risk", follow_redirects=False)

    assert response.status_code in (302, 303)
    assert response.headers["location"].startswith("/login")


def test_nav_has_a_risk_link_after_labs_marked_current(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    nav = re.search(r'<nav class="site-nav".*?</nav>', _page(test_client), re.S).group(0)

    assert re.findall(r">(\w+)</a>", nav)[:5] == [
        "Overview",
        "Portfolio",
        "Labs",
        "Risk",
        "Accounts",
    ]
    assert '<a href="/risk" aria-current="page">Risk</a>' in nav


def test_other_pages_link_to_risk_without_marking_it_current(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    assert '<a href="/risk">Risk</a>' in test_client.get("/glossary").text


def test_asks_portfolio_for_this_users_figures(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    _page(test_client)

    assert _ex_post_calls(fake_portfolio) == [("ex_post_risk", "u1", "VWCE.DE")]


# --- the summary table ------------------------------------------------------------------------


def test_the_section_is_labelled_ex_post_and_historical(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = _page(test_client)

    assert "Ex-post · invested assets" in html
    assert "historical" in html
    # R15: copy says "historical", never "expected".
    assert "expected" not in html.lower()


def test_a_column_per_window_with_its_dates(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    head = re.search(r"<thead.*?</thead>", _table(_page(test_client)), re.S).group(0)

    for label in RISK_WINDOWS:
        assert f">{label}<" in head
    assert '<span class="mono">2025-10-16</span>' in head
    assert '<span class="mono">2026-10-16</span>' in head


def test_a_row_per_measure_with_its_frequency(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = _page(test_client)
    rows = _rows(html)

    assert len(rows) == 12
    assert "weekly" in _row(html, "Volatility")
    assert "daily" in _row(html, "Max drawdown")


def test_figures_are_formatted_by_kind(client):
    """Spec §10: volatility, drawdown and VaR to 1 decimal, ratios to 2. Returns to 1 decimal
    with their sign, as the Labs risk panel shows CAGR (RK-9)."""
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = _page(test_client)

    assert ">+12.3%<" in _cells(_row(html, "Cumulative return"))[0]
    assert ">−4.6%<" in _cells(_row(html, "CAGR"))[0]
    assert ">15.3%<" in _cells(_row(html, "Volatility"))[0]
    assert ">−23.5%<" in _cells(_row(html, "Max drawdown"))[0]
    assert ">0.84<" in _cells(_row(html, "Sharpe"))[0]
    assert ">1.20<" in _cells(_row(html, "Sortino"))[0]
    assert ">2.1%<" in _cells(_row(html, "1-day VaR 95%"))[0]


def test_only_returns_are_coloured_as_gains_or_losses(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = _page(test_client)

    assert 'class="mono gain"' in _cells(_row(html, "Cumulative return"))[0]
    assert 'class="mono loss"' in _cells(_row(html, "CAGR"))[0]
    for label in ("Volatility", "Max drawdown", "Sharpe", "1-day VaR 95%", "1-day CVaR 99%"):
        cell = _cells(_row(html, label))[0]
        assert "gain" not in cell and "loss" not in cell, label


def test_sharpe_shows_its_standard_error(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = _page(test_client)

    assert "1 s.e." in _row(html, "Sharpe")
    assert '± <span class="mono">0.31</span>' in _cells(_row(html, "Sharpe"))[0]


def test_each_figure_has_its_metadata_in_its_title(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    cell = _cells(_row(_page(test_client), "Volatility"))[0]

    assert (
        'title="1Y: 2025-10-16 to 2026-10-16 · weekly · 52 obs · 0 gaps · '
        'ex-post · invested assets"'
    ) in cell
    assert 'data-label="1Y"' in cell


def test_the_max_drawdown_names_its_dates(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    cell = _cells(_row(_page(test_client), "Max drawdown"))[0]

    assert "2026-02-02" in cell
    assert "2026-04-07" in cell
    assert "not recovered" in cell


def test_an_uncovered_window_says_why_once_and_greys_its_cells(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = _page(test_client)

    head = re.search(r"<thead.*?</thead>", _table(html), re.S).group(0)
    assert head.count("portfolio history from 2023-01-02") == 2  # 5Y and 10Y
    five_y = _cells(_row(html, "Volatility"))[2]
    assert "Not covered" in five_y
    assert "%" not in re.sub(r'title="[^"]*"', "", five_y)
    assert html.count("portfolio history from 2023-01-02") == 2


def test_a_greyed_figure_shows_its_reason_never_a_zero(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    start = _STARTS["1Y"]
    fake_portfolio.ex_post = _risk(
        one_y=_window(
            "1Y",
            var_99=_greyed("var_99", start, "insufficient history (have 251, need 500)"),
        )
    )

    cell = _cells(_row(_page(test_client), "1-day VaR 99%"))[0]

    assert "insufficient history (have 251, need 500)" in cell
    assert "0.0%" not in cell


def test_flags_are_marked_and_explained_once(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    start = _STARTS["1Y"]
    fake_portfolio.ex_post = _risk(
        one_y=_window(
            "1Y",
            var_99=_figure("var_99", start, flags=("nonsync_daily", "tail_unstable")),
            var_95=_figure("var_95", start, flags=("nonsync_daily",)),
        )
    )

    html = _page(test_client)

    assert '<sup class="risk-flag">1, 2</sup>' in _cells(_row(html, "1-day VaR 99%"))[0]
    assert '<sup class="risk-flag">1</sup>' in _cells(_row(html, "1-day VaR 95%"))[0]
    legend = re.search(r'<ol class="risk-legend".*?</ol>', html, re.S).group(0)
    assert legend.count("<li") == 2
    assert "different markets" in legend
    assert "1,000 days" in legend
    assert "Under 3 years" not in legend


def test_no_flags_no_legend(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = _page(test_client)

    assert "risk-legend" not in html
    assert "risk-flag" not in html


def test_no_risk_free_rate_says_which_figures_are_greyed(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.ex_post = _risk(rf_available=False)

    html = _page(test_client)

    assert "No risk-free rate" in html
    assert "downside deviation, Sharpe and Sortino" in html


def test_the_risk_free_note_is_absent_when_the_rate_is_there(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    assert "No risk-free rate" not in _page(test_client)


def test_the_holdings_left_out_are_named(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.ex_post = _risk(
        excluded=(ExcludedHolding("7203.T", "no_fx_rate", "no FX series for JPY"),)
    )

    html = _page(test_client)

    assert '<span class="mono">7203.T</span> — no FX series for JPY' in html
    assert "scaled to 100%" in html


def test_the_as_of_date_is_shown(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    assert '<span class="mono">2026-10-16</span>' in re.search(
        r'<p class="as-of">.*?</p>', _page(test_client), re.S
    ).group(0)


# --- empty and error states -------------------------------------------------------------------


def test_nothing_to_measure_says_so(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.ex_post = _risk(windows=(), base_date=None, end_date=None, rf_available=False)

    html = _page(test_client)

    assert "Nothing to measure yet" in html
    assert 'href="/accounts"' in html
    assert 'aria-label="Ex-post risk figures"' not in html
    # Nothing was measured, so there's no rate to miss.
    assert "No risk-free rate" not in html


def test_a_failing_portfolio_shows_the_sections_notice_and_still_renders_the_page(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.ex_post_error = PortfolioUnavailable("down")

    html = _page(test_client)

    assert "Couldn't load the ex-post figures." in html
    assert "reach the portfolio service right now" in html
    assert "site-nav" in html
    assert 'aria-label="Ex-post risk figures"' not in html


# --- logging ----------------------------------------------------------------------------------


def _messages(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "web.api.risk"]


def test_logs_what_was_shown(client, caplog):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.ex_post = _risk(
        excluded=(ExcludedHolding("7203.T", "no_fx_rate", "no FX series for JPY"),)
    )

    with caplog.at_level(logging.INFO, logger="web.api.risk"):
        _page(test_client)

    messages = _messages(caplog)
    assert any("loading" in m for m in messages), messages
    assert any(
        "3 of 5 windows covered" in m and "24 of 60 figures greyed" in m and "7203.T" in m
        for m in messages
    ), messages


def test_logs_that_there_was_nothing_to_measure(client, caplog):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.ex_post = _risk(windows=(), base_date=None, end_date=None)

    with caplog.at_level(logging.INFO, logger="web.api.risk"):
        _page(test_client)

    assert any("nothing to measure" in m for m in _messages(caplog))


def test_logs_a_failure_as_an_error(client, caplog):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.ex_post_error = PortfolioUnavailable("down")

    with caplog.at_level(logging.INFO, logger="web.api.risk"):
        _page(test_client)

    errors = [r for r in caplog.records if r.name == "web.api.risk" and r.levelname == "ERROR"]
    assert errors


# --- glossary links ---------------------------------------------------------------------------


def _glossary_links(html):
    return set(re.findall(r'<a class="term-link" href="/glossary#([\w-]+)">(.*?)</a>', html))


def test_every_measure_links_to_its_glossary_entry(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    anchors = {anchor for anchor, _ in _glossary_links(_page(test_client))}

    assert {
        "ex-post",
        "cumulative-return",
        "cagr",
        "volatility",
        "downside-deviation",
        "max-drawdown",
        "sharpe",
        "sortino",
        "calmar",
        "var",
        "cvar",
    } <= anchors


def test_every_glossary_link_lands_on_an_entry(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    anchors = {anchor for anchor, _ in _glossary_links(_page(test_client))}
    glossary = test_client.get("/glossary").text

    assert anchors
    assert {a for a in anchors if f'<dt id="{a}"' not in glossary} == set()


# --- the benchmark (RK-14) ---------------------------------------------------------------------


def _relative_group(html):
    """The summary table's second body: the figures against the benchmark."""
    return re.findall(r"<tbody.*?</tbody>", _table(html), re.S)[1]


def _benchmark_form(html):
    return re.search(r'<form[^>]*id="risk-benchmark-form".*?</form>', html, re.S).group(0)


def test_the_benchmark_is_asked_for_and_kept_in_the_field(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    response = test_client.get("/risk?benchmark=IWDA.AS")

    assert response.status_code == 200
    assert _ex_post_calls(fake_portfolio) == [("ex_post_risk", "u1", "IWDA.AS")]
    form = _benchmark_form(response.text)
    assert 'method="get"' in form and 'action="/risk"' in form
    assert 'name="benchmark"' in form and 'value="IWDA.AS"' in form


def test_no_benchmark_shows_the_default_in_the_field(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    assert 'value="VWCE.DE"' in _benchmark_form(_page(test_client))


def test_the_field_suggests_the_tracked_tickers(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    form = _benchmark_form(_page(test_client))

    assert 'list="risk-benchmark-options"' in form
    datalist = re.search(r'<datalist id="risk-benchmark-options">(.*?)</datalist>', form, re.S)[1]
    assert re.findall(r'<option value="([^"]+)">', datalist) == ["IWDA.AS", "VWCE.DE"]


def test_a_failed_ticker_list_still_shows_the_field_and_the_figures(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.tickers_error = PortfolioUnavailable("down")

    html = _page(test_client)

    form = _benchmark_form(html)
    assert 'name="benchmark"' in form
    assert "<option" not in form
    assert "couldn&#39;t be loaded" in form or "couldn't be loaded" in form
    assert ">+12.3%<" in html


def test_an_over_long_benchmark_says_so_and_shows_the_default(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    html = test_client.get("/risk?benchmark=" + "A" * 21).text

    assert _ex_post_calls(fake_portfolio) == [("ex_post_risk", "u1", "VWCE.DE")]
    form = _benchmark_form(html)
    assert "at most 20 characters" in form
    assert 'aria-invalid="true"' in form


def test_the_relative_group_names_the_benchmark_and_its_first_date(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    group = _relative_group(_page(test_client))

    heading = re.sub(r"<[^>]+>", "", re.search(r"<th[^>]*colspan.*?</th>", group, re.S).group(0))
    assert " ".join(heading.split()) == "Against VWCE.DE, history from 2019-07-29"
    assert '<span class="mono">VWCE.DE</span>' in group


def test_the_relative_rows_are_formatted_in_ink(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    group = _relative_group(_page(test_client))

    for label, text in (
        ("Beta", ">0.93<"),
        ("Correlation", ">0.88<"),
        ("Tracking error", ">4.3%<"),
        ("Information ratio", ">−0.22<"),
    ):
        row = next(r for r in re.findall(r"<tr role=\"row\".*?</tr>", group, re.S) if label in r)
        assert "weekly" in row
        assert text in _cells(row)[0], label
    # A beta or a correlation isn't a gain or a loss.
    assert 'class="mono gain' not in group and 'class="mono loss' not in group


def test_an_unavailable_benchmark_says_why_once(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    reason = "benchmark unavailable: not tracked"
    greyed = {name: _greyed(name, _STARTS["1Y"], reason, flags=()) for name in RELATIVE_FIGURE_KEYS}
    fake_portfolio.ex_post = _risk(
        one_y=_window("1Y", relative=greyed),
        benchmark=BenchmarkStatus("NOPE.XX", False, "not tracked", None),
    )

    group = _relative_group(test_client.get("/risk?benchmark=NOPE.XX").text)

    heading = re.sub(r"<[^>]+>", "", re.search(r"<th[^>]*colspan.*?</th>", group, re.S).group(0))
    assert " ".join(heading.split()) == "Against NOPE.XX: unavailable, not tracked"
    assert group.count(reason) == 0
    assert "Unavailable" in group


def test_the_relative_figures_link_to_the_glossary(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    anchors = {anchor for anchor, _ in _glossary_links(_page(test_client))}

    assert {"benchmark", "beta", "correlation", "tracking-error", "information-ratio"} <= anchors


def test_logs_the_benchmark(client, caplog):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    with caplog.at_level(logging.INFO, logger="web.api.risk"):
        _page(test_client)

    messages = _messages(caplog)
    assert any(
        "against VWCE.DE (available)" in m and "8 of 20 relative" in m for m in messages
    ), messages


# --- the ex-ante section (RK-18) ---------------------------------------------------------------


def _ex_ante_html(html):
    return re.search(
        r'<section class="risk-section" aria-labelledby="risk-ex-ante-title".*?</section>',
        html,
        re.S,
    ).group(0)


def _ex_ante_calls(fake_portfolio):
    return [call for call in fake_portfolio.calls if call[0] == "ex_ante_risk"]


def test_ex_ante_sends_the_holdings_mix_over_3y_by_default(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    _page(test_client)

    ((_, assets, window, benchmark, exclude),) = _ex_ante_calls(fake_portfolio)
    assert [ticker for ticker, _ in assets] == ["IWDA.AS", "AAPL", "TSLA"]
    assert sum(weight for _, weight in assets) == 1
    assert (window, benchmark, exclude) == ("3Y", "VWCE.DE", ())


def test_ex_ante_window_and_exclusions_reach_portfolio(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    test_client.get("/risk?ex_ante_window=5Y&exclude=TSLA&benchmark=IWDA.AS")

    ((_, _, window, benchmark, exclude),) = _ex_ante_calls(fake_portfolio)
    assert (window, benchmark, exclude) == ("5Y", "IWDA.AS", ("TSLA",))


def test_an_unknown_window_falls_back_to_3y(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    test_client.get("/risk?ex_ante_window=2Y")

    assert _ex_ante_calls(fake_portfolio)[0][2] == "3Y"


def test_an_exclusion_not_in_the_mix_isnt_sent(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    test_client.get("/risk?exclude=NOPE.XX&exclude=TSLA")

    assert _ex_ante_calls(fake_portfolio)[0][4] == ("TSLA",)


def test_excluding_every_holding_sends_no_exclusion(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    test_client.get("/risk?exclude=IWDA.AS&exclude=AAPL&exclude=TSLA")

    # portfolio would reject it with a 422; the page measures the whole mix instead.
    assert _ex_ante_calls(fake_portfolio)[0][4] == ()


def test_the_ex_ante_section_is_labelled_and_historical(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    section = _ex_ante_html(_page(test_client))

    assert "Ex-ante · constant weights" in section
    assert "historical" in section
    assert "expected" not in section.lower()


def test_the_ex_ante_summary_shows_sigma_p_and_the_diversification_ratio(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    section = _ex_ante_html(_page(test_client))

    assert '<span class="mono">14.2%</span>' in section  # σ_p
    assert '<span class="mono">1.23</span>' in section  # diversification ratio
    assert '<span class="mono">1.9</span>' in section  # effective N
    assert f"· {EX_ANTE_LABEL}" in section  # every cell's title carries the label


def test_the_decomposition_shows_weight_beside_risk(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    section = _ex_ante_html(_page(test_client))

    table = re.search(r"<table[^>]*risk-decomposition.*?</table>", section, re.S).group(0)
    tsla = re.search(r"<tr[^>]*>(?:(?!</tr>).)*TSLA.*?</tr>", table, re.S).group(0)
    assert "10.0%" in tsla and "16.1%" in tsla and "1.61" in tsla


def test_an_outsized_holding_is_named_in_a_note(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    section = _ex_ante_html(_page(test_client))

    note = re.search(r'<p class="risk-insight">.*?</p>', section, re.S).group(0)
    assert "TSLA" in note and "10.0%" in note and "16.1%" in note
    assert "IWDA.AS" not in note


def test_no_note_when_risk_follows_weight(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.ex_ante = _ex_ante(
        holdings=(
            ExAnteHolding("IWDA.AS", 0.7, 0.15, 0.13, 0.1, 0.7, 0.95),
            ExAnteHolding("AAPL", 0.2, 0.25, 0.14, 0.03, 0.2, 0.71),
            ExAnteHolding("TSLA", 0.1, 0.55, 0.23, 0.01, 0.1, 0.52),
        )
    )

    assert 'class="risk-insight"' not in _ex_ante_html(_page(test_client))


def test_a_greyed_decomposition_says_why_once(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    reason = "insufficient history (52 of 104 weeks)"
    fake_portfolio.ex_ante = _ex_ante(
        diversification_ratio=_ex_ante_figure(None, reason=reason),
        holdings=tuple(
            ExAnteHolding(t, w, None, None, None, None, None)
            for t, w in (("IWDA.AS", 0.7), ("AAPL", 0.2), ("TSLA", 0.1))
        ),
    )

    section = _ex_ante_html(_page(test_client))

    assert section.count(reason) >= 1
    assert "70.0%" in section  # the weights are still shown


def test_the_euro_var_is_on_the_measured_holdings_value(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    section = _ex_ante_html(_page(test_client))

    # var_95 is 0.0213 / 2 on €10,000.
    assert "1 in 20" in section
    assert "€106.50" in section
    assert "€10,000.00" in section


def test_the_heatmap_has_a_square_per_pair_with_its_bucket(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    section = _ex_ante_html(_page(test_client))

    heatmap = re.search(r"<table[^>]*risk-heatmap.*?</table>", section, re.S).group(0)
    assert heatmap.count("0.97") == 2  # mirrored
    assert re.search(r'class="[^"]*corr-4[^"]*"[^>]*>\s*<span class="mono">0.97', heatmap)
    assert re.search(r'class="[^"]*corr-0[^"]*"[^>]*>\s*<span class="mono">−0.12', heatmap)
    assert "TSLA and VWCE.DE: 2023-10-20 to 2026-10-16 · weekly" in heatmap


def test_the_limiting_holding_can_be_excluded(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.ex_ante = _ex_ante(
        window=ExAnteWindow("Max", date(2009, 9, 25), date(2015, 3, 6), END, ("TSLA",))
    )

    section = _ex_ante_html(test_client.get("/risk?ex_ante_window=Max").text)

    assert "limited by" in section.lower()
    assert 'href="/risk?ex_ante_window=Max&amp;exclude=TSLA#risk-ex-ante"' in section


def test_an_excluded_holding_can_be_included_again(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.ex_ante = _ex_ante(
        statuses=(
            BacktestAssetStatus("IWDA.AS", "included", None),
            BacktestAssetStatus("AAPL", "included", None),
            BacktestAssetStatus("TSLA", "excluded", "excluded by request"),
        )
    )

    section = _ex_ante_html(test_client.get("/risk?exclude=TSLA").text)

    assert 'href="/risk?#risk-ex-ante"' in section
    assert "Include" in section


def test_the_window_links_mark_the_current_one(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    section = _ex_ante_html(test_client.get("/risk?benchmark=IWDA.AS").text)

    assert re.search(r'href="[^"]*ex_ante_window=3Y[^"]*" aria-current="true"', section)
    assert 'href="/risk?benchmark=IWDA.AS&amp;ex_ante_window=5Y#risk-ex-ante"' in section


def test_the_benchmark_form_keeps_the_ex_ante_choices(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = test_client.get("/risk?ex_ante_window=5Y&exclude=TSLA").text

    form = _benchmark_form(html)
    assert '<input type="hidden" name="ex_ante_window" value="5Y">' in form
    assert '<input type="hidden" name="exclude" value="TSLA">' in form


def test_the_benchmark_form_and_ex_ante_links_keep_the_what_if(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = test_client.get("/risk?candidate=EGLN.L&candidate_weight=10&funding=pro_rata").text

    assert _hidden(_benchmark_form(html)) == [
        ("candidate", "EGLN.L"),
        ("candidate_weight", "10"),
        ("funding", "pro_rata"),
    ]
    assert (
        'href="/risk?candidate=EGLN.L&amp;candidate_weight=10&amp;funding=pro_rata'
        '&amp;ex_ante_window=5Y#risk-ex-ante"' in html
    )


def test_the_benchmark_form_and_ex_ante_links_keep_the_ranking(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = test_client.get("/risk?rank_weight=5&rank_by=diversification_ratio").text

    assert _hidden(_benchmark_form(html)) == [
        ("rank_weight", "5"),
        ("rank_by", "diversification_ratio"),
    ]
    assert (
        'href="/risk?rank_weight=5&amp;rank_by=diversification_ratio&amp;ex_ante_window=5Y'
        '#risk-ex-ante"' in html
    )


def _hidden(form):
    return re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', form)


def test_ex_ante_left_out_holdings_are_named(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.ex_ante = _ex_ante(
        statuses=(
            BacktestAssetStatus("IWDA.AS", "included", None),
            BacktestAssetStatus("AAPL", "included", None),
            BacktestAssetStatus("TSLA", "no_fx_rate", "no FX series for USD"),
        )
    )

    section = _ex_ante_html(_page(test_client))

    assert "no FX series for USD" in section


def test_an_ex_ante_failure_leaves_the_ex_post_summary(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.ex_ante_error = PortfolioUnavailable("down")

    html = _page(test_client)

    assert "Couldn't load the ex-ante figures." in _ex_ante_html(html)
    assert 'aria-label="Ex-post risk figures"' in html


def test_a_holdings_failure_shows_the_ex_ante_notice(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.holdings_error = PortfolioUnavailable("down")

    html = _page(test_client)

    assert "Couldn't load the ex-ante figures." in _ex_ante_html(html)
    assert _ex_ante_calls(fake_portfolio) == []


def test_an_ex_post_failure_leaves_the_ex_ante_section(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.ex_post_error = PortfolioUnavailable("down")

    section = _ex_ante_html(_page(test_client))

    assert '<span class="mono">14.2%</span>' in section


def test_no_holdings_means_no_ex_ante_call(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.holdings = Holdings(holdings=(), totals=None)

    section = _ex_ante_html(_page(test_client))

    assert _ex_ante_calls(fake_portfolio) == []
    assert "Nothing to measure yet" in section


def test_logs_the_ex_ante_outcome(client, caplog):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.ex_ante = _ex_ante(
        window=ExAnteWindow("Max", date(2009, 9, 25), date(2015, 3, 6), END, ("TSLA",))
    )

    with caplog.at_level(logging.INFO, logger="web.api.risk"):
        test_client.get("/risk?ex_ante_window=Max&exclude=NOPE.XX")

    messages = _messages(caplog)
    assert any("ex-ante" in m and "3 holdings" in m and "Max" in m for m in messages), messages
    assert any("NOPE.XX" in m and "not in the mix" in m for m in messages), messages
    assert any(
        "ex-ante" in m and "limited by TSLA" in m and "0 of 10 correlation cells greyed" in m
        for m in messages
    ), messages


def _fx_html(section):
    return re.search(r'aria-label="FX decomposition".*?</table>', section, re.S).group(0)


def test_the_fx_decomposition_shows_each_usd_holdings_four_figures(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    section = _ex_ante_html(_page(test_client))

    table = _fx_html(section)
    aapl = re.search(r"<tr[^>]*>(?:(?!</tr>).)*AAPL.*?</tr>", table, re.S).group(0)
    for printed in ("USD", "26.1%", "8.1%", "−0.19", "25.0%"):
        assert printed in aapl, printed
    assert "IWDA.AS" not in table  # a EUR holding has no currency risk to split out
    assert "historically" in section


def test_a_greyed_fx_correlation_shows_its_reason(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    reason = "insufficient history (have 52, need 104)"
    row = replace(_FX_ROWS[0], correlation=_ex_ante_figure(None, reason=reason))
    fake_portfolio.ex_ante = _ex_ante(fx_decomposition=(row,))

    assert reason in _fx_html(_ex_ante_html(_page(test_client)))


def test_an_all_eur_mix_says_there_is_no_currency_risk(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.ex_ante = _ex_ante(fx_decomposition=())

    section = _ex_ante_html(_page(test_client))

    assert 'aria-label="FX decomposition"' not in section
    assert "Every holding measured is in euros" in section


def test_logs_the_fx_decomposition_count(client, caplog):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    with caplog.at_level(logging.INFO, logger="web.api.risk"):
        test_client.get("/risk")

    messages = _messages(caplog)
    assert any(
        "ex-ante" in m and "FX decomposition 2 holdings, 0 of 8 figures greyed" in m
        for m in messages
    ), messages


def test_the_ex_ante_terms_link_to_the_glossary(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    anchors = {anchor for anchor, _ in _glossary_links(_page(test_client))}

    assert {
        "ex-ante",
        "diversification-ratio",
        "effective-n",
        "share-of-risk",
        "correlation-matrix",
        "fx-decomposition",
    } <= anchors
