"""UI-3: the theme cookie, POST /theme, and the server-rendered data-theme on <html>.

Kept apart from test_routes.py (already near the repo's 64KB file limit). Only auth is faked:
every page these tests render is either public or gated by the session check alone.
"""

import json
import re

import pytest
from fastapi.testclient import TestClient
from web import settings as settings_module
from web.api.app import create_app
from web.api.deps import get_auth_client
from web.auth_client.base import AuthResult, AuthUser
from web.auth_client.errors import SessionInvalid
from web.settings import Settings

_THEME_COOKIE = "stakehold_theme"
_CSRF_COOKIE = "stakehold_csrf"
_HTML_TAG = re.compile(r"<html\b[^>]*>")


class FakeAuthClient:
    """Anonymous unless `user` is set. A theme cookie alone makes the browser send a Cookie
    header, so the session check runs even for a logged-out visitor and must fail cleanly."""

    def __init__(self):
        self.user: AuthUser | None = None

    async def validate_session(self, *, cookie_header):
        if self.user is None:
            raise SessionInvalid("no session")
        return AuthResult(user=self.user, set_cookie="stakehold_session=tok; Path=/")


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


@pytest.fixture
def fake_auth():
    return FakeAuthClient()


@pytest.fixture
def client(fake_auth):
    app = create_app(_settings())
    app.dependency_overrides[get_auth_client] = lambda: fake_auth
    with TestClient(app) as test_client:
        yield test_client


def _csrf_token(test_client):
    return test_client.get("/login").cookies[_CSRF_COOKIE]


def _post_theme(test_client, theme, *, next_url="/accounts", headers=None, csrf_token=None):
    token = csrf_token if csrf_token is not None else _csrf_token(test_client)
    return test_client.post(
        "/theme",
        data={"theme": theme, "next": next_url, "csrf_token": token},
        headers=headers or {},
        follow_redirects=False,
    )


def _theme_set_cookie(response):
    return [c for c in response.headers.get_list("set-cookie") if c.startswith(f"{_THEME_COOKIE}=")]


def _html_tag(response):
    return _HTML_TAG.search(response.text).group(0)


# --- POST /theme ------------------------------------------------------------


@pytest.mark.parametrize("theme", ["light", "dark", "system"])
def test_post_theme_sets_the_cookie_and_redirects_back(client, theme):
    response = _post_theme(client, theme)

    assert response.status_code == 303
    assert response.headers["location"] == "/accounts"
    [cookie] = _theme_set_cookie(response)
    assert cookie.startswith(f"{_THEME_COOKIE}={theme};")


def test_theme_cookie_attributes(client):
    response = _post_theme(client, "dark")

    [cookie] = _theme_set_cookie(response)
    attributes = {a.strip().split("=")[0].lower() for a in cookie.split(";")[1:]}
    assert {"httponly", "path", "samesite", "max-age"} <= attributes
    assert "path=/" in cookie.lower()
    assert "samesite=lax" in cookie.lower()
    assert "max-age=31536000" in cookie.lower()
    assert "secure" not in attributes


def test_theme_cookie_is_secure_when_cookie_secure_is_true(fake_auth):
    app = create_app(_settings(cookie_secure=True))
    app.dependency_overrides[get_auth_client] = lambda: fake_auth
    with TestClient(app) as test_client:
        token = test_client.get("/login").cookies["__Host-stakehold_csrf"]
        # httpx won't send a Secure cookie over the test client's plain http, so set it by hand.
        test_client.cookies.set("__Host-stakehold_csrf", token)
        response = _post_theme(test_client, "dark", csrf_token=token)

    [cookie] = _theme_set_cookie(response)
    assert "secure" in {a.strip().lower() for a in cookie.split(";")[1:]}


def test_htmx_post_theme_answers_with_a_trigger_not_a_redirect(client):
    response = _post_theme(client, "dark", headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert "location" not in response.headers
    assert "HX-Redirect" not in response.headers
    assert json.loads(response.headers["HX-Trigger"]) == {"stakehold:theme": "dark"}
    assert _theme_set_cookie(response)


def test_post_theme_rejects_an_unknown_value(client):
    response = _post_theme(client, "sepia")

    assert response.status_code == 400
    assert _theme_set_cookie(response) == []


@pytest.mark.parametrize("token", ["", "wrong-token"])
def test_post_theme_requires_a_matching_csrf_token(client, token):
    _csrf_token(client)

    response = _post_theme(client, "dark", csrf_token=token)

    assert response.status_code == 303
    assert response.headers["location"] == "/accounts"
    assert _theme_set_cookie(response) == []


@pytest.mark.parametrize(
    "next_url",
    [
        "//evil.example",
        "https://evil.example/",
        "/\\evil.example",
        # Browsers strip tabs and newlines from a URL, turning this into //evil.example.
        "/\t/evil.example",
        "/\n/evil.example",
        "accounts",
        "",
    ],
)
def test_post_theme_only_redirects_within_the_site(client, next_url):
    response = _post_theme(client, "dark", next_url=next_url)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_post_theme_keeps_the_query_string_of_next(client):
    response = _post_theme(client, "dark", next_url="/accounts/holdings?account=Main")

    assert response.headers["location"] == "/accounts/holdings?account=Main"


# --- rendering --------------------------------------------------------------


@pytest.mark.parametrize("cookie", [None, "system", "sepia"])
def test_no_manual_choice_renders_no_data_theme(client, cookie):
    if cookie is not None:
        client.cookies.set(_THEME_COOKIE, cookie)

    response = client.get("/login")

    assert "data-theme" not in _html_tag(response)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_manual_choice_renders_data_theme(client, theme):
    client.cookies.set(_THEME_COOKIE, theme)

    response = client.get("/login")

    assert _html_tag(response) == f'<html lang="en" data-theme="{theme}">'


def test_forbidden_page_follows_the_theme_cookie(client, fake_auth):
    """The 403 page is rendered by an app-level exception handler, not routes.py, so this
    proves the theme reaches every template rather than only those routes.py renders."""
    fake_auth.user = AuthUser(user_id="u1", email="ann@example.com", display_name="Ann")
    client.cookies.set("stakehold_session", "tok")
    client.cookies.set(_THEME_COOKIE, "dark")

    response = client.get("/admin")

    assert response.status_code == 403
    assert _html_tag(response) == '<html lang="en" data-theme="dark">'


@pytest.mark.parametrize("theme", ["light", "dark", "system"])
def test_toggle_marks_the_current_choice(client, theme):
    if theme != "system":
        client.cookies.set(_THEME_COOKIE, theme)

    response = client.get("/login")

    pressed = re.findall(
        r'<button[^>]*name="theme"[^>]*value="(\w+)"[^>]*aria-pressed="true"', response.text
    )
    assert pressed == [theme]


def test_toggle_posts_back_to_the_current_page(client):
    response = client.get("/login", params={"from": "a"})

    form = re.search(r'<form[^>]*action="/theme".*?</form>', response.text, re.S).group(0)
    assert 'method="post"' in form
    assert 'name="csrf_token"' in form
    assert 'name="next" value="/login?from=a"' in form


def test_both_logos_are_rendered(client):
    response = client.get("/login")

    assert 'class="logo-on-light" src="/brand/logos/logo-stone.svg"' in response.text
    assert 'class="logo-on-dark" src="/brand/logos/logo-gold.svg"' in response.text


def test_theme_script_is_loaded(client):
    response = client.get("/login")

    assert '<script src="/static/theme.js" defer></script>' in response.text
