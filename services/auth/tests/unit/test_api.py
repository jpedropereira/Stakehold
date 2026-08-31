"""HTTP-layer tests: status codes, cookie flags, and that internal-secret enforcement works --
using the fake in-memory repositories (the `repos` fixture, tests/unit/conftest.py) so the
real service, real dependencies, and real exception handlers all run without a Postgres
connection. Only the connection pool itself is faked (pool.open() would otherwise try to
connect for real during the app's lifespan)."""

import pytest
from auth.api.app import create_app
from auth.settings import Settings
from auth.throttle import AttemptThrottle
from fastapi.testclient import TestClient

PASSWORD = "correct horse battery staple"  # pragma: allowlist secret


class _FakeConnectionContext:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, conn=None, connection_error: Exception | None = None):
        self.conn = conn
        self._connection_error = connection_error

    def open(self, wait=True):
        pass

    def close(self):
        pass

    def connection(self):
        if self._connection_error is not None:
            raise self._connection_error
        return _FakeConnectionContext(self.conn)


def _settings(**overrides):
    kwargs = dict(
        database_url="postgresql://unused",
        internal_secret="test-secret",  # pragma: allowlist secret
        session_ttl_days=14,
        session_absolute_ttl_days=90,
        cookie_secure=False,
        api_host="127.0.0.1",
        api_port=8001,
        pool_min_size=1,
        pool_max_size=5,
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


def _build_client(monkeypatch, pool, **setting_overrides):
    monkeypatch.setattr("auth.api.app.connection_pool", lambda *a, **k: pool)
    settings = _settings(**setting_overrides)
    return TestClient(create_app(settings)), settings


@pytest.fixture
def healthy_pool(conn):
    return _FakePool(conn=conn)


@pytest.fixture
def client(repos, monkeypatch, healthy_pool):
    test_client, settings = _build_client(monkeypatch, healthy_pool)
    with test_client as entered:
        yield entered, settings


def _signup(test_client, settings, **overrides):
    headers = {"X-Internal-Secret": settings.internal_secret}
    headers.update(overrides.pop("headers", {}))
    body = dict(email="person@example.com", password=PASSWORD, display_name="Person")
    body.update(overrides)
    return test_client.post("/signup", json=body, headers=headers)


def _login(test_client, settings, **overrides):
    headers = {"X-Internal-Secret": settings.internal_secret}
    headers.update(overrides.pop("headers", {}))
    body = dict(email="person@example.com", password=PASSWORD)
    body.update(overrides)
    return test_client.post("/login", json=body, headers=headers)


def test_health_is_reachable_without_the_internal_secret(client):
    test_client, _ = client

    response = test_client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_reports_503_when_the_schema_has_not_been_applied(
    repos, monkeypatch, make_connection
):
    # `auth db init` never having run must not look healthy: without this the container reports
    # ready while every endpoint 500s on `relation "users" does not exist`.
    pool = _FakePool(conn=make_connection(health_row=(None, None)))
    test_client, _ = _build_client(monkeypatch, pool)

    with test_client as entered:
        response = entered.get("/health")

    assert response.status_code == 503


def test_health_reports_503_when_the_database_is_unreachable(repos, monkeypatch):
    pool = _FakePool(connection_error=RuntimeError("pool exhausted"))
    test_client, _ = _build_client(monkeypatch, pool)

    with test_client as entered:
        response = entered.get("/health")

    assert response.status_code == 503


def test_signup_without_internal_secret_is_rejected(client):
    test_client, _ = client

    response = test_client.post(
        "/signup",
        json={"email": "a@example.com", "password": PASSWORD, "display_name": "A"},
    )

    assert response.status_code == 401


def test_signup_with_wrong_internal_secret_is_rejected(client):
    test_client, _ = client

    response = test_client.post(
        "/signup",
        json={"email": "a@example.com", "password": PASSWORD, "display_name": "A"},
        headers={"X-Internal-Secret": "not-the-secret"},  # pragma: allowlist secret
    )

    assert response.status_code == 401


def test_non_ascii_internal_secret_is_rejected_with_401_not_a_500(client):
    # Starlette decodes header bytes as latin-1, so a raw 0xE9 byte arrives as U+00E9 --
    # secrets.compare_digest raises TypeError on any str above ASCII, turning a bad secret into
    # a 500 (and an unauthenticated caller into a crash vector).
    test_client, _ = client

    response = test_client.post(
        "/signup",
        json={"email": "a@example.com", "password": PASSWORD, "display_name": "A"},
        headers={"X-Internal-Secret": "sécret".encode("latin-1")},  # pragma: allowlist secret
    )

    assert response.status_code == 401


def test_signup_returns_201_with_the_user_and_sets_the_session_cookie(client):
    test_client, settings = client

    response = _signup(test_client, settings)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "person@example.com"
    assert body["display_name"] == "Person"
    assert "password" not in body
    assert "token" not in body

    set_cookie = response.headers.get("set-cookie", "").lower()
    assert "stakehold_session=" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "path=/" in set_cookie


def test_signup_sets_secure_flag_when_configured(repos, monkeypatch, healthy_pool):
    test_client, settings = _build_client(monkeypatch, healthy_pool, cookie_secure=True)

    with test_client as entered:
        response = _signup(entered, settings)

    assert "secure" in response.headers.get("set-cookie", "").lower()


def test_signup_normalizes_email_to_lowercase(client):
    test_client, settings = client

    response = _signup(test_client, settings, email="Person@Example.COM")

    assert response.json()["email"] == "person@example.com"


def test_signup_duplicate_email_returns_409(client):
    test_client, settings = client
    _signup(test_client, settings)

    response = _signup(test_client, settings, display_name="Someone Else")

    assert response.status_code == 409


def test_repeated_signups_from_one_client_eventually_return_429(client):
    # Unthrottled, /signup is both an unlimited 409-based account-enumeration oracle and an
    # unauthenticated Argon2 (64 MiB, 40 threads) exhaustion vector.
    test_client, settings = client
    test_client.app.state.signup_throttle = AttemptThrottle(max_attempts=2)

    _signup(test_client, settings, email="a@example.com")
    _signup(test_client, settings, email="b@example.com")
    response = _signup(test_client, settings, email="c@example.com")

    assert response.status_code == 429


def test_signup_throttle_keys_on_the_forwarded_client_ip(client):
    test_client, settings = client
    test_client.app.state.signup_throttle = AttemptThrottle(max_attempts=1)

    _signup(
        test_client, settings, email="a@example.com", headers={"X-Forwarded-For": "203.0.113.1"}
    )
    response = _signup(
        test_client, settings, email="b@example.com", headers={"X-Forwarded-For": "203.0.113.2"}
    )

    assert response.status_code == 201


def test_login_with_correct_password_returns_200_and_sets_cookie(client):
    test_client, settings = client
    _signup(test_client, settings)
    test_client.cookies.clear()

    response = _login(test_client, settings)

    assert response.status_code == 200
    assert "stakehold_session=" in response.headers.get("set-cookie", "")


def test_login_with_wrong_password_returns_401(client):
    test_client, settings = client
    _signup(test_client, settings)
    test_client.cookies.clear()

    response = _login(test_client, settings, password="wrong password")  # pragma: allowlist secret

    assert response.status_code == 401


def test_login_with_unknown_email_returns_the_same_401_as_wrong_password(client):
    test_client, settings = client

    response = _login(
        test_client,
        settings,
        email="nobody@example.com",
        password="whatever12345",  # pragma: allowlist secret
    )

    assert response.status_code == 401


def test_repeated_failed_logins_eventually_return_429(client):
    test_client, settings = client
    _signup(test_client, settings)
    test_client.cookies.clear()
    test_client.app.state.login_throttle = AttemptThrottle(max_attempts=2)

    responses = [
        _login(test_client, settings, password="wrong password")  # pragma: allowlist secret
        for _ in range(3)
    ]

    assert responses[-1].status_code == 429


def test_login_throttle_keys_on_the_forwarded_client_ip(client):
    # Every request reaches auth from `web`'s container address, so without X-Forwarded-For the
    # throttle key collapses to the email alone and anyone can lock a known user out.
    test_client, settings = client
    _signup(test_client, settings)
    test_client.cookies.clear()
    test_client.app.state.login_throttle = AttemptThrottle(max_attempts=2)

    for _ in range(3):
        _login(
            test_client,
            settings,
            password="wrong password",  # pragma: allowlist secret
            headers={"X-Forwarded-For": "203.0.113.1"},
        )

    response = _login(test_client, settings, headers={"X-Forwarded-For": "203.0.113.2"})

    assert response.status_code == 200


def test_validate_session_returns_200_for_a_freshly_signed_up_session(client):
    test_client, settings = client
    _signup(test_client, settings)

    response = test_client.post(
        "/sessions/validate", headers={"X-Internal-Secret": settings.internal_secret}
    )

    assert response.status_code == 200
    assert response.json()["email"] == "person@example.com"


def test_validate_session_without_a_cookie_returns_401(client):
    test_client, settings = client

    response = test_client.post(
        "/sessions/validate", headers={"X-Internal-Secret": settings.internal_secret}
    )

    assert response.status_code == 401


def test_logout_clears_the_cookie_and_returns_204(client):
    test_client, settings = client
    _signup(test_client, settings)

    response = test_client.post("/logout", headers={"X-Internal-Secret": settings.internal_secret})

    assert response.status_code == 204

    follow_up = test_client.post(
        "/sessions/validate", headers={"X-Internal-Secret": settings.internal_secret}
    )
    assert follow_up.status_code == 401


def test_logout_without_a_cookie_is_idempotent(client):
    test_client, settings = client

    response = test_client.post("/logout", headers={"X-Internal-Secret": settings.internal_secret})

    assert response.status_code == 204
