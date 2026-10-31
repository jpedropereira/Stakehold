"""GET /risk's candidate ranking (RK-29): the GET form (`?rank_weight=`), what it sends portfolio's
POST /risk/what-if/ranking, the ranked table and its order links, the greyed and unranked
candidates, the cap, the 1Y note, and the empty and error states. The window and exclusions are
the ex-ante section's, and every form keeps the others' fields.

Like test_risk_what_if_page.py, it carries its own minimal fakes. The ex-post and ex-ante
answers are "nothing to measure", so only the ranking has content.
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
    BacktestAssetStatus,
    BenchmarkStatus,
    CandidateRanking,
    ExAnteRisk,
    ExPostRisk,
    Holding,
    Holdings,
    LookThrough,
    RankedCandidate,
    RankingWindow,
    RiskFigure,
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

    async def ex_ante_risk(self, *, assets, window, benchmark, exclude, rolling_pair=None):
        statuses = tuple(BacktestAssetStatus(t, "excluded", "excluded") for t, _ in assets)
        return ExAnteRisk(LABEL, "eur", False, None, BENCHMARK, statuses, *(None,) * 7)

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


def _page(test_client, query=""):
    response = test_client.get(f"/risk{query}")
    assert response.status_code == 200
    return response.text


def _section(page: str) -> str:
    match = re.search(
        r'<section class="risk-section" aria-labelledby="risk-ranking-title".*?</section>',
        page,
        re.S,
    )
    assert match, "no ranking section"
    return match.group(0)


def _get(test_client, query=""):
    return _section(_page(test_client, query))


def _text(section: str) -> str:
    words = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", section)).split())
    return re.sub(r"\( ", "(", re.sub(r" ([.,:;)']|'s\b)", r"\1", words))


def _hidden(form: str) -> list[tuple[str, str]]:
    return re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', form)


def _form(page: str, form_id: str) -> str:
    match = re.search(rf'<form[^>]*id="{form_id}".*?</form>', page, re.S)
    assert match, f"no form {form_id}"
    return match.group(0)


def _ranks(fake):
    return [call for call in fake.calls if call[0] == "rank"]


RANK_QUERY = "?rank_weight=5"


# --- the form ------------------------------------------------------------------------------------


def test_an_unsent_form_shows_five_percent_and_calls_nothing(client):
    test_client, fake = client

    section = _get(test_client)

    assert _ranks(fake) == []
    form = _form(section, "risk-ranking-form")
    assert 'method="get" action="/risk#risk-ranking"' in form
    assert re.search(
        r'<input type="text" id="risk-ranking-weight" name="rank_weight" value="5"', form
    )
    assert 'inputmode="decimal"' in form
    assert "<table" not in section


def test_a_sent_form_ranks_the_mix_over_the_ex_ante_window(client):
    test_client, fake = client

    _get(
        test_client, "?rank_weight=7,5&ex_ante_window=5Y&exclude=TSLA&rank_by=diversification_ratio"
    )

    assert _ranks(fake) == [
        ("rank", _MIX, Decimal("0.075"), "5Y", ("TSLA",), "diversification_ratio")
    ]


def test_an_invalid_weight_is_an_inline_error_and_sends_nothing(client):
    test_client, fake = client

    section = _get(test_client, "?rank_weight=abc")

    assert _ranks(fake) == []
    form = _form(section, "risk-ranking-form")
    assert 'aria-invalid="true" aria-describedby="risk-ranking-weight-error"' in form
    assert "Enter a weight above 0% and below 100%." in _text(form)


def test_one_year_says_why_and_sends_nothing(client):
    test_client, fake = client

    section = _get(test_client, "?rank_weight=5&ex_ante_window=1Y")

    assert _ranks(fake) == []
    assert "Ranking needs an ex-ante window of 3Y or more" in _text(section)


# --- the table -----------------------------------------------------------------------------------


def test_the_ranking_table_in_portfolios_order(client):
    test_client, _ = client

    section = _get(test_client, RANK_QUERY)
    text = _text(section)

    rows = re.findall(r'<tr role="row" class="ranking-row">(.*?)</tr>', section, re.S)
    assert len(rows) == 2
    first = _text(rows[0])
    assert first.startswith("1 SGLD.L Invesco Physical Gold")
    assert "−0.7 pts" in first
    assert "+0.09" in first
    assert "1.2%" in first  # share of risk
    assert "−0.23" in first  # correlation with the mix
    assert "± 0.04" in first  # ΔSharpe's s.e.
    assert "2010" in first and "856" in first  # history start and weeks
    assert _text(rows[1]).startswith("2 IEMA.AS")
    assert "historical" in text
    assert "expected" not in text.lower()
    # Every number in mono; no change coloured as a gain or a loss.
    assert '<span class="mono">' in rows[0]
    assert not re.search(r'class="[^"]*\b(gain|loss)\b', section)


def test_the_window_weight_and_why_sharpe_isnt_an_order(client):
    test_client, _ = client

    text = _text(_get(test_client, RANK_QUERY))

    assert "5.0%" in text
    assert "16 Oct 2026" in text or "2026-10-16" in text
    assert "never ordered by" in text


def test_the_order_links_mark_the_current_one(client):
    test_client, _ = client

    section = _get(test_client, f"{RANK_QUERY}&benchmark=IWDA.AS")

    orders = re.findall(r'<a href="/risk\?[^"]*rank_by=(\w+)#risk-ranking"', section)
    assert sorted(orders) == ["diversification_ratio", "sigma_p"]
    current = re.search(r'<a href="([^"]*)" aria-current="true"', section)
    assert current and "rank_by=sigma_p" in html.unescape(current.group(1))
    assert "benchmark=IWDA.AS" in html.unescape(current.group(1))
    assert "rank_weight=5" in html.unescape(current.group(1))


def test_greyed_unranked_and_over_cap_candidates_are_listed(client):
    test_client, _ = client

    text = _text(_get(test_client, RANK_QUERY))

    assert "GAPPY.L" in text and GREY in text
    assert "LATE.DE" in text and "insufficient history (have 80, need 156)" in text
    assert "0700.HK" in text and "no FX series for HKD" in text
    assert "3 more tracked tickers" in text
    assert "ZPRV.DE" in text


def test_no_risk_free_rate_is_said(client):
    test_client, fake = client
    fake.ranking = replace(RANKING, rf_available=False)

    assert "No risk-free rate" in _text(_get(test_client, RANK_QUERY))


# --- empty and error states ----------------------------------------------------------------------


def test_nothing_to_rank_says_why(client):
    test_client, fake = client
    fake.ranking = replace(
        RANKING,
        nothing="no tracked ticker outside the mix to rank",
        window=None,
        ranked=None,
        greyed=None,
        unranked=(),
        over_cap=(),
    )

    section = _get(test_client, RANK_QUERY)

    assert "Every ticker tracked is already one of your holdings." in _text(section)
    assert "<table" not in section


def test_a_failed_ranking_shows_a_banner_and_keeps_the_form(client, caplog):
    test_client, fake = client
    fake.ranking_error = PortfolioUnavailable("down")

    with caplog.at_level(logging.INFO, logger="web.api.risk"):
        section = _get(test_client, RANK_QUERY)

    assert "Couldn't rank the candidates." in _text(section)
    _form(section, "risk-ranking-form")
    assert "risk: portfolio service unavailable loading the ranking." in caplog.text


def test_holdings_unavailable_shows_a_banner(client):
    test_client, fake = client
    fake.holdings_error = PortfolioUnavailable("down")

    section = _get(test_client, RANK_QUERY)

    assert "Couldn't load your holdings to rank against." in _text(section)
    assert _ranks(fake) == []


def test_no_holdings_yet_shows_the_empty_state(client):
    test_client, fake = client
    fake.holdings = Holdings(holdings=(), totals=None)

    section = _get(test_client, RANK_QUERY)

    assert "Nothing to rank against yet" in _text(section)
    assert _ranks(fake) == []


# --- the forms keep each other's fields ----------------------------------------------------------


def test_the_ranking_form_keeps_every_other_control(client):
    test_client, _ = client
    query = (
        "?benchmark=IWDA.AS&ex_ante_window=5Y&exclude=TSLA&corr_a=AAPL&corr_b=portfolio"
        "&candidate=EGLN.L&candidate_weight=10&funding=pro_rata&rank_weight=5"
        "&rank_by=diversification_ratio"
    )

    form = _form(_get(test_client, query), "risk-ranking-form")

    assert _hidden(form) == [
        ("benchmark", "IWDA.AS"),
        ("ex_ante_window", "5Y"),
        ("exclude", "TSLA"),
        ("corr_a", "AAPL"),
        ("corr_b", "portfolio"),
        ("candidate", "EGLN.L"),
        ("candidate_weight", "10"),
        ("funding", "pro_rata"),
        ("rank_by", "diversification_ratio"),
    ]


def test_the_other_forms_keep_the_ranking(client):
    test_client, _ = client

    page = _page(test_client, "?rank_weight=5&rank_by=diversification_ratio")

    # The benchmark form's are in test_risk_page.py, which has ex-post figures to show it.
    kept = _hidden(_form(page, "risk-what-if-form"))
    assert ("rank_weight", "5") in kept
    assert ("rank_by", "diversification_ratio") in kept


# --- logging -------------------------------------------------------------------------------------


def test_logs_the_request_and_a_summary(client, caplog):
    test_client, _ = client

    with caplog.at_level(logging.INFO, logger="web.api.risk"):
        _get(test_client, RANK_QUERY)

    assert (
        "risk: loading the ranking at 5% of the mix, ranked by sigma_p, over 3Y, 3 holdings, "
        "excluding none." in caplog.text
    )
    assert (
        "risk: ranking over 3Y, 2 ranked, 1 greyed, 2 not ranked, 3 past the cap of 50, "
        "first SGLD.L." in caplog.text
    )


def test_logs_an_unsent_form(client, caplog):
    test_client, _ = client

    with caplog.at_level(logging.INFO, logger="web.api.risk"):
        _get(test_client)

    assert "risk: ranking not asked for, nothing sent." in caplog.text
