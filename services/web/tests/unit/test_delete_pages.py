"""Deleting a transaction: the Delete link on each row of the transaction log, the dialog it
opens, the no-JS confirmation page behind the link, and the POST both of them send.

Kept apart from test_transactions_page.py, which covers the log itself. Like that file, it
carries its own minimal fakes: only the calls these pages make.
"""

import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from web import settings as settings_module
from web.api.app import create_app
from web.api.deps import get_auth_client, get_portfolio_client
from web.auth_client.base import AuthResult, AuthUser
from web.auth_client.errors import SessionInvalid
from web.portfolio_client.base import Account, Transaction, TransactionDeleteResult
from web.portfolio_client.errors import PortfolioUnavailable, TransactionNotFound
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"
CSRF_TOKEN = "csrf-tok"
MINUS = "−"
_STATIC_DIR = Path(__file__).resolve().parents[2] / "src" / "web" / "static"


class FakeAuthClient:
    def __init__(self):
        self.validate_result: AuthResult | None = None

    async def validate_session(self, *, cookie_header):
        if self.validate_result is None:
            raise SessionInvalid("no session")
        return self.validate_result


class FakePortfolioClient:
    def __init__(self):
        self.list_accounts_result: list[Account] = [
            Account(id=1, account_label="main", broker="acme", created_at="2026-01-01T00:00:00Z")
        ]
        self.list_transactions_result: list[Transaction] = [_buy()]
        self.list_transactions_error: Exception | None = None
        self.delete_result = TransactionDeleteResult(
            status="ok", transactions_deleted=1, positions_rebuild="ok", returns_rebuild="ok"
        )
        self.delete_error: Exception | None = None
        self.deletes: list[tuple] = []

    async def list_accounts(self, *, user_id):
        return self.list_accounts_result

    async def list_transactions(self, *, user_id, account_label):
        if self.list_transactions_error:
            raise self.list_transactions_error
        return self.list_transactions_result

    async def delete_transaction(self, *, user_id, account_label, broker_transaction_ref):
        self.deletes.append((user_id, account_label, broker_transaction_ref))
        if self.delete_error:
            raise self.delete_error
        return self.delete_result


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


def _buy(*, ref="buy-1", account_id=1) -> Transaction:
    return Transaction(
        account_id=account_id,
        broker_transaction_ref=ref,
        transaction_type="BUY",
        trade_datetime=datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc),
        instrument_identifier="VWCE.DE",
        instrument_identifier_kind="TICKER",
        quantity=Decimal("10.000000"),
        price=Decimal("112.3400"),
        native_currency="EUR",
        native_amount=Decimal("-1124.4000"),
        eur_amount=Decimal("-1124.4000"),
        fx_rate=None,
        fx_rate_source=None,
        fees_native=Decimal("1.0000"),
    )


def _my_isa(fake_portfolio):
    fake_portfolio.list_accounts_result = [
        Account(id=2, account_label="my isa", broker="acme", created_at="2026-01-01T00:00:00Z")
    ]


def _confirm_page(test_client, account="main", ref="buy-1"):
    return test_client.get(
        "/transactions/delete", params={"account": account, "ref": ref}, follow_redirects=False
    )


def _post(test_client, *, account="main", ref="buy-1", csrf_token=CSRF_TOKEN):
    return test_client.post(
        "/transactions/delete",
        data={"account": account, "ref": ref, "csrf_token": csrf_token},
        follow_redirects=False,
    )


def _text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _form(html: str, action: str) -> str:
    match = re.search(rf'<form\b[^>]*action="{re.escape(action)}"[^>]*>.*?</form>', html, re.S)
    assert match, f"no form posting to {action}"
    return match.group(0)


def _hidden(form: str, name: str) -> str | None:
    match = re.search(rf'<input type="hidden" name="{name}" value="([^"]*)"', form)
    return match.group(1) if match else None


# --- the no-JS confirmation page ------------------------------------------------


def test_confirm_page_requires_login(client):
    test_client, _, _ = client

    response = _confirm_page(test_client)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_confirm_page_shows_the_transaction_and_a_warning(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = _confirm_page(test_client)

    assert response.status_code == 200
    text = _text(response.text)
    assert "Delete this transaction?" in text
    assert "This can't be undone." in text.replace("&#39;", "'")
    assert "2026-01-05" in text
    assert "VWCE.DE" in text
    assert f"{MINUS}1,124.40 EUR" in text
    assert "buy-1" in text


def test_confirm_page_form_posts_the_account_and_ref_with_a_csrf_token(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _my_isa(fake_portfolio)

    html = _confirm_page(test_client, account="my isa").text

    form = _form(html, "/transactions/delete")
    assert 'method="post"' in form
    assert _hidden(form, "account") == "my isa"
    assert _hidden(form, "ref") == "buy-1"
    assert _hidden(form, "csrf_token") == CSRF_TOKEN


def test_confirm_page_confirm_button_is_not_the_gold_call_to_action(client):
    """Gold means "act here" (charter chapter II): a destructive confirm must never look like
    the page's primary action. Nor oxblood, which means a loss."""
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    form = _form(_confirm_page(test_client).text, "/transactions/delete")

    assert re.search(r'<button type="submit" class="btn btn-secondary">Delete permanently', form)
    assert "btn-primary" not in form
    assert not re.search(r"\b(gain|loss)\b", form)


def test_confirm_page_cancel_goes_back_to_the_log(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _my_isa(fake_portfolio)

    html = _confirm_page(test_client, account="my isa").text

    assert re.search(r'<a [^>]*href="/transactions\?account=my%20isa"[^>]*>Cancel</a>', html)


def test_confirm_page_unknown_account_renders_404(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = _confirm_page(test_client, account="someone-elses")

    assert response.status_code == 404
    assert "Account not found" in response.text


def test_confirm_page_for_a_transaction_already_gone_goes_back_to_the_log(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = _confirm_page(test_client, ref="gone")

    assert response.status_code == 303
    assert response.headers["location"] == "/transactions?account=main&deleted=0"


def test_confirm_page_degrades_when_transactions_unavailable(client):
    """Without the transaction there is nothing to show the visitor before they confirm, so no
    form either: a delete they couldn't check isn't offered."""
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_transactions_error = PortfolioUnavailable("down")

    response = _confirm_page(test_client)

    assert response.status_code == 200
    assert "Can&#39;t reach the portfolio service right now" in response.text
    assert 'action="/transactions/delete"' not in response.text


@pytest.mark.parametrize("params", [{"account": "main"}, {"ref": "buy-1"}, {}])
def test_confirm_page_requires_an_account_and_a_ref(client, params):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = test_client.get("/transactions/delete", params=params)

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
    assert "Couldn't delete." in response.text.replace("&#39;", "'")
    assert fake_portfolio.deletes == []


def test_delete_deletes_this_users_transaction_and_reports_the_rebuilds(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _my_isa(fake_portfolio)
    fake_portfolio.delete_result = TransactionDeleteResult(
        status="ok", transactions_deleted=1, positions_rebuild="ok", returns_rebuild="partial"
    )

    response = _post(test_client, account="my isa", ref="buy 1")

    assert fake_portfolio.deletes == [("u1", "my isa", "buy 1")]
    assert response.status_code == 303
    assert response.headers["location"] == (
        "/transactions?account=my%20isa&deleted=1&positions=ok&returns=partial"
    )


def test_delete_of_a_transaction_already_gone_says_nothing_was_deleted(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.delete_error = TransactionNotFound("gone")

    response = _post(test_client)

    assert response.status_code == 303
    assert response.headers["location"] == "/transactions?account=main&deleted=0"


def test_delete_when_portfolio_is_unavailable_keeps_the_confirmation(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.delete_error = PortfolioUnavailable("down")

    response = _post(test_client)

    assert response.status_code == 503
    assert "Can&#39;t reach the portfolio service right now" in response.text
    assert _hidden(_form(response.text, "/transactions/delete"), "ref") == "buy-1"


# --- the transaction log: the outcome banner -------------------------------------


def test_log_confirms_a_delete_with_what_the_rebuilds_did(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = test_client.get(
        "/transactions",
        params={"account": "main", "deleted": "1", "positions": "ok", "returns": "ok"},
    ).text

    assert "<strong>Transaction deleted.</strong> Holdings and returns updated." in html


def test_log_says_when_there_was_nothing_to_delete(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = test_client.get("/transactions", params={"account": "main", "deleted": "0"}).text

    text = _text(html).replace("&#39;", "'")
    assert "Nothing deleted. That transaction isn't in this account any more." in text


def test_log_shows_no_outcome_banner_by_default(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = test_client.get("/transactions", params={"account": "main"}).text

    assert "Transaction deleted." not in html
    assert "Nothing deleted." not in html


# --- the transaction log: the Delete link and its dialog --------------------------


def test_each_row_links_to_its_confirmation_page(client):
    """The link is the no-JS path: it works with no script at all."""
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    _my_isa(fake_portfolio)
    fake_portfolio.list_transactions_result = [_buy(ref="a&b", account_id=2)]

    html = test_client.get("/transactions", params={"account": "my isa"}).text

    assert 'href="/transactions/delete?account=my%20isa&amp;ref=a%26b"' in html


def test_delete_link_names_the_transaction_for_screen_readers(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = test_client.get("/transactions", params={"account": "main"}).text

    link = re.search(r"<a [^>]*data-confirm=[^>]*>.*?</a>", html, re.S).group(0)
    assert _text(link) == "Delete BUY on 2026-01-05"


def test_delete_link_carries_the_summary_the_dialog_shows(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = test_client.get("/transactions", params={"account": "main"}).text

    assert f'data-confirm-summary="2026-01-05 · BUY VWCE.DE · {MINUS}1,124.40 EUR"' in html
    assert 'data-confirm="delete-transaction-dialog"' in html


def test_log_carries_one_dialog_that_posts_the_delete(client):
    test_client, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    html = test_client.get("/transactions", params={"account": "main"}).text

    assert html.count("<dialog") == 1
    dialog = re.search(r'<dialog\b[^>]*id="delete-transaction-dialog".*?</dialog>', html, re.S)
    assert dialog, "no delete dialog"
    form = _form(dialog.group(0), "/transactions/delete")
    assert 'method="post"' in form
    assert _hidden(form, "csrf_token") == CSRF_TOKEN
    # Filled by the script from the link the visitor clicked.
    assert _hidden(form, "account") == ""
    assert _hidden(form, "ref") == ""
    assert "This can't be undone." in form
    assert re.search(r'<button type="submit" class="btn btn-secondary">Delete permanently', form)
    assert re.search(r'<button type="submit" formmethod="dialog"[^>]*>Cancel</button>', form)
    assert re.search(r'<script src="/static/confirm_dialog\.js\?v=\w+" defer></script>', html)


def test_empty_log_has_no_dialog(client):
    test_client, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_transactions_result = []

    html = test_client.get("/transactions", params={"account": "main"}).text

    assert "<dialog" not in html
    assert "confirm_dialog.js" not in html


def test_confirm_dialog_script_writes_text_never_markup():
    """The summary comes from data the visitor's broker file supplied: it must be set as text."""
    script = (_STATIC_DIR / "confirm_dialog.js").read_text()

    assert "textContent" in script
    assert "innerHTML" not in script
    assert "showModal" in script
