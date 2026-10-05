"""Route-level tests using a fake AuthClient (no live auth service), the same fake-dependency
approach auth's own tests/unit/test_api.py uses for its repositories."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from web import settings as settings_module
from web.api.app import create_app
from web.api.deps import get_auth_client, get_portfolio_client
from web.auth_client.base import AuthResult, AuthUser
from web.auth_client.errors import (
    AuthUnavailable,
    EmailAlreadyRegistered,
    InvalidCredentials,
    InvalidRequest,
    RateLimited,
    SessionInvalid,
)
from web.portfolio_client.base import (
    Account,
    AccountWriteResult,
    Position,
    Return,
    TransactionWriteResult,
)
from web.portfolio_client.errors import (
    AccountLabelConflict,
    AccountNotFound,
    InvalidPortfolioRequest,
    PortfolioUnavailable,
)
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"


class FakeAuthClient:
    def __init__(self):
        self.signup_result: AuthResult | None = None
        self.signup_error: Exception | None = None
        self.login_result: AuthResult | None = None
        self.login_error: Exception | None = None
        self.validate_result: AuthResult | None = None
        self.validate_error: Exception | None = None
        self.logout_error: Exception | None = None
        self.calls: list[tuple] = []

    async def signup(self, *, email, password, display_name, client_ip):
        self.calls.append(("signup", email, display_name, client_ip))
        if self.signup_error:
            raise self.signup_error
        return self.signup_result

    async def login(self, *, email, password, client_ip):
        self.calls.append(("login", email, client_ip))
        if self.login_error:
            raise self.login_error
        return self.login_result

    async def validate_session(self, *, cookie_header):
        self.calls.append(("validate_session", cookie_header))
        if self.validate_error:
            raise self.validate_error
        return self.validate_result

    async def logout(self, *, cookie_header):
        self.calls.append(("logout", cookie_header))
        if self.logout_error:
            raise self.logout_error


class FakePortfolioClient:
    def __init__(self):
        self.list_accounts_result: list[Account] = []
        self.list_accounts_error: Exception | None = None
        self.list_brokers_result: list[str] = []
        self.list_brokers_error: Exception | None = None
        self.list_tickers_result: list[str] = []
        self.list_tickers_error: Exception | None = None
        # Logged apart from `calls`: the manual-entry form fetches tickers on every render, and
        # tests asserting `calls` are about the write, not the picker.
        self.list_tickers_calls = 0
        self.create_account_result: AccountWriteResult | None = None
        self.create_account_error: Exception | None = None
        self.add_transaction_result: TransactionWriteResult | None = None
        self.add_transaction_error: Exception | None = None
        self.last_add_transaction: dict | None = None
        self.import_transactions_result: TransactionWriteResult | None = None
        self.import_transactions_error: Exception | None = None
        self.list_positions_result: list = []
        self.list_positions_error: Exception | None = None
        self.list_returns_result: list = []
        self.list_returns_error: Exception | None = None
        self.calls: list[tuple] = []

    async def list_accounts(self, *, user_id):
        self.calls.append(("list_accounts", user_id))
        if self.list_accounts_error:
            raise self.list_accounts_error
        return self.list_accounts_result

    async def list_brokers(self):
        self.calls.append(("list_brokers",))
        if self.list_brokers_error:
            raise self.list_brokers_error
        return self.list_brokers_result

    async def list_tickers(self):
        self.list_tickers_calls += 1
        if self.list_tickers_error:
            raise self.list_tickers_error
        return self.list_tickers_result

    async def create_account(self, *, user_id, broker, account_label):
        self.calls.append(("create_account", user_id, broker, account_label))
        if self.create_account_error:
            raise self.create_account_error
        return self.create_account_result

    async def add_transaction(
        self,
        *,
        user_id,
        account_label,
        broker_transaction_ref,
        transaction_type,
        trade_datetime,
        native_amount,
        native_currency,
        instrument_identifier=None,
        instrument_identifier_kind=None,
        quantity=None,
        price=None,
        fx_rate=None,
        fees_native=None,
    ):
        self.calls.append(
            ("add_transaction", user_id, account_label, broker_transaction_ref, transaction_type)
        )
        self.last_add_transaction = dict(
            trade_datetime=trade_datetime,
            native_amount=native_amount,
            quantity=quantity,
            price=price,
            fx_rate=fx_rate,
            fees_native=fees_native,
            instrument_identifier=instrument_identifier,
            instrument_identifier_kind=instrument_identifier_kind,
        )
        if self.add_transaction_error:
            raise self.add_transaction_error
        return self.add_transaction_result

    async def import_transactions(self, *, user_id, account_label, filename, content, content_type):
        self.calls.append(("import_transactions", user_id, account_label, filename))
        if self.import_transactions_error:
            raise self.import_transactions_error
        return self.import_transactions_result

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


def _settings(**overrides) -> Settings:
    kwargs = dict(
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
    kwargs.update(overrides)
    return Settings(**kwargs)


def _build_client(fake_auth_client, fake_portfolio_client, **setting_overrides):
    settings = _settings(**setting_overrides)
    app = create_app(settings)
    app.dependency_overrides[get_auth_client] = lambda: fake_auth_client
    app.dependency_overrides[get_portfolio_client] = lambda: fake_portfolio_client
    return TestClient(app), settings


@pytest.fixture
def fake_auth():
    return FakeAuthClient()


@pytest.fixture
def fake_portfolio():
    return FakePortfolioClient()


@pytest.fixture
def client(fake_auth, fake_portfolio):
    test_client, settings = _build_client(fake_auth, fake_portfolio)
    with test_client as entered:
        yield entered, settings, fake_auth, fake_portfolio


def _logged_in(test_client, fake_auth):
    fake_auth.validate_result = AuthResult(user=_logged_in_user(), set_cookie=SESSION_COOKIE)
    test_client.cookies.set("stakehold_session", "tok")


def _accounts_csrf_token(test_client):
    response = test_client.get("/accounts")
    return response.cookies["stakehold_csrf"]


def _transactions_new_csrf_token(test_client, account_label):
    response = test_client.get("/transactions/new", params={"account": account_label})
    return response.cookies["stakehold_csrf"]


def _transactions_import_csrf_token(test_client, account_label):
    response = test_client.get("/transactions/import", params={"account": account_label})
    return response.cookies["stakehold_csrf"]


def _login_csrf_token(test_client):
    response = test_client.get("/login")
    return response.cookies["stakehold_csrf"]


def _signup_csrf_token(test_client):
    response = test_client.get("/signup")
    return response.cookies["stakehold_csrf"]


def _logged_in_user():
    return AuthUser(user_id="u1", email="ann@example.com", display_name="Ann")


def _admin_user():
    return AuthUser(user_id="u9", email="ada@example.com", display_name="Ada", is_admin=True)


def _logged_in_as_admin(test_client, fake_auth):
    fake_auth.validate_result = AuthResult(user=_admin_user(), set_cookie=SESSION_COOKIE)
    test_client.cookies.set("stakehold_session", "tok")


# --- health -----------------------------------------------------------------


def test_health(client):
    test_client, _, _, _ = client

    response = test_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- anonymous root -----------------------------------------------------------


def test_root_redirects_to_login_when_anonymous(client):
    test_client, _, _, _ = client

    response = test_client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_root_renders_when_logged_in(client):
    test_client, _, fake_auth, _ = client
    fake_auth.validate_result = AuthResult(user=_logged_in_user(), set_cookie=SESSION_COOKIE)
    test_client.cookies.set("stakehold_session", "tok")

    response = test_client.get("/")

    assert response.status_code == 200
    assert "Welcome, Ann." in response.text


def test_root_proxies_renewed_session_cookie(client):
    test_client, _, fake_auth, _ = client
    fake_auth.validate_result = AuthResult(user=_logged_in_user(), set_cookie=SESSION_COOKIE)
    test_client.cookies.set("stakehold_session", "tok")

    response = test_client.get("/")

    assert "stakehold_session" in response.headers.get("set-cookie", "")


# --- admin --------------------------------------------------------------------


def test_admin_nav_link_is_hidden_from_a_regular_user(client):
    test_client, _, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = test_client.get("/")

    assert 'href="/admin"' not in response.text


def test_admin_nav_link_is_shown_to_an_admin(client):
    test_client, _, fake_auth, _ = client
    _logged_in_as_admin(test_client, fake_auth)

    response = test_client.get("/")

    assert 'href="/admin"' in response.text


def test_admin_page_renders_for_an_admin(client):
    test_client, _, fake_auth, _ = client
    _logged_in_as_admin(test_client, fake_auth)

    response = test_client.get("/admin")

    assert response.status_code == 200
    assert "<h1>Admin</h1>" in response.text


def test_admin_page_is_forbidden_to_a_regular_user(client):
    test_client, _, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = test_client.get("/admin", follow_redirects=False)

    assert response.status_code == 403
    assert "<h1>Admin</h1>" not in response.text
    assert "You don't have access to this page." in response.text


def test_admin_page_redirects_an_anonymous_visitor_to_login(client):
    test_client, _, _, _ = client

    response = test_client.get("/admin", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_admin_page_redirects_to_login_when_auth_cannot_confirm_the_session(client):
    # Auth down is treated as logged out (deps._resolve_user), so the gate can't fail open.
    test_client, _, fake_auth, _ = client
    fake_auth.validate_error = AuthUnavailable("down")
    test_client.cookies.set("stakehold_session", "tok")

    response = test_client.get("/admin", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


# --- login: GET ---------------------------------------------------------------


def test_login_form_renders_with_csrf_cookie(client):
    test_client, _, _, _ = client

    response = test_client.get("/login")

    assert response.status_code == 200
    assert "stakehold_csrf" in response.cookies


def test_login_form_redirects_when_already_logged_in(client):
    test_client, _, fake_auth, _ = client
    fake_auth.validate_result = AuthResult(user=_logged_in_user(), set_cookie=SESSION_COOKIE)
    test_client.cookies.set("stakehold_session", "tok")

    response = test_client.get("/login", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


# --- login: POST ---------------------------------------------------------------


def test_login_without_csrf_cookie_is_rejected(client):
    test_client, _, fake_auth, _ = client

    response = test_client.post(
        "/login", data={"email": "a@example.com", "password": "x", "csrf_token": "anything"}
    )

    assert response.status_code == 400
    assert fake_auth.calls == []


def test_login_with_wrong_csrf_field_is_rejected(client):
    test_client, _, fake_auth, _ = client
    _login_csrf_token(test_client)

    response = test_client.post(
        "/login",
        data={"email": "a@example.com", "password": "x", "csrf_token": "wrong-token"},
    )

    assert response.status_code == 400
    assert fake_auth.calls == []


def test_login_success_redirects_and_proxies_cookie(client):
    test_client, _, fake_auth, _ = client
    token = _login_csrf_token(test_client)
    fake_auth.login_result = AuthResult(user=_logged_in_user(), set_cookie=SESSION_COOKIE)

    response = test_client.post(
        "/login",
        data={
            "email": "ann@example.com",
            "password": "hunter2hunter2",  # pragma: allowlist secret
            "csrf_token": token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "stakehold_session" in response.headers.get("set-cookie", "")
    assert fake_auth.calls == [("login", "ann@example.com", "testclient")]


def test_login_invalid_credentials(client):
    test_client, _, fake_auth, _ = client
    token = _login_csrf_token(test_client)
    fake_auth.login_error = InvalidCredentials("ann@example.com")

    response = test_client.post(
        "/login",
        data={
            "email": "ann@example.com",
            "password": "wrong",  # pragma: allowlist secret
            "csrf_token": token,
        },
    )

    assert response.status_code == 401
    assert "That email and password don&#39;t match." in response.text


def test_login_rate_limited(client):
    test_client, _, fake_auth, _ = client
    token = _login_csrf_token(test_client)
    fake_auth.login_error = RateLimited("login")

    response = test_client.post(
        "/login", data={"email": "a@example.com", "password": "x", "csrf_token": token}
    )

    assert response.status_code == 429
    assert "Too many attempts" in response.text


def test_login_invalid_request(client):
    test_client, _, fake_auth, _ = client
    token = _login_csrf_token(test_client)
    fake_auth.login_error = InvalidRequest("login")

    response = test_client.post(
        "/login", data={"email": "a@example.com", "password": "x", "csrf_token": token}
    )

    assert response.status_code == 400
    assert "Check your email and password" in response.text


def test_login_auth_unavailable(client):
    test_client, _, fake_auth, _ = client
    token = _login_csrf_token(test_client)
    fake_auth.login_error = AuthUnavailable("boom")

    response = test_client.post(
        "/login", data={"email": "a@example.com", "password": "x", "csrf_token": token}
    )

    assert response.status_code == 503
    assert "Can&#39;t reach the sign-in service" in response.text


def test_login_unrecognized_auth_client_error_falls_back_to_default_message(client):
    # SessionInvalid is a real AuthClientError subclass, just not one login_submit's error map
    # names -- any AuthClientError the fake (or a future real one) raises that isn't explicitly
    # mapped must still render a response, not crash the request.
    test_client, _, fake_auth, _ = client
    token = _login_csrf_token(test_client)
    fake_auth.login_error = SessionInvalid()

    response = test_client.post(
        "/login", data={"email": "a@example.com", "password": "x", "csrf_token": token}
    )

    assert response.status_code == 503
    assert "Can&#39;t reach the sign-in service" in response.text


def test_login_htmx_error_returns_partial_only(client):
    test_client, _, fake_auth, _ = client
    token = _login_csrf_token(test_client)
    fake_auth.login_error = InvalidCredentials("a@example.com")

    response = test_client.post(
        "/login",
        data={"email": "a@example.com", "password": "x", "csrf_token": token},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 401
    assert "That email and password don&#39;t match." in response.text
    assert "<html" not in response.text
    assert response.headers["X-Form-Error"] == "true"


def test_login_htmx_unmapped_exception_has_no_form_error_header(fake_auth, fake_portfolio):
    # htmx-error-swap.js forces every response carrying X-Form-Error into the form, bypassing
    # htmx's normal error handling -- this header must stay off anything _render_form_error
    # didn't build, or an unmapped crash (not just a mapped AuthClientError) would get silently
    # rendered into the form instead of surfacing as an htmx error. raise_server_exceptions=False
    # makes the TestClient behave like a real browser against an unhandled exception.
    settings = _settings()
    app = create_app(settings)
    app.dependency_overrides[get_auth_client] = lambda: fake_auth
    app.dependency_overrides[get_portfolio_client] = lambda: fake_portfolio
    with TestClient(app, raise_server_exceptions=False) as entered:
        token = _login_csrf_token(entered)
        fake_auth.login_error = RuntimeError("boom")

        response = entered.post(
            "/login",
            data={"email": "a@example.com", "password": "x", "csrf_token": token},
            headers={"HX-Request": "true"},
        )

    assert response.status_code == 500
    assert "X-Form-Error" not in response.headers


def test_login_htmx_success_returns_hx_redirect(client):
    test_client, _, fake_auth, _ = client
    token = _login_csrf_token(test_client)
    fake_auth.login_result = AuthResult(user=_logged_in_user(), set_cookie=SESSION_COOKIE)

    response = test_client.post(
        "/login",
        data={
            "email": "ann@example.com",
            "password": "hunter2hunter2",  # pragma: allowlist secret
            "csrf_token": token,
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert response.headers["hx-redirect"] == "/"
    assert "stakehold_session" in response.headers.get("set-cookie", "")


# --- signup ---------------------------------------------------------------


def test_signup_form_renders_with_csrf_cookie(client):
    test_client, _, _, _ = client

    response = test_client.get("/signup")

    assert response.status_code == 200
    assert "stakehold_csrf" in response.cookies


def test_signup_success_redirects_and_proxies_cookie(client):
    test_client, _, fake_auth, _ = client
    token = _signup_csrf_token(test_client)
    fake_auth.signup_result = AuthResult(user=_logged_in_user(), set_cookie=SESSION_COOKIE)

    response = test_client.post(
        "/signup",
        data={
            "email": "ann@example.com",
            "password": "hunter2hunter2",  # pragma: allowlist secret
            "display_name": "Ann",
            "csrf_token": token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "stakehold_session" in response.headers.get("set-cookie", "")


def test_signup_email_already_registered(client):
    test_client, _, fake_auth, _ = client
    token = _signup_csrf_token(test_client)
    fake_auth.signup_error = EmailAlreadyRegistered("ann@example.com")

    response = test_client.post(
        "/signup",
        data={
            "email": "ann@example.com",
            "password": "hunter2hunter2",  # pragma: allowlist secret
            "display_name": "Ann",
            "csrf_token": token,
        },
    )

    assert response.status_code == 409
    assert "already registered" in response.text


def test_signup_without_csrf_cookie_is_rejected(client):
    test_client, _, fake_auth, _ = client

    response = test_client.post(
        "/signup",
        data={
            "email": "ann@example.com",
            "password": "hunter2hunter2",  # pragma: allowlist secret
            "display_name": "Ann",
            "csrf_token": "anything",
        },
    )

    assert response.status_code == 400
    assert fake_auth.calls == []


# --- logout ---------------------------------------------------------------


def test_logout_clears_cookie_and_redirects(client):
    test_client, _, fake_auth, _ = client
    fake_auth.validate_result = AuthResult(user=_logged_in_user(), set_cookie=SESSION_COOKIE)
    test_client.cookies.set("stakehold_session", "tok")
    home_response = test_client.get("/")
    csrf_token = home_response.cookies["stakehold_csrf"]

    response = test_client.post("/logout", data={"csrf_token": csrf_token}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    set_cookie = response.headers.get("set-cookie", "")
    assert "stakehold_session=" in set_cookie
    # Must match auth's own set_session_cookie flags, or a browser holding a cookie set with
    # those flags won't accept the deleting Set-Cookie as clearing it.
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()
    logout_call = next(call for call in fake_auth.calls if call[0] == "logout")
    assert "stakehold_session=tok" in logout_call[1]


def test_logout_cookie_deletion_is_secure_when_cookie_secure_is_true(fake_auth):
    settings = _settings(cookie_secure=True)
    app = create_app(settings)
    app.dependency_overrides[get_auth_client] = lambda: fake_auth
    # https base_url: a Secure cookie (the CSRF cookie, here) is only stored/resent by httpx's
    # cookie jar over https, same reasoning as the integration test's web_client fixture.
    with TestClient(app, base_url="https://testserver") as entered:
        fake_auth.validate_result = AuthResult(user=_logged_in_user(), set_cookie=SESSION_COOKIE)
        entered.cookies.set("stakehold_session", "tok")
        home_response = entered.get("/")
        csrf_token = home_response.cookies["__Host-stakehold_csrf"]

        response = entered.post("/logout", data={"csrf_token": csrf_token}, follow_redirects=False)

    set_cookie = response.headers.get("set-cookie", "")
    assert "Secure" in set_cookie


def test_logout_without_csrf_is_rejected_without_calling_auth(client):
    test_client, _, fake_auth, _ = client

    response = test_client.post("/logout", data={"csrf_token": "wrong"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert fake_auth.calls == []


def test_logout_survives_auth_unavailable(client):
    test_client, _, fake_auth, _ = client
    fake_auth.validate_result = AuthResult(user=_logged_in_user(), set_cookie=SESSION_COOKIE)
    test_client.cookies.set("stakehold_session", "tok")
    home_response = test_client.get("/")
    csrf_token = home_response.cookies["stakehold_csrf"]
    fake_auth.logout_error = AuthUnavailable("boom")

    response = test_client.post("/logout", data={"csrf_token": csrf_token}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert "stakehold_session=" in response.headers.get("set-cookie", "")


# --- accounts: GET ---------------------------------------------------------------


def test_accounts_page_redirects_to_login_when_anonymous(client):
    test_client, _, _, _ = client

    response = test_client.get("/accounts", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_accounts_page_lists_accounts_and_brokers(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_accounts_result = [
        Account(id=1, account_label="main", broker="acme", created_at="2026-01-01T00:00:00Z")
    ]
    fake_portfolio.list_brokers_result = ["acme", "globex"]

    response = test_client.get("/accounts")

    assert response.status_code == 200
    assert "main" in response.text
    assert "acme" in response.text
    assert "stakehold_csrf" in response.cookies


def test_accounts_page_renders_even_when_portfolio_is_unavailable(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_accounts_error = PortfolioUnavailable("boom")
    fake_portfolio.list_brokers_error = PortfolioUnavailable("boom")

    response = test_client.get("/accounts")

    assert response.status_code == 200
    # A portfolio outage must not look identical to "you have no accounts" -- the visitor needs
    # to be able to tell data failed to load from having genuinely zero accounts.
    assert "Can&#39;t reach the portfolio service" in response.text


# --- accounts: POST ---------------------------------------------------------------


def test_create_account_without_csrf_cookie_is_rejected(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    response = test_client.post(
        "/accounts", data={"broker": "acme", "account_label": "main", "csrf_token": "anything"}
    )

    assert response.status_code == 400
    # The error re-render still needs the account/broker lists (read-only), but the CSRF failure
    # must still block the write.
    assert ("create_account", "u1", "acme", "main") not in fake_portfolio.calls


def test_create_account_success_redirects(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    token = _accounts_csrf_token(test_client)
    fake_portfolio.calls = []  # drop the GET /accounts page's own list_accounts/list_brokers
    fake_portfolio.create_account_result = AccountWriteResult(
        status="ok", rows_saved=1, accounts_created=1
    )

    response = test_client.post(
        "/accounts",
        data={"broker": "acme", "account_label": "main", "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/accounts"
    assert fake_portfolio.calls == [("create_account", _logged_in_user().user_id, "acme", "main")]


def test_create_account_label_conflict(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    token = _accounts_csrf_token(test_client)
    fake_portfolio.list_accounts_result = [
        Account(id=1, account_label="existing", broker="acme", created_at="2026-01-01T00:00:00Z")
    ]
    fake_portfolio.create_account_error = AccountLabelConflict("conflict")

    response = test_client.post(
        "/accounts", data={"broker": "acme", "account_label": "main", "csrf_token": token}
    )

    assert response.status_code == 409
    assert "already used with a different broker" in response.text
    # The error re-render must not silently drop the visitor's existing accounts or the
    # logged-in nav/logout chrome, even though this is a full-page (non-htmx) response.
    assert "existing" in response.text
    assert "site-nav" in response.text
    assert "logout-form" in response.text


def test_create_account_invalid_request_shows_server_message(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    token = _accounts_csrf_token(test_client)
    fake_portfolio.create_account_error = InvalidPortfolioRequest("broker must not be blank")

    response = test_client.post(
        "/accounts", data={"broker": "", "account_label": "main", "csrf_token": token}
    )

    assert response.status_code == 422
    assert "broker must not be blank" in response.text


def test_create_account_portfolio_unavailable(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    token = _accounts_csrf_token(test_client)
    fake_portfolio.create_account_error = PortfolioUnavailable("boom")

    response = test_client.post(
        "/accounts", data={"broker": "acme", "account_label": "main", "csrf_token": token}
    )

    assert response.status_code == 503
    assert "Can&#39;t reach the portfolio service" in response.text


def test_create_account_htmx_success_returns_hx_redirect(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    token = _accounts_csrf_token(test_client)
    fake_portfolio.create_account_result = AccountWriteResult(
        status="ok", rows_saved=1, accounts_created=1
    )

    response = test_client.post(
        "/accounts",
        data={"broker": "acme", "account_label": "main", "csrf_token": token},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert response.headers["hx-redirect"] == "/accounts"


# --- transactions/new: GET ---------------------------------------------------------------


def test_transaction_new_form_redirects_to_login_when_anonymous(client):
    test_client, _, _, _ = client

    response = test_client.get(
        "/transactions/new", params={"account": "main"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_transaction_new_form_requires_account_param(client):
    test_client, _, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = test_client.get("/transactions/new")

    assert response.status_code == 422


def test_transaction_new_form_renders_with_account_and_csrf(client):
    test_client, _, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = test_client.get("/transactions/new", params={"account": "main"})

    assert response.status_code == 200
    assert "main" in response.text
    assert "stakehold_csrf" in response.cookies


def test_transaction_new_form_shows_confirmation_banner_when_saved(client):
    test_client, _, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = test_client.get("/transactions/new", params={"account": "main", "saved": "1"})

    assert response.status_code == 200
    assert "saved" in response.text.lower()


def test_transaction_new_form_offers_data_stores_tickers(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_tickers_result = ["AAPL", "IWDA.AS"]

    response = test_client.get("/transactions/new", params={"account": "main"})

    assert response.status_code == 200
    assert 'list="ticker-options"' in response.text
    assert '<option value="IWDA.AS">' in response.text
    assert 'name="instrument_identifier_kind"' not in response.text


def test_transaction_new_form_still_renders_when_the_ticker_list_is_unavailable(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_tickers_error = PortfolioUnavailable("down")

    response = test_client.get("/transactions/new", params={"account": "main"})

    assert response.status_code == 200
    assert 'name="instrument_identifier"' in response.text
    assert "ticker list couldn&#39;t be loaded" in response.text


# --- transactions/new: POST ---------------------------------------------------------------


def _transaction_form_data(csrf_token: str, account_label: str = "main", **overrides) -> dict:
    data = {
        "account_label": account_label,
        "broker_transaction_ref": "ref-1",
        "transaction_type": "DEPOSIT",
        "trade_datetime": "2026-01-05T12:00",
        "native_amount": "100.00",
        "native_currency": "EUR",
        "instrument_identifier": "",
        "quantity": "",
        "price": "",
        "fx_rate": "",
        "fees_native": "",
        "csrf_token": csrf_token,
    }
    data.update(overrides)
    return data


def test_add_transaction_without_csrf_cookie_is_rejected(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    response = test_client.post("/transactions/new", data=_transaction_form_data("anything"))

    assert response.status_code == 400
    assert fake_portfolio.calls == []


def test_add_transaction_success_redirects_with_saved_flag(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    token = _transactions_new_csrf_token(test_client, "main")
    fake_portfolio.add_transaction_result = TransactionWriteResult(
        status="ok", rows_saved=1, transactions_saved=1
    )

    response = test_client.post(
        "/transactions/new",
        data=_transaction_form_data(token),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/transactions/new?account=main&saved=1"
    assert fake_portfolio.calls == [
        ("add_transaction", _logged_in_user().user_id, "main", "ref-1", "DEPOSIT")
    ]


def test_add_transaction_sends_an_instrument_as_a_ticker(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    token = _transactions_new_csrf_token(test_client, "main")
    fake_portfolio.add_transaction_result = TransactionWriteResult(
        status="ok", rows_saved=1, transactions_saved=1
    )

    test_client.post(
        "/transactions/new",
        data=_transaction_form_data(
            token,
            transaction_type="BUY",
            instrument_identifier=" IWDA.AS ",
            quantity="5",
            price="128.65",
        ),
        follow_redirects=False,
    )

    assert fake_portfolio.last_add_transaction["instrument_identifier"] == "IWDA.AS"
    assert fake_portfolio.last_add_transaction["instrument_identifier_kind"] == "TICKER"


def test_add_transaction_without_an_instrument_sends_no_kind(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    token = _transactions_new_csrf_token(test_client, "main")
    fake_portfolio.add_transaction_result = TransactionWriteResult(
        status="ok", rows_saved=1, transactions_saved=1
    )

    test_client.post(
        "/transactions/new", data=_transaction_form_data(token), follow_redirects=False
    )

    assert fake_portfolio.last_add_transaction["instrument_identifier"] is None
    assert fake_portfolio.last_add_transaction["instrument_identifier_kind"] is None


def test_add_transaction_leaves_a_blank_amount_for_portfolio_to_derive(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    token = _transactions_new_csrf_token(test_client, "main")
    fake_portfolio.add_transaction_result = TransactionWriteResult(
        status="ok", rows_saved=1, transactions_saved=1
    )

    response = test_client.post(
        "/transactions/new",
        data=_transaction_form_data(
            token,
            transaction_type="BUY",
            instrument_identifier="IWDA.AS",
            native_amount="",
            quantity="5",
            price="128.65",
        ),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert fake_portfolio.last_add_transaction["native_amount"] is None


def test_transaction_new_form_explains_that_a_trades_amount_is_calculated(client):
    test_client, _, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = test_client.get("/transactions/new", params={"account": "main"})

    assert "leave blank for buys and sells" in response.text
    assert 'name="native_amount" required' not in response.text


def test_add_transaction_error_rerender_keeps_the_ticker_list(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    token = _transactions_new_csrf_token(test_client, "main")
    fake_portfolio.list_tickers_result = ["IWDA.AS"]
    fake_portfolio.add_transaction_error = InvalidPortfolioRequest(
        "IWDA.AM isn't in Stakehold's market data -- pick a ticker from the list."
    )

    response = test_client.post(
        "/transactions/new",
        data=_transaction_form_data(token, instrument_identifier="IWDA.AM"),
    )

    assert response.status_code == 422
    assert "isn&#39;t in Stakehold&#39;s market data" in response.text
    assert '<option value="IWDA.AS">' in response.text


def test_add_transaction_invalid_datetime_is_rejected_before_calling_portfolio(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    token = _transactions_new_csrf_token(test_client, "main")

    response = test_client.post(
        "/transactions/new",
        data=_transaction_form_data(token, trade_datetime="not-a-date"),
    )

    assert response.status_code == 400
    assert fake_portfolio.calls == []


def test_add_transaction_converts_non_utc_offset_to_utc(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    token = _transactions_new_csrf_token(test_client, "main")
    fake_portfolio.add_transaction_result = TransactionWriteResult(
        status="ok", rows_saved=1, transactions_saved=1
    )

    response = test_client.post(
        "/transactions/new",
        data=_transaction_form_data(token, trade_datetime="2026-01-05T23:30:00+05:00"),
        follow_redirects=False,
    )

    assert response.status_code == 303
    # A +05:00 offset must be converted to UTC (18:30), not have its offset silently discarded.
    assert fake_portfolio.last_add_transaction["trade_datetime"] == datetime(
        2026, 1, 5, 18, 30, tzinfo=timezone.utc
    )


def test_add_transaction_rejects_non_finite_amount(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    token = _transactions_new_csrf_token(test_client, "main")

    response = test_client.post(
        "/transactions/new",
        data=_transaction_form_data(token, native_amount="NaN"),
    )

    assert response.status_code == 400
    assert fake_portfolio.calls == []


def test_add_transaction_account_not_found(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    token = _transactions_new_csrf_token(test_client, "main")
    fake_portfolio.add_transaction_error = AccountNotFound("missing")

    response = test_client.post("/transactions/new", data=_transaction_form_data(token))

    assert response.status_code == 404
    assert "doesn&#39;t exist" in response.text
    # A full-page error re-render must not silently log the user out of the nav/logout chrome.
    assert "site-nav" in response.text
    assert "logout-form" in response.text


def test_add_transaction_invalid_request_shows_server_message(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    token = _transactions_new_csrf_token(test_client, "main")
    fake_portfolio.add_transaction_error = InvalidPortfolioRequest(
        "fx_rate is required for non-EUR currency"
    )

    response = test_client.post("/transactions/new", data=_transaction_form_data(token))

    assert response.status_code == 422
    assert "fx_rate is required for non-EUR currency" in response.text


# --- transactions/import: GET ---------------------------------------------------------------


def test_transaction_import_form_requires_account_param(client):
    test_client, _, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = test_client.get("/transactions/import")

    assert response.status_code == 422


def test_transaction_import_form_renders_with_account_and_csrf(client):
    test_client, _, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = test_client.get("/transactions/import", params={"account": "main"})

    assert response.status_code == 200
    assert "main" in response.text
    assert "stakehold_csrf" in response.cookies


def test_transaction_import_form_shows_confirmation_with_row_count(client):
    test_client, _, fake_auth, _ = client
    _logged_in(test_client, fake_auth)

    response = test_client.get("/transactions/import", params={"account": "main", "imported": "3"})

    assert response.status_code == 200
    assert "3" in response.text


# --- transactions/import: POST ---------------------------------------------------------------


def test_import_transactions_without_csrf_cookie_is_rejected(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)

    response = test_client.post(
        "/transactions/import",
        data={"account_label": "main", "csrf_token": "anything"},
        files={"file": ("export.csv", b"a,b\n1,2\n", "text/csv")},
    )

    assert response.status_code == 400
    assert fake_portfolio.calls == []


def test_import_transactions_success_redirects_with_row_count(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    token = _transactions_import_csrf_token(test_client, "main")
    fake_portfolio.import_transactions_result = TransactionWriteResult(
        status="ok", rows_saved=5, transactions_saved=5
    )

    response = test_client.post(
        "/transactions/import",
        data={"account_label": "main", "csrf_token": token},
        files={"file": ("export.csv", b"a,b\n1,2\n", "text/csv")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/transactions/import?account=main&imported=5"
    assert fake_portfolio.calls == [
        ("import_transactions", _logged_in_user().user_id, "main", "export.csv")
    ]


def test_import_transactions_broker_parser_not_found(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    token = _transactions_import_csrf_token(test_client, "main")
    fake_portfolio.import_transactions_error = InvalidPortfolioRequest(
        "no parser registered for broker 'acme'"
    )

    response = test_client.post(
        "/transactions/import",
        data={"account_label": "main", "csrf_token": token},
        files={"file": ("export.csv", b"a,b\n1,2\n", "text/csv")},
    )

    assert response.status_code == 422
    assert "no parser registered" in response.text


def test_import_transactions_account_not_found(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    token = _transactions_import_csrf_token(test_client, "main")
    fake_portfolio.import_transactions_error = AccountNotFound("missing")

    response = test_client.post(
        "/transactions/import",
        data={"account_label": "main", "csrf_token": token},
        files={"file": ("export.csv", b"a,b\n1,2\n", "text/csv")},
    )

    assert response.status_code == 404
    assert "doesn&#39;t exist" in response.text
    # A full-page error re-render must not silently log the user out of the nav/logout chrome.
    assert "site-nav" in response.text
    assert "logout-form" in response.text


# --- accounts: per-account "View portfolio" link -----------------------------


def test_accounts_page_links_to_portfolio_view(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_accounts_result = [
        Account(id=1, account_label="main", broker="acme", created_at="2026-01-01T00:00:00Z")
    ]

    response = test_client.get("/accounts")

    assert response.status_code == 200
    assert '/portfolio?account=main">View portfolio</a>' in response.text


# --- portfolio view: GET /portfolio -------------------------------------------


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


def _return_row(*, ticker="AAPL", price_date=date(2026, 1, 5)) -> Return:
    return Return(
        account_id=1,
        canonical_ticker=ticker,
        price_date=price_date,
        quantity=Decimal("10"),
        market_value_native=Decimal("1600"),
        market_value_eur=Decimal("1600"),
        cost_basis_eur=Decimal("1500"),
        unrealized_return_eur=Decimal("100"),
        unrealized_return_pct=Decimal("0.06666667"),
        dividends_received_eur=Decimal("0"),
        total_return_eur=Decimal("100"),
        total_return_pct=Decimal("0.06666667"),
    )


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


def test_portfolio_page_renders_positions_and_returns(client):
    test_client, _, fake_auth, fake_portfolio = client
    _logged_in(test_client, fake_auth)
    fake_portfolio.list_positions_result = [_position()]
    fake_portfolio.list_returns_result = [_return_row()]

    response = test_client.get("/portfolio", params={"account": "main"})

    assert response.status_code == 200
    assert "AAPL" in response.text
    assert 'id="returns-chart"' in response.text
    assert 'id="returns-chart-data"' in response.text
    assert "100" in response.text  # the total return figure
    assert fake_portfolio.calls == [
        ("list_positions", _logged_in_user().user_id, "main"),
        ("list_returns", _logged_in_user().user_id, "main"),
    ]


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
