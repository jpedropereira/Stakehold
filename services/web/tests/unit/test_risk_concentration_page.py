"""GET /risk's concentration section (RK-23): the look-through of today's mix, its unmapped
share shown prominently, the snapshot date, the currency table's two views, the tables' "show
all", and the empty and error states. The holdings are fetched once for both of the sections
that need them, and `?exclude=` stays ex-ante's.

Like test_risk_page.py, it carries its own minimal fakes. The ex-post and ex-ante answers are
"nothing to measure", so only this section has content.
"""

import html
import logging
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
from web.portfolio_client.base import (
    BacktestAssetStatus,
    BenchmarkStatus,
    ExAnteRisk,
    ExPostRisk,
    Holding,
    Holdings,
    LookThrough,
    LookThroughAsset,
    LookThroughBucket,
    LookThroughConcentration,
    LookThroughDimension,
    LookThroughExposures,
    LookThroughName,
    LookThroughNames,
    LookThroughSnapshot,
)
from web.portfolio_client.errors import PortfolioUnavailable
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"
D = Decimal
BENCHMARK = BenchmarkStatus(
    ticker="VWCE.DE", available=False, reason="not fetched", first_date=None
)
NO_KNOWN = "no known exposure: every holding is unmapped or unknown"


def _held(ticker, market_value) -> Holding:
    return Holding(
        canonical_ticker=ticker,
        quantity=D("1"),
        average_cost_eur=None,
        cost_basis_eur=D("1"),
        native_currency="EUR",
        mixed_currency=False,
        average_cost_native=None,
        market_value_native=None,
        priced=True,
        price_date=date(2026, 10, 16),
        market_value_eur=D(market_value),
        unrealized_return_eur=None,
        unrealized_return_pct=None,
        dividends_received_eur=None,
        total_return_eur=None,
        total_return_pct=None,
        accounts=(),
    )


# 70/13/15/2 by market value: build_default_mix sends these weights, largest first.
_HOLDINGS = (
    _held("IWDA.AS", "7000"),
    _held("VWCE.DE", "1300"),
    _held("EGLN.L", "1500"),
    _held("BRK-B", "200"),
)


def _concentration(**changes):
    fields = dict(
        top_shares={1: D("0.678"), 5: D("0.85"), 10: D("0.87")},
        known_share=D("0.87"),
        herfindahl=D("0.5432"),
        effective_n=D("1.84"),
        reason=None,
    )
    return LookThroughConcentration(**{**fields, **changes})


def _dimension(label, *buckets, **concentration):
    return LookThroughDimension(
        label,
        tuple(LookThroughBucket(b, D(w), k) for b, w, k in buckets),
        _concentration(**concentration),
    )


def _asset(ticker, kind, weight, note=None, matched_in=None):
    return LookThroughAsset(ticker, "included", None, kind, D(weight), matched_in, note)


def _look_through(**changes) -> LookThrough:
    countries = [(f"Country {i:02}", "0.03", "known") for i in range(12)]
    exposures = dict(
        snapshots=(LookThroughSnapshot("IWDA.AS", "world", date(2026, 8, 30), 1324, D("100.03")),),
        coverage={
            "look_through": D("0.7"),
            "direct_matched": D("0.02"),
            "direct_unmatched": D("0"),
            "commodity": D("0.15"),
            "unmapped": D("0.13"),
        },
        dimensions={
            "currency": _dimension(
                "listing currency of holdings",
                ("USD", "0.678", "known"),
                ("EUR", "0.15", "known"),
                ("unmapped: VWCE.DE", "0.13", "unmapped"),
            ),
            "trading_currency": _dimension(
                "trading currency of positions",
                ("EUR", "0.83", "known"),
                ("USD", "0.02", "known"),
                ("GBP", "0.15", "known"),
            ),
            "sector": _dimension(
                "sector",
                ("Information Technology", "0.2", "known"),
                ("unmapped: VWCE.DE", "0.13", "unmapped"),
            ),
            "country": _dimension("country", *countries),
            "asset_class": _dimension(
                "asset class",
                ("unmapped: VWCE.DE", "1", "unmapped"),
                top_shares={1: D(0), 5: D(0), 10: D(0)},
                known_share=D(0),
                herfindahl=None,
                effective_n=None,
                reason=NO_KNOWN,
            ),
        },
        names=LookThroughNames(
            top=tuple(
                LookThroughName(
                    f"ISSUER {i:02}",
                    "United States",
                    D("0.04") - D(i) / 1000,
                    ("IWDA.AS", "BRK-B") if i == 0 else ("IWDA.AS",),
                )
                for i in range(25)
            ),
            n_names=1311,
            concentration=_concentration(),
        ),
    )
    lt = LookThrough(
        assets=(
            _asset("IWDA.AS", "look_through", "0.7"),
            _asset("EGLN.L", "commodity", "0.15"),
            _asset("VWCE.DE", "unmapped", "0.13", note="no holdings data for this ETF"),
            _asset("BRK-B", "direct", "0.02", matched_in="IWDA.AS"),
        ),
        exposures=LookThroughExposures(**exposures),
    )
    return LookThrough(**{**lt.__dict__, **changes})


class FakeAuthClient:
    def __init__(self):
        self.validate_result: AuthResult | None = None

    async def validate_session(self, *, cookie_header):
        if self.validate_result is None:
            raise SessionInvalid("no session")
        return self.validate_result


class FakePortfolioClient:
    def __init__(self):
        self.calls: list[tuple] = []
        self.holdings = Holdings(holdings=_HOLDINGS, totals=None)
        self.holdings_error: Exception | None = None
        self.look_through_result: LookThrough = _look_through()
        self.look_through_error: Exception | None = None

    async def list_tickers(self):
        return []

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
        self.calls.append(("list_holdings", user_id))
        if self.holdings_error:
            raise self.holdings_error
        return self.holdings

    async def ex_ante_risk(self, *, assets, window, benchmark, exclude, rolling_pair=None):
        self.calls.append(("ex_ante_risk", tuple(assets), tuple(exclude)))
        statuses = tuple(BacktestAssetStatus(t, "excluded", "excluded") for t, _ in assets)
        return ExAnteRisk(
            "ex-ante · constant weights",
            "eur",
            False,
            None,
            BENCHMARK,
            statuses,
            *(None,) * 7,
        )

    async def look_through(self, *, assets):
        self.calls.append(("look_through", tuple(assets)))
        if self.look_through_error:
            raise self.look_through_error
        return self.look_through_result


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


def _section(test_client, url="/risk"):
    response = test_client.get(url)
    assert response.status_code == 200
    return re.search(
        r'<section class="risk-section" aria-labelledby="risk-concentration-title".*?</section>',
        response.text,
        re.S,
    ).group(0)


def _text(markup):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", markup)))


def _calls(fake, name):
    return [call for call in fake.calls if call[0] == name]


def _table(section, label):
    return re.search(
        rf'<div class="table-scroll"[^>]*aria-label="{label}".*?</table>', section, re.S
    ).group(0)


def test_sends_the_default_mix_and_shows_the_section(client):
    test_client, fake = client

    section = _section(test_client)

    assert _calls(fake, "look_through") == [
        (
            "look_through",
            (
                ("IWDA.AS", D("0.7")),
                ("EGLN.L", D("0.15")),
                ("VWCE.DE", D("0.13")),
                ("BRK-B", D("0.02")),
            ),
        )
    ]
    assert "Concentration · look-through" in section


def test_the_holdings_are_fetched_once_for_both_sections(client):
    test_client, fake = client

    _section(test_client)

    assert len(_calls(fake, "list_holdings")) == 1
    assert len(_calls(fake, "ex_ante_risk")) == 1
    assert len(_calls(fake, "look_through")) == 1


def test_exclude_is_ex_antes_and_leaves_the_look_through_whole(client):
    test_client, fake = client

    _section(test_client, "/risk?exclude=VWCE.DE")

    assert _calls(fake, "ex_ante_risk")[0][2] == ("VWCE.DE",)
    assert len(_calls(fake, "look_through")[0][1]) == 4


def test_the_unmapped_share_is_prominent_and_names_each_fund(client):
    test_client, _ = client

    section = _section(test_client)

    unmapped = re.search(r'<p class="risk-insight lt-unmapped">.*?</p>', section, re.S).group(0)
    text = _text(unmapped)
    assert "13.0%" in text
    assert "VWCE.DE (no holdings data for this ETF)" in text


def test_coverage_lists_each_share(client):
    test_client, _ = client

    text = _text(
        re.search(r'<ul class="lt-coverage".*?</ul>', _section(test_client), re.S).group(0)
    )

    assert "Looked through a fund's holdings 70.0%" in text
    assert "Commodities 15.0%" in text
    assert "sector and country unknown" not in text  # a 0 share isn't listed


def test_the_snapshot_date_is_shown(client):
    test_client, _ = client

    text = _text(_section(test_client))

    assert "IWDA.AS's holdings as of 2026-08-30 (1324 lines)" in text


def test_the_currency_table_shows_both_views(client):
    test_client, _ = client

    table = _table(_section(test_client), "Exposure by currency")

    headers = re.findall(r'<th[^>]*role="columnheader"[^>]*>([^<]*)</th>', table)
    assert headers == ["Currency", "Listing currency of holdings", "Trading currency of positions"]
    gbp = re.search(r"<tr[^>]*>(?:(?!</tr>).)*GBP.*?</tr>", table, re.S).group(0)
    assert re.findall(r'<span class="mono">([^<]*)</span>', gbp) == ["0.0%", "15.0%"]


def test_a_bar_is_an_svg_width_not_a_style(client):
    test_client, _ = client

    table = _table(_section(test_client), "Exposure by currency")

    assert '<rect width="67.8"' in table
    assert "style=" not in table


def test_unmapped_and_unknown_buckets_are_in_the_caption_tone(client):
    test_client, _ = client

    table = _table(_section(test_client), "Exposure by sector")

    assert '<span class="risk-greyed">unmapped: VWCE.DE</span>' in table


def test_known_buckets_past_ten_and_names_past_ten_are_in_show_all(client):
    test_client, _ = client
    section = _section(test_client)

    country = _table(section, "Exposure by country")
    assert "Country 09" in country and "Country 10" not in country
    more = re.findall(r'<details class="lt-more">.*?</details>', section, re.S)
    assert any("Country 10" in d and "Country 11" in d for d in more)
    names = _table(section, "Top look-through names")
    assert "ISSUER 09" in names and "ISSUER 10" not in names
    assert any("ISSUER 24" in d for d in more)


def test_names_show_where_they_come_from_and_the_count(client):
    test_client, _ = client

    text = _text(_section(test_client))

    assert "via IWDA.AS, BRK-B" in text
    assert "1311" in text


def test_concentration_lines_and_a_greyed_one(client):
    test_client, _ = client

    text = _text(_section(test_client))

    assert "effective number 1.8" in text.lower()
    assert NO_KNOWN in text


def test_nothing_included_says_so_and_lists_what_was_left_out(client):
    test_client, fake = client
    fake.look_through_result = LookThrough(
        assets=(LookThroughAsset("IWDA.AS", "not_tracked", "not tracked", None, None, None, None),),
        exposures=None,
    )

    text = _text(_section(test_client))

    assert "Nothing could be looked through" in text
    assert "IWDA.AS — not tracked" in text


def test_no_holdings_means_no_look_through_call(client):
    test_client, fake = client
    fake.holdings = Holdings(holdings=(), totals=None)

    text = _text(_section(test_client))

    assert _calls(fake, "look_through") == []
    assert "Nothing to look through yet" in text


def test_a_look_through_failure_shows_its_notice_and_leaves_the_rest(client):
    test_client, fake = client
    fake.look_through_error = PortfolioUnavailable("down")

    response = test_client.get("/risk")
    section = _section(test_client)

    assert "Couldn't load the concentration figures." in section
    assert 'id="risk-ex-ante-title"' in response.text


def test_a_holdings_failure_shows_both_sections_notices(client):
    test_client, fake = client
    fake.holdings_error = PortfolioUnavailable("down")

    response = test_client.get("/risk")

    assert "Couldn't load the ex-ante figures." in response.text
    assert "Couldn't load the concentration figures." in _section(test_client)
    assert _calls(fake, "look_through") == []


def test_logs_the_look_through_outcome(client, caplog):
    test_client, _ = client

    with caplog.at_level(logging.INFO, logger="web.api.risk"):
        test_client.get("/risk")

    messages = [r.getMessage() for r in caplog.records if r.name == "web.api.risk"]
    assert "risk: loading the look-through for 4 holdings." in messages
    assert (
        "risk: look-through: 70.0% looked through, 2.0% direct (2.0% matched), 15.0% commodity, "
        "13.0% unmapped (VWCE.DE), snapshots IWDA.AS 2026-08-30, 1311 names, left out none."
    ) in messages


def test_logs_a_look_through_with_nothing_included(client, caplog):
    test_client, fake = client
    fake.look_through_result = LookThrough(
        assets=(LookThroughAsset("IWDA.AS", "failed", "couldn't reach data-store", *(None,) * 4),),
        exposures=None,
    )

    with caplog.at_level(logging.INFO, logger="web.api.risk"):
        test_client.get("/risk")

    messages = [r.getMessage() for r in caplog.records if r.name == "web.api.risk"]
    assert (
        "risk: look-through included nothing, left out: " "IWDA.AS (couldn't reach data-store)."
    ) in messages


# --- ticker names (UX-2) ------------------------------------------------------------------------

_NAMED = '<span class="ticker" tabindex="0"><span class="mono">{}</span>'


def test_the_snapshot_and_unmapped_funds_are_named(client):
    test_client, fake = client
    fake.tracked_names = {
        "IWDA.AS": "iShares Core MSCI World",
        "VWCE.DE": "Vanguard FTSE All-World",
    }

    section = _section(test_client)

    snapshots = re.search(r"<p[^>]*>(?:(?!</p>).)*'s holdings as of.*?</p>", section, re.S)
    assert _NAMED.format("IWDA.AS") in snapshots.group(0)
    assert "iShares Core MSCI World" in snapshots.group(0)
    unmapped = re.search(r'<p class="risk-insight lt-unmapped">.*?</p>', section, re.S).group(0)
    assert _NAMED.format("VWCE.DE") in unmapped
    assert "Vanguard FTSE All-World" in unmapped


def test_a_fund_left_out_of_the_look_through_is_named(client):
    test_client, fake = client
    fake.look_through_result = LookThrough(
        assets=(LookThroughAsset("IWDA.AS", "not_tracked", "not tracked", None, None, None, None),),
        exposures=None,
    )
    fake.tracked_names = {"IWDA.AS": "iShares Core MSCI World"}

    section = _section(test_client)

    item = re.search(r"<li>(?:(?!</li>).)*not tracked.*?</li>", section, re.S).group(0)
    assert _NAMED.format("IWDA.AS") in item
