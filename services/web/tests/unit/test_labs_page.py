"""GET /labs (LB-6): the user's current holdings backtested over 1M to Max -- the period table,
the assets portfolio couldn't backtest, the holdings left out of the mix, and the empty and
error states. LB-7: the form that edits the mix, held in the query string. LB-8: the growth
chart for one period, chosen in the query string. LB-10: the assets the mix leaves out for
not covering a period, each row's own Max start, and asset lines that always start hidden.

Like test_portfolio_page.py, it carries its own minimal fakes: only the calls this one page makes.
"""

import json
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
    BacktestGrowth,
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
        self.tickers = ["IWDA.AS", "VWRL.AS", "AAPL"]
        self.tickers_error: Exception | None = None
        self.tickers_calls = 0
        # Holdings and backtest calls, in order. The ticker list is counted apart, since it is
        # fetched alongside the holdings and its place among them isn't fixed.
        self.calls: list[tuple] = []

    async def list_tickers(self):
        self.tickers_calls += 1
        if self.tickers_error:
            raise self.tickers_error
        return self.tickers

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


def _periods(returns, max_start=MAX_START, left_out=None):
    """A row's periods from {period: fraction or None}; a period not given is 0, None is
    "Not enough history". `left_out` is {period: tickers} for the mix's row."""
    left_out = left_out or {}
    return {
        p: BacktestPeriodReturn(
            start=max_start if p == "Max" else date(2025, 10, 9),
            end=END,
            covered=returns.get(p, "0") is not None,
            return_pct=None if returns.get(p, "0") is None else Decimal(returns.get(p, "0")),
            gaps=0,
            left_out=tuple(left_out.get(p, ())),
        )
        for p in BACKTEST_PERIODS
    }


def _row(ticker, weight, *, max_start=MAX_START, left_out=None, **returns):
    return BacktestRow(
        ticker=ticker,
        weight=Decimal(weight),
        periods=_periods(returns, max_start, left_out),
    )


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
        growth=_growth(
            mix=("0", "0.0123456789", "-0.05"),
            assets={"IWDA.AS": ("0", "0.02", "-0.04"), "VWRL.AS": ("0", "-0.01", "-0.08")},
        ),
        statuses=(_status("IWDA.AS"), _status("VWRL.AS")),
    )


def _growth(mix, assets, period="1Y", left_out=()):
    """A growth series over three days; a line given as None doesn't cover the period."""
    line = lambda values: None if values is None else tuple(Decimal(v) for v in values)  # noqa
    return BacktestGrowth(
        period=period,
        dates=(date(2025, 10, 9), date(2025, 10, 10), date(2025, 10, 13)),
        mix=line(mix),
        assets={ticker: line(values) for ticker, values in assets.items()},
        left_out=tuple(left_out),
    )


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", "", html).split())


def _page(test_client, query=""):
    response = test_client.get("/labs" + query)
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


def _with_a_short_history(fake_portfolio, left_out):
    """VWRL.AS's history is too short for some periods; `left_out` is {period: tickers} on the
    mix's row."""
    result = fake_portfolio.backtest_result
    fake_portfolio.backtest_result = Backtest(
        end_date=END,
        mix=_row(None, "1", left_out=left_out),
        assets=result.assets,
        growth=result.growth,
        statuses=result.statuses,
    )


def _table_notes(html):
    """The notes between the table and the growth section."""
    between = html.split("</table>", 1)[1].split('<section class="labs-growth"', 1)[0]
    return [_text(note) for note in re.findall(r'<p class="labs-note">(.*?)</p>', between, re.S)]


def test_the_mix_names_the_assets_it_leaves_out_by_run_of_periods(client):
    """LB-9: the mix's figure covers only the assets whose history covers the period. The note
    says which columns that changes, once per run of periods."""
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)
    _with_a_short_history(
        fake_portfolio,
        {p: ["VWRL.AS"] for p in ("3M", "6M", "1Y", "2Y")} | {"5Y": ["IWDA.AS", "VWRL.AS"]},
    )

    html = _page(test_client)

    assert _table_notes(html) == [
        "From 3M to 2Y the mix leaves out VWRL.AS: not enough history.",
        "Over 5Y the mix leaves out IWDA.AS, VWRL.AS: not enough history.",
    ]
    between = html.split("</table>", 1)[1].split('<section class="labs-growth"', 1)[0]
    assert '<span class="mono">IWDA.AS, VWRL.AS</span>' in between


def test_no_left_out_note_when_the_mix_covers_every_asset(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    assert _table_notes(_page(test_client)) == []


def test_an_asset_whose_max_starts_later_shows_its_own_start(client):
    """LB-9: each asset's Max is its whole history, and the mix's starts on the earliest
    asset's first day, so the header's date is the mix's and a later one is the asset's own."""
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)
    result = fake_portfolio.backtest_result
    fake_portfolio.backtest_result = Backtest(
        end_date=END,
        mix=result.mix,
        assets=(
            _row("IWDA.AS", "0.75"),
            _row("VWRL.AS", "0.25", max_start=date(2024, 1, 2)),
        ),
        growth=result.growth,
        statuses=result.statuses,
    )

    mix, iwda, vwrl = _body_rows(_page(test_client))

    assert '<span class="mono">2024-01-02</span>' in _cell(vwrl, "Max")
    assert re.search(r'data-label="Max"[^>]*data-start="2024-01-02"', vwrl)
    # The same start as the mix's: the header already says it.
    assert "period-start" not in _cell(iwda, "Max")
    assert re.search(r'data-label="Max"[^>]*data-start="2021-03-15"', iwda)
    assert "period-start" not in _cell(mix, "Max")


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

    assert {"backtest", "buy-and-hold", "max-period"} <= anchors


def test_every_glossary_link_lands_on_an_entry(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    anchors = {anchor for anchor, _ in _glossary_links(_page(test_client))}
    glossary = test_client.get("/glossary").text

    assert anchors
    assert {a for a in anchors if f'<dt id="{a}"' not in glossary} == set()


# --- the growth chart (LB-8) ------------------------------------------------------------------


def _growth_section(html):
    match = re.search(r'<section class="labs-growth".*?</section>', html, re.S)
    assert match, "no growth section"
    return match.group(0)


def _period_links(html):
    """(period, href, current) for each period link, in order."""
    nav = re.search(r'<nav class="chart-toggle labs-periods".*?</nav>', html, re.S).group(0)
    return [
        (label, href.replace("&amp;", "&"), bool(current))
        for href, current, label in re.findall(
            r'<a [^>]*href="([^"]*)"( aria-current="true")?[^>]*>(\w+)</a>', nav
        )
    ]


def _island(html):
    match = re.search(
        r'<script id="labs-chart-data" type="application/json">(.*?)</script>', html, re.S
    )
    assert match, "no data island"
    return json.loads(match.group(1))


def test_the_backtest_asks_for_1y_growth_by_default(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    _page(test_client)

    assert fake_portfolio.calls[-1][2] == "1Y"


@pytest.mark.parametrize("period", BACKTEST_PERIODS)
def test_the_backtest_asks_for_the_period_in_the_query_string(client, period):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    _page(test_client, f"?period={period}")

    assert fake_portfolio.calls[-1][2] == period


@pytest.mark.parametrize("period", ["bogus", "max", "1y", "", "12M"])
def test_an_unknown_period_falls_back_to_1y(client, period):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    html = _page(test_client, f"?period={period}")

    assert fake_portfolio.calls[-1][2] == "1Y"
    assert [label for label, _, current in _period_links(html) if current] == ["1Y"]


def test_period_links_run_1m_to_max_and_mark_the_current_one(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    links = _period_links(_page(test_client, "?period=5Y"))

    assert [label for label, _, _ in links] == list(BACKTEST_PERIODS)
    assert [href for _, href, _ in links] == [f"/labs?period={p}" for p in BACKTEST_PERIODS]
    assert [label for label, _, current in links if current] == ["5Y"]


def test_period_links_keep_the_rest_of_the_query_string(client):
    """The mix LB-7 puts in the query string survives a change of period."""
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    links = _period_links(_page(test_client, "?a=1&period=3M&b=x+y&a=2"))

    assert links[0][1] == "/labs?a=1&b=x+y&a=2&period=1M"


def test_the_growth_section_follows_the_table(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    html = _page(test_client)

    assert html.index('<section class="labs-growth"') > html.index("</table>")
    assert "Growth" in re.search(r"<h2[^>]*>(.*?)</h2>", _growth_section(html), re.S).group(1)


def test_the_data_island_carries_every_series_for_the_period(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)
    fake_portfolio.backtest_result = Backtest(
        end_date=END,
        mix=fake_portfolio.backtest_result.mix,
        assets=fake_portfolio.backtest_result.assets,
        growth=_growth(
            mix=("0", "0.02", "-0.04"),
            assets={"IWDA.AS": ("0", "0.02", "-0.04"), "VWRL.AS": None},
            period="5Y",
            left_out=("VWRL.AS",),
        ),
        statuses=fake_portfolio.backtest_result.statuses,
    )

    island = _island(_page(test_client, "?period=5Y"))

    assert island == {
        "period": "5Y",
        "dates": ["2025-10-09", "2025-10-10", "2025-10-13"],
        "mix": [0.0, 0.02, -0.04],
        "assets": [
            {"ticker": "IWDA.AS", "values": [0.0, 0.02, -0.04]},
            {"ticker": "VWRL.AS", "values": None},
        ],
        "left_out": ["VWRL.AS"],
    }


def test_the_canvas_sits_alone_in_its_sizing_frame_and_the_scripts_load_in_order(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    section = _growth_section(_page(test_client))

    assert re.search(
        r'<div class="chart-frame">\s*<canvas id="labs-chart"[^>]*></canvas>\s*</div>', section
    )
    scripts = re.findall(r'<script src="([^"]+)" defer></script>', section)
    assert scripts == [
        "/static/vendor/chart.min.js",
        "/static/chart_theme.js",
        "/static/labs_chart.js",
    ]


def test_asset_lines_start_hidden_beside_the_mixs(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    section = _growth_section(_page(test_client))

    boxes = re.findall(r"<input [^>]*>", section)
    assert len(boxes) == 2
    assert not [box for box in boxes if "checked" in box]


def _with_a_line_leaving_out_vwrl(fake_portfolio):
    """LB-9: VWRL.AS doesn't cover 1Y, so it has no line and the mix's line is IWDA.AS's."""
    result = fake_portfolio.backtest_result
    fake_portfolio.backtest_result = Backtest(
        end_date=END,
        mix=result.mix,
        assets=result.assets,
        growth=_growth(
            mix=("0", "0.1", "0.2"),
            assets={"IWDA.AS": ("0", "0.1", "0.2"), "VWRL.AS": None},
            left_out=("VWRL.AS",),
        ),
        statuses=result.statuses,
    )


def test_asset_toggles_ship_hidden_one_per_asset_in_table_order(client):
    """Without the script they'd do nothing, so it reveals them. An asset with no line for the
    period can't be shown, and says why. Every box starts unchecked even when the mix leaves an
    asset out (LB-10): the mix is always the main line."""
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)
    _with_a_line_leaving_out_vwrl(fake_portfolio)

    section = _growth_section(_page(test_client))

    toggles = re.search(r'<fieldset class="labs-series" hidden>.*?</fieldset>', section, re.S)
    assert toggles, "no hidden toggles"
    boxes = re.findall(r"<input [^>]*>", toggles.group(0))
    assert len(boxes) == 2
    assert 'data-series="IWDA.AS"' in boxes[0] and "disabled" not in boxes[0]
    assert 'data-series="VWRL.AS"' in boxes[1] and "disabled" in boxes[1]
    assert not [box for box in boxes if "checked" in box]
    labels = re.findall(r"<label[^>]*>(.*?)</label>", toggles.group(0), re.S)
    assert "Not enough history" in labels[1] and "Not enough history" not in labels[0]
    assert "The heavier line is the mix." in toggles.group(0)


def test_a_mix_line_that_leaves_assets_out_names_them(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)
    _with_a_line_leaving_out_vwrl(fake_portfolio)

    section = _growth_section(_page(test_client))

    notes = [_text(n) for n in re.findall(r'<p class="chart-note">(.*?)</p>', section, re.S)]
    assert "Over 1Y the mix leaves out VWRL.AS: not enough history." in notes
    assert '<canvas id="labs-chart"' in section


def test_a_mix_line_over_every_asset_has_no_left_out_note(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    assert "leaves out" not in _growth_section(_page(test_client))


def test_no_line_at_all_draws_no_chart_but_keeps_the_period_links(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)
    result = fake_portfolio.backtest_result
    fake_portfolio.backtest_result = Backtest(
        end_date=END,
        mix=result.mix,
        assets=result.assets,
        growth=_growth(mix=None, assets={"IWDA.AS": None, "VWRL.AS": None}, period="Max"),
        statuses=result.statuses,
    )

    html = _page(test_client, "?period=Max")

    section = _growth_section(html)
    assert "No asset's history covers Max" in section
    assert "<canvas" not in section and "labs-chart-data" not in section
    assert len(_period_links(html)) == len(BACKTEST_PERIODS)


@pytest.mark.parametrize(
    "setup",
    [
        lambda fp: setattr(fp, "backtest_error", PortfolioUnavailable("down")),
        lambda fp: setattr(
            fp,
            "backtest_result",
            Backtest(
                end_date=None,
                mix=None,
                assets=(),
                growth=None,
                statuses=(_status("7203.T", "no_fx_rate", "no JPY exchange rate"),),
            ),
        ),
    ],
    ids=["backtest failed", "nothing included"],
)
def test_no_growth_section_without_a_backtested_mix(client, setup):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_holdings(fake_portfolio, _holding("IWDA.AS", "100"))
    setup(fake_portfolio)

    html = _page(test_client)

    assert "labs-growth" not in html and "<canvas" not in html
    assert "labs_chart.js" not in html


def test_no_growth_section_without_holdings(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = _page(test_client)

    assert "labs-growth" not in html and "<canvas" not in html


def test_logs_the_period_and_a_fallback(client, caplog):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    with caplog.at_level(logging.INFO, logger="web.api.labs"):
        _page(test_client, "?period=nope")

    messages = [r.getMessage() for r in caplog.records if r.name == "web.api.labs"]
    assert any("'nope'" in m and "1Y" in m for m in messages), messages
    assert any("2 included" in m and "1Y" in m for m in messages), messages


# --- editing the mix (LB-7) -------------------------------------------------------------------


def _get(test_client, query):
    response = test_client.get(f"/labs?{query}", follow_redirects=False)
    assert response.status_code == 200, response.status_code
    return response.text


def _form(html):
    match = re.search(r'<form[^>]*class="labs-form".*?</form>', html, re.S)
    assert match, "no mix form"
    return match.group(0)


def _form_rows(html):
    """(ticker, weight) of each row the form shows, the empty row included."""
    form = _form(html)
    tickers = re.findall(r'<input[^>]*name="ticker"[^>]*value="([^"]*)"', form)
    weights = re.findall(r'<input[^>]*name="weight"[^>]*value="([^"]*)"', form)
    return list(zip(tickers, weights, strict=True))


def _hidden_last(html):
    form = _form(html)
    tickers = re.findall(r'<input type="hidden" name="last_ticker" value="([^"]*)"', form)
    weights = re.findall(r'<input type="hidden" name="last_weight" value="([^"]*)"', form)
    return list(zip(tickers, weights, strict=True))


def _row_errors(html):
    return re.findall(r'<p class="labs-row-error"[^>]*>(.*?)</p>', _form(html), re.S)


def test_the_form_starts_from_the_default_mix_with_an_empty_row(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    html = _page(test_client)

    form = _form(html)
    assert 'method="get"' in form and 'action="/labs"' in form
    assert _form_rows(html) == [("IWDA.AS", "75"), ("VWRL.AS", "25"), ("", "")]
    assert _hidden_last(html) == [("IWDA.AS", "75"), ("VWRL.AS", "25")]
    assert fake_portfolio.tickers_calls == 1


def test_the_ticker_field_suggests_every_tracked_ticker(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    form = _form(_page(test_client))

    datalist = re.search(r'<datalist id="labs-ticker-options">(.*?)</datalist>', form, re.S)[1]
    assert re.findall(r'<option value="([^"]+)">', datalist) == ["IWDA.AS", "VWRL.AS", "AAPL"]
    assert form.count('list="labs-ticker-options"') == 3


def test_each_asset_row_has_a_remove_button_naming_it(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    form = _form(_page(test_client))

    buttons = re.findall(r'<button[^>]*name="remove"[^>]*>', form)
    assert len(buttons) == 2
    assert 'value="0"' in buttons[0] and 'aria-label="Remove IWDA.AS"' in buttons[0]
    assert 'value="1"' in buttons[1] and 'aria-label="Remove VWRL.AS"' in buttons[1]


def test_an_edited_mix_is_backtested_as_fractions_without_loading_holdings(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    html = _get(test_client, "ticker=AAPL&weight=60&ticker=iwda.as&weight=40&ticker=&weight=")

    assert fake_portfolio.calls == [
        ("backtest", (("AAPL", Decimal("0.6")), ("IWDA.AS", Decimal("0.4"))), "1Y"),
    ]
    assert _form_rows(html) == [("AAPL", "60"), ("IWDA.AS", "40"), ("", "")]
    assert _hidden_last(html) == [("AAPL", "60"), ("IWDA.AS", "40")]
    assert "<table" in html


def test_an_edited_mix_shows_no_notes_about_the_holdings(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    html = _get(test_client, "ticker=AAPL&weight=100")

    assert "labs-note" not in html
    assert "your current holdings" not in html


def test_the_total_is_shown_and_a_total_off_100_says_it_is_scaled(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    html = _get(test_client, "ticker=AAPL&weight=60&ticker=IWDA.AS&weight=60")

    total = re.search(r'<p class="labs-total"[^>]*>(.*?)</p>', _form(html), re.S)[1]
    assert '<span class="mono">120.00%</span>' in total
    assert "scaled to 100%" in total


def test_a_total_of_100_has_no_scaling_note(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    html = _get(test_client, "ticker=AAPL&weight=60&ticker=IWDA.AS&weight=40")

    total = re.search(r'<p class="labs-total"[^>]*>(.*?)</p>', _form(html), re.S)[1]
    assert '<span class="mono">100.00%</span>' in total
    assert "scaled" not in total


def test_row_errors_show_beside_their_row_and_the_table_keeps_the_last_mix(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    html = _get(
        test_client,
        "ticker=NOPE&weight=40&ticker=AAPL&weight=0"
        "&ticker=IWDA.AS&weight=30&ticker=iwda.as&weight=30"
        "&last_ticker=IWDA.AS&last_weight=75&last_ticker=VWRL.AS&last_weight=25",
    )

    assert fake_portfolio.calls == [
        ("backtest", (("IWDA.AS", Decimal("0.75")), ("VWRL.AS", Decimal("0.25"))), "1Y"),
    ]
    assert "<table" in html
    form = _form(html)
    rows = re.findall(r'<div class="labs-row">.*?<!-- /row -->', form, re.S)
    assert "track this ticker." in rows[0]
    assert '<a href="/tickers/new?symbol=NOPE">Add a ticker</a>' in rows[0]
    assert 'aria-invalid="true"' in rows[0]
    assert "Enter a weight above 0%." in rows[1]
    assert "Already in the mix" not in rows[2]
    assert "Already in the mix above." in rows[3]
    # The rows keep what was typed, and the hidden fields keep the mix the table shows.
    assert _form_rows(html)[:4] == [
        ("NOPE", "40"),
        ("AAPL", "0"),
        ("IWDA.AS", "30"),
        ("IWDA.AS", "30"),
    ]
    assert _hidden_last(html) == [("IWDA.AS", "75"), ("VWRL.AS", "25")]
    assert "the table still shows the last mix that worked" in html


def test_errors_with_no_last_mix_show_no_table(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    html = _get(test_client, "ticker=NOPE&weight=100")

    assert fake_portfolio.calls == []
    assert "<table" not in html
    assert "Fix the rows marked below to see figures." in html
    assert _hidden_last(html) == []


def test_more_than_fifty_rows_is_an_error_for_the_whole_form(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.tickers = [f"T{i:02d}" for i in range(51)]

    html = _get(test_client, "&".join(f"ticker=T{i:02d}&weight=1" for i in range(51)))

    assert fake_portfolio.calls == []
    assert "A mix can have at most 50 assets." in _form(html)


def test_remove_redirects_to_the_mix_without_that_row(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    response = test_client.get(
        "/labs?ticker=AAPL&weight=60&ticker=IWDA.AS&weight=40&ticker=&weight="
        "&last_ticker=AAPL&last_weight=100&remove=0",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/labs?ticker=IWDA.AS&weight=40&last_ticker=AAPL&last_weight=100"
    )
    assert fake_portfolio.calls == []


def test_removing_the_last_asset_leaves_an_empty_mix_not_the_default(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    response = test_client.get("/labs?ticker=AAPL&weight=100&remove=0", follow_redirects=False)

    assert response.headers["location"] == "/labs?ticker="
    html = _get(test_client, "ticker=")
    assert fake_portfolio.calls == []
    assert "Add an asset below to backtest it." in html
    assert "<table" not in html
    assert _form_rows(html) == [("", "")]


def test_the_form_resets_to_the_portfolio(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    form = _form(_get(test_client, "ticker=AAPL&weight=100"))

    assert '<a class="btn btn-secondary" href="/labs">Reset to my portfolio</a>' in form


def test_a_ticker_list_failure_shows_a_notice_and_still_runs_the_mix(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)
    fake_portfolio.tickers_error = PortfolioUnavailable("down")

    html = _get(test_client, "ticker=ANY&weight=100")

    assert fake_portfolio.calls == [("backtest", (("ANY", Decimal("1")),), "1Y")]
    assert "The ticker list couldn&#39;t be loaded right now" in _form(html)


def test_a_holdings_failure_still_offers_an_empty_form(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_holdings_error = PortfolioUnavailable("down")

    html = _page(test_client)

    assert _form_rows(html) == [("", "")]
    assert _hidden_last(html) == []


def test_the_page_loads_the_add_row_script(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    assert '<script src="/static/labs_form.js" defer></script>' in _page(test_client)


def test_logs_an_edited_mix_and_its_errors(client, caplog):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    with caplog.at_level(logging.INFO, logger="web.api.labs"):
        _get(
            test_client,
            "ticker=NOPE&weight=50&ticker=AAPL&weight=50&last_ticker=AAPL&last_weight=1",
        )

    messages = [r.getMessage() for r in caplog.records if r.name == "web.api.labs"]
    assert any("edited mix, 2 rows, 1 with errors" in m for m in messages), messages
    assert any(
        "backtesting the last valid mix" in m and "1 assets" in m for m in messages
    ), messages


def test_an_edited_mix_is_backtested_for_the_chosen_period_and_the_form_keeps_it(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_backtest(fake_portfolio)

    html = _get(test_client, "ticker=AAPL&weight=100&period=Max")

    assert fake_portfolio.calls == [("backtest", (("AAPL", Decimal("1")),), "Max")]
    assert '<input type="hidden" name="period" value="Max">' in _form(html)


def test_remove_keeps_the_chosen_period(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = test_client.get(
        "/labs?ticker=AAPL&weight=60&ticker=IWDA.AS&weight=40&period=5Y&remove=1",
        follow_redirects=False,
    )

    assert response.headers["location"] == "/labs?ticker=AAPL&weight=60&period=5Y"
