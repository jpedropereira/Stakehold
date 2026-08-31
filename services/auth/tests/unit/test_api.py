"""HTTP-layer tests: status codes, cookie flags, and that internal-secret enforcement works --
using the fake in-memory repositories (the `repos` fixture, tests/unit/conftest.py) so the
real service, real dependencies, and real exception handlers all run without a Postgres
connection. Only the connection pool itself is faked (pool.open() would otherwise try to
connect for real during the app's lifespan)."""

import pytest
from auth.api.app import create_app
from auth.settings import Settings
from fastapi.testclient import TestClient


class _FakeConnectionContext:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


class _FakePool:
    def open(self, wait=True):
        pass

    def close(self):
        pass

    def connection(self):
        return _FakeConnectionContext()


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


@pytest.fixture
def client(repos, monkeypatch):
    monkeypatch.setattr("auth.api.app.connection_pool", lambda *a, **k: _FakePool())
    settings = _settings()
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client, settings


def _signup(test_client, settings, **overrides):
    body = dict(
        email="person@example.com",
        password="correct horse battery staple",  # pragma: allowlist secret
        display_name="Person",
    )
    body.update(overrides)
    return test_client.post(
        "/signup", json=body, headers={"X-Internal-Secret": settings.internal_secret}
    )


def test_health_is_reachable_without_the_internal_secret(client):
    test_client, _ = client

    response = test_client.get("/health")

    assert response.status_code == 200


def test_signup_without_internal_secret_is_rejected(client):
    test_client, _ = client

    response = test_client.post(
        "/signup",
        json={
            "email": "a@example.com",
            "password": "correct horse battery staple",  # pragma: allowlist secret
            "display_name": "A",
        },
    )

    assert response.status_code == 401


def test_signup_with_wrong_internal_secret_is_rejected(client):
    test_client, _ = client

    response = test_client.post(
        "/signup",
        json={
            "email": "a@example.com",
            "password": "correct horse battery staple",  # pragma: allowlist secret
            "display_name": "A",
        },
        headers={"X-Internal-Secret": "not-the-secret"},  # pragma: allowlist secret
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


def test_signup_sets_secure_flag_when_configured(repos, monkeypatch):
    monkeypatch.setattr("auth.api.app.connection_pool", lambda *a, **k: _FakePool())
    settings = _settings(cookie_secure=True)
    app = create_app(settings)

    with TestClient(app) as test_client:
        response = _signup(test_client, settings)

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


def test_login_with_correct_password_returns_200_and_sets_cookie(client):
    test_client, settings = client
    _signup(test_client, settings)
    test_client.cookies.clear()

    response = test_client.post(
        "/login",
        json={
            "email": "person@example.com",
            "password": "correct horse battery staple",  # pragma: allowlist secret
        },
        headers={"X-Internal-Secret": settings.internal_secret},
    )

    assert response.status_code == 200
    assert "stakehold_session=" in response.headers.get("set-cookie", "")


def test_login_with_wrong_password_returns_401(client):
    test_client, settings = client
    _signup(test_client, settings)
    test_client.cookies.clear()

    response = test_client.post(
        "/login",
        json={
            "email": "person@example.com",
            "password": "wrong password",  # pragma: allowlist secret
        },
        headers={"X-Internal-Secret": settings.internal_secret},
    )

    assert response.status_code == 401


def test_login_with_unknown_email_returns_the_same_401_as_wrong_password(client):
    test_client, settings = client

    response = test_client.post(
        "/login",
        json={
            "email": "nobody@example.com",
            "password": "whatever12345",  # pragma: allowlist secret
        },
        headers={"X-Internal-Secret": settings.internal_secret},
    )

    assert response.status_code == 401


def test_repeated_failed_logins_eventually_return_429(client):
    test_client, settings = client
    _signup(test_client, settings)
    test_client.cookies.clear()

    responses = [
        test_client.post(
            "/login",
            json={
                "email": "person@example.com",
                "password": "wrong password",  # pragma: allowlist secret
            },
            headers={"X-Internal-Secret": settings.internal_secret},
        )
        for _ in range(6)
    ]

    assert responses[-1].status_code == 429


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
