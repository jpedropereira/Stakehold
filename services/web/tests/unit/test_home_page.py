"""GET /: the overview across every account (UI-11) -- headline totals and one row per account,
each degrading on its own when its figures can't be loaded.

Kept apart from test_routes.py, which is over the repo's 64KB file limit. Like
test_portfolio_page.py, it carries its own minimal fakes: only the calls this one page makes.
"""

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
from web.portfolio_client.base import Account, Return
from web.portfolio_client.errors import AccountNotFound, PortfolioUnavailable
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"


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
        self.list_accounts_result: list[Account] = []
        self.list_accounts_error: Exception | None = None
        # Per account label: a list of rows, or an exception to raise.
        self.latest_returns: dict[str, list[Return] | Exception] = {}
        self.calls: list[tuple] = []

    async def list_accounts(self, *, user_id):
        self.calls.append(("list_accounts", user_id))
        if self.list_accounts_error:
            raise self.list_accounts_error
        return self.list_accounts_result

    async def list_latest_returns(self, *, user_id, account_label):
        self.calls.append(("list_latest_returns", user_id, account_label))
        result = self.latest_returns.get(account_label, [])
        if isinstance(result, Exception):
            raise result
        return result


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


def _account(label, account_id=1, broker="acme") -> Account:
    return Account(
        id=account_id, account_label=label, broker=broker, created_at="2026-01-01T00:00:00Z"
    )


def _latest(*, ticker="AAPL", market_value, cost_basis, dividends="0", price_date=None) -> Return:
    market_value_eur, cost_basis_eur = Decimal(market_value), Decimal(cost_basis)
    unrealized = market_value_eur - cost_basis_eur
    total = unrealized + Decimal(dividends)
    return Return(
        account_id=1,
        canonical_ticker=ticker,
        price_date=price_date or date(2026, 10, 6),
        quantity=Decimal("10"),
        market_value_native=market_value_eur,
        market_value_eur=market_value_eur,
        cost_basis_eur=cost_basis_eur,
        unrealized_return_eur=unrealized,
        unrealized_return_pct=None,
        dividends_received_eur=Decimal(dividends),
        total_return_eur=total,
        total_return_pct=None,
    )


def _tile(html, label):
    """The value markup of the summary tile labelled `label`, or None if there's no such tile."""
    match = re.search(
        rf'<p class="tile-label">{label}</p>\s*<p class="tile-value">(.*?)</p>', html, re.DOTALL
    )
    return None if match is None else match.group(1).strip()


def _account_row(html, label):
    """The markup of the overview row for the account labelled `label`, or None."""
    for row in re.findall(r'<li class="account-row">(.*?)</li>', html, re.DOTALL):
        if f">{label}<" in row:
            return row
    return None


def _two_accounts(fake_portfolio):
    fake_portfolio.list_accounts_result = [
        _account("my isa", 1, broker="acme"),
        _account("pension", 2, broker="globex"),
    ]
    fake_portfolio.latest_returns = {
        "my isa": [
            _latest(ticker="AAPL", market_value="1500", cost_basis="1000"),
            _latest(ticker="MSFT", market_value="500", cost_basis="500", dividends="20"),
        ],
        "pension": [_latest(ticker="VWCE", market_value="800", cost_basis="1000")],
    }


def test_root_redirects_to_login_when_anonymous(client):
    test_client, _, _ = client

    response = test_client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_overview_totals_every_account(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _two_accounts(fake_portfolio)

    html = test_client.get("/").text

    assert "€2,800.00" in _tile(html, "Market value")
    assert "€2,500.00" in _tile(html, "Cost basis")
    total_return = _tile(html, "Total return")
    assert "+€320.00" in total_return and "+12.80%" in total_return
    assert 'class="mono gain"' in total_return
    assert "€20.00" in _tile(html, "Dividends received")
    assert "Prices as of" in html and "2026-10-06" in html


def test_overview_fetches_latest_returns_once_per_account(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _two_accounts(fake_portfolio)

    test_client.get("/")

    latest_calls = sorted(c for c in fake_portfolio.calls if c[0] == "list_latest_returns")
    assert latest_calls == [
        ("list_latest_returns", "u1", "my isa"),
        ("list_latest_returns", "u1", "pension"),
    ]


def test_overview_row_shows_the_accounts_figures_and_links_to_its_holdings(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _two_accounts(fake_portfolio)

    html = test_client.get("/").text

    isa = _account_row(html, "my isa")
    assert 'href="/portfolio?account=my%20isa"' in isa
    assert "acme" in isa
    assert "€2,000.00" in isa
    assert "+€520.00" in isa and "+34.67%" in isa
    pension = _account_row(html, "pension")
    assert "−€200.00" in pension and "−20.00%" in pension
    assert 'class="mono loss"' in pension


def test_overview_one_failing_account_degrades_only_its_row(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _two_accounts(fake_portfolio)
    fake_portfolio.latest_returns["pension"] = PortfolioUnavailable("down")

    response = test_client.get("/")

    assert response.status_code == 200
    html = response.text
    assert "Couldn't load figures" in _account_row(html, "pension")
    assert "€2,000.00" in _tile(html, "Market value")
    assert "Totals leave out 1 account that couldn't be loaded." in html


def test_overview_an_account_gone_since_listing_degrades_like_a_failure(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _two_accounts(fake_portfolio)
    fake_portfolio.latest_returns["pension"] = AccountNotFound("gone")

    response = test_client.get("/")

    assert response.status_code == 200
    assert "Couldn't load figures" in _account_row(response.text, "pension")


def test_overview_an_account_with_no_figures_yet_is_waiting_not_failed(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _two_accounts(fake_portfolio)
    fake_portfolio.latest_returns["pension"] = []

    html = test_client.get("/").text

    pension = _account_row(html, "pension")
    assert "Waiting for prices" in pension
    assert "Couldn't load" not in pension
    assert "Totals leave out" not in html
    assert "€2,000.00" in _tile(html, "Market value")


def test_overview_shows_no_tiles_when_no_account_has_figures(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_accounts_result = [_account("new")]

    html = test_client.get("/").text

    assert _tile(html, "Market value") is None
    assert "Waiting for prices" in _account_row(html, "new")


def test_overview_first_run_points_to_creating_an_account(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    html = test_client.get("/").text

    assert "No accounts yet" in html
    assert 'href="/accounts"' in html and "Create an account" in html
    assert _tile(html, "Market value") is None


def test_overview_accounts_outage_is_not_a_first_run(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_accounts_error = PortfolioUnavailable("down")

    response = test_client.get("/")

    assert response.status_code == 200
    assert "reach the portfolio service right now, so your accounts" in response.text
    assert "No accounts yet" not in response.text
    assert not [c for c in fake_portfolio.calls if c[0] == "list_latest_returns"]


def test_overview_is_the_current_nav_entry(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = test_client.get("/").text

    assert '<a href="/" aria-current="page"' in html
