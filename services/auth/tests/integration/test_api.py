import os

import psycopg
import pytest
from auth import cli
from auth.api.app import create_app
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _connect():
    return psycopg.connect(os.environ["AUTH_DATABASE_URL"])


def _internal_secret():
    return os.environ["AUTH_INTERNAL_SECRET"]


@pytest.fixture(autouse=True)
def _clean_tables():
    cli.main(["db", "init"])
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE sessions, users CASCADE")
        conn.commit()
    yield


@pytest.fixture
def client():
    # https, not the default http://testserver -- production's default AUTH_COOKIE_SECURE=true
    # marks the session cookie Secure, and httpx's cookie jar (correctly) won't re-send a Secure
    # cookie on a later request over plain http, which would make every round-trip test fail for
    # a reason that has nothing to do with auth's own logic.
    with TestClient(create_app(), base_url="https://testserver") as test_client:
        yield test_client


def _signup(client, email, **overrides):
    body = dict(email=email, password="correct horse battery staple", display_name="Person")
    body.update(overrides)
    return client.post("/signup", json=body, headers={"X-Internal-Secret": _internal_secret()})


def test_signup_login_validate_logout_round_trip(client):
    secret = _internal_secret()

    signup_response = _signup(client, "a@example.com")
    assert signup_response.status_code == 201

    validate_response = client.post("/sessions/validate", headers={"X-Internal-Secret": secret})
    assert validate_response.status_code == 200
    assert validate_response.json()["email"] == "a@example.com"

    logout_response = client.post("/logout", headers={"X-Internal-Secret": secret})
    assert logout_response.status_code == 204

    post_logout = client.post("/sessions/validate", headers={"X-Internal-Secret": secret})
    assert post_logout.status_code == 401


def test_duplicate_email_returns_409_not_a_raw_db_error(client):
    _signup(client, "dup@example.com")

    response = _signup(client, "dup@example.com")

    assert response.status_code == 409


def test_login_round_trip_after_signup(client):
    secret = _internal_secret()
    _signup(client, "login@example.com")
    client.cookies.clear()

    response = client.post(
        "/login",
        json={"email": "login@example.com", "password": "correct horse battery staple"},
        headers={"X-Internal-Secret": secret},
    )

    assert response.status_code == 200
    assert response.json()["email"] == "login@example.com"


def test_sliding_renewal_moves_expiry_but_never_the_absolute_cap(client):
    secret = _internal_secret()
    _signup(client, "renew@example.com")

    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT expires_at, absolute_expires_at FROM sessions")
        before_expires_at, before_absolute = cur.fetchone()

    client.post("/sessions/validate", headers={"X-Internal-Secret": secret})

    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT expires_at, absolute_expires_at FROM sessions")
        after_expires_at, after_absolute = cur.fetchone()

    assert after_expires_at >= before_expires_at
    assert after_absolute == before_absolute


def test_renewal_clamps_expiry_to_the_absolute_cap(client):
    secret = _internal_secret()
    _signup(client, "clamp@example.com")

    with _connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE sessions SET absolute_expires_at = now() + interval '1 hour'")
        conn.commit()

    client.post("/sessions/validate", headers={"X-Internal-Secret": secret})

    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT expires_at, absolute_expires_at FROM sessions")
        expires_at, absolute_expires_at = cur.fetchone()

    # Sliding TTL defaults to 14 days -- without the clamp this would be ~14 days out, not equal
    # to the absolute cap set 1 hour from signup.
    assert expires_at == absolute_expires_at


def test_purge_expired_deletes_only_expired_sessions(client):
    secret = _internal_secret()
    _signup(client, "expired@example.com")
    _signup(client, "still-live@example.com")

    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE sessions SET expires_at = now() - interval '1 minute' "
            "WHERE user_id = (SELECT id FROM users WHERE email = 'expired@example.com')"
        )
        conn.commit()

    exit_code = cli.main(["sessions", "purge-expired"])
    assert exit_code == 0

    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM sessions")
        remaining = cur.fetchone()[0]
    assert remaining == 1
