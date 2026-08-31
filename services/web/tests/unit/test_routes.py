"""Route-level tests using a fake AuthClient (no live auth service), the same fake-dependency
approach auth's own tests/unit/test_api.py uses for its repositories."""

import pytest
from fastapi.testclient import TestClient
from web import settings as settings_module
from web.api.app import create_app
from web.api.deps import get_auth_client
from web.auth_client.base import AuthResult, AuthUser
from web.auth_client.errors import (
    AuthUnavailable,
    EmailAlreadyRegistered,
    InvalidCredentials,
    InvalidRequest,
    RateLimited,
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

    def signup(self, *, email, password, display_name, client_ip):
        self.calls.append(("signup", email, display_name, client_ip))
        if self.signup_error:
            raise self.signup_error
        return self.signup_result

    def login(self, *, email, password, client_ip):
        self.calls.append(("login", email, client_ip))
        if self.login_error:
            raise self.login_error
        return self.login_result

    def validate_session(self, *, cookie_header):
        self.calls.append(("validate_session", cookie_header))
        if self.validate_error:
            raise self.validate_error
        return self.validate_result

    def logout(self, *, cookie_header):
        self.calls.append(("logout", cookie_header))
        if self.logout_error:
            raise self.logout_error


def _settings(**overrides) -> Settings:
    kwargs = dict(
        auth_api_url="http://auth.internal",
        internal_secret="test-secret",  # pragma: allowlist secret
        brand_dir=settings_module._DEFAULT_BRAND_DIR,
        cookie_secure=False,
        api_host="127.0.0.1",
        api_port=8000,
        http_timeout_seconds=5.0,
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


def _build_client(fake_auth_client, **setting_overrides):
    settings = _settings(**setting_overrides)
    app = create_app(settings)
    app.dependency_overrides[get_auth_client] = lambda: fake_auth_client
    return TestClient(app), settings


@pytest.fixture
def fake_auth():
    return FakeAuthClient()


@pytest.fixture
def client(fake_auth):
    test_client, settings = _build_client(fake_auth)
    with test_client as entered:
        yield entered, settings, fake_auth


def _login_csrf_token(test_client):
    response = test_client.get("/login")
    return response.cookies["stakehold_csrf"]


def _signup_csrf_token(test_client):
    response = test_client.get("/signup")
    return response.cookies["stakehold_csrf"]


def _logged_in_user():
    return AuthUser(user_id="u1", email="ann@example.com", display_name="Ann")


# --- health -----------------------------------------------------------------


def test_health(client):
    test_client, _, _ = client

    response = test_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- anonymous root -----------------------------------------------------------


def test_root_redirects_to_login_when_anonymous(client):
    test_client, _, _ = client

    response = test_client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_root_renders_when_logged_in(client):
    test_client, _, fake_auth = client
    fake_auth.validate_result = AuthResult(user=_logged_in_user(), set_cookie=SESSION_COOKIE)
    test_client.cookies.set("stakehold_session", "tok")

    response = test_client.get("/")

    assert response.status_code == 200
    assert "Welcome, Ann." in response.text


def test_root_proxies_renewed_session_cookie(client):
    test_client, _, fake_auth = client
    fake_auth.validate_result = AuthResult(user=_logged_in_user(), set_cookie=SESSION_COOKIE)
    test_client.cookies.set("stakehold_session", "tok")

    response = test_client.get("/")

    assert "stakehold_session" in response.headers.get("set-cookie", "")


# --- login: GET ---------------------------------------------------------------


def test_login_form_renders_with_csrf_cookie(client):
    test_client, _, _ = client

    response = test_client.get("/login")

    assert response.status_code == 200
    assert "stakehold_csrf" in response.cookies


def test_login_form_redirects_when_already_logged_in(client):
    test_client, _, fake_auth = client
    fake_auth.validate_result = AuthResult(user=_logged_in_user(), set_cookie=SESSION_COOKIE)
    test_client.cookies.set("stakehold_session", "tok")

    response = test_client.get("/login", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


# --- login: POST ---------------------------------------------------------------


def test_login_without_csrf_cookie_is_rejected(client):
    test_client, _, fake_auth = client

    response = test_client.post(
        "/login", data={"email": "a@example.com", "password": "x", "csrf_token": "anything"}
    )

    assert response.status_code == 400
    assert fake_auth.calls == []


def test_login_with_wrong_csrf_field_is_rejected(client):
    test_client, _, fake_auth = client
    _login_csrf_token(test_client)

    response = test_client.post(
        "/login",
        data={"email": "a@example.com", "password": "x", "csrf_token": "wrong-token"},
    )

    assert response.status_code == 400
    assert fake_auth.calls == []


def test_login_success_redirects_and_proxies_cookie(client):
    test_client, _, fake_auth = client
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
    test_client, _, fake_auth = client
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
    test_client, _, fake_auth = client
    token = _login_csrf_token(test_client)
    fake_auth.login_error = RateLimited("login")

    response = test_client.post(
        "/login", data={"email": "a@example.com", "password": "x", "csrf_token": token}
    )

    assert response.status_code == 429
    assert "Too many attempts" in response.text


def test_login_invalid_request(client):
    test_client, _, fake_auth = client
    token = _login_csrf_token(test_client)
    fake_auth.login_error = InvalidRequest("login")

    response = test_client.post(
        "/login", data={"email": "a@example.com", "password": "x", "csrf_token": token}
    )

    assert response.status_code == 400
    assert "Check your email and password" in response.text


def test_login_auth_unavailable(client):
    test_client, _, fake_auth = client
    token = _login_csrf_token(test_client)
    fake_auth.login_error = AuthUnavailable("boom")

    response = test_client.post(
        "/login", data={"email": "a@example.com", "password": "x", "csrf_token": token}
    )

    assert response.status_code == 503
    assert "Can&#39;t reach the sign-in service" in response.text


def test_login_htmx_error_returns_partial_only(client):
    test_client, _, fake_auth = client
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


def test_login_htmx_success_returns_hx_redirect(client):
    test_client, _, fake_auth = client
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
    test_client, _, _ = client

    response = test_client.get("/signup")

    assert response.status_code == 200
    assert "stakehold_csrf" in response.cookies


def test_signup_success_redirects_and_proxies_cookie(client):
    test_client, _, fake_auth = client
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
    test_client, _, fake_auth = client
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
    test_client, _, fake_auth = client

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
    test_client, _, fake_auth = client
    fake_auth.validate_result = AuthResult(user=_logged_in_user(), set_cookie=SESSION_COOKIE)
    test_client.cookies.set("stakehold_session", "tok")
    home_response = test_client.get("/")
    csrf_token = home_response.cookies["stakehold_csrf"]

    response = test_client.post("/logout", data={"csrf_token": csrf_token}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    set_cookie = response.headers.get("set-cookie", "")
    assert "stakehold_session=" in set_cookie
    logout_call = next(call for call in fake_auth.calls if call[0] == "logout")
    assert "stakehold_session=tok" in logout_call[1]


def test_logout_without_csrf_is_rejected_without_calling_auth(client):
    test_client, _, fake_auth = client

    response = test_client.post("/logout", data={"csrf_token": "wrong"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert fake_auth.calls == []


def test_logout_survives_auth_unavailable(client):
    test_client, _, fake_auth = client
    fake_auth.validate_result = AuthResult(user=_logged_in_user(), set_cookie=SESSION_COOKIE)
    test_client.cookies.set("stakehold_session", "tok")
    home_response = test_client.get("/")
    csrf_token = home_response.cookies["stakehold_csrf"]
    fake_auth.logout_error = AuthUnavailable("boom")

    response = test_client.post("/logout", data={"csrf_token": csrf_token}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert "stakehold_session=" in response.headers.get("set-cookie", "")
