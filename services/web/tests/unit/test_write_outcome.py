"""The confirmation after a transaction write: what portfolio's follow-on rebuilds did (UI-7).

Kept apart from test_routes.py, which is at the repo's 64KB file limit. Like
test_portfolio_page.py, it carries its own minimal fakes: only the calls these pages make.
"""

import pytest
from fastapi.testclient import TestClient
from web import settings as settings_module
from web.api.app import create_app
from web.api.deps import get_auth_client, get_portfolio_client
from web.auth_client.base import AuthResult, AuthUser
from web.auth_client.errors import SessionInvalid
from web.portfolio_client.base import Account, TransactionWriteResult
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"


class FakeAuthClient:
    def __init__(self):
        self.validate_result: AuthResult | None = None

    async def validate_session(self, *, cookie_header):
        if self.validate_result is None:
            raise SessionInvalid("no session")
        return self.validate_result


class FakePortfolioClient:
    def __init__(self):
        self.write_result = TransactionWriteResult(status="ok", rows_saved=1, transactions_saved=1)

    async def list_accounts(self, *, user_id):
        return [
            Account(id=1, account_label="main", broker="acme", created_at="2026-01-01T00:00:00Z")
        ]

    async def list_tickers(self):
        return []

    async def add_transaction(self, **kwargs):
        return self.write_result

    async def import_transactions(self, **kwargs):
        return self.write_result


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


def _result(saved=1, positions=None, returns=None) -> TransactionWriteResult:
    return TransactionWriteResult(
        status="ok" if saved else "nothing_to_do",
        rows_saved=saved,
        transactions_saved=saved,
        positions_rebuild=positions,
        returns_rebuild=returns,
    )


def _post_new(test_client):
    token = test_client.get("/transactions/new", params={"account": "main"}).cookies[
        "stakehold_csrf"
    ]
    data = {
        "account_label": "main",
        "broker_transaction_ref": "ref-1",
        "transaction_type": "DEPOSIT",
        "trade_datetime": "2026-01-05T12:00",
        "native_amount": "100.00",
        "native_currency": "EUR",
        "csrf_token": token,
    }
    return test_client.post("/transactions/new", data=data, follow_redirects=False)


def _post_import(test_client):
    token = test_client.get("/transactions/import", params={"account": "main"}).cookies[
        "stakehold_csrf"
    ]
    return test_client.post(
        "/transactions/import",
        data={"account_label": "main", "csrf_token": token},
        files={"file": ("export.csv", b"a,b\n1,2\n", "text/csv")},
        follow_redirects=False,
    )


def _banner(response) -> str:
    start = response.text.index('<p class="confirmation-banner">')
    return response.text[start : response.text.index("</p>", start)]


# --- the redirect carries the statuses -------------------------------------------------------


def test_add_redirect_carries_the_rebuild_statuses(client):
    test_client, fake_portfolio = client
    fake_portfolio.write_result = _result(positions="ok", returns="partial")

    response = _post_new(test_client)

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/transactions/new?account=main&saved=1&positions=ok&returns=partial"
    )


def test_add_redirect_without_statuses_from_an_older_portfolio(client):
    test_client, fake_portfolio = client
    fake_portfolio.write_result = _result()

    response = _post_new(test_client)

    assert response.headers["location"] == "/transactions/new?account=main&saved=1"


def test_add_redirect_reports_a_duplicate_as_zero_saved(client):
    test_client, fake_portfolio = client
    fake_portfolio.write_result = _result(saved=0, positions="skipped", returns="skipped")

    response = _post_new(test_client)

    assert response.headers["location"] == (
        "/transactions/new?account=main&saved=0&positions=skipped&returns=skipped"
    )


def test_import_redirect_carries_the_rebuild_statuses(client):
    test_client, fake_portfolio = client
    fake_portfolio.write_result = _result(saved=5, positions="failed", returns="skipped")

    response = _post_import(test_client)

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/transactions/import?account=main&imported=5&positions=failed&returns=skipped"
    )


# --- the confirmation page renders them ------------------------------------------------------


def _new_page(test_client, **params):
    return test_client.get("/transactions/new", params={"account": "main", **params})


def test_saved_with_both_rebuilds_done(client):
    test_client, _ = client

    response = _new_page(test_client, saved="1", positions="ok", returns="ok")

    assert "Transaction saved. Holdings and returns updated." in _banner(response)


def test_saved_with_returns_left_to_the_scheduled_refresh(client):
    test_client, _ = client

    response = _new_page(test_client, saved="1", positions="ok", returns="failed")

    assert (
        "Transaction saved. Holdings updated. Returns couldn't be updated now; they'll catch "
        "up on the next scheduled refresh." in _banner(response).replace("&#39;", "'")
    )


def test_saved_without_statuses_says_only_saved(client):
    test_client, _ = client

    response = _new_page(test_client, saved="1")

    assert _banner(response).endswith("Transaction saved.")


def test_saved_ignores_an_unknown_status(client):
    test_client, _ = client

    response = _new_page(test_client, saved="1", positions="<b>x</b>", returns="ok")

    assert _banner(response).endswith("Transaction saved.")
    assert "&lt;b&gt;" not in response.text


def test_nothing_saved_says_it_was_already_recorded(client):
    test_client, _ = client

    response = _new_page(test_client, saved="0", positions="skipped", returns="skipped")

    assert "Nothing new to save — that transaction was already recorded." in _banner(response)


def test_no_banner_without_a_saved_param(client):
    test_client, _ = client

    response = _new_page(test_client, positions="ok", returns="ok")

    assert "confirmation-banner" not in response.text


def test_imported_with_some_holdings_waiting_for_a_mapping(client):
    test_client, _ = client

    response = test_client.get(
        "/transactions/import",
        params={"account": "main", "imported": "1200", "positions": "partial", "returns": "ok"},
    )

    banner = _banner(response)
    assert 'Imported <span class="mono">1,200</span> transaction(s).' in banner
    assert banner.endswith("Some holdings are waiting for a ticker mapping. Returns updated.")


def test_imported_nothing_says_every_row_was_already_recorded(client):
    test_client, _ = client

    response = test_client.get("/transactions/import", params={"account": "main", "imported": "0"})

    assert "Nothing new to import — every row was already recorded." in _banner(response)
