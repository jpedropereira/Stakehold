"""GET /transactions/new beside the account's recent transactions (UX-11): the form first, then
the ten newest rows, so a reader adding one sees what's already there and, after the redirect
back, the row just saved.

Kept apart from test_transaction_form.py (the form's own shape) and test_routes.py (the submit
tests, and over the repo's 64KB file limit), with its own minimal fakes.
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
from web.portfolio_client.base import Account, TrackedTicker, Transaction
from web.portfolio_client.errors import AccountLabelConflict, PortfolioUnavailable
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"


class FakeAuthClient:
    def __init__(self):
        self.validate_result: AuthResult | None = None

    async def validate_session(self, *, cookie_header):
        if self.validate_result is None:
            raise SessionInvalid("no session")
        return self.validate_result


def _txn(day: int, *, kind="BUY", ticker="VWCE.DE", kind_of_id="TICKER") -> Transaction:
    trade = kind in ("BUY", "SELL")
    return Transaction(
        account_id=1,
        broker_transaction_ref=f"ref-{day:02d}",
        transaction_type=kind,
        trade_datetime=datetime(2026, 1, day, 9, 30, tzinfo=timezone.utc),
        instrument_identifier=ticker if trade else None,
        instrument_identifier_kind=kind_of_id if trade else None,
        quantity=Decimal("10") if trade else None,
        price=Decimal("112.34") if trade else None,
        native_currency="EUR",
        native_amount=Decimal(f"-{day}00.00") if trade else Decimal("2000.00"),
        eur_amount=Decimal(f"-{day}00.00") if trade else Decimal("2000.00"),
        fx_rate=None,
        fx_rate_source=None,
        fees_native=None,
    )


class FakePortfolioClient:
    def __init__(self):
        # portfolio returns the log oldest first.
        self.transactions: list[Transaction] = [_txn(day) for day in range(1, 13)]
        self.transactions_error: Exception | None = None
        self.add_error: Exception | None = None
        self.tracked_calls = 0

    async def list_accounts(self, *, user_id):
        return [
            Account(id=1, account_label="main", broker="acme", created_at="2026-01-01T00:00:00Z")
        ]

    async def list_tickers(self):
        return ["VWCE.DE"]

    async def list_transactions(self, *, user_id, account_label):
        if self.transactions_error:
            raise self.transactions_error
        return self.transactions

    async def list_tracked_tickers(self, *, requested_by=None, statuses=None, tickers=None):
        self.tracked_calls += 1
        return [
            TrackedTicker(
                ticker="VWCE.DE",
                display_name="Vanguard FTSE All-World",
                asset_type="ETF",
                currency="EUR",
                exchange=None,
                origin="config",
                status="ready",
                enabled=True,
                attempts=0,
                last_error=None,
                requested_by=None,
                requested_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                ready_at=None,
            )
        ]

    async def add_transaction(self, **kwargs):
        raise self.add_error


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


# The two grid children: the form's card, then the recent transactions' card.
_SPLIT = re.compile(
    r'<div class="split split-form-start">\s*(<section class="card split-form-card">.*?</section>)'
    r'\s*(<section class="card card-wide"[^>]*>.*?</section>)\s*</div>',
    re.DOTALL,
)


def _cards(html: str) -> tuple[str, str]:
    match = _SPLIT.search(html)
    assert match, "no form-and-list split on the add page"
    return match.group(1), match.group(2)


def _page(test_client) -> str:
    return test_client.get("/transactions/new", params={"account": "main"}).text


def _rows(card: str) -> list[str]:
    tbody = re.search(r"<tbody\b[^>]*>(.*?)</tbody>", card, flags=re.S).group(1)
    return re.findall(r"<tr\b[^>]*>.*?</tr>", tbody, flags=re.S)


def test_the_form_comes_before_the_recent_transactions(client):
    test_client, _ = client

    html = _page(test_client)

    form, recent = _cards(html)
    assert '<form class="transaction-form"' in form
    assert '<h2 id="recent-transactions">Recent transactions</h2>' in recent
    # The account header spans the page above both.
    assert html.index('class="account-header"') < html.index('class="split split-form-start"')


def test_the_ten_newest_are_listed_newest_first(client):
    test_client, _ = client

    rows = _rows(_cards(_page(test_client))[1])

    assert len(rows) == 10
    assert "2026-01-12" in rows[0]
    assert "2026-01-03" in rows[-1]


def test_each_row_shows_date_type_instrument_and_amount_with_no_delete(client):
    test_client, _ = client

    recent = _cards(_page(test_client))[1]

    head = re.search(r"<thead\b.*?</thead>", recent, flags=re.S).group(0)
    assert re.findall(r">([A-Z][a-z]+)</th>", head) == ["Date", "Type", "Instrument", "Amount"]
    assert "Delete" not in recent
    assert "data-confirm" not in recent


def test_a_ticker_shows_its_name(client):
    test_client, _ = client

    recent = _cards(_page(test_client))[1]

    assert "Vanguard FTSE All-World" in recent


def test_names_are_not_fetched_when_no_row_is_a_ticker(client):
    test_client, fake_portfolio = client
    fake_portfolio.transactions = [_txn(1, kind="DEPOSIT")]

    _page(test_client)

    assert fake_portfolio.tracked_calls == 0


def test_the_card_links_to_every_transaction(client):
    test_client, _ = client

    recent = _cards(_page(test_client))[1]

    assert 'href="/transactions?account=main"' in recent


def test_an_account_with_none_says_so(client):
    test_client, fake_portfolio = client
    fake_portfolio.transactions = []

    recent = _cards(_page(test_client))[1]

    assert "No transactions yet." in recent
    assert "<table" not in recent


def test_a_failed_list_keeps_the_form_and_says_so(client):
    test_client, fake_portfolio = client
    fake_portfolio.transactions_error = PortfolioUnavailable("down")

    response = test_client.get("/transactions/new", params={"account": "main"})

    assert response.status_code == 200
    form, recent = _cards(response.text)
    assert '<form class="transaction-form"' in form
    assert "Can&#39;t reach the portfolio service" in recent
    assert "No transactions yet." not in recent


def test_a_save_lands_back_beside_the_list(client):
    test_client, _ = client

    html = test_client.get("/transactions/new", params={"account": "main", "saved": "1"}).text

    form, recent = _cards(html)
    assert "Transaction saved." in form
    assert len(_rows(recent)) == 10


def test_a_failed_save_keeps_the_list_beside_the_form(client):
    test_client, fake_portfolio = client
    fake_portfolio.add_error = AccountLabelConflict("no")
    token = test_client.get("/transactions/new", params={"account": "main"}).cookies[
        "stakehold_csrf"
    ]

    response = test_client.post(
        "/transactions/new",
        data={
            "csrf_token": token,
            "account_label": "main",
            "broker_transaction_ref": "manual-1",
            "transaction_type": "DEPOSIT",
            "trade_datetime": "2026-10-09T14:05",
            "native_amount": "100",
            "native_currency": "EUR",
        },
    )

    assert response.status_code >= 400
    form, recent = _cards(response.text)
    assert "Couldn&#39;t save." in form
    assert len(_rows(recent)) == 10
