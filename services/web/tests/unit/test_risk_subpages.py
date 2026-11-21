"""UX-6: /risk split into four sub-pages under one sub-nav. Each route renders only its own
sections and calls only its own client methods; the sub-nav marks the current page and carries
the whole query, so every control's state survives a trip between sub-pages.

Its own minimal fakes, as each risk page test has: every figure call fails, since what a section
shows is its own test file's subject. Here only which sections render, and which calls are made.
"""

import html as html_lib
import re
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from web import settings as settings_module
from web.api.app import create_app
from web.api.deps import get_auth_client, get_portfolio_client
from web.auth_client.base import AuthResult, AuthUser
from web.auth_client.errors import SessionInvalid
from web.portfolio_client.base import Holding, Holdings, LookThrough
from web.portfolio_client.errors import PortfolioUnavailable
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"
END = date(2026, 10, 16)

# Each sub-page and the section headings it holds (their ids), in sub-nav order.
SUBPAGES = {
    "/risk": ("risk-ex-post-title",),
    "/risk/ex-ante": ("risk-ex-ante-title",),
    "/risk/what-if": ("risk-what-if-title", "risk-ranking-title"),
    "/risk/concentration": ("risk-concentration-title",),
}
ALL_SECTIONS = {section for sections in SUBPAGES.values() for section in sections}
TITLES = {
    "/risk": "Ex-post · Risk — Stakehold",
    "/risk/ex-ante": "Ex-ante · Risk — Stakehold",
    "/risk/what-if": "What if · Risk — Stakehold",
    "/risk/concentration": "Concentration · Risk — Stakehold",
}
# The calls each page makes on a plain view. The what-if and the ranking call portfolio only
# when their form is sent.
CALLS = {
    "/risk": {"ex_post_risk", "list_tickers", "list_tracked_tickers"},
    "/risk/ex-ante": {"list_holdings", "ex_ante_risk", "list_tickers", "list_tracked_tickers"},
    "/risk/what-if": {"list_holdings", "list_tickers", "list_tracked_tickers"},
    "/risk/concentration": {"list_holdings", "look_through", "list_tracked_tickers"},
}


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


class FakeAuthClient:
    def __init__(self):
        self.validate_result: AuthResult | None = None

    async def validate_session(self, *, cookie_header):
        if self.validate_result is None:
            raise SessionInvalid("no session")
        return self.validate_result


class FakePortfolioClient:
    def __init__(self):
        self.calls: list[str] = []
        self.holdings = Holdings(
            holdings=(_held("IWDA.AS", "7000"), _held("AAPL", "2000"), _held("TSLA", "1000")),
            totals=None,
        )
        self.holdings_error: Exception | None = None

    async def list_tickers(self):
        self.calls.append("list_tickers")
        return ["IWDA.AS", "VWCE.DE"]

    async def list_tracked_tickers(self, *, requested_by=None, statuses=None, tickers=None):
        self.calls.append("list_tracked_tickers")
        return [SimpleNamespace(ticker="AAPL", display_name="Apple Inc.", status="ready")]

    async def ex_post_risk(self, *, user_id, benchmark):
        self.calls.append("ex_post_risk")
        raise PortfolioUnavailable("down")

    async def list_holdings(self, *, user_id):
        self.calls.append("list_holdings")
        if self.holdings_error:
            raise self.holdings_error
        return self.holdings

    async def ex_ante_risk(self, *, assets, window, benchmark, exclude, rolling_pair=None):
        self.calls.append("ex_ante_risk")
        raise PortfolioUnavailable("down")

    async def look_through(self, *, assets):
        self.calls.append("look_through")
        return LookThrough(assets=(), exposures=None)

    async def what_if(self, *, assets, candidate, funding, window, exclude):
        self.calls.append("what_if")
        raise PortfolioUnavailable("down")

    async def rank_candidates(self, *, assets, weight, window, exclude, rank_by):
        self.calls.append("rank_candidates")
        raise PortfolioUnavailable("down")


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
        user = AuthUser(user_id="u1", email="ann@example.com", display_name="Ann")
        fake_auth.validate_result = AuthResult(user=user, set_cookie=SESSION_COOKIE)
        test_client.cookies.set("stakehold_session", "tok")
        yield test_client, fake_auth, fake_portfolio


def _page(test_client, url):
    response = test_client.get(url)
    assert response.status_code == 200
    return response.text


def _subnav(page_html):
    nav = re.search(r'<nav class="risk-subnav"[^>]* aria-label="Risk">(.*?)</nav>', page_html, re.S)
    assert nav, "no risk sub-nav"
    return [
        (html_lib.unescape(href), current, label)
        for href, current, label in re.findall(
            r'<a href="([^"]*)"( aria-current="page")?>([^<]*)</a>', nav.group(1)
        )
    ]


def _section_ids(page_html):
    return set(re.findall(r'<h2 id="(risk-[a-z-]+-title)"', page_html))


# --- each route renders its own sections and makes its own calls ------------------------------


@pytest.mark.parametrize("path", SUBPAGES)
def test_each_sub_page_renders_only_its_own_sections(client, path):
    test_client, _, _ = client

    assert _section_ids(_page(test_client, path)) == set(SUBPAGES[path])


@pytest.mark.parametrize("path", SUBPAGES)
def test_each_sub_page_calls_only_its_own_client_methods(client, path):
    test_client, _, fake_portfolio = client

    _page(test_client, path)

    assert set(fake_portfolio.calls) == CALLS[path]


def test_the_what_if_page_calls_the_what_if_and_ranking_only_when_their_forms_are_sent(client):
    test_client, _, fake_portfolio = client

    _page(test_client, "/risk/what-if?candidate=MSFT&candidate_weight=10&rank_weight=5")

    assert {"what_if", "rank_candidates"} <= set(fake_portfolio.calls)
    assert "ex_ante_risk" not in fake_portfolio.calls


@pytest.mark.parametrize("path", SUBPAGES)
def test_each_sub_page_has_its_own_title(client, path):
    test_client, _, _ = client

    assert f"<title>{TITLES[path]}</title>" in _page(test_client, path)


@pytest.mark.parametrize("path", SUBPAGES)
def test_the_site_nav_marks_risk_on_every_sub_page(client, path):
    test_client, _, _ = client

    assert '<a href="/risk" aria-current="page">Risk</a>' in _page(test_client, path)


@pytest.mark.parametrize("path", SUBPAGES)
def test_an_anonymous_visitor_is_sent_to_login(client, path):
    test_client, fake_auth, _ = client
    fake_auth.validate_result = None

    response = test_client.get(path, follow_redirects=False)

    assert response.status_code in (302, 303)
    assert response.headers["location"].startswith("/login")


# --- the sub-nav ------------------------------------------------------------------------------


@pytest.mark.parametrize("path", SUBPAGES)
def test_the_sub_nav_lists_the_four_pages_and_marks_the_current_one(client, path):
    test_client, _, _ = client

    links = _subnav(_page(test_client, path))

    assert [(href, label) for href, _, label in links] == [
        ("/risk", "Ex-post"),
        ("/risk/ex-ante", "Ex-ante"),
        ("/risk/what-if", "What if"),
        ("/risk/concentration", "Concentration"),
    ]
    assert [href for href, current, _ in links if current] == [path]


@pytest.mark.parametrize("path", SUBPAGES)
def test_the_sub_nav_carries_the_whole_query(client, path):
    test_client, _, _ = client
    query = "benchmark=IWDA.AS&ex_ante_window=5Y&exclude=AAPL&candidate=MSFT&rank_by=sigma_p"

    links = _subnav(_page(test_client, f"{path}?{query}"))

    assert [href for href, _, _ in links] == [f"{p}?{query}" for p in SUBPAGES]


# --- failures stay on their own page ----------------------------------------------------------


def test_a_failed_section_shows_its_notice_on_its_own_page_only(client):
    test_client, _, _ = client

    ex_ante = _page(test_client, "/risk/ex-ante")
    concentration = _page(test_client, "/risk/concentration")

    assert "Couldn't load the ex-ante figures." in ex_ante
    assert "Couldn't load the ex-post figures." not in ex_ante
    assert "Couldn't load the ex-ante figures." not in concentration


@pytest.mark.parametrize(
    ("path", "notice"),
    [
        ("/risk/what-if", "Couldn't load your holdings to compare against."),
        ("/risk/concentration", "Couldn't load the concentration figures."),
    ],
)
def test_a_failed_holdings_call_shows_its_notice_on_the_holdings_pages(client, path, notice):
    test_client, _, fake_portfolio = client
    fake_portfolio.holdings_error = PortfolioUnavailable("down")

    assert notice in _page(test_client, path)


# --- the what-if page names ex-ante's window and exclusions -----------------------------------


def _window_line(page_html):
    line = re.search(r'<p class="labs-note" id="risk-window-line">(.*?)</p>', page_html, re.S)
    assert line, "no window line"
    return line.group(1)


def test_the_what_if_page_names_the_ex_ante_window_it_measures_over(client):
    test_client, _, _ = client

    line = _window_line(_page(test_client, "/risk/what-if"))

    assert 'Over the ex-ante window, <span class="mono">3Y</span>.' in line


def test_the_what_if_page_names_the_exclusions_actually_sent(client):
    """A stale exclusion (not in the mix) isn't sent, so it isn't named."""
    test_client, _, _ = client

    line = _window_line(
        _page(test_client, "/risk/what-if?ex_ante_window=5Y&exclude=TSLA&exclude=NOPE")
    )

    text = re.sub(r"<[^>]+>", "", line)
    assert "Over the ex-ante window, 5Y, excluding TSLA." in text
    assert "NOPE" not in text


def test_the_window_line_links_to_ex_ante_with_the_query(client):
    test_client, _, _ = client
    query = "ex_ante_window=5Y&candidate=MSFT"

    line = _window_line(_page(test_client, f"/risk/what-if?{query}"))

    href = html_lib.unescape(
        re.search(r'<a href="([^"]*)">Change it on Ex-ante</a>', line).group(1)
    )
    assert href == f"/risk/ex-ante?{query}#risk-ex-ante"


def test_no_mix_no_window_line(client):
    test_client, _, fake_portfolio = client
    fake_portfolio.holdings = Holdings(holdings=(), totals=None)

    assert 'id="risk-window-line"' not in _page(test_client, "/risk/what-if")


# --- old in-page anchors ----------------------------------------------------------------------


@pytest.mark.parametrize("path", SUBPAGES)
def test_only_the_ex_post_page_loads_the_old_anchor_script(client, path):
    """A fragment never reaches the server, so /risk#risk-ranking can only be sent on by JS."""
    test_client, _, _ = client

    loaded = "/static/risk_anchor.js" in _page(test_client, path)

    assert loaded is (path == "/risk")
