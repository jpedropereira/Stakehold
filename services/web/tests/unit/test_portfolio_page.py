"""GET /portfolio: the account's holdings, summary tiles and return chart (task 12, UI-6).

Kept apart from test_routes.py, which is over the repo's 64KB file limit. Like test_theme.py,
it carries its own minimal fakes: only the calls this one page makes.
"""

import json
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
from web.portfolio_client.base import Account, Position, Return
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
        self.calls: list[tuple] = []

    async def list_accounts(self, *, user_id):
        return self.list_accounts_result

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

    async def list_latest_returns(self, *, user_id, account_label):
        self.calls.append(("list_latest_returns", user_id, account_label))
        if self.list_latest_returns_error:
            raise self.list_latest_returns_error
        return self.list_latest_returns_result


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
        rf'<p class="tile-label">{label}</p>\s*<p class="tile-value">(.*?)</p>', html, re.DOTALL
    )
    return None if match is None else match.group(1).strip()


def _holding_cells(html, ticker):
    """The <td> contents of the holdings-table row for `ticker`."""
    tbody = re.search(
        r'<table class="positions-table holdings-table">.*?<tbody>(.*?)</tbody>', html, re.DOTALL
    )
    for row in re.findall(r"<tr>(.*?)</tr>", tbody.group(1), re.DOTALL):
        cells = [c.strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)]
        if cells and cells[0] == f'<span class="mono">{ticker}</span>':
            return cells
    raise AssertionError(f"no holdings row for {ticker}")


def _chart_payload(html):
    match = re.search(
        r'<script id="returns-chart-data" type="application/json">(.*?)</script>', html, re.DOTALL
    )
    return json.loads(match.group(1))


def test_portfolio_page_requires_login(client):
    test_client, _, _, _ = client

    response = test_client.get("/portfolio", params={"account": "main"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_portfolio_page_sets_csrf_cookie_and_supports_logout(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = []
    fake_portfolio.list_returns_result = []

    response = test_client.get("/portfolio", params={"account": "main"})

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


def test_portfolio_page_account_not_found_sets_csrf_cookie(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_error = AccountNotFound("missing")
    fake_portfolio.list_returns_result = []

    response = test_client.get("/portfolio", params={"account": "missing"})

    assert response.status_code == 404
    assert "logout-form" in response.text
    csrf_token = response.cookies["stakehold_csrf"]
    assert f'value="{csrf_token}"' in response.text


def test_portfolio_page_fetches_positions_returns_and_latest_returns(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    response = test_client.get("/portfolio", params={"account": "main"})

    assert response.status_code == 200
    user_id = _logged_in_user().user_id
    assert sorted(fake_portfolio.calls) == [
        ("list_latest_returns", user_id, "main"),
        ("list_positions", user_id, "main"),
        ("list_returns", user_id, "main"),
    ]


def test_portfolio_page_renders_summary_tiles(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_latest_returns_result = [
        _return_row(ticker="AAPL", market_value="1600", cost_basis="1500", dividends="12.5"),
        _return_row(ticker="MSFT", market_value="900", cost_basis="1000"),
    ]

    response = test_client.get("/portfolio", params={"account": "main"})

    assert _tile(response.text, "Market value") == '<span class="mono">€2,500.00</span>'
    assert _tile(response.text, "Cost basis") == '<span class="mono">€2,500.00</span>'
    assert _tile(response.text, "Dividends received") == '<span class="mono">€12.50</span>'
    # 100 - 100 + 12.5: a gain, and its percentage recomputed from the summed euros.
    assert _tile(response.text, "Total return") == (
        '<span class="mono gain">+€12.50</span> <span class="mono gain">+0.50%</span>'
    )


def test_portfolio_page_colors_a_total_return_loss(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_latest_returns_result = [_return_row(market_value="900", cost_basis="1000")]

    response = test_client.get("/portfolio", params={"account": "main"})

    assert _tile(response.text, "Total return") == (
        '<span class="mono loss">−€100.00</span> <span class="mono loss">−10.00%</span>'
    )


def test_portfolio_page_says_which_date_prices_are_from(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_latest_returns_result = [
        _return_row(ticker="AAPL", price_date=date(2026, 10, 6)),
        _return_row(ticker="MSFT", price_date=date(2026, 10, 2)),
    ]

    response = test_client.get("/portfolio", params={"account": "main"})

    assert 'Prices as of <span class="mono">2026-10-06</span>' in response.text


def test_portfolio_page_renders_holdings_with_value_weight_and_return(client):
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

    response = test_client.get("/portfolio", params={"account": "main"})

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


def test_portfolio_page_leaves_a_zero_return_uncolored(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = [_position()]
    fake_portfolio.list_latest_returns_result = [
        _return_row(market_value="1500", cost_basis="1500")
    ]

    response = test_client.get("/portfolio", params={"account": "main"})

    assert _holding_cells(response.text, "AAPL")[5:] == [
        '<span class="mono">€0.00</span>',
        '<span class="mono">0.00%</span>',
    ]
    assert "mono gain" not in response.text
    assert "mono loss" not in response.text


def test_portfolio_page_shows_a_holding_with_no_return_row_yet(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = [_position(ticker="AAPL"), _position(ticker="NEW")]
    fake_portfolio.list_latest_returns_result = [_return_row(ticker="AAPL")]

    response = test_client.get("/portfolio", params={"account": "main"})

    assert _holding_cells(response.text, "NEW")[3:] == [
        '<span class="mono">—</span>',
        '<span class="mono">—</span>',
        '<span class="mono">—</span>',
        '<span class="mono">—</span>',
    ]
    assert _holding_cells(response.text, "AAPL")[4] == '<span class="mono">100.00%</span>'


def test_portfolio_page_omits_tiles_before_any_return_exists(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = [_position()]

    response = test_client.get("/portfolio", params={"account": "main"})

    assert response.status_code == 200
    assert _tile(response.text, "Market value") is None
    assert "Prices as of" not in response.text
    assert "AAPL" in response.text


def test_portfolio_page_chart_payload_carries_both_series(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_returns_result = [
        _return_row(price_date=date(2026, 1, 5), market_value="1600", cost_basis="1500"),
        _return_row(price_date=date(2026, 1, 6), market_value="1450", cost_basis="1500"),
    ]

    response = test_client.get("/portfolio", params={"account": "main"})

    assert 'id="returns-chart"' in response.text
    assert _chart_payload(response.text) == [
        {"date": "2026-01-05", "market_value_eur": 1600.0, "total_return_eur": 100.0},
        {"date": "2026-01-06", "market_value_eur": 1450.0, "total_return_eur": -50.0},
    ]


def test_portfolio_page_chart_controls_are_hidden_until_the_script_runs(client):
    # Without JS the buttons would do nothing, so they ship hidden and the chart script shows them.
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_returns_result = [_return_row()]

    response = test_client.get("/portfolio", params={"account": "main"})

    assert '<div class="chart-controls" hidden>' in response.text
    for label in ("Total return", "Market value", "1M", "6M", "1Y", "All"):
        assert f">{label}</button>" in response.text


def test_portfolio_page_filters_closed_positions(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = [
        _position(ticker="AAPL", quantity="10"),
        _position(ticker="MSFT", quantity="0"),
    ]
    fake_portfolio.list_returns_result = []

    response = test_client.get("/portfolio", params={"account": "main"})

    assert response.status_code == 200
    assert "AAPL" in response.text
    assert "MSFT" not in response.text


def test_portfolio_page_renders_empty_state_when_no_positions_or_returns(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = []
    fake_portfolio.list_returns_result = []

    response = test_client.get("/portfolio", params={"account": "main"})

    assert response.status_code == 200
    assert "No positions yet" in response.text
    assert "No return history yet" in response.text
    assert "Can&#39;t reach the portfolio service" not in response.text


def test_the_empty_returns_state_no_longer_asks_for_a_manual_rebuild(client):
    # Returns now rebuild after every write and on a schedule; a missing chart means missing
    # prices, which nothing the visitor runs would fix.
    test_client, _, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = test_client.get("/portfolio", params={"account": "main"})

    assert "rebuild-returns" not in response.text
    assert "returns appear once Stakehold has prices for your holdings' dates" in response.text


def test_portfolio_page_account_not_found_renders_404(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_error = AccountNotFound("missing")
    fake_portfolio.list_returns_result = []

    response = test_client.get("/portfolio", params={"account": "missing"})

    assert response.status_code == 404
    assert "doesn't exist" in response.text


def test_portfolio_page_degrades_when_positions_unavailable(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_error = PortfolioUnavailable("boom")
    fake_portfolio.list_returns_result = [_return_row()]

    response = test_client.get("/portfolio", params={"account": "main"})

    assert response.status_code == 200
    assert "Can&#39;t reach the portfolio service" in response.text
    assert 'id="returns-chart"' in response.text


def test_portfolio_page_account_not_found_from_latest_returns_renders_404(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_latest_returns_error = AccountNotFound("missing")

    response = test_client.get("/portfolio", params={"account": "main"})

    assert response.status_code == 404
    assert "doesn't exist" in response.text


def test_portfolio_page_degrades_when_latest_returns_unavailable(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = [_position()]
    fake_portfolio.list_returns_result = [_return_row()]
    fake_portfolio.list_latest_returns_error = PortfolioUnavailable("boom")

    response = test_client.get("/portfolio", params={"account": "main"})

    assert response.status_code == 200
    assert "Can&#39;t reach the portfolio service" in response.text
    assert _tile(response.text, "Market value") is None
    assert "Prices as of" not in response.text
    assert _holding_cells(response.text, "AAPL")[3] == '<span class="mono">—</span>'
    assert 'id="returns-chart"' in response.text


def test_portfolio_page_degrades_when_returns_unavailable(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = [_position()]
    fake_portfolio.list_returns_error = PortfolioUnavailable("boom")

    response = test_client.get("/portfolio", params={"account": "main"})

    assert response.status_code == 200
    assert "Can&#39;t reach the portfolio service" in response.text
    assert "AAPL" in response.text
    assert "No return history yet" in response.text
