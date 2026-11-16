"""UX-4: the site header. The main nav holds only the six sections; Admin, the theme switch and
Log out live in an account menu, a <details> labelled with the user's name, so it works without
JS. Logged out, there is no menu and the theme switch sits in the header as before.

Kept apart from test_routes.py, which is near the repo's 64KB file limit.
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
from web.settings import Settings

_SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"
_SECTIONS = ["Overview", "Portfolio", "Labs", "Risk", "Accounts", "Tickers"]

_NAV = re.compile(r'<nav class="site-nav"[^>]*>(.*?)</nav>', re.S)
_MENU = re.compile(r'<details class="account-menu">(.*?)</details>', re.S)
_SUMMARY = re.compile(r"<summary[^>]*>(.*?)</summary>", re.S)
_THEME_FORM = re.compile(r'<form action="/theme"[^>]*>(.*?)</form>', re.S)


class FakeAuthClient:
    def __init__(self):
        self.user: AuthUser | None = None

    async def validate_session(self, *, cookie_header):
        if self.user is None:
            raise SessionInvalid("no session")
        return AuthResult(user=self.user, set_cookie=_SESSION_COOKIE)


class FakePortfolioClient:
    """Just enough for /accounts to render: one account, one broker, no transactions."""

    async def list_accounts(self, *, user_id):
        return [
            Account(id=1, account_label="main", broker="acme", created_at="2026-01-01T00:00:00Z")
        ]

    async def list_brokers(self):
        return [Broker(broker="acme", imports=("transactions",))]

    async def list_transactions(self, *, user_id, account_label):
        return []


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
def fake_auth():
    return FakeAuthClient()


@pytest.fixture
def client(fake_auth):
    app = create_app(_settings())
    app.dependency_overrides[get_auth_client] = lambda: fake_auth
    app.dependency_overrides[get_portfolio_client] = lambda: FakePortfolioClient()
    with TestClient(app) as test_client:
        yield test_client


def _log_in(test_client, fake_auth, *, admin=False):
    fake_auth.user = AuthUser(
        user_id="u1", email="ann@example.com", display_name="Ann", is_admin=admin
    )
    test_client.cookies.set("stakehold_session", "tok")


def _nav(html):
    return _NAV.search(html).group(1)


def _menu(html):
    menus = _MENU.findall(html)
    assert len(menus) == 1, f"expected one account menu, found {len(menus)}"
    return menus[0]


# --- main nav -------------------------------------------------------------------------------


@pytest.mark.parametrize("admin", [False, True])
def test_main_nav_holds_only_the_six_sections(client, fake_auth, admin):
    _log_in(client, fake_auth, admin=admin)

    nav = _nav(client.get("/accounts").text)

    assert re.findall(r">(\w+)</a>", nav) == _SECTIONS
    assert 'href="/admin"' not in nav


def test_main_nav_marks_the_current_section(client, fake_auth):
    _log_in(client, fake_auth)

    nav = _nav(client.get("/accounts").text)

    assert re.findall(r'aria-current="page"', nav) == ['aria-current="page"']
    assert '<a href="/accounts" aria-current="page">Accounts</a>' in nav


# --- account menu ---------------------------------------------------------------------------


def test_account_menu_is_labelled_with_the_user_name(client, fake_auth):
    _log_in(client, fake_auth)

    menu = _menu(client.get("/accounts").text)

    assert "Ann" in _SUMMARY.search(menu).group(1)


def test_account_menu_holds_the_theme_switch_and_log_out(client, fake_auth):
    _log_in(client, fake_auth)

    menu = _menu(client.get("/accounts").text)

    assert 'class="theme-toggle"' in menu
    assert '<form action="/logout" method="post" class="logout-form">' in menu
    assert ">Log out</button>" in menu


def test_account_menu_lists_admin_for_an_admin_only(client, fake_auth):
    _log_in(client, fake_auth)
    assert 'href="/admin"' not in _menu(client.get("/accounts").text)

    _log_in(client, fake_auth, admin=True)
    assert '<a href="/admin">Admin</a>' in _menu(client.get("/accounts").text)


def test_on_admin_pages_the_menu_link_is_current_and_no_section_is(client, fake_auth):
    _log_in(client, fake_auth, admin=True)

    html = client.get("/admin").text

    assert '<a href="/admin" aria-current="page">Admin</a>' in _menu(html)
    assert "aria-current" not in _nav(html)


def test_theme_form_in_the_menu_posts_as_before(client, fake_auth):
    _log_in(client, fake_auth)
    client.cookies.set("stakehold_theme", "dark")

    response = client.get("/accounts", params={"x": "1"})

    form_tag = re.search(r'<form action="/theme"[^>]*>', _menu(response.text)).group(0)
    assert 'method="post"' in form_tag
    assert 'hx-post="/theme"' in form_tag
    assert 'hx-swap="none"' in form_tag
    form = _THEME_FORM.search(_menu(response.text)).group(1)
    csrf = response.cookies.get("stakehold_csrf") or client.cookies.get("stakehold_csrf")
    assert f'name="csrf_token" value="{csrf}"' in form
    assert '<input type="hidden" name="next" value="/accounts?x=1">' in form
    pressed = dict(re.findall(r'value="(\w+)" class="btn btn-ghost" aria-pressed="(\w+)"', form))
    assert pressed == {"light": "false", "dark": "true", "system": "false"}


# --- logged out -----------------------------------------------------------------------------


def test_logged_out_pages_keep_the_bare_theme_switch(client):
    html = client.get("/login").text

    assert 'class="theme-toggle"' in html
    assert "account-menu" not in html
    assert 'class="site-nav"' not in html


def test_the_menu_script_is_loaded(client, fake_auth):
    _log_in(client, fake_auth)

    html = client.get("/accounts").text

    assert re.search(r'<script src="/static/account_menu\.js\?v=\w+" defer></script>', html)
