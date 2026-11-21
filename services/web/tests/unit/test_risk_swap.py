"""UX-10: Risk's switches swap only their own section. On /risk/ex-ante the window and the
Exclude/Include links swap #risk-ex-ante; on /risk/what-if the ranking's order swaps
#risk-ranking. Each is still a link back at its section, so it works without JS. An htmx request
aimed at the region gets it alone, plus out-of-band copies of what else carries the query: the
sub-nav (it carries the whole query, UX-6), and on /risk/what-if the window line's link and the
what-if form's hidden inputs.

Its own minimal fakes, copied from test_risk_ranking_page.py.
"""

import html
import logging
import re
from dataclasses import replace
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
from web.portfolio_client.base import (
    RISK_FIGURE_KEYS,
    BacktestAssetStatus,
    BenchmarkStatus,
    CandidateRanking,
    ExAnteRisk,
    ExAnteWindow,
    ExPostRisk,
    Holding,
    Holdings,
    LookThrough,
    RankedCandidate,
    RankingWindow,
    RiskFigure,
    SeriesRisk,
    UnrankedCandidate,
    WhatIfRow,
)
from web.portfolio_client.errors import PortfolioUnavailable
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"
LABEL = "ex-ante · constant weights"
END = date(2026, 10, 16)
START = date(2023, 10, 13)
BENCHMARK = BenchmarkStatus(
    ticker="VWCE.DE", available=False, reason="not fetched", first_date=None
)


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


HOLDINGS = Holdings(
    holdings=(_held("IWDA.AS", "6000"), _held("AAPL", "3000"), _held("TSLA", "1000")),
    totals=None,
)
_MIX = (("IWDA.AS", Decimal("0.6")), ("AAPL", Decimal("0.3")), ("TSLA", Decimal("0.1")))


def _figure(value, reason=None, flags=(), se=None):
    return RiskFigure(value, START, END, "weekly", 52.0, 156, 0, flags, reason, se)


def _row(before, after, delta, se=None):
    return WhatIfRow(None if before is None else _figure(before), _figure(after), delta, se)


def _candidate(ticker, name, d_sigma, d_ratio, **rows):
    return RankedCandidate(
        ticker,
        name,
        date(2010, 5, 21),
        856,
        {
            "sigma_p": _row(0.1412, 0.1412 + d_sigma, d_sigma),
            "diversification_ratio": _row(1.16, 1.16 + d_ratio, d_ratio),
            "candidate_pcr": _row(None, 0.0123, None),
            "candidate_correlation": _row(None, -0.2345, None),
            "sharpe": _row(0.8012, 0.8311, 0.0299, se=0.0412),
            **rows,
        },
    )


GREY = "insufficient history (have 96, need 104)"
RANKING = CandidateRanking(
    label=LABEL,
    rf_available=True,
    statuses=tuple(BacktestAssetStatus(t, "included", None) for t, _ in _MIX),
    nothing=None,
    universe=53,
    cap=50,
    over_cap=("ZPRV.DE", "ZPRX.DE", "ZSIL.L"),
    weight=0.05,
    rank_by="sigma_p",
    window=RankingWindow("3Y", date(2023, 10, 16), START, END),
    ranked=(
        _candidate("SGLD.L", "Invesco Physical Gold", -0.0071, 0.0912),
        _candidate("IEMA.AS", "iShares MSCI EM", -0.0012, 0.0344),
    ),
    greyed=(
        _candidate(
            "GAPPY.L",
            "Gappy fund",
            0.0003,
            0.0,
            diversification_ratio=WhatIfRow(
                _figure(None, GREY, ("insufficient_history",)),
                _figure(None, GREY, ("insufficient_history",)),
                None,
                None,
            ),
        ),
    ),
    unranked=(
        UnrankedCandidate(
            "LATE.DE",
            "Late fund",
            "short_history",
            "insufficient history (have 80, need 156)",
            date(2025, 3, 7),
            80,
        ),
        UnrankedCandidate("0700.HK", "Tencent", "no_fx_rate", "no FX series for HKD", None, None),
    ),
)


class FakeAuthClient:
    def __init__(self):
        self.validate_result = None

    async def validate_session(self, *, cookie_header):
        if self.validate_result is None:
            raise SessionInvalid("no session")
        return self.validate_result


class FakePortfolioClient:
    def __init__(self):
        self.calls = []
        self.holdings = HOLDINGS
        self.holdings_error: Exception | None = None
        self.tickers = ["IWDA.AS", "EGLN.L"]
        self.ranking = RANKING
        self.ranking_error: Exception | None = None

    async def list_tickers(self):
        return self.tickers

    # {ticker: display_name} for tickers data-store tracks (UX-2); a test sets its own.
    tracked_names: dict[str, str] = {}
    tracked_error: Exception | None = None

    async def list_tracked_tickers(self, *, requested_by=None, statuses=None, tickers=None):
        if self.tracked_error:
            raise self.tracked_error
        return [
            SimpleNamespace(ticker=ticker, display_name=name, status="ready")
            for ticker, name in self.tracked_names.items()
        ]

    async def ex_post_risk(self, *, user_id, benchmark):
        return ExPostRisk("ex-post · invested assets", None, None, False, BENCHMARK, (), ())

    async def list_holdings(self, *, user_id):
        if self.holdings_error:
            raise self.holdings_error
        return self.holdings

    ex_ante_error: Exception | None = None

    async def ex_ante_risk(self, *, assets, window, benchmark, exclude, rolling_pair=None):
        self.calls.append(("ex_ante", window, tuple(exclude)))
        if self.ex_ante_error:
            raise self.ex_ante_error
        # Measured, with a column, so the Exclude and Include links render (UX-10 swaps them).
        statuses = tuple(
            BacktestAssetStatus(t, "excluded" if t in exclude else "included", None)
            for t, _ in assets
        )
        figures = {key: _figure(0.1) for key in RISK_FIGURE_KEYS}
        return ExAnteRisk(
            LABEL,
            "eur",
            True,
            ExAnteWindow(window, START, START, END, ()),
            BENCHMARK,
            statuses,
            _figure(0.14),
            _figure(1.2),
            1.8,
            (),
            SeriesRisk(figures, None, None, None, None),
            None,
            None,
        )

    async def look_through(self, *, assets):
        return LookThrough(assets=(), exposures=None)

    async def what_if(self, *, assets, candidate, funding, window, exclude):
        # The what-if section's own tests are test_risk_what_if_page.py.
        raise PortfolioUnavailable("not under test here")

    async def rank_candidates(self, *, assets, weight, window, exclude, rank_by):
        self.calls.append(("rank", tuple(assets), weight, window, tuple(exclude), rank_by))
        if self.ranking_error:
            raise self.ranking_error
        return replace(self.ranking, rank_by=rank_by)


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
        yield test_client, fake_portfolio


# --- helpers ----------------------------------------------------------------------------------

RANK_QUERY = "?rank_weight=5"


def _get(test_client, path, query="", target=None, **headers):
    sent = dict(headers)
    if target is not None:
        sent.update({"HX-Request": "true", "HX-Target": target})
    response = test_client.get(path + query, headers=sent)
    assert response.status_code == 200
    return response


def _open_tag(page, element_id):
    match = re.search(rf'<[a-z]+ [^>]*\bid="{element_id}"[^>]*>', page)
    assert match, f"no element #{element_id}"
    return match.group(0)


def _attr(tag, name):
    match = re.search(rf'\b{name}="([^"]*)"', tag)
    return None if match is None else html.unescape(match.group(1))


def _group(page, label):
    match = re.search(
        rf'<div class="segmented[^"]*"[^>]*aria-label="{label}">.*?</div>', page, re.S
    )
    assert match, f"no group {label}"
    return match.group(0)


def _options(fragment):
    return re.findall(r"<a [^>]*>", fragment)


def _current(group):
    return re.findall(r'aria-current="true"[^>]*>(?:<span class="mono">)?([^<]+)<', group)


def _subnav(page):
    match = re.search(r'<nav [^>]*id="risk-subnav".*?</nav>', page, re.S)
    assert match, "no sub-nav"
    return match.group(0)


def _ranks(fake):
    return [call for call in fake.calls if call[0] == "rank"]


def _swaps_into(tag, region):
    href = _attr(tag, "href")
    assert href.endswith(f"#{region}"), href
    assert _attr(tag, "hx-get") == href.split("#")[0]
    assert _attr(tag, "hx-push-url") == href
    assert _attr(tag, "hx-target") == f"#{region}"
    assert _attr(tag, "hx-select") == f"#{region}"
    return True


# --- /risk/ex-ante ----------------------------------------------------------------------------


def test_the_ex_ante_window_options_swap_the_section(client):
    test_client, _ = client

    page = _get(test_client, "/risk/ex-ante").text

    options = _options(_group(page, "Ex-ante window"))
    assert options
    assert all(_swaps_into(o, "risk-ex-ante") for o in options)


def test_an_include_link_swaps_the_section_and_keeps_its_ticker_for_focus(client):
    test_client, _ = client

    page = _get(test_client, "/risk/ex-ante", "?exclude=AAPL").text

    links = [o for o in _options(page) if "Include" in page.split(o, 1)[1][:200]]
    assert links, "no include link"
    assert _swaps_into(links[0], "risk-ex-ante")
    assert _attr(links[0], "data-swap-focus") == "AAPL"


def test_the_ex_ante_section_is_a_region_that_announces_its_window(client):
    test_client, _ = client

    plain = _open_tag(_get(test_client, "/risk/ex-ante", "?ex_ante_window=5Y").text, "risk-ex-ante")
    excluding = _open_tag(
        _get(test_client, "/risk/ex-ante", "?ex_ante_window=5Y&exclude=AAPL").text, "risk-ex-ante"
    )

    assert "data-swap-region" in plain
    assert _attr(plain, "data-swap-announce") == "Ex-ante over 5Y"
    assert _attr(excluding, "data-swap-announce") == "Ex-ante over 5Y, excluding AAPL"


def test_an_ex_ante_swap_sends_the_section_and_the_sub_nav(client):
    test_client, fake = client

    swap = _get(test_client, "/risk/ex-ante", "?ex_ante_window=5Y", target="risk-ex-ante").text

    assert "<html" not in swap and "<header" not in swap
    assert swap.lstrip().startswith(_open_tag(swap, "risk-ex-ante"))
    assert _current(_group(swap, "Ex-ante window")) == ["5Y"]
    nav = _subnav(swap)
    assert 'hx-swap-oob="true"' in _open_tag(swap, "risk-subnav")
    assert all("ex_ante_window=5Y" in _attr(o, "href") for o in _options(nav))
    assert ("ex_ante", "5Y", ()) in fake.calls
    assert "<script src" not in swap


def test_a_failed_ex_ante_load_shows_its_notice_inside_the_region(client):
    test_client, fake = client
    fake.ex_ante_error = PortfolioUnavailable("down")

    swap = _get(test_client, "/risk/ex-ante", "?ex_ante_window=5Y", target="risk-ex-ante").text

    assert swap.lstrip().startswith(_open_tag(swap, "risk-ex-ante"))
    assert "the ex-ante figures." in swap
    assert "<html" not in swap


def test_the_ex_ante_page_loads_the_chart_scripts_outside_the_region(client):
    """A window the first view had no chart for can bring one, so the scripts load anyway."""
    test_client, _ = client

    page = _get(test_client, "/risk/ex-ante").text

    region_end = page.index("</section>", page.index('id="risk-ex-ante"'))
    assert page.index("risk_rolling_chart.js") > region_end
    assert "vendor/chart.min.js" in page


# --- /risk/what-if's ranking ------------------------------------------------------------------


def test_the_ranking_order_options_swap_the_ranking(client):
    test_client, _ = client

    page = _get(test_client, "/risk/what-if", RANK_QUERY).text

    options = _options(_group(page, "Order by"))
    assert len(options) == 2
    assert all(_swaps_into(o, "risk-ranking") for o in options)
    assert "data-swap-region" in _open_tag(page, "risk-ranking")
    assert _attr(_open_tag(page, "risk-ranking"), "data-swap-announce") == "Ranked by volatility"


def test_a_ranking_swap_sends_the_ranking_and_every_carrier_of_its_order(client):
    test_client, fake = client

    swap = _get(
        test_client,
        "/risk/what-if",
        RANK_QUERY + "&rank_by=diversification_ratio",
        target="risk-ranking",
    ).text

    assert "<html" not in swap
    assert swap.lstrip().startswith(_open_tag(swap, "risk-ranking"))
    assert 'id="risk-what-if"' not in swap
    assert _current(_group(swap, "Order by")) == ["Diversification ratio"]
    assert all("rank_by=diversification_ratio" in _attr(o, "href") for o in _options(_subnav(swap)))
    line = _open_tag(swap, "risk-window-line")
    assert 'hx-swap-oob="true"' in line
    kept = re.search(r'<div id="risk-what-if-kept".*?</div>', swap, re.S).group(0)
    assert 'hx-swap-oob="true"' in kept
    assert '<input type="hidden" name="rank_by" value="diversification_ratio">' in kept
    assert _ranks(fake)[-1][-1] == "diversification_ratio"


@pytest.mark.parametrize(
    "path, headers",
    [
        ("/risk/ex-ante", {}),
        ("/risk/ex-ante", {"HX-Request": "true", "HX-Target": "risk-ranking"}),
        (
            "/risk/ex-ante",
            {
                "HX-Request": "true",
                "HX-Target": "risk-ex-ante",
                "HX-History-Restore-Request": "true",
            },
        ),
        ("/risk/what-if", {"HX-Request": "true", "HX-Target": "risk-ex-ante"}),
    ],
    ids=["plain", "other-pages-region", "history-restore", "ex-ante-region-on-what-if"],
)
def test_anything_else_gets_the_full_page(client, path, headers):
    test_client, _ = client

    page = _get(test_client, path, RANK_QUERY, **headers).text

    assert "<html" in page
    assert "hx-swap-oob" not in page


@pytest.mark.parametrize(
    "path, target", [("/risk/ex-ante", "risk-ex-ante"), ("/risk/what-if", "risk-ranking")]
)
def test_every_response_varies_on_the_htmx_headers(client, path, target):
    test_client, _ = client

    for sent in (None, target):
        vary = _get(test_client, path, RANK_QUERY, target=sent).headers["vary"]

        assert "HX-Request" in vary and "HX-Target" in vary


def test_a_swap_is_logged(client, caplog):
    test_client, _ = client

    with caplog.at_level(logging.INFO, logger="web.api.risk"):
        _get(test_client, "/risk/ex-ante", target="risk-ex-ante")

    assert "risk: sending #risk-ex-ante only." in caplog.messages
