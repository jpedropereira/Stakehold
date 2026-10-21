"""GET /risk (RK-12): the risk page, whose first section is the ex-post summary -- every window's
figures for every measure, their metadata, flags and greyed reasons, the holdings left out, and
the empty and error states. A failing section shows its own notice; the page still renders.

Like test_labs_page.py, it carries its own minimal fakes: only the calls this one page makes.
"""

import logging
import re
from dataclasses import replace
from datetime import date

import pytest
from fastapi.testclient import TestClient
from web import settings as settings_module
from web.api.app import create_app
from web.api.deps import get_auth_client, get_portfolio_client
from web.auth_client.base import AuthResult, AuthUser
from web.auth_client.errors import SessionInvalid
from web.portfolio_client.base import (
    RISK_FIGURE_KEYS,
    RISK_WINDOWS,
    ExcludedHolding,
    ExPostRisk,
    ExPostWindow,
    RiskFigure,
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
_WEEKLY = {"volatility", "downside_deviation", "sharpe", "sortino"}
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
    "cvar_99": 0.0482,
}


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

    async def ex_post_risk(self, *, user_id):
        self.calls.append(("ex_post_risk", user_id))
        if self.ex_post_error:
            raise self.ex_post_error
        return self.ex_post


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


def _window(label, *, covered=True, **figures) -> ExPostWindow:
    start = _STARTS[label]
    if covered:
        all_figures = {name: _figure(name, start) for name in RISK_FIGURE_KEYS}
    else:
        all_figures = {name: _greyed(name, start, UNCOVERED) for name in RISK_FIGURE_KEYS}
    all_figures.update(figures)
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
        windows=tuple(windows),
        excluded=(),
    )
    return replace(risk, **changes)


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

    assert fake_portfolio.calls == [("ex_post_risk", "u1")]


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
    assert "risk-table" not in html
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
    assert "risk-table" not in html


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
