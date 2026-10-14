"""GET /labs (LB-6): the user's current holdings backtested over 1M to Max -- the period table,
the assets portfolio couldn't backtest, the holdings left out of the mix, and the empty and
error states.

Like test_portfolio_page.py, it carries its own minimal fakes: only the calls this one page makes.
"""

import logging
import re
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
    BACKTEST_PERIODS,
    Backtest,
    BacktestAssetStatus,
    BacktestPeriodReturn,
    BacktestRow,
    Holding,
    Holdings,
)
from web.portfolio_client.errors import InvalidPortfolioRequest, PortfolioUnavailable
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"
END = date(2026, 10, 9)
MAX_START = date(2021, 3, 15)


class FakeAuthClient:
    """Anonymous until `_logged_in` sets a result."""

    def __init__(self):
        self.validate_result: AuthResult | None = None

    async def validate_session(self, *, cookie_header):
        if self.validate_result is None:
            raise SessionInvalid("no session")
        return self.validate_result


class FakePortfolioClient:
    def __init__(self):
        self.holdings = Holdings(holdings=(), totals=None)
        self.list_holdings_error: Exception | None = None
        self.backtest_result: Backtest | None = None
        self.backtest_error: Exception | None = None
        self.calls: list[tuple] = []

    async def list_holdings(self, *, user_id):
        self.calls.append(("list_holdings", user_id))
        if self.list_holdings_error:
            raise self.list_holdings_error
        return self.holdings

    async def backtest(self, *, assets, period):
        self.calls.append(("backtest", tuple(assets), period))
        if self.backtest_error:
            raise self.backtest_error
        return self.backtest_result


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


def _holding(ticker, value):
    """A holding as portfolio's GET /holdings serves it; unpriced when `value` is None."""
    priced = value is not None
    return Holding(
        canonical_ticker=ticker,
        quantity=Decimal("1"),
        average_cost_eur=Decimal("1"),
        cost_basis_eur=Decimal("1"),
        native_currency="EUR",
        mixed_currency=False,
        average_cost_native=Decimal("1"),
        market_value_native=Decimal(value) if priced else None,
        priced=priced,
        price_date=END if priced else None,
        market_value_eur=Decimal(value) if priced else None,
        unrealized_return_eur=None,
        unrealized_return_pct=None,
        dividends_received_eur=None,
        total_return_eur=None,
        total_return_pct=None,
        accounts=(),
    )


def _periods(returns):
    """A row's periods from {period: fraction or None}; a period not given is 0, None is
    "Not enough history"."""
    return {
        p: BacktestPeriodReturn(
            start=MAX_START if p == "Max" else date(2025, 10, 9),
            end=END,
            covered=returns.get(p, "0") is not None,
            return_pct=None if returns.get(p, "0") is None else Decimal(returns.get(p, "0")),
            gaps=0,
        )
        for p in BACKTEST_PERIODS
    }


def _row(ticker, weight, **returns):
    return BacktestRow(ticker=ticker, weight=Decimal(weight), periods=_periods(returns))


def _status(ticker, status="included", reason=None):
    return BacktestAssetStatus(ticker=ticker, status=status, reason=reason)


def _with_holdings(fake_portfolio, *holdings):
    fake_portfolio.holdings = Holdings(holdings=tuple(holdings), totals=None)


def _with_a_backtest(fake_portfolio):
    """Two priced holdings, both backtested."""
    _with_holdings(fake_portfolio, _holding("IWDA.AS", "750"), _holding("VWRL.AS", "250"))
    fake_portfolio.backtest_result = Backtest(
        end_date=END,
        mix=_row(None, "1", **{"1M": "0.0184", "1Y": "-0.05"}),
        assets=(
            _row("IWDA.AS", "0.75", **{"1M": "0.02"}),
            _row("VWRL.AS", "0.25", **{"1M": "-0.01"}),
        ),
        growth=None,
        statuses=(_status("IWDA.AS"), _status("VWRL.AS")),
    )


def _page(test_client):
    response = test_client.get("/labs")
    assert response.status_code == 200
    return response.text


def _table(html):
    match = re.search(r"<table .*?</table>", html, re.S)
    assert match, "no table"
    return match.group(0)


def _body_rows(html):
    return re.findall(r'<tr role="row"[^>]*>.*?</tr>', _table(html).split("<tbody", 1)[1], re.S)


def _cell(row_html, label):
    match = re.search(rf'<td[^>]*data-label="{re.escape(label)}"[^>]*>(.*?)</td>', row_html, re.S)
    return None if match is None else match.group(1)


def _first_cell(row_html):
    return re.sub(r"<[^>]+>", "", re.search(r"<t[hd][^>]*>(.*?)</t[hd]>", row_html, re.S)[1])


# --- access and nav ---------------------------------------------------------------------------


def test_anonymous_visitor_is_sent_to_login(client):
    test_client, _, _ = client

    response = test_client.get("/labs", follow_redirects=False)

    assert response.status_code in (302, 303)
    assert response.headers["location"].startswith("/login")


def test_nav_has_a_labs_link_marked_current(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    nav = re.search(r'<nav class="site-nav".*?</nav>', _page(test_client), re.S).group(0)

    assert re.findall(r">(\w+)</a>", nav)[:4] == ["Overview", "Portfolio", "Labs", "Accounts"]
    assert '<a href="/labs" aria-current="page">Labs</a>' in nav


def test_other_pages_link_to_labs_without_marking_it_current(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = test_client.get("/glossary").text

    assert '<a href="/labs">Labs</a>' in html


# --- the mix sent to portfolio ----------------------------------------------------------------


def test_backtests_the_priced_holdings_weighted_by_market_value(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)
    _with_holdings(
        fake_portfolio,
        _holding("VWRL.AS", "250"),
        _holding("NEW", None),
        _holding("IWDA.AS", "750"),
    )

    _page(test_client)

    assert fake_portfolio.calls == [
        ("list_holdings", "u1"),
        ("backtest", (("IWDA.AS", Decimal("0.75")), ("VWRL.AS", Decimal("0.25"))), "1Y"),
    ]


def test_unpriced_holdings_are_named_in_a_note(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)
    _with_holdings(
        fake_portfolio, _holding("IWDA.AS", "750"), _holding("NEW.L", None), _holding("ZZ", None)
    )

    note = re.search(r'<p class="labs-note"[^>]*>(.*?)</p>', _page(test_client), re.S).group(1)

    assert "NEW.L" in note and "ZZ" in note
    assert "no price yet" in note


def test_no_note_when_every_holding_is_in_the_mix(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    assert "labs-note" not in _page(test_client)


def test_more_holdings_than_the_backtest_takes_sends_the_largest_and_says_so(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)
    _with_holdings(fake_portfolio, *(_holding(f"T{i:02d}", str(100 + i)) for i in range(53)))

    html = _page(test_client)

    sent = fake_portfolio.calls[1][1]
    assert len(sent) == 50
    assert {ticker for ticker, _ in sent}.isdisjoint({"T00", "T01", "T02"})
    note = re.search(r'<p class="labs-note"[^>]*>(.*?)</p>', html, re.S).group(1)
    assert "largest 50" in note
    assert "T00" in note and "T01" in note and "T02" in note


# --- the period table -------------------------------------------------------------------------


def test_mix_row_comes_first_then_each_asset_with_its_weight(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    rows = _body_rows(_page(test_client))

    assert [_first_cell(r).strip() for r in rows] == ["Mix", "IWDA.AS", "VWRL.AS"]
    assert '<span class="mono">100.00%</span>' in _cell(rows[0], "Weight")
    assert '<span class="mono">75.00%</span>' in _cell(rows[1], "Weight")
    assert '<span class="mono">25.00%</span>' in _cell(rows[2], "Weight")


def test_period_columns_run_from_1m_to_max(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    head = _table(_page(test_client)).split("<tbody", 1)[0]
    labels = [
        re.sub(r"<[^>]+>", " ", h).split()[0]
        for h in re.findall(r"<th[^>]*>(.*?)</th>", head, re.S)
    ]

    assert labels[2:] == list(BACKTEST_PERIODS)


def test_max_column_shows_its_start_date_and_the_table_its_end_date(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    html = _page(test_client)

    max_header = re.findall(r"<th[^>]*>(.*?)</th>", _table(html), re.S)[-1]
    assert '<span class="mono">2021-03-15</span>' in max_header
    assert '<span class="mono">2026-10-09</span>' in html.split("<table", 1)[0]


def test_returns_are_signed_percentages_coloured_by_gain_or_loss(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    mix, _, vwrl = _body_rows(_page(test_client))

    assert '<span class="mono gain">+1.84%</span>' in _cell(mix, "1M")
    assert '<span class="mono loss">−5.00%</span>' in _cell(mix, "1Y")
    assert '<span class="mono">0.00%</span>' in _cell(mix, "3M")
    assert '<span class="mono loss">−1.00%</span>' in _cell(vwrl, "1M")


def test_a_period_longer_than_the_history_says_so_for_the_asset_and_the_mix(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)
    result = fake_portfolio.backtest_result
    fake_portfolio.backtest_result = Backtest(
        end_date=END,
        mix=_row(None, "1", **{"5Y": None}),
        assets=(_row("IWDA.AS", "0.75"), _row("VWRL.AS", "0.25", **{"5Y": None})),
        growth=None,
        statuses=result.statuses,
    )

    mix, iwda, vwrl = _body_rows(_page(test_client))

    assert "Not enough history" in _cell(mix, "5Y")
    assert "Not enough history" in _cell(vwrl, "5Y")
    assert "Not enough history" not in _cell(iwda, "5Y")
    assert "mono" not in _cell(vwrl, "5Y")


def test_assets_portfolio_couldnt_backtest_are_listed_with_their_reason(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)
    _with_holdings(
        fake_portfolio,
        _holding("IWDA.AS", "600"),
        _holding("VWRL.AS", "200"),
        _holding("7203.T", "200"),
    )
    result = fake_portfolio.backtest_result
    fake_portfolio.backtest_result = Backtest(
        end_date=result.end_date,
        mix=result.mix,
        assets=result.assets,
        growth=None,
        statuses=result.statuses + (_status("7203.T", "no_fx_rate", "no JPY exchange rate"),),
    )

    html = _page(test_client)

    left_out = re.search(r'<ul class="labs-unavailable"[^>]*>(.*?)</ul>', html, re.S).group(1)
    assert "7203.T" in left_out and "no JPY exchange rate" in left_out
    assert "IWDA.AS" not in left_out
    assert html.index("labs-unavailable") > html.index("</table>")
    assert "7203.T" not in _table(html)


def test_no_unavailable_list_when_every_asset_is_included(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    assert "labs-unavailable" not in _page(test_client)


def test_no_included_asset_shows_no_table_only_the_reasons(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_holdings(fake_portfolio, _holding("7203.T", "100"))
    fake_portfolio.backtest_result = Backtest(
        end_date=None,
        mix=None,
        assets=(),
        growth=None,
        statuses=(_status("7203.T", "no_fx_rate", "no JPY exchange rate"),),
    )

    html = _page(test_client)

    assert "<table" not in html
    assert "None of your holdings could be backtested" in html
    assert "no JPY exchange rate" in html


# --- empty and error states -------------------------------------------------------------------


def test_no_holdings_explains_there_is_nothing_to_backtest(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    html = _page(test_client)

    assert "Nothing to backtest yet" in html
    assert '<a href="/accounts">' in html
    assert "<table" not in html
    assert [c[0] for c in fake_portfolio.calls] == ["list_holdings"]


def test_only_unpriced_holdings_explains_and_names_them(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_holdings(fake_portfolio, _holding("NEW.L", None))

    html = _page(test_client)

    assert "Nothing to backtest yet" in html
    assert "NEW.L" in re.search(r'<p class="labs-note"[^>]*>(.*?)</p>', html, re.S).group(1)
    assert [c[0] for c in fake_portfolio.calls] == ["list_holdings"]


def test_a_holdings_failure_shows_a_notice_and_runs_no_backtest(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_holdings_error = PortfolioUnavailable("down")

    html = _page(test_client)

    assert "Couldn't load your holdings." in html
    assert "Nothing to backtest yet" not in html
    assert [c[0] for c in fake_portfolio.calls] == ["list_holdings"]


def test_a_backtest_failure_shows_a_notice_and_keeps_the_notes(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_holdings(fake_portfolio, _holding("IWDA.AS", "100"), _holding("NEW.L", None))
    fake_portfolio.backtest_error = PortfolioUnavailable("down")

    html = _page(test_client)

    assert "Couldn't run the backtest." in html
    assert "reach the portfolio service right now" in html
    assert "NEW.L" in re.search(r'<p class="labs-note"[^>]*>(.*?)</p>', html, re.S).group(1)
    assert "<table" not in html


def test_a_rejected_mix_shows_portfolios_reason(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_holdings(fake_portfolio, _holding("IWDA.AS", "100"))
    fake_portfolio.backtest_error = InvalidPortfolioRequest("ticker is too long")

    html = _page(test_client)

    assert "Couldn't run the backtest." in html
    assert "ticker is too long" in html


def test_logs_what_was_backtested(client, caplog):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)
    _with_holdings(
        fake_portfolio, _holding("IWDA.AS", "750"), _holding("VWRL.AS", "250"), _holding("N", None)
    )

    with caplog.at_level(logging.INFO, logger="web.api.labs"):
        _page(test_client)

    messages = [r.getMessage() for r in caplog.records if r.name == "web.api.labs"]
    assert any(
        "3 holdings" in m and "2 in the mix" in m and "1 unpriced" in m for m in messages
    ), messages
    assert any("2 included" in m and "0 left out" in m for m in messages), messages


def test_logs_that_there_was_nothing_to_backtest(client, caplog):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    with caplog.at_level(logging.INFO, logger="web.api.labs"):
        _page(test_client)

    messages = [r.getMessage() for r in caplog.records if r.name == "web.api.labs"]
    assert any("nothing to backtest" in m for m in messages), messages


# --- glossary links ---------------------------------------------------------------------------


def _glossary_links(html):
    """(anchor, label) for every label that links to its glossary entry."""
    return set(re.findall(r'<a class="term-link" href="/glossary#([\w-]+)">(.*?)</a>', html))


def test_the_page_links_its_terms_to_the_glossary(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    anchors = {anchor for anchor, _ in _glossary_links(_page(test_client))}

    assert {"backtest", "buy-and-hold", "longest-shared-history"} <= anchors


def test_every_glossary_link_lands_on_an_entry(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    anchors = {anchor for anchor, _ in _glossary_links(_page(test_client))}
    glossary = test_client.get("/glossary").text

    assert anchors
    assert {a for a in anchors if f'<dt id="{a}"' not in glossary} == set()
