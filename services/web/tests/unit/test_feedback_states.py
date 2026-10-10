"""UI-12: message banners, in-flight state, branded error pages and the auth pages.

Kept apart from test_routes.py, which is past the repo's 64KB file limit. Like the other page
test files, it carries its own minimal fakes: only the calls these pages make.
"""

import html as html_lib
import re

import pytest
from fastapi.testclient import TestClient
from web import settings as settings_module
from web.api.app import create_app
from web.api.deps import get_auth_client, get_portfolio_client
from web.auth_client.base import AuthResult, AuthUser
from web.auth_client.errors import (
    EmailAlreadyRegistered,
    InvalidCredentials,
    SessionInvalid,
)
from web.portfolio_client.base import Account, Broker
from web.portfolio_client.errors import InvalidPortfolioRequest
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"
_CSRF_COOKIE = "stakehold_csrf"
_BANNER = re.compile(r'<div class="banner[^"]*" data-kind="(\w+)">(.*?)</div>', re.DOTALL)
_TAG = re.compile(r"<[^>]+>")


class FakeAuthClient:
    """Anonymous until `_logged_in` sets a result. `validate_error` replaces the session check's
    outcome entirely, to make any logged-in page fail with an unhandled exception."""

    def __init__(self):
        self.validate_result: AuthResult | None = None
        self.validate_error: Exception | None = None
        self.login_error: Exception | None = None
        self.signup_error: Exception | None = None

    async def validate_session(self, *, cookie_header):
        if self.validate_error is not None:
            raise self.validate_error
        if self.validate_result is None:
            raise SessionInvalid("no session")
        return self.validate_result

    async def login(self, *, email, password, client_ip):
        raise self.login_error

    async def signup(self, *, email, password, display_name, client_ip):
        raise self.signup_error


class FakePortfolioClient:
    def __init__(self):
        self.write_error: Exception = InvalidPortfolioRequest("fx_rate must be positive")

    async def list_accounts(self, *, user_id):
        return [
            Account(id=1, account_label="main", broker="acme", created_at="2026-01-01T00:00:00Z")
        ]

    async def list_brokers(self):
        return [Broker(broker="acme", imports=("transactions",))]

    async def list_tickers(self):
        return []

    async def create_account(self, **kwargs):
        raise self.write_error

    async def add_transaction(self, **kwargs):
        raise self.write_error

    async def import_transactions(self, **kwargs):
        raise self.write_error

    async def import_cashflow(self, **kwargs):
        raise self.write_error


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
def fakes():
    return FakeAuthClient(), FakePortfolioClient()


def _app(fakes):
    fake_auth, fake_portfolio = fakes
    app = create_app(_settings())
    app.dependency_overrides[get_auth_client] = lambda: fake_auth
    app.dependency_overrides[get_portfolio_client] = lambda: fake_portfolio
    return app


@pytest.fixture
def client(fakes):
    # raise_server_exceptions=False: an unhandled exception reaches the app's own 500 handler,
    # the way it does for a real browser, rather than being re-raised into the test.
    with TestClient(_app(fakes), raise_server_exceptions=False) as test_client:
        yield test_client


def _logged_in(test_client, fake_auth):
    user = AuthUser(user_id="u1", email="ann@example.com", display_name="Ann")
    fake_auth.validate_result = AuthResult(user=user, set_cookie=SESSION_COOKIE)
    test_client.cookies.set("stakehold_session", "tok")


_TEMPLATE = re.compile(r"<template\b.*?</template>", re.DOTALL)


def _banners(html: str) -> list[tuple[str, str]]:
    """(kind, plain text) for every banner the page shows. Not those inside a <template>:
    base.html carries one on every page for htmx-error-swap.js, and it isn't shown."""
    shown = _TEMPLATE.sub("", html)
    return [
        (kind, " ".join(html_lib.unescape(_TAG.sub("", body)).split()))
        for kind, body in _BANNER.findall(shown)
    ]


def _token(test_client, path, **params):
    return test_client.get(path, params=params).cookies[_CSRF_COOKIE]


# --- form errors: the banner, its lead word, and the live region ------------------------------

_LOGIN = {"email": "a@example.com", "password": "x"}
_SIGNUP = {"email": "a@example.com", "password": "x" * 12, "display_name": "Ann"}
_ACCOUNT = {"broker": "acme", "account_label": "second"}
_TRANSACTION = {
    "account_label": "main",
    "broker_transaction_ref": "ref-1",
    "transaction_type": "DEPOSIT",
    "trade_datetime": "2026-01-05T12:00",
    "native_amount": "100.00",
    "native_currency": "USD",
    "fx_rate": "-1",
}


def _post_form(test_client, form, *, htmx=False, csrf_token=None):
    path, get_params, data, files = form
    token = csrf_token if csrf_token is not None else _token(test_client, path, **get_params)
    return test_client.post(
        path,
        data={**data, "csrf_token": token},
        files=files,
        headers={"HX-Request": "true"} if htmx else {},
        follow_redirects=False,
    )


# (form, its lead word, whether it needs a session)
_FORMS = {
    "login": (("/login", {}, _LOGIN, None), "Couldn't log in.", False),
    "signup": (("/signup", {}, _SIGNUP, None), "Couldn't sign up.", False),
    "accounts": (("/accounts", {}, _ACCOUNT, None), "Couldn't create the account.", True),
    "add": (("/transactions/new", {"account": "main"}, _TRANSACTION, None), "Couldn't save.", True),
    "import": (
        (
            "/transactions/import",
            {"account": "main"},
            {"account_label": "main", "importer": "transactions"},
            {"file": ("export.csv", b"a,b\n1,2\n", "text/csv")},
        ),
        "Couldn't import.",
        True,
    ),
}


def _set_up_form(name, test_client, fakes):
    fake_auth, _ = fakes
    form, lead, needs_session = _FORMS[name]
    fake_auth.login_error = InvalidCredentials("a@example.com")
    fake_auth.signup_error = EmailAlreadyRegistered("a@example.com")
    if needs_session:
        _logged_in(test_client, fake_auth)
    return form, lead


@pytest.mark.parametrize("name", list(_FORMS))
@pytest.mark.parametrize("htmx", [False, True], ids=["full-page", "htmx"])
def test_form_error_is_an_error_banner_led_by_the_forms_lead(client, fakes, name, htmx):
    form, lead = _set_up_form(name, client, fakes)

    response = _post_form(client, form, htmx=htmx)

    assert response.status_code >= 400
    [(kind, text)] = _banners(response.text)
    assert kind == "error"
    assert text.startswith(lead + " ")
    assert len(text) > len(lead) + 1, "the message follows the lead"


@pytest.mark.parametrize("name", list(_FORMS))
def test_csrf_failure_gets_the_forms_lead(client, fakes, name):
    form, lead = _set_up_form(name, client, fakes)

    response = _post_form(client, form, csrf_token="forged")

    assert response.status_code == 400
    assert _banners(response.text) == [
        (
            "error",
            f"{lead} Something went wrong with that submission. Refresh the page and try " "again.",
        )
    ]


@pytest.mark.parametrize(
    "path, params, session",
    [
        ("/login", {}, False),
        ("/signup", {}, False),
        ("/accounts", {}, True),
        ("/transactions/new", {"account": "main"}, True),
        ("/transactions/import", {"account": "main"}, True),
    ],
)
def test_form_errors_slot_is_a_polite_live_region(client, fakes, path, params, session):
    if session:
        _logged_in(client, fakes[0])

    html = client.get(path, params=params).text

    assert '<div id="form-errors" aria-live="polite">' in html


def test_a_page_with_no_error_shows_no_banner(client):
    assert _banners(client.get("/login").text) == []


# --- in-flight state ------------------------------------------------------------------------

_HTMX_FORM = re.compile(r"<form\b[^>]*\bhx-post=[^>]*>", re.DOTALL)


@pytest.mark.parametrize(
    "path, params, session",
    [
        ("/login", {}, False),
        ("/signup", {}, False),
        ("/accounts", {}, True),
        ("/transactions/new", {"account": "main"}, True),
        ("/transactions/import", {"account": "main"}, True),
    ],
)
def test_every_htmx_form_disables_its_submit_while_in_flight(client, fakes, path, params, session):
    if session:
        _logged_in(client, fakes[0])

    html = client.get(path, params=params).text

    forms = _HTMX_FORM.findall(html)
    assert forms, "no htmx form on the page"
    for form in forms:
        assert "hx-disabled-elt=\"find button[type='submit']\"" in form, form


@pytest.mark.parametrize(
    "path, params, session, busy",
    [
        ("/login", {}, False, "Logging in…"),
        ("/signup", {}, False, "Signing up…"),
        ("/accounts", {}, True, "Creating…"),
        ("/transactions/new", {"account": "main"}, True, "Saving…"),
        ("/transactions/import", {"account": "main"}, True, "Uploading…"),
    ],
)
def test_every_htmx_form_has_an_in_flight_indicator(client, fakes, path, params, session, busy):
    if session:
        _logged_in(client, fakes[0])

    html = client.get(path, params=params).text

    assert f'<span class="htmx-indicator busy" role="status">{busy}</span>' in html


def test_htmx_is_told_not_to_inject_its_indicator_styles(client):
    # The strict CSP blocks the inline <style> htmx would otherwise add on every page load;
    # web.css carries those rules instead.
    html = client.get("/login").text

    assert """<meta name="htmx-config" content='{"includeIndicatorStyles": false}'>""" in html


def test_every_page_carries_the_request_failed_banner_template(client):
    html = client.get("/login").text

    match = re.search(r'<template id="request-failed-banner">(.*?)</template>', html, re.DOTALL)
    assert match
    assert _banners(match.group(1)) == [("error", "Something went wrong. Try again in a moment.")]


# --- branded error pages --------------------------------------------------------------------


def _assert_branded(response, heading):
    assert response.headers["content-type"].startswith("text/html")
    assert f"<h1>{heading}</h1>" in html_lib.unescape(response.text)
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    # Rendered without a session or a CSRF token: no nav, no logout, no theme toggle.
    assert 'class="site-nav"' not in response.text
    assert 'class="theme-toggle"' not in response.text


def test_unknown_path_is_a_branded_404(client):
    response = client.get("/no-such-page")

    assert response.status_code == 404
    _assert_branded(response, "Page not found")


def test_unknown_path_is_a_branded_404_for_a_logged_in_user_too(client, fakes):
    _logged_in(client, fakes[0])

    response = client.get("/no-such-page")

    assert response.status_code == 404
    _assert_branded(response, "Page not found")


def test_other_http_errors_keep_their_status_and_headers(client):
    response = client.get("/logout")

    assert response.status_code == 405
    assert response.headers["Allow"] == "POST"
    _assert_branded(response, "Couldn't open that page")


def test_a_malformed_request_is_a_branded_422(client, fakes):
    _logged_in(client, fakes[0])

    response = client.get("/transactions/new")

    assert response.status_code == 422
    _assert_branded(response, "Couldn't open that page")


def test_an_unhandled_exception_is_a_branded_500_with_security_headers(client, fakes):
    fake_auth, _ = fakes
    client.cookies.set("stakehold_session", "tok")
    fake_auth.validate_error = RuntimeError("boom")

    response = client.get("/accounts")

    assert response.status_code == 500
    _assert_branded(response, "Something went wrong")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "boom" not in response.text


def test_error_pages_follow_the_theme_cookie(client):
    client.cookies.set("stakehold_theme", "dark")

    html = client.get("/no-such-page").text

    assert '<html lang="en" data-theme="dark">' in html


def test_the_existing_404_and_403_pages_are_unchanged(client, fakes):
    _logged_in(client, fakes[0])

    response = client.get("/transactions/new", params={"account": "nope"})

    assert response.status_code == 404
    assert "<h1>Account not found</h1>" in response.text


# --- auth pages -----------------------------------------------------------------------------

_HEADER = re.compile(r'<header class="site-header">(.*?)</header>', re.DOTALL)
_AUTH_MARK = re.compile(r'<div class="auth-mark">(.*?)</div>', re.DOTALL)


@pytest.mark.parametrize("path", ["/login", "/signup"])
def test_auth_pages_show_the_mark_in_the_card_not_the_header(client, path):
    html = client.get(path).text

    assert "logo-stone.svg" not in _HEADER.search(html).group(1)
    mark = _AUTH_MARK.search(html)
    assert mark, "no mark in the card"
    assert 'class="logo-on-light" src="/brand/logos/logo-stone.svg"' in mark.group(1)
    assert 'class="logo-on-dark" src="/brand/logos/logo-gold.svg"' in mark.group(1)


def test_other_pages_keep_the_header_mark(client, fakes):
    _logged_in(client, fakes[0])

    html = client.get("/accounts").text

    assert '<a href="/" class="brand-mark">' in _HEADER.search(html).group(1)
    assert 'class="auth-mark"' not in html


def test_signup_states_the_password_minimum_up_front(client):
    html = client.get("/signup").text

    assert 'aria-describedby="password-hint"' in html
    assert '<p class="field-hint" id="password-hint">At least 12 characters.</p>' in html


@pytest.mark.parametrize("path", ["/login", "/signup"])
def test_password_toggle_ships_hidden_for_its_script_to_reveal(client, path):
    html = client.get(path).text

    assert (
        '<button type="button" class="btn btn-ghost password-toggle" aria-controls="password" '
        'aria-pressed="false" hidden>Show</button>'
    ) in html
    assert '<script src="/static/password_toggle.js" defer></script>' in html


def test_password_toggle_script_is_only_on_the_auth_pages(client, fakes):
    _logged_in(client, fakes[0])

    assert "password_toggle.js" not in client.get("/accounts").text
