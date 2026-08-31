"""End-to-end signup/login/logout through both services' real code.

`HttpAuthClient` is pointed at auth's real ASGI app via a Starlette `TestClient` (itself an
`httpx.Client` subclass) instead of a network call -- this exercises the actual HTTP contract
(status codes, headers, Set-Cookie proxying) against auth's live Postgres, with no extra
container beyond the one CI/local dev already brings up for auth's own integration suite.
"""

import os
from pathlib import Path

import psycopg
import pytest
from auth import cli as auth_cli
from auth.api.app import create_app as create_auth_app
from fastapi.testclient import TestClient
from web.api.app import create_app as create_web_app
from web.api.deps import get_auth_client
from web.auth_client.http_client import HttpAuthClient
from web.settings import Settings

pytestmark = pytest.mark.integration

_BRAND_DIR = Path(__file__).resolve().parents[4] / "brand"
PASSWORD = "correct horse battery staple"  # pragma: allowlist secret


def _auth_connect():
    return psycopg.connect(os.environ["AUTH_DATABASE_URL"])


@pytest.fixture(autouse=True)
def _clean_auth_tables():
    auth_cli.main(["db", "init"])
    with _auth_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE sessions, users CASCADE")
        conn.commit()
    yield


def _web_settings() -> Settings:
    return Settings(
        auth_api_url="https://auth.internal",
        internal_secret=os.environ["AUTH_INTERNAL_SECRET"],
        brand_dir=_BRAND_DIR,
        cookie_secure=True,
        api_host="127.0.0.1",
        api_port=8000,
        http_timeout_seconds=5.0,
    )


@pytest.fixture
def web_client():
    settings = _web_settings()
    web_app = create_web_app(settings)

    # https base_url on both TestClients: auth's default AUTH_COOKIE_SECURE=true marks its
    # session cookie Secure, and web's own settings.cookie_secure=True does the same for its
    # CSRF cookie -- httpx's cookie jar (correctly) won't re-send a Secure cookie over plain
    # http, which would break every round trip for a reason unrelated to either service's logic.
    with TestClient(create_auth_app(), base_url=settings.auth_api_url) as auth_test_client:
        real_auth_client = HttpAuthClient(auth_test_client, settings.internal_secret)
        web_app.dependency_overrides[get_auth_client] = lambda: real_auth_client

        with TestClient(web_app, base_url="https://testserver") as web_test_client:
            yield web_test_client


def _csrf_token(response) -> str:
    return next(v for k, v in response.cookies.items() if "csrf" in k)


def test_signup_then_home_then_logout_round_trip(web_client):
    csrf_token = _csrf_token(web_client.get("/signup"))

    signup_response = web_client.post(
        "/signup",
        data={
            "email": "ann@example.com",
            "password": PASSWORD,
            "display_name": "Ann",
            "csrf_token": csrf_token,
        },
        follow_redirects=False,
    )
    assert signup_response.status_code == 303
    assert signup_response.headers["location"] == "/"
    assert "stakehold_session" in signup_response.headers.get("set-cookie", "")

    home_response = web_client.get("/")
    assert home_response.status_code == 200
    assert "Welcome, Ann." in home_response.text

    logout_csrf = _csrf_token(home_response)
    logout_response = web_client.post(
        "/logout", data={"csrf_token": logout_csrf}, follow_redirects=False
    )
    assert logout_response.status_code == 303
    assert logout_response.headers["location"] == "/login"

    after_logout = web_client.get("/", follow_redirects=False)
    assert after_logout.status_code == 303
    assert after_logout.headers["location"] == "/login"


def test_signup_duplicate_email_is_rejected(web_client):
    first_csrf = _csrf_token(web_client.get("/signup"))
    web_client.post(
        "/signup",
        data={
            "email": "dup@example.com",
            "password": PASSWORD,
            "display_name": "Dup",
            "csrf_token": first_csrf,
        },
    )

    second_csrf = _csrf_token(web_client.get("/signup"))
    second_response = web_client.post(
        "/signup",
        data={
            "email": "dup@example.com",
            "password": PASSWORD,
            "display_name": "Dup Again",
            "csrf_token": second_csrf,
        },
    )

    assert second_response.status_code == 409
    assert "already registered" in second_response.text


def test_login_with_wrong_password_is_rejected(web_client):
    signup_csrf = _csrf_token(web_client.get("/signup"))
    web_client.post(
        "/signup",
        data={
            "email": "bob@example.com",
            "password": PASSWORD,
            "display_name": "Bob",
            "csrf_token": signup_csrf,
        },
        follow_redirects=False,
    )
    web_client.post("/logout", data={"csrf_token": _csrf_token(web_client.get("/"))})

    login_csrf = _csrf_token(web_client.get("/login"))
    response = web_client.post(
        "/login",
        data={"email": "bob@example.com", "password": "wrong-password", "csrf_token": login_csrf},
    )

    assert response.status_code == 401
    assert "don" in response.text  # "don&#39;t match" -- HTML-escaped apostrophe
