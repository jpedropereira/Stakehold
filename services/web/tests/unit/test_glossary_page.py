"""GET /glossary: what each figure on the holdings pages and the overview means.

The holdings pages link each column header and tile label to its entry here, and their own tests
check that every such link lands on an entry. This file checks the page itself: who can see it,
that every entry has its anchor, and that it needs nothing from the portfolio service.

Carries its own minimal fakes, like test_home_page.py: only what this one page touches.
"""

import re

import pytest
from fastapi.testclient import TestClient
from web import settings as settings_module
from web.api.app import create_app
from web.api.deps import get_auth_client, get_portfolio_client
from web.auth_client.base import AuthResult, AuthUser
from web.auth_client.errors import SessionInvalid
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"

# Every term the glossary defines, by anchor. The holdings pages' links point at these.
ENTRIES = (
    "units",
    "avg-cost",
    "cost-basis",
    "market-value",
    "weight",
    "unrealized-return",
    "dividends",
    "total-return",
    "prices-as-of",
)


class FakeAuthClient:
    """Anonymous until `_logged_in` sets a result."""

    def __init__(self):
        self.validate_result: AuthResult | None = None

    async def validate_session(self, *, cookie_header):
        if self.validate_result is None:
            raise SessionInvalid("no session")
        return self.validate_result


class UnusablePortfolioClient:
    """Fails any call, so the page rendering proves it never asked the portfolio service."""

    def __getattr__(self, name):
        raise AssertionError(f"the glossary called the portfolio service ({name})")


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
    fake_auth = FakeAuthClient()
    app = create_app(_settings())
    app.dependency_overrides[get_auth_client] = lambda: fake_auth
    app.dependency_overrides[get_portfolio_client] = UnusablePortfolioClient
    with TestClient(app) as test_client:
        yield test_client, fake_auth


def _logged_in(test_client, fake_auth):
    user = AuthUser(user_id="u1", email="ann@example.com", display_name="Ann")
    fake_auth.validate_result = AuthResult(user=user, set_cookie=SESSION_COOKIE)
    test_client.cookies.set("stakehold_session", "tok")


def _entry(html, anchor):
    """The definition text following the term anchored at `anchor`, or None."""
    match = re.search(rf'<dt id="{anchor}"[^>]*>.*?</dt>\s*<dd>(.*?)</dd>', html, re.DOTALL)
    return None if match is None else match.group(1)


def test_anonymous_visitor_is_sent_to_login(client):
    test_client, _ = client

    response = test_client.get("/glossary", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_renders_without_the_portfolio_service(client):
    test_client, fake_auth = client
    _logged_in(test_client, fake_auth)

    response = test_client.get("/glossary")

    assert response.status_code == 200
    assert "<h1>Glossary</h1>" in response.text


@pytest.mark.parametrize("anchor", ENTRIES)
def test_every_term_has_an_anchored_definition(client, anchor):
    test_client, fake_auth = client
    _logged_in(test_client, fake_auth)

    html = test_client.get("/glossary").text

    assert _entry(html, anchor), f"no definition anchored at #{anchor}"


def test_no_nav_item_is_marked_current(client):
    """Reached from the figures it explains, not from the nav, so no section claims it."""
    test_client, fake_auth = client
    _logged_in(test_client, fake_auth)

    html = test_client.get("/glossary").text

    assert 'aria-current="page"' not in html


def test_total_return_says_it_includes_dividends(client):
    """The two percentages the holdings table shows side by side, unrealized and total return,
    differ by dividends: the page must say so."""
    test_client, fake_auth = client
    _logged_in(test_client, fake_auth)

    html = test_client.get("/glossary").text

    assert "dividends" in _entry(html, "total-return").lower()
    assert "growth" not in html.lower()
