"""Deleting an account: the Delete link on each account of the accounts list, the dialog it
opens, the no-JS confirmation page behind the link, and the POST both of them send.

The transaction delete's own tests are in test_delete_pages.py. Like that file, this one carries
its own minimal fakes: only the calls these pages make.
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
from web.portfolio_client.base import Account, AccountDeleteResult, Broker, Transaction
from web.portfolio_client.errors import AccountNotFound, PortfolioUnavailable
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"
CSRF_TOKEN = "csrf-tok"


class FakeAuthClient:
    def __init__(self):
        self.validate_result: AuthResult | None = None

    async def validate_session(self, *, cookie_header):
        if self.validate_result is None:
            raise SessionInvalid("no session")
        return self.validate_result


class FakePortfolioClient:
    def __init__(self):
        self.list_accounts_result: list[Account] = [_account("main")]
        self.list_accounts_error: Exception | None = None
        # Per account label: its transactions, or an exception to raise.
        self.transactions: dict[str, list[Transaction] | Exception] = {"main": [_txn("t1")]}
        self.transaction_calls: list[tuple] = []
        self.delete_result = AccountDeleteResult(
            status="ok", accounts_deleted=1, transactions_deleted=12
        )
        self.delete_error: Exception | None = None
        self.deletes: list[tuple] = []

    async def list_accounts(self, *, user_id):
        if self.list_accounts_error:
            raise self.list_accounts_error
        return self.list_accounts_result

    async def list_brokers(self):
        return [Broker(broker="acme", imports=("transactions",))]

    async def list_transactions(self, *, user_id, account_label):
        self.transaction_calls.append((user_id, account_label))
        result = self.transactions.get(account_label, [])
        if isinstance(result, Exception):
            raise result
        return result

    async def delete_account(self, *, user_id, account_label):
        self.deletes.append((user_id, account_label))
        if self.delete_error:
            raise self.delete_error
        return self.delete_result


def _account(label, *, id=1, broker="acme"):
    return Account(id=id, account_label=label, broker=broker, created_at="2026-01-01T00:00:00Z")


def _txn(ref) -> Transaction:
    return Transaction(
        account_id=1,
        broker_transaction_ref=ref,
        transaction_type="DEPOSIT",
        trade_datetime=datetime(2026, 1, 1, tzinfo=timezone.utc),
        instrument_identifier=None,
        instrument_identifier_kind=None,
        quantity=None,
        price=None,
        native_currency="EUR",
        native_amount=Decimal("100"),
        eur_amount=Decimal("100"),
        fx_rate=None,
        fx_rate_source=None,
        fees_native=None,
    )


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
    test_client.cookies.set("stakehold_csrf", CSRF_TOKEN)


def _my_isa(fake_portfolio, transactions):
    fake_portfolio.list_accounts_result = [_account("my isa", id=2)]
    fake_portfolio.transactions = {"my isa": transactions}


def _confirm_page(test_client, account="main"):
    return test_client.get("/accounts/delete", params={"account": account}, follow_redirects=False)


def _post(test_client, *, account="main", csrf_token=CSRF_TOKEN):
    return test_client.post(
        "/accounts/delete",
        data={"account": account, "csrf_token": csrf_token},
        follow_redirects=False,
    )


def _text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split()).replace("&#39;", "'")


def _form(html: str, action: str) -> str:
    match = re.search(rf'<form\b[^>]*action="{re.escape(action)}"[^>]*>.*?</form>', html, re.S)
    assert match, f"no form posting to {action}"
    return match.group(0)


def _hidden(form: str, name: str) -> str | None:
    match = re.search(rf'<input type="hidden" name="{name}" value="([^"]*)"', form)
    return match.group(1) if match else None


# --- the accounts list: the Delete link and its dialog ----------------------------


def test_each_account_links_to_its_confirmation_page(client):
    """The link is the no-JS path: it works with no script at all."""
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _my_isa(fake_portfolio, [])

    html = test_client.get("/accounts").text

    assert 'href="/accounts/delete?account=my%20isa"' in html


def test_delete_link_names_the_account_for_screen_readers(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = test_client.get("/accounts").text

    link = re.search(r"<a [^>]*data-confirm=[^>]*>.*?</a>", html, re.S).group(0)
    assert _text(link) == "Delete main"


@pytest.mark.parametrize(
    "transactions, summary",
    [
        ([_txn("a"), _txn("b")], "This deletes my isa and its 2 transactions."),
        ([_txn("a")], "This deletes my isa and its 1 transaction."),
        ([], "This deletes my isa. It has no transactions."),
        (PortfolioUnavailable("down"), "This deletes my isa and every transaction in it."),
    ],
)
def test_delete_link_says_how_many_transactions_go_with_the_account(client, transactions, summary):
    """A count that couldn't be loaded never reads as "no transactions"."""
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _my_isa(fake_portfolio, transactions)

    html = test_client.get("/accounts").text

    assert f'data-confirm-summary="{summary}"' in html
    assert 'data-confirm="delete-account-dialog"' in html


def test_counts_are_asked_for_this_users_accounts(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_accounts_result = [_account("a", id=1), _account("b", id=2)]

    test_client.get("/accounts")

    assert sorted(fake_portfolio.transaction_calls) == [("u1", "a"), ("u1", "b")]


def test_accounts_page_carries_one_dialog_that_posts_the_delete(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = test_client.get("/accounts").text

    assert html.count("<dialog") == 1
    dialog = re.search(r'<dialog\b[^>]*id="delete-account-dialog".*?</dialog>', html, re.S)
    assert dialog, "no delete dialog"
    form = _form(dialog.group(0), "/accounts/delete")
    assert 'method="post"' in form
    assert _hidden(form, "csrf_token") == CSRF_TOKEN
    assert _hidden(form, "account") == ""
    assert "This can't be undone." in form
    assert re.search(r'<button type="submit" class="btn btn-secondary">Delete permanently', form)
    assert re.search(r'<button type="submit" formmethod="dialog"[^>]*>Cancel</button>', form)
    assert re.search(r'<script src="/static/confirm_dialog\.js\?v=\w+" defer></script>', html)


def test_accounts_page_with_no_accounts_has_no_dialog(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_accounts_result = []

    html = test_client.get("/accounts").text

    assert "<dialog" not in html
    assert "confirm_dialog.js" not in html


# --- the accounts list: the outcome banner ------------------------------------------


@pytest.mark.parametrize(
    "transactions, expected",
    [
        ("12", "Account deleted. 12 transactions went with it."),
        ("1", "Account deleted. 1 transaction went with it."),
        ("0", "Account deleted."),
    ],
)
def test_accounts_page_confirms_a_delete(client, transactions, expected):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = test_client.get("/accounts", params={"deleted": "1", "transactions": transactions}).text

    banner = re.search(r'<div class="banner banner-success".*?</div>', html, re.S).group(0)
    assert _text(banner) == expected


def test_accounts_page_says_when_there_was_nothing_to_delete(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = test_client.get("/accounts", params={"deleted": "0"}).text

    assert "Nothing deleted. That account no longer exists." in _text(html)


def test_accounts_page_shows_no_outcome_banner_by_default(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    text = _text(test_client.get("/accounts").text)

    assert "Account deleted." not in text
    assert "Nothing deleted." not in text


# --- the no-JS confirmation page -------------------------------------------------


def test_confirm_page_requires_login(client):
    test_client, _, _ = client

    response = _confirm_page(test_client)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_confirm_page_says_what_goes_with_the_account(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _my_isa(fake_portfolio, [_txn("a"), _txn("b")])

    response = _confirm_page(test_client, account="my isa")

    assert response.status_code == 200
    text = _text(response.text)
    assert "Delete this account?" in text
    assert "This deletes my isa and its 2 transactions." in text
    assert "This can't be undone." in text


def test_confirm_page_form_posts_the_account_with_a_csrf_token(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _my_isa(fake_portfolio, [])

    form = _form(_confirm_page(test_client, account="my isa").text, "/accounts/delete")

    assert 'method="post"' in form
    assert _hidden(form, "account") == "my isa"
    assert _hidden(form, "csrf_token") == CSRF_TOKEN
    assert re.search(r'<button type="submit" class="btn btn-secondary">Delete permanently', form)
    assert "btn-primary" not in form
    assert re.search(r'<a [^>]*href="/accounts"[^>]*>Cancel</a>', form)


def test_confirm_page_unknown_account_renders_404(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = _confirm_page(test_client, account="someone-elses")

    assert response.status_code == 404
    assert "Account not found" in response.text


def test_confirm_page_still_offers_the_delete_when_the_count_is_unavailable(client):
    """The account itself is known, so the delete is still offered; only the count is vague."""
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.transactions = {"main": PortfolioUnavailable("down")}

    response = _confirm_page(test_client)

    assert response.status_code == 200
    assert "This deletes main and every transaction in it." in _text(response.text)
    assert _hidden(_form(response.text, "/accounts/delete"), "account") == "main"


def test_confirm_page_requires_an_account(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = test_client.get("/accounts/delete")

    assert response.status_code == 422


# --- the POST -----------------------------------------------------------------------


def test_delete_requires_login(client):
    test_client, _, fake_portfolio = client

    response = _post(test_client)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert fake_portfolio.deletes == []


def test_delete_rejects_a_bad_csrf_token(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    response = _post(test_client, csrf_token="forged")

    assert response.status_code == 400
    assert "Couldn't delete." in _text(response.text)
    assert fake_portfolio.deletes == []


def test_delete_deletes_this_users_account_and_says_how_many_transactions_went(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    response = _post(test_client, account="my isa")

    assert fake_portfolio.deletes == [("u1", "my isa")]
    assert response.status_code == 303
    assert response.headers["location"] == "/accounts?deleted=1&transactions=12"


def test_delete_of_an_account_already_gone_says_nothing_was_deleted(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.delete_error = AccountNotFound("gone")

    response = _post(test_client)

    assert response.status_code == 303
    assert response.headers["location"] == "/accounts?deleted=0"


def test_delete_when_portfolio_is_unavailable_keeps_the_confirmation(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.delete_error = PortfolioUnavailable("down")

    response = _post(test_client)

    assert response.status_code == 503
    assert "Can&#39;t reach the portfolio service right now" in response.text
    assert _hidden(_form(response.text, "/accounts/delete"), "account") == "main"
