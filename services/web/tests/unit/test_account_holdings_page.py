"""GET /accounts/holdings: one account's holdings, summary tiles and return chart (task 12,
UI-6), and what is still pending: holdings waiting for prices, instruments waiting for a mapping
(UI-7). The page lived at /portfolio?account=X until PF-2; that URL now redirects here.

Kept apart from test_routes.py, which is over the repo's 64KB file limit. Like test_theme.py,
it carries its own minimal fakes: only the calls this one page makes.
"""

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import web
from fastapi.testclient import TestClient
from web import settings as settings_module
from web.api.app import create_app
from web.api.deps import get_auth_client, get_portfolio_client
from web.auth_client.base import AuthResult, AuthUser
from web.auth_client.errors import SessionInvalid
from web.portfolio_client.base import (
    Account,
    GrowthPoint,
    Holdings,
    PortfolioReturns,
    Position,
    Return,
    UnmappedInstrument,
)
from web.portfolio_client.errors import AccountNotFound, PortfolioUnavailable
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"


class FakeAuthClient:
    """Anonymous until `_logged_in` sets a result."""

    def __init__(self):
        self.validate_result: AuthResult | None = None
        self.calls: list[tuple] = []

    async def validate_session(self, *, cookie_header):
        if self.validate_result is None:
            raise SessionInvalid("no session")
        return self.validate_result

    async def logout(self, *, cookie_header):
        self.calls.append(("logout", cookie_header))


class FakePortfolioClient:
    def __init__(self):
        # The account header 404s a label the user doesn't own, so "main" exists by default.
        self.list_accounts_result: list[Account] = [
            Account(id=1, account_label="main", broker="acme", created_at="2026-01-01T00:00:00Z")
        ]
        self.list_positions_result: list = []
        self.list_positions_error: Exception | None = None
        self.list_returns_result: list = []
        self.list_returns_error: Exception | None = None
        self.list_latest_returns_result: list = []
        self.list_latest_returns_error: Exception | None = None
        self.list_unmapped_instruments_result: list = []
        self.list_unmapped_instruments_error: Exception | None = None
        self.list_growth_result: list = []
        self.list_growth_error: Exception | None = None
        self.list_accounts_error: Exception | None = None
        # {ticker: status} data-store reports for tickers it tracks directly (TK-9).
        self.tracked_statuses: dict[str, str] = {}
        self.list_tracked_tickers_error: Exception | None = None
        self.calls: list[tuple] = []

    async def list_accounts(self, *, user_id):
        if self.list_accounts_error:
            raise self.list_accounts_error
        return self.list_accounts_result

    async def list_unmapped_instruments(self, *, user_id):
        self.calls.append(("list_unmapped_instruments", user_id))
        if self.list_unmapped_instruments_error:
            raise self.list_unmapped_instruments_error
        return self.list_unmapped_instruments_result

    async def list_positions(self, *, user_id, account_label):
        self.calls.append(("list_positions", user_id, account_label))
        if self.list_positions_error:
            raise self.list_positions_error
        return self.list_positions_result

    async def list_returns(self, *, user_id, account_label):
        self.calls.append(("list_returns", user_id, account_label))
        if self.list_returns_error:
            raise self.list_returns_error
        return self.list_returns_result

    async def list_tracked_tickers(self, *, requested_by=None, statuses=None, tickers=None):
        self.calls.append(("list_tracked_tickers", tuple(tickers or ())))
        if self.list_tracked_tickers_error:
            raise self.list_tracked_tickers_error
        return [
            SimpleNamespace(ticker=ticker, status=self.tracked_statuses[ticker])
            for ticker in tickers or ()
            if ticker in self.tracked_statuses
        ]

    async def list_growth(self, *, user_id, account_label):
        self.calls.append(("list_growth", user_id, account_label))
        if self.list_growth_error:
            raise self.list_growth_error
        return self.list_growth_result

    async def list_latest_returns(self, *, user_id, account_label):
        self.calls.append(("list_latest_returns", user_id, account_label))
        if self.list_latest_returns_error:
            raise self.list_latest_returns_error
        return self.list_latest_returns_result

    async def list_holdings(self, *, user_id):
        # Only for the bare /portfolio check below; the combined page has its own tests.
        return Holdings(holdings=(), totals=None)

    async def list_portfolio_returns(self, *, user_id):
        return PortfolioReturns(series=(), by_ticker=())


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
        yield test_client, None, fake_auth, fake_portfolio


def _logged_in_user():
    return AuthUser(user_id="u1", email="ann@example.com", display_name="Ann")


def _logged_in(test_client, fake_auth):
    fake_auth.validate_result = AuthResult(user=_logged_in_user(), set_cookie=SESSION_COOKIE)
    test_client.cookies.set("stakehold_session", "tok")


def _position(*, ticker="AAPL", quantity="10") -> Position:
    return Position(
        account_id=1,
        canonical_ticker=ticker,
        as_of_date=date(2026, 1, 5),
        quantity=Decimal(quantity),
        average_cost_native=Decimal("150"),
        native_currency="EUR",
        average_cost_eur=Decimal("150"),
        cost_basis_eur=Decimal(quantity) * Decimal("150"),
    )


def _return_row(
    *,
    ticker="AAPL",
    price_date=date(2026, 1, 5),
    market_value="1600",
    cost_basis="1500",
    dividends="0",
) -> Return:
    market_value_eur, cost_basis_eur = Decimal(market_value), Decimal(cost_basis)
    unrealized = market_value_eur - cost_basis_eur
    total = unrealized + Decimal(dividends)

    def pct(amount):
        return None if cost_basis_eur == 0 else (amount / cost_basis_eur).quantize(Decimal("1E-8"))

    return Return(
        account_id=1,
        canonical_ticker=ticker,
        price_date=price_date,
        quantity=Decimal("10"),
        market_value_native=market_value_eur,
        market_value_eur=market_value_eur,
        cost_basis_eur=cost_basis_eur,
        unrealized_return_eur=unrealized,
        unrealized_return_pct=pct(unrealized),
        dividends_received_eur=Decimal(dividends),
        total_return_eur=total,
        total_return_pct=pct(total),
    )


def _tile(html, label):
    """The value markup of the summary tile labelled `label`, or None if there's no such tile."""
    match = re.search(
        rf'<p class="tile-label">(?:<a [^>]*>)?{label}(?:</a>)?</p>'
        r'\s*<p class="tile-value">(.*?)</p>',
        html,
        re.DOTALL,
    )
    return None if match is None else match.group(1).strip()


def _holding_cells(html, ticker):
    """The <td> contents of the holdings-table row for `ticker`."""
    tbody = re.search(
        r'<table class="[^"]*\bholdings-table\b[^"]*"[^>]*>.*?<tbody\b[^>]*>(.*?)</tbody>',
        html,
        re.DOTALL,
    )
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", tbody.group(1), re.DOTALL):
        cells = [c.strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)]
        if cells and cells[0] == f'<span class="mono">{ticker}</span>':
            return cells
    raise AssertionError(f"no holdings row for {ticker}")


def _chart_payload(html):
    match = re.search(
        r'<script id="returns-chart-data" type="application/json">(.*?)</script>', html, re.DOTALL
    )
    return json.loads(match.group(1))


def test_holdings_page_requires_login(client):
    test_client, _, _, _ = client

    response = test_client.get(
        "/accounts/holdings", params={"account": "main"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_holdings_page_sets_csrf_cookie_and_supports_logout(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = []
    fake_portfolio.list_returns_result = []

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert response.status_code == 200
    assert "site-nav" in response.text
    assert "logout-form" in response.text
    csrf_token = response.cookies["stakehold_csrf"]
    assert f'value="{csrf_token}"' in response.text

    logout_response = test_client.post(
        "/logout", data={"csrf_token": csrf_token}, follow_redirects=False
    )

    assert logout_response.status_code == 303
    assert logout_response.headers["location"] == "/login"
    logout_call = next(call for call in fake_auth.calls if call[0] == "logout")
    assert logout_call is not None


def test_holdings_page_account_not_found_sets_csrf_cookie(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_error = AccountNotFound("missing")
    fake_portfolio.list_returns_result = []

    response = test_client.get("/accounts/holdings", params={"account": "missing"})

    assert response.status_code == 404
    assert "logout-form" in response.text
    csrf_token = response.cookies["stakehold_csrf"]
    assert f'value="{csrf_token}"' in response.text


def test_holdings_page_fetches_positions_returns_latest_returns_and_growth(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert response.status_code == 200
    user_id = _logged_in_user().user_id
    assert sorted(fake_portfolio.calls) == [
        ("list_growth", user_id, "main"),
        ("list_latest_returns", user_id, "main"),
        ("list_positions", user_id, "main"),
        ("list_returns", user_id, "main"),
        ("list_unmapped_instruments", user_id),
    ]


def test_holdings_page_renders_summary_tiles(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_latest_returns_result = [
        _return_row(ticker="AAPL", market_value="1600", cost_basis="1500", dividends="12.5"),
        _return_row(ticker="MSFT", market_value="900", cost_basis="1000"),
    ]

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert _tile(response.text, "Market value") == '<span class="mono">€2,500.00</span>'
    assert _tile(response.text, "Cost basis") == '<span class="mono">€2,500.00</span>'
    assert _tile(response.text, "Dividends received") == '<span class="mono">€12.50</span>'
    # 100 - 100 + 12.5: a gain, and its percentage recomputed from the summed euros.
    assert _tile(response.text, "Total return") == (
        '<span class="mono gain">+€12.50</span> <span class="mono gain">+0.50%</span>'
    )


def test_holdings_page_colors_a_total_return_loss(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_latest_returns_result = [_return_row(market_value="900", cost_basis="1000")]

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert _tile(response.text, "Total return") == (
        '<span class="mono loss">−€100.00</span> <span class="mono loss">−10.00%</span>'
    )


def test_holdings_page_says_which_date_prices_are_from(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_latest_returns_result = [
        _return_row(ticker="AAPL", price_date=date(2026, 10, 6)),
        _return_row(ticker="MSFT", price_date=date(2026, 10, 2)),
    ]

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert 'Prices as of <span class="mono">2026-10-06</span>' in response.text


def test_holdings_page_renders_holdings_with_value_weight_and_return(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = [
        _position(ticker="AAPL", quantity="10"),
        _position(ticker="MSFT", quantity="10"),
    ]
    fake_portfolio.list_latest_returns_result = [
        _return_row(ticker="AAPL", market_value="1500", cost_basis="1200"),
        _return_row(ticker="MSFT", market_value="500", cost_basis="600"),
    ]

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert response.status_code == 200
    assert _holding_cells(response.text, "AAPL") == [
        '<span class="mono">AAPL</span>',
        '<span class="mono">10</span>',
        '<span class="mono">€150.00</span>',
        '<span class="mono">€1,500.00</span>',
        '<span class="mono">75.00%</span>',
        '<span class="mono gain">+€300.00</span>',
        '<span class="mono gain">+25.00%</span>',
    ]
    assert _holding_cells(response.text, "MSFT")[3:] == [
        '<span class="mono">€500.00</span>',
        '<span class="mono">25.00%</span>',
        '<span class="mono loss">−€100.00</span>',
        '<span class="mono loss">−16.67%</span>',
    ]


def test_holdings_page_leaves_a_zero_return_uncolored(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = [_position()]
    fake_portfolio.list_latest_returns_result = [
        _return_row(market_value="1500", cost_basis="1500")
    ]

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert _holding_cells(response.text, "AAPL")[5:] == [
        '<span class="mono">€0.00</span>',
        '<span class="mono">0.00%</span>',
    ]
    assert "mono gain" not in response.text
    assert "mono loss" not in response.text


def test_holdings_page_shows_a_holding_with_no_return_row_yet(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = [_position(ticker="AAPL"), _position(ticker="NEW")]
    fake_portfolio.list_latest_returns_result = [_return_row(ticker="AAPL")]

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    # One cell spanning market value, weight and both return columns, instead of four dashes.
    assert _holding_cells(response.text, "NEW")[3:] == [
        '<span class="entry-empty">Waiting for prices</span>'
    ]
    assert 'colspan="4"' in response.text
    assert _holding_cells(response.text, "AAPL")[4] == '<span class="mono">100.00%</span>'


def test_holdings_page_omits_tiles_before_any_return_exists(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = [_position()]

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert response.status_code == 200
    assert _tile(response.text, "Market value") is None
    assert "Prices as of" not in response.text
    assert "AAPL" in response.text


def test_holdings_page_chart_payload_carries_the_euro_series(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_returns_result = [
        _return_row(price_date=date(2026, 1, 5), market_value="1600", cost_basis="1500"),
        _return_row(price_date=date(2026, 1, 6), market_value="1450", cost_basis="1500"),
    ]

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert 'id="returns-chart"' in response.text
    assert _chart_payload(response.text) == [
        {
            "date": "2026-01-05",
            "market_value_eur": 1600.0,
            "total_return_eur": 100.0,
            "growth_pct": None,
        },
        {
            "date": "2026-01-06",
            "market_value_eur": 1450.0,
            "total_return_eur": -50.0,
            "growth_pct": None,
        },
    ]


def test_holdings_page_chart_canvas_sits_alone_in_its_sizing_frame(client):
    # LY-3: the frame, not the canvas's width, sets the chart's height, and Chart.js measures the
    # canvas's parent, so the frame must hold nothing else.
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_returns_result = [_return_row()]

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert re.search(
        r'<div class="chart-frame">\s*<canvas id="returns-chart"[^>]*></canvas>\s*</div>',
        response.text,
    )


def test_holdings_page_chart_controls_are_hidden_until_the_script_runs(client):
    # Without JS the buttons would do nothing, so they ship hidden and the chart script shows them.
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_returns_result = [_return_row()]

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert '<div class="chart-controls" hidden>' in response.text
    for label in ("Total return", "Market value", "1M", "6M", "1Y", "All"):
        assert f">{label}</button>" in response.text


def test_holdings_page_filters_closed_positions(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = [
        _position(ticker="AAPL", quantity="10"),
        _position(ticker="MSFT", quantity="0"),
    ]
    fake_portfolio.list_returns_result = []

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert response.status_code == 200
    assert "AAPL" in response.text
    assert "MSFT" not in response.text


def test_holdings_page_renders_empty_state_when_no_positions_or_returns(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = []
    fake_portfolio.list_returns_result = []

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert response.status_code == 200
    assert "No positions yet" in response.text
    assert "No return history yet" in response.text
    assert "Can&#39;t reach the portfolio service" not in response.text


def test_the_empty_returns_state_no_longer_asks_for_a_manual_rebuild(client):
    # Returns now rebuild after every write and on a schedule; a missing chart means missing
    # prices, which nothing the visitor runs would fix.
    test_client, _, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert "rebuild-returns" not in response.text
    assert "returns appear once Stakehold has prices for your holdings' dates" in response.text


def test_holdings_page_account_not_found_renders_404(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_error = AccountNotFound("missing")
    fake_portfolio.list_returns_result = []

    response = test_client.get("/accounts/holdings", params={"account": "missing"})

    assert response.status_code == 404
    assert "doesn't exist" in response.text


def test_holdings_page_degrades_when_positions_unavailable(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_error = PortfolioUnavailable("boom")
    fake_portfolio.list_returns_result = [_return_row()]

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert response.status_code == 200
    assert "Can&#39;t reach the portfolio service" in response.text
    assert 'id="returns-chart"' in response.text


def test_holdings_page_account_not_found_from_latest_returns_renders_404(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_latest_returns_error = AccountNotFound("missing")

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert response.status_code == 404
    assert "doesn't exist" in response.text


def test_holdings_page_degrades_when_latest_returns_unavailable(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = [_position()]
    fake_portfolio.list_returns_result = [_return_row()]
    fake_portfolio.list_latest_returns_error = PortfolioUnavailable("boom")

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert response.status_code == 200
    assert "Can&#39;t reach the portfolio service" in response.text
    assert _tile(response.text, "Market value") is None
    assert "Prices as of" not in response.text
    # Prices may well exist: the call failed, so the row shows dashes, not "Waiting for prices".
    assert _holding_cells(response.text, "AAPL")[3] == '<span class="mono">—</span>'
    assert "Waiting for prices" not in response.text
    assert 'id="returns-chart"' in response.text


def test_holdings_page_degrades_when_returns_unavailable(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = [_position()]
    fake_portfolio.list_returns_error = PortfolioUnavailable("boom")

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert response.status_code == 200
    assert "Can&#39;t reach the portfolio service" in response.text
    assert "AAPL" in response.text
    assert "No return history yet" in response.text


# --- UI-7: instruments waiting for a ticker mapping, and freshness ---------------------------


def _unmapped(identifier, broker="acme", kind="ISIN") -> UnmappedInstrument:
    return UnmappedInstrument(kind=kind, identifier=identifier, broker=broker)


def _mapping_notice(html):
    """The warning banner's text, its bold lead unwrapped."""
    match = re.search(r'<div id="unmapped-notice">.*?<p>(.*?)</p>', html, re.DOTALL)
    if match is None:
        return None
    return match.group(1).replace("<strong>", "").replace("</strong>", "")


def test_mapping_notice_is_a_warning_banner(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_unmapped_instruments_result = [_unmapped("IE00B4L5Y983")]

    html = test_client.get("/accounts/holdings", params={"account": "main"}).text

    assert re.search(r'<div id="unmapped-notice">\s*<div class="banner" data-kind="warning">', html)


def test_holdings_page_lists_instruments_waiting_for_a_mapping_at_this_broker(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = [_position()]
    fake_portfolio.list_unmapped_instruments_result = [
        _unmapped("IE00B4L5Y983"),
        _unmapped("SGLD", kind="SYMBOL"),
        _unmapped("US0000000001", broker="globex"),
    ]

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    notice = _mapping_notice(response.text)
    assert notice.startswith("Waiting for a ticker mapping:")
    assert '<span class="mono">ISIN IE00B4L5Y983</span>' in notice
    assert '<span class="mono">SYMBOL SGLD</span>' in notice
    assert "US0000000001" not in response.text
    assert "They appear as holdings once mapped." in notice


def test_holdings_page_shows_the_mapping_notice_with_no_holdings_yet(client):
    # The case that used to look like a bug: a buy saved, and nothing on the page.
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_unmapped_instruments_result = [_unmapped("IE00B4L5Y983")]

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert "IE00B4L5Y983" in _mapping_notice(response.text)
    assert "No positions yet" in response.text


def test_holdings_page_mapping_notice_tells_a_regular_user_an_admin_maps_them(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_unmapped_instruments_result = [_unmapped("IE00B4L5Y983")]

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    notice = _mapping_notice(response.text)
    assert "An admin needs to map them." in notice
    assert "/admin/mappings" not in response.text


def test_holdings_page_mapping_notice_links_an_admin_to_the_mappings_page(client):
    test_client, _, fake_auth, fake_portfolio = client
    admin = AuthUser(user_id="u1", email="ann@example.com", display_name="Ann", is_admin=True)
    fake_auth.validate_result = AuthResult(user=admin, set_cookie=SESSION_COOKIE)
    test_client.cookies.set("stakehold_session", "tok")
    fake_portfolio.list_unmapped_instruments_result = [_unmapped("IE00B4L5Y983")]

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    notice = _mapping_notice(response.text)
    assert '<a href="/admin/mappings">Map them</a>' in notice
    assert "An admin needs to map them." not in notice


def test_holdings_page_has_no_mapping_notice_when_everything_is_mapped(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_unmapped_instruments_result = [_unmapped("X", broker="globex")]

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert _mapping_notice(response.text) is None


def test_holdings_page_degrades_when_unmapped_instruments_unavailable(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = [_position()]
    fake_portfolio.list_latest_returns_result = [_return_row()]
    fake_portfolio.list_unmapped_instruments_error = PortfolioUnavailable("boom")

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert response.status_code == 200
    assert "Can&#39;t reach the portfolio service" in response.text
    assert _mapping_notice(response.text) is None
    assert _holding_cells(response.text, "AAPL")[3] == '<span class="mono">€1,600.00</span>'


def test_holdings_page_shows_no_mapping_notice_when_the_broker_is_unknown(client):
    # The account list failed, so the header degraded and the broker can't be matched.
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_accounts_error = PortfolioUnavailable("boom")
    fake_portfolio.list_unmapped_instruments_result = [_unmapped("IE00B4L5Y983")]

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert response.status_code == 200
    assert _mapping_notice(response.text) is None


_FRESHNESS = "Prices update daily; returns are recalculated a few times a day."


def test_holdings_page_says_prices_refresh_automatically(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = [_position()]
    fake_portfolio.list_latest_returns_result = [_return_row()]

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert re.search(r'<p class="as-of">Prices as of .*?' + re.escape(_FRESHNESS), response.text)


def test_the_empty_returns_state_says_prices_refresh_automatically(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = [_position()]

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert "No return history yet" in response.text
    assert _FRESHNESS in response.text


# --- a holding whose ticker is still loading (ticker onboarding, TK-9) --------------------------


@pytest.mark.parametrize(
    "status, note",
    [
        ("pending", "Prices loading"),
        ("backfilling", "Prices loading"),
        ("failed", "Prices couldn&#39;t be loaded"),
        ("ready", "Waiting for prices"),
    ],
)
def test_a_holding_without_prices_says_where_its_ticker_stands(client, status, note):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = [_position(ticker="AAPL"), _position(ticker="NEW")]
    fake_portfolio.list_latest_returns_result = [_return_row(ticker="AAPL")]
    fake_portfolio.tracked_statuses = {"NEW": status}

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert _holding_cells(response.text, "NEW")[3:] == [f'<span class="entry-empty">{note}</span>']
    # Only the holdings without prices are asked about.
    assert ("list_tracked_tickers", ("NEW",)) in fake_portfolio.calls


def test_every_holding_priced_asks_nothing_about_loading(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = [_position(ticker="AAPL")]
    fake_portfolio.list_latest_returns_result = [_return_row(ticker="AAPL")]

    test_client.get("/accounts/holdings", params={"account": "main"})

    assert not [c for c in fake_portfolio.calls if c[0] == "list_tracked_tickers"]


def test_an_unanswered_status_call_still_says_waiting_for_prices(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = [_position(ticker="NEW")]
    fake_portfolio.list_tracked_tickers_error = PortfolioUnavailable("down")

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert response.status_code == 200
    assert _holding_cells(response.text, "NEW")[3:] == [
        '<span class="entry-empty">Waiting for prices</span>'
    ]


# --- growth since inception ---


def _growth(*pairs):
    return [GrowthPoint(price_date=d, growth_pct=Decimal(g)) for d, g in pairs]


def _with_returns_and_growth(fake_portfolio, growth):
    fake_portfolio.list_returns_result = [
        _return_row(price_date=date(2026, 1, 5), market_value="1500", cost_basis="1500"),
        _return_row(price_date=date(2026, 1, 6), market_value="1685", cost_basis="1500"),
    ]
    fake_portfolio.list_latest_returns_result = [
        _return_row(price_date=date(2026, 1, 6), market_value="1685", cost_basis="1500")
    ]
    fake_portfolio.list_growth_result = growth


def test_holdings_page_chart_payload_carries_growth_per_date(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_returns_and_growth(
        fake_portfolio, _growth((date(2026, 1, 5), "0E-8"), (date(2026, 1, 6), "0.1234"))
    )

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert [p["growth_pct"] for p in _chart_payload(response.text)] == [0.0, 0.1234]


def test_holdings_page_offers_the_growth_view_when_there_is_growth(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_returns_and_growth(fake_portfolio, _growth((date(2026, 1, 6), "0.1234")))

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert re.search(
        r'<button type="button" class="btn btn-ghost" data-chart-view="growth_pct" '
        r'aria-pressed="false">Growth %</button>',
        response.text,
    )


def test_holdings_page_tiles_show_the_latest_growth_as_a_gain(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_returns_and_growth(
        fake_portfolio, _growth((date(2026, 1, 5), "0"), (date(2026, 1, 6), "0.1234"))
    )

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert (
        _tile(response.text, "Growth since inception") == '<span class="mono gain">+12.34%</span>'
    )


def test_holdings_page_tiles_show_negative_growth_as_a_loss(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_returns_and_growth(fake_portfolio, _growth((date(2026, 1, 6), "-0.0525")))

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert _tile(response.text, "Growth since inception") == '<span class="mono loss">−5.25%</span>'


def test_holdings_page_degrades_when_growth_is_unavailable(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_returns_and_growth(fake_portfolio, [])
    fake_portfolio.list_growth_error = PortfolioUnavailable("boom")

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert response.status_code == 200
    assert "Can&#39;t reach the portfolio service" in response.text
    # Only growth is missing: the other figures and the euro chart still show.
    assert _tile(response.text, "Growth since inception") == '<span class="mono">—</span>'
    assert _tile(response.text, "Market value") == '<span class="mono">€1,685.00</span>'
    assert 'id="returns-chart"' in response.text
    assert 'data-chart-view="growth_pct"' not in response.text
    assert [p["growth_pct"] for p in _chart_payload(response.text)] == [None, None]


def test_holdings_page_account_not_found_from_growth_renders_404(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_growth_error = AccountNotFound("missing")

    response = test_client.get("/accounts/holdings", params={"account": "main"})

    assert response.status_code == 404
    assert "doesn't exist" in response.text


# --- the old URL: GET /portfolio?account=X (PF-2) ---------------------------


def test_old_portfolio_url_redirects_permanently_to_the_holdings_page(client):
    test_client, _, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = test_client.get("/portfolio", params={"account": "main"}, follow_redirects=False)

    assert response.status_code == 308
    assert response.headers["location"] == "/accounts/holdings?account=main"


def test_old_portfolio_url_keeps_an_encoded_label_as_it_was(client):
    test_client, _, _, _ = client

    response = test_client.get("/portfolio?account=my%20isa%20%26%20co", follow_redirects=False)

    assert response.headers["location"] == "/accounts/holdings?account=my%20isa%20%26%20co"


def test_old_portfolio_url_redirects_before_asking_who_is_logged_in(client):
    """An anonymous visitor gets the 308, not the login redirect: the holdings page it points
    at asks for the login, so the redirect itself needs no auth call."""
    test_client, _, _, _ = client

    response = test_client.get("/portfolio", params={"account": "main"}, follow_redirects=False)

    assert response.status_code == 308


def test_old_portfolio_url_lands_on_the_holdings_page(client):
    test_client, _, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = test_client.get("/portfolio", params={"account": "main"})

    assert response.status_code == 200
    assert str(response.url).endswith("/accounts/holdings?account=main")
    assert 'class="account-header"' in response.text


def test_portfolio_without_an_account_is_the_combined_page(client):
    """PF-4: the bare /portfolio is the page across every account (test_portfolio_page.py)."""
    test_client, _, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = test_client.get("/portfolio", follow_redirects=False)

    assert response.status_code == 200
    assert "No holdings yet" in response.text


def test_no_template_links_to_the_old_portfolio_url():
    """Every link to one account's holdings goes straight to /accounts/holdings rather than
    through the redirect. The bare /portfolio is the combined page (PF-4), so only
    /portfolio?account is the old URL."""
    templates = Path(web.__file__).parent / "templates"
    offenders = [
        str(path.relative_to(templates))
        for path in sorted(templates.rglob("*.html"))
        if "/portfolio?" in path.read_text()
    ]

    assert offenders == []


# --- layout: table and chart side by side (LY-5) ------------------------------------------------


def _page(test_client, account="main"):
    return test_client.get("/accounts/holdings", params={"account": account}).text


def _split(html):
    """The two-column container's two cards, in document order, or None without it. Neither
    card nests a <section>, so each one ends at its first </section>. Repeated from
    test_portfolio_page.py (test files can't import each other), for this page's modifier."""
    match = re.search(
        r'<div class="split split-account">\s*(<section class="[^"]*">.*?</section>)\s*'
        r'(<section class="[^"]*">.*?</section>)\s*</div>',
        html,
        re.S,
    )
    return None if match is None else match.groups()


def _with_a_priced_holding(fake_portfolio):
    fake_portfolio.list_positions_result = [_position(ticker="AAPL")]
    fake_portfolio.list_latest_returns_result = [_return_row(ticker="AAPL")]
    fake_portfolio.list_returns_result = [_return_row(ticker="AAPL")]


def test_holdings_and_chart_cards_share_the_split_holdings_first(client):
    # DOM order is reading order: CSS grid places the columns, the markup never reorders them.
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_priced_holding(fake_portfolio)

    holdings, returns = _split(_page(test_client))

    assert "<h1>Holdings</h1>" in holdings
    assert _holding_cells(holdings, "AAPL")
    assert "<h1>Returns over time</h1>" in returns
    assert '<canvas id="returns-chart"' in returns
    # The chart card is the one that stays in view beside a tall table.
    assert re.match(r'<section class="[^"]*\bsplit-aside\b', returns)


def test_holdings_page_is_a_full_width_page(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_priced_holding(fake_portfolio)

    assert re.search(r'<main id="main" class="page page-full"', _page(test_client))


def test_account_header_as_of_line_and_tiles_span_the_page_above_the_split(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_priced_holding(fake_portfolio)

    html = _page(test_client)

    split_at = html.index('<div class="split split-account">')
    assert html.index('class="account-header"') < split_at
    assert html.index('class="as-of"') < split_at
    assert html.index('class="summary-tiles"') < split_at
    assert not any('class="tile"' in card for card in _split(html))


def test_a_positions_failure_keeps_both_cards_in_the_split(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_error = PortfolioUnavailable("boom")
    fake_portfolio.list_returns_result = [_return_row()]

    html = _page(test_client)
    holdings, returns = _split(html)

    assert html.index("Can&#39;t reach the portfolio service") < html.index('class="split ')
    assert "No positions yet" in holdings
    assert '<canvas id="returns-chart"' in returns


def test_a_returns_failure_keeps_both_cards_in_the_split(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_priced_holding(fake_portfolio)
    fake_portfolio.list_returns_error = PortfolioUnavailable("boom")

    holdings, returns = _split(_page(test_client))

    assert _holding_cells(holdings, "AAPL")
    assert "No return history yet" in returns


def test_empty_holdings_and_history_still_render_in_the_split(client):
    test_client, _, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    holdings, returns = _split(_page(test_client))

    assert "No positions yet" in holdings
    assert "No return history yet" in returns


def test_a_holding_waiting_for_prices_sits_in_the_split_holdings_card(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _with_a_priced_holding(fake_portfolio)
    fake_portfolio.list_positions_result.append(_position(ticker="NEW"))
    fake_portfolio.tracked_statuses = {"NEW": "pending"}

    holdings, _ = _split(_page(test_client))

    assert _holding_cells(holdings, "NEW")[3:] == [
        '<span class="entry-empty">Prices loading</span>'
    ]


def test_an_unknown_account_has_no_split(client):
    test_client, _, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = _page(test_client, account="nope")

    assert "doesn't exist" in html
    assert 'class="split' not in html


def _glossary_links(html):
    """(anchor, label) for every figure label that links to its glossary entry."""
    return set(re.findall(r'<a class="term-link" href="/glossary#([\w-]+)">(.*?)</a>', html))


def _a_priced_account(fake_portfolio):
    fake_portfolio.list_positions_result = [_position()]
    fake_portfolio.list_latest_returns_result = [_return_row()]


def test_each_figure_header_and_tile_links_to_its_glossary_entry(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _a_priced_account(fake_portfolio)

    links = _glossary_links(_page(test_client))

    assert {
        ("units", "Quantity"),
        ("avg-cost", "Avg cost"),
        ("market-value", "Market value"),
        ("weight", "Weight"),
        ("unrealized-return", "Unrealized return"),
        ("cost-basis", "Cost basis"),
        ("total-return", "Total return"),
        ("growth", "Growth since inception"),
        ("dividends", "Dividends received"),
    } <= links


def test_every_glossary_link_lands_on_an_entry(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _a_priced_account(fake_portfolio)

    anchors = {anchor for anchor, _ in _glossary_links(_page(test_client))}
    glossary = test_client.get("/glossary").text

    assert anchors
    assert {a for a in anchors if f'<dt id="{a}"' not in glossary} == set()


def test_holdings_card_links_to_the_glossary_for_phone_widths(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _a_priced_account(fake_portfolio)

    holdings, _ = _split(_page(test_client))

    assert '<a href="/glossary">What these figures mean</a>' in holdings
