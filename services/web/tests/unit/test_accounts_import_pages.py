"""The accounts page and the broker-file import page (UI-10).

Kept apart from test_routes.py, which is past the repo's 64KB file limit. Like
test_transactions_page.py, it carries its own minimal fakes: only the calls these pages make.
"""

import re

import pytest
from fastapi.testclient import TestClient
from web import settings as settings_module
from web.api.app import create_app
from web.api.deps import get_auth_client, get_portfolio_client
from web.auth_client.base import AuthResult, AuthUser
from web.auth_client.errors import SessionInvalid
from web.portfolio_client.base import Account, Broker
from web.portfolio_client.errors import AccountLabelConflict, PortfolioUnavailable
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"
MAIN = Account(id=1, account_label="main", broker="acme", created_at="2026-01-01T00:00:00Z")


class FakeAuthClient:
    def __init__(self):
        self.validate_result: AuthResult | None = None

    async def validate_session(self, *, cookie_header):
        if self.validate_result is None:
            raise SessionInvalid("no session")
        return self.validate_result


class FakePortfolioClient:
    def __init__(self):
        self.list_accounts_result: list[Account] = [MAIN]
        self.list_accounts_error: Exception | None = None
        self.list_brokers_result: list[Broker] = [Broker(broker="acme", imports=("transactions",))]
        self.create_account_error: Exception | None = None

    async def list_accounts(self, *, user_id):
        if self.list_accounts_error:
            raise self.list_accounts_error
        return self.list_accounts_result

    async def list_brokers(self):
        return self.list_brokers_result

    async def create_account(self, *, user_id, broker, account_label):
        if self.create_account_error:
            raise self.create_account_error
        return None


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


_DETAILS = re.compile(r'<details class="new-account"( open)?>')


def _disclosure_open(html: str) -> bool:
    match = _DETAILS.search(html)
    assert match, "no new-account disclosure in page"
    return match.group(1) is not None


def _post_account(test_client, **data):
    token = test_client.get("/accounts").cookies["stakehold_csrf"]
    return test_client.post(
        "/accounts",
        data={"broker": "acme", "account_label": "main", "csrf_token": token, **data},
        follow_redirects=False,
    )


# --- accounts: the list ------------------------------------------------------------------


def test_account_list_comes_before_the_create_form(client):
    test_client, _ = client

    html = test_client.get("/accounts").text

    assert html.index('class="account-list"') < html.index('<form class="account-form"')


def test_account_label_opens_its_holdings(client):
    test_client, _ = client

    html = test_client.get("/accounts").text

    assert '<a class="account-label" href="/portfolio?account=main">main' in html


def test_account_row_has_secondary_links_to_its_other_pages(client):
    test_client, _ = client

    html = test_client.get("/accounts").text

    for href, label in [
        ("/transactions?account=main", "Transactions"),
        ("/transactions/new?account=main", "Add"),
        ("/transactions/import?account=main", "Import"),
    ]:
        assert f'<a class="btn btn-secondary" href="{href}">{label}</a>' in html


def test_account_links_encode_the_label(client):
    test_client, fake_portfolio = client
    fake_portfolio.list_accounts_result = [
        Account(id=2, account_label="joint & co", broker="acme", created_at="2026-01-01T00:00:00Z")
    ]

    html = test_client.get("/accounts").text

    assert 'href="/portfolio?account=joint%20%26%20co"' in html
    assert 'href="/transactions/import?account=joint%20%26%20co"' in html


# --- accounts: the new-account disclosure ------------------------------------------------


def test_new_account_form_is_collapsed_once_the_user_has_accounts(client):
    test_client, _ = client

    html = test_client.get("/accounts").text

    assert _disclosure_open(html) is False
    assert "<summary>New account</summary>" in html


def test_new_account_form_is_open_on_first_run(client):
    test_client, fake_portfolio = client
    fake_portfolio.list_accounts_result = []

    html = test_client.get("/accounts").text

    assert _disclosure_open(html) is True


def test_first_run_explains_the_steps(client):
    test_client, fake_portfolio = client
    fake_portfolio.list_accounts_result = []

    html = test_client.get("/accounts").text

    assert 'class="first-run-steps"' in html
    assert "Create an account" in html
    assert "import" in html.lower()


def test_first_run_steps_are_not_shown_once_the_user_has_accounts(client):
    test_client, _ = client

    html = test_client.get("/accounts").text

    assert 'class="first-run-steps"' not in html


def test_an_outage_is_not_a_first_run(client):
    """Regression guard: a failed account list must never read as "you have no accounts"."""
    test_client, fake_portfolio = client
    fake_portfolio.list_accounts_error = PortfolioUnavailable("boom")

    html = test_client.get("/accounts").text

    assert 'class="first-run-steps"' not in html
    assert _disclosure_open(html) is False


def test_an_outage_notice_is_outside_the_collapsed_form(client):
    test_client, fake_portfolio = client
    fake_portfolio.list_accounts_error = PortfolioUnavailable("boom")

    html = test_client.get("/accounts").text

    notice = html.index("Can&#39;t reach the portfolio service")
    assert notice < html.index('<details class="new-account"')


def test_new_account_form_is_open_on_an_error_rerender(client):
    """A plain (no-JS) post that fails re-renders the page; a collapsed form would hide the
    message the visitor needs."""
    test_client, fake_portfolio = client
    fake_portfolio.create_account_error = AccountLabelConflict("taken")

    response = _post_account(test_client)

    assert response.status_code == 409
    assert _disclosure_open(response.text) is True
    assert "That label is already used with a different broker." in response.text


def test_new_account_form_is_open_on_a_custom_broker_error(client):
    test_client, _ = client

    response = _post_account(test_client, broker="__custom__", custom_broker="")

    assert response.status_code == 422
    assert _disclosure_open(response.text) is True


def test_new_account_form_is_open_on_a_csrf_failure(client):
    test_client, _ = client
    test_client.get("/accounts")

    response = test_client.post(
        "/accounts",
        data={"broker": "acme", "account_label": "main", "csrf_token": "wrong"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert _disclosure_open(response.text) is True


def test_an_error_rerender_still_lists_the_accounts(client):
    test_client, fake_portfolio = client
    fake_portfolio.create_account_error = AccountLabelConflict("taken")

    response = _post_account(test_client)

    assert '<a class="account-label" href="/portfolio?account=main">' in response.text


# --- import: the upload form -------------------------------------------------------------


def _import_page(test_client, **params):
    return test_client.get("/transactions/import", params={"account": "main", **params})


def test_import_file_field_is_a_real_file_input_styled_as_a_drop_zone(client):
    test_client, _ = client

    html = _import_page(test_client).text

    assert re.search(
        r'<input type="file" id="file" name="file" class="drop-zone"[^>]* required', html
    )


def test_import_page_states_the_size_limit(client):
    test_client, _ = client

    html = _import_page(test_client).text

    assert '<span class="mono">20</span> MB' in html


def test_import_page_shows_the_account_and_broker(client):
    test_client, _ = client

    html = _import_page(test_client).text

    assert "<h1>main</h1>" in html
    assert '<span class="account-broker">acme</span>' in html


def test_no_drop_zone_when_the_broker_has_no_importer(client):
    test_client, fake_portfolio = client
    fake_portfolio.list_brokers_result = []

    html = _import_page(test_client).text

    assert 'class="drop-zone"' not in html


# --- import: the result summary ----------------------------------------------------------


def _summary(html: str) -> str:
    """From the summary's start to the end of its next-steps links, its last line."""
    start = html.index('<div class="import-summary">')
    return html[start : html.index("</p>", html.index('<p class="next-steps">', start))]


def test_import_summary_reports_the_count_and_the_rebuild_outcome(client):
    test_client, _ = client

    html = _import_page(test_client, imported="12", positions="ok", returns="ok").text

    summary = _summary(html)
    assert 'Imported <span class="mono">12</span> transaction(s).' in summary
    assert "Holdings and returns updated." in summary


def test_import_summary_links_to_the_next_steps(client):
    test_client, _ = client

    summary = _summary(_import_page(test_client, imported="12").text)

    assert '<a href="/portfolio?account=main">View holdings</a>' in summary
    assert '<a href="/transactions?account=main">View transactions</a>' in summary


def test_import_summary_for_nothing_new_still_links_to_the_next_steps(client):
    test_client, _ = client

    summary = _summary(_import_page(test_client, imported="0").text)

    assert "Nothing new to import" in summary
    assert "View transactions" in summary


def test_import_summary_offers_another_import(client):
    test_client, _ = client

    html = _import_page(test_client, imported="12").text

    assert "Import another file" in html
    assert 'class="drop-zone"' in html


def test_no_import_summary_before_an_import(client):
    test_client, _ = client

    html = _import_page(test_client).text

    assert 'class="import-summary"' not in html
    assert "Import another file" not in html
