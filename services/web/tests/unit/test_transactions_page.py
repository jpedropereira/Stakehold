"""GET /transactions: an account's transaction log, newest first (UI-8).

Kept apart from test_routes.py, which is at the repo's 64KB file limit. Like
test_portfolio_page.py, it carries its own minimal fakes: only the calls this one page makes.
"""

import re
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from web import settings as settings_module
from web.api.app import create_app
from web.api.deps import get_auth_client, get_portfolio_client
from web.auth_client.base import AuthResult, AuthUser
from web.auth_client.errors import SessionInvalid
from web.portfolio_client.base import Account, Transaction
from web.portfolio_client.errors import AccountNotFound, PortfolioUnavailable
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"
MINUS = "−"


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
        # The account header 404s a label the user doesn't own, so "main" exists by default.
        self.list_accounts_result: list[Account] = [
            Account(id=1, account_label="main", broker="acme", created_at="2026-01-01T00:00:00Z")
        ]
        self.list_accounts_error: Exception | None = None
        self.list_transactions_result: list[Transaction] = []
        self.list_transactions_error: Exception | None = None
        self.calls: list[tuple] = []

    async def list_accounts(self, *, user_id):
        if self.list_accounts_error:
            raise self.list_accounts_error
        return self.list_accounts_result

    async def list_transactions(self, *, user_id, account_label):
        self.calls.append(("list_transactions", user_id, account_label))
        if self.list_transactions_error:
            raise self.list_transactions_error
        return self.list_transactions_result


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


def _buy(*, ref="buy-1", day=5, currency="EUR", **overrides) -> Transaction:
    fields = dict(
        account_id=1,
        broker_transaction_ref=ref,
        transaction_type="BUY",
        trade_datetime=datetime(2026, 1, day, 9, 30, tzinfo=timezone.utc),
        instrument_identifier="VWCE.DE",
        instrument_identifier_kind="TICKER",
        quantity=Decimal("10.000000"),
        price=Decimal("112.3400"),
        native_currency=currency,
        native_amount=Decimal("-1124.4000"),
        eur_amount=Decimal("-1124.4000"),
        fx_rate=None,
        fx_rate_source=None,
        fees_native=Decimal("1.0000"),
    )
    fields.update(overrides)
    return Transaction(**fields)


def _deposit(*, ref="dep-1", day=1) -> Transaction:
    return Transaction(
        account_id=1,
        broker_transaction_ref=ref,
        transaction_type="DEPOSIT",
        trade_datetime=datetime(2026, 1, day, 8, 0, tzinfo=timezone.utc),
        instrument_identifier=None,
        instrument_identifier_kind=None,
        quantity=None,
        price=None,
        native_currency="EUR",
        native_amount=Decimal("2000.0000"),
        eur_amount=Decimal("2000.0000"),
        fx_rate=None,
        fx_rate_source=None,
        fees_native=None,
    )


def _rows(html: str) -> list[str]:
    tbody = re.search(r"<tbody\b[^>]*>(.*?)</tbody>", html, flags=re.S).group(1)
    return re.findall(r"<tr\b[^>]*>.*?</tr>", tbody, flags=re.S)


def _cells(row: str) -> list[str]:
    """Each cell's text, tags stripped and whitespace collapsed."""
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)
    return [" ".join(re.sub(r"<[^>]+>", " ", cell).split()) for cell in cells]


def _get(test_client, account="main"):
    return test_client.get("/transactions", params={"account": account})


# --- access ---------------------------------------------------------------------


def test_transactions_page_requires_login(client):
    test_client, _, _ = client

    response = test_client.get("/transactions", params={"account": "main"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


@pytest.mark.parametrize("params", [{}, {"account": ""}])
def test_transactions_page_requires_an_account(client, params):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = test_client.get("/transactions", params=params)

    assert response.status_code == 422


def test_transactions_page_asks_portfolio_for_this_users_account(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    response = _get(test_client)

    assert response.status_code == 200
    assert fake_portfolio.calls == [("list_transactions", "u1", "main")]


# --- the list -----------------------------------------------------------------


def test_transactions_page_lists_newest_first(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    # portfolio returns the log oldest first.
    fake_portfolio.list_transactions_result = [
        _deposit(ref="dep-1", day=1),
        _buy(ref="buy-1", day=5),
        _buy(ref="buy-2", day=9),
    ]

    rows = _rows(_get(test_client).text)

    assert [_cells(row)[-1] for row in rows] == ["buy-2", "buy-1", "dep-1"]


def test_transactions_page_renders_a_buy(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_transactions_result = [_buy()]

    [row] = _rows(_get(test_client).text)

    assert _cells(row) == [
        "2026-01-05",
        "BUY",
        "VWCE.DE",
        "10",
        "112.34 EUR",
        f"{MINUS}1,124.40 EUR",
        "1.00 EUR",
        "buy-1",
    ]


def test_transactions_page_shows_dashes_for_a_cash_only_row(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_transactions_result = [_deposit()]

    [row] = _rows(_get(test_client).text)

    assert _cells(row) == [
        "2026-01-01",
        "DEPOSIT",
        "—",
        "—",
        "—",
        "2,000.00 EUR",
        "—",
        "dep-1",
    ]


def test_transactions_page_shows_the_eur_amount_under_a_non_eur_amount(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_transactions_result = [
        _buy(
            currency="USD",
            native_amount=Decimal("-1503.5000"),
            eur_amount=Decimal("-1400.1000"),
            fx_rate=Decimal("1.0739"),
        )
    ]

    [row] = _rows(_get(test_client).text)

    cells = _cells(row)
    assert cells[4] == "112.34 USD"
    assert cells[5] == f"{MINUS}1,503.50 USD {MINUS}€1,400.10"
    assert '<span class="mono eur-amount">' in row


def test_transactions_page_does_not_repeat_a_eur_amount_in_eur(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_transactions_result = [_buy()]

    [row] = _rows(_get(test_client).text)

    assert "eur-amount" not in row


def test_transactions_page_keeps_the_full_reference_when_it_is_shortened(client):
    """A long reference is cut off with an ellipsis by CSS, so the full value must stay in the
    page (selectable, read by a screen reader) and in a tooltip."""
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    ref = "3f2b9c1e-8a4d-4f6b-9c2e-7d1a5b0e4c3f"
    fake_portfolio.list_transactions_result = [_buy(ref=ref)]

    [row] = _rows(_get(test_client).text)

    assert f'<span class="mono reference" title="{ref}">{ref}</span>' in row


def test_transactions_page_names_a_non_ticker_instrument_by_its_kind(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_transactions_result = [
        _buy(instrument_identifier="IE00BK5BQT80", instrument_identifier_kind="ISIN")
    ]

    [row] = _rows(_get(test_client).text)

    assert _cells(row)[2] == "ISIN IE00BK5BQT80"


def test_transactions_page_type_is_a_neutral_badge(client):
    """Forest and oxblood mean gains and losses only: a SELL or a WITHDRAWAL isn't a loss."""
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_transactions_result = [
        _buy(transaction_type="SELL", quantity=Decimal("-5"), native_amount=Decimal("600")),
        _deposit(),
    ]

    html = _get(test_client).text

    assert '<span class="badge">SELL</span>' in html
    assert '<span class="badge">DEPOSIT</span>' in html
    tbody = re.search(r"<tbody\b[^>]*>(.*?)</tbody>", html, flags=re.S).group(1)
    assert not re.search(r'class="[^"]*\b(gain|loss)\b', tbody)


def test_transactions_page_marks_the_transactions_subnav_link_current(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = _get(test_client).text

    assert '<a href="/transactions?account=main" aria-current="page">Transactions</a>' in html
    assert html.count('aria-current="page"') == 2  # the top nav's Accounts, and this link


# --- empty and failure states ---------------------------------------------------


def test_transactions_page_empty_state_links_to_add_and_import(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = _get(test_client).text

    assert "<table" not in html
    assert 'id="transactions-empty"' in html
    empty_state = html.split('id="transactions-empty"', 1)[1].split("</p>", 1)[0]
    assert 'href="/transactions/new?account=main"' in empty_state
    assert 'href="/transactions/import?account=main"' in empty_state


def test_transactions_page_url_encodes_the_account_in_empty_state_links(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_accounts_result = [
        Account(id=2, account_label="my isa", broker="acme", created_at="2026-01-01T00:00:00Z")
    ]

    html = _get(test_client, account="my isa").text

    assert 'href="/transactions/new?account=my%20isa"' in html


def test_transactions_page_unknown_account_renders_404(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = _get(test_client, account="someone-elses")

    assert response.status_code == 404
    assert "Account not found" in response.text


def test_transactions_page_account_not_found_from_portfolio_renders_404(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_transactions_error = AccountNotFound("missing")

    response = _get(test_client)

    assert response.status_code == 404
    assert "Account not found" in response.text


def test_transactions_page_degrades_when_transactions_unavailable(client):
    """An outage must not read as an account with no transactions."""
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_transactions_error = PortfolioUnavailable("down")

    response = _get(test_client)

    assert response.status_code == 200
    assert "Can&#39;t reach the portfolio service right now" in response.text
    assert 'id="transactions-empty"' not in response.text
    assert "<table" not in response.text


def test_transactions_page_degrades_to_a_label_only_header_when_accounts_unavailable(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_accounts_error = PortfolioUnavailable("down")
    fake_portfolio.list_transactions_result = [_deposit()]

    response = _get(test_client)

    assert response.status_code == 200
    assert "<h1>main</h1>" in response.text
    assert 'class="account-broker"' not in response.text
    assert len(_rows(response.text)) == 1
