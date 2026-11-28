"""The Tickers pages (ticker onboarding, TK-8): looking a symbol up, adding it, the list of
tickers with their loading status, and an admin's changes.

Kept apart from test_routes.py, which is over the repo's 64KB file limit, with its own minimal
fakes, like test_portfolio_page.py.
"""

import html
import re
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from web import settings as settings_module
from web.api.app import create_app
from web.api.deps import get_auth_client, get_portfolio_client
from web.auth_client.base import AuthResult, AuthUser
from web.auth_client.errors import SessionInvalid
from web.portfolio_client.base import (
    TickerLookup,
    TrackedTicker,
    TrackedTickerAddResult,
)
from web.portfolio_client.errors import (
    InvalidTickerRequest,
    PortfolioUnavailable,
    TickerChangeRefused,
    TickerLimitReached,
    TickerNotOnProvider,
    TickerProviderUnavailable,
)
from web.settings import Settings

SESSION_COOKIE = "stakehold_session=tok; Path=/; HttpOnly; SameSite=lax"


class FakeAuthClient:
    def __init__(self):
        self.validate_result: AuthResult | None = None

    async def validate_session(self, *, cookie_header):
        if self.validate_result is None:
            raise SessionInvalid("no session")
        return self.validate_result


def tracked(ticker="ASML.AS", *, status="pending", origin="request", enabled=True, error=None):
    return TrackedTicker(
        ticker=ticker,
        display_name=f"{ticker} Holding",
        asset_type="equity",
        currency="EUR",
        exchange="Amsterdam",
        origin=origin,
        status=status,
        enabled=enabled,
        attempts=3 if status == "failed" else 0,
        last_error=error,
        requested_by="u1",
        requested_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
        ready_at=None,
    )


def lookup(outcome="available", *, ticker="ASML.AS", status=None):
    return TickerLookup(
        ticker=ticker,
        outcome=outcome,
        status=status,
        display_name="ASML Holding N.V." if outcome != "not_found" else None,
        currency="EUR" if outcome != "not_found" else None,
        exchange="Amsterdam" if outcome != "not_found" else None,
        asset_type="equity" if outcome == "available" else None,
    )


class FakePortfolioClient:
    def __init__(self):
        self.lookup_result: TickerLookup | None = lookup()
        self.lookup_error: Exception | None = None
        self.add_result = TrackedTickerAddResult(ticker=tracked(), created=True)
        self.add_error: Exception | None = None
        self.list_result: list[TrackedTicker] = []
        self.list_error: Exception | None = None
        self.update_error: Exception | None = None
        self.calls: list[tuple] = []

    async def lookup_ticker(self, ticker):
        self.calls.append(("lookup", ticker))
        if self.lookup_error:
            raise self.lookup_error
        return self.lookup_result

    async def add_tracked_ticker(self, *, ticker, requested_by, enforce_limit):
        self.calls.append(("add", ticker, requested_by, enforce_limit))
        if self.add_error:
            raise self.add_error
        return self.add_result

    async def list_tracked_tickers(self, *, requested_by=None, statuses=None, tickers=None):
        self.calls.append(("list", requested_by, statuses, tickers))
        if self.list_error:
            raise self.list_error
        return self.list_result

    # The admin mappings page's own calls, for its link to the add page.
    async def list_ticker_mappings(self):
        return []

    async def list_all_unmapped_instruments(self):
        return []

    async def list_tickers(self):
        return []

    async def update_tracked_ticker(self, *, ticker, enabled=None, retry=False):
        self.calls.append(("update", ticker, enabled, retry))
        if self.update_error:
            raise self.update_error
        return tracked(ticker)


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
    fake_auth, fake_portfolio = FakeAuthClient(), FakePortfolioClient()
    app = create_app(_settings())
    app.dependency_overrides[get_auth_client] = lambda: fake_auth
    app.dependency_overrides[get_portfolio_client] = lambda: fake_portfolio
    with TestClient(app) as test_client:
        yield test_client, fake_auth, fake_portfolio


def _log_in(test_client, fake_auth, *, admin=False):
    user = AuthUser(user_id="u1", email="ann@example.com", display_name="Ann", is_admin=admin)
    fake_auth.validate_result = AuthResult(user=user, set_cookie=SESSION_COOKIE)
    test_client.cookies.set("stakehold_session", "tok")


def text(response) -> str:
    """The page as a reader sees it: messages with apostrophes are HTML-escaped in the source."""
    return html.unescape(response.text)


def _csrf(test_client):
    return test_client.get("/tickers/new").cookies["stakehold_csrf"]


# --- navigation ----------------------------------------------------------------------------------


def test_every_user_has_a_tickers_link(client):
    test_client, fake_auth, _ = client
    _log_in(test_client, fake_auth)

    response = test_client.get("/tickers")

    assert '<a href="/tickers" aria-current="page">Tickers</a>' in text(response)


def test_the_pages_need_a_login(client):
    test_client, _, _ = client

    for path in ("/tickers", "/tickers/new"):
        response = test_client.get(path, follow_redirects=False)
        assert response.status_code in (302, 303)
        assert response.headers["location"].startswith("/login")


# --- look up -------------------------------------------------------------------------------------


def test_the_add_page_starts_with_an_empty_lookup_form(client):
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth)

    response = test_client.get("/tickers/new")

    assert response.status_code == 200
    assert 'action="/tickers/new"' in text(response)
    assert 'name="symbol"' in text(response)
    # No lookup until a symbol is given; the only call is the list beside the form (UX-11).
    assert [c for c in fake_portfolio.calls if c[0] != "list"] == []


def test_an_available_symbol_is_shown_for_confirmation(client):
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth)

    response = test_client.get("/tickers/new", params={"symbol": "asml.as"})

    assert [c for c in fake_portfolio.calls if c[0] == "lookup"] == [("lookup", "asml.as")]
    assert "ASML Holding N.V." in text(response)
    assert "Amsterdam" in text(response)
    assert '<span class="mono">EUR</span>' in text(response)
    # The confirm form posts the provider's own spelling, not what was typed.
    assert 'name="symbol" value="ASML.AS"' in text(response)
    assert 'action="/tickers"' in text(response)
    assert "Add ASML.AS" in text(response)


@pytest.mark.parametrize("currency", ["GBp", "ZAc", "ILA"])
def test_a_minor_unit_symbol_is_offered_without_a_warning(client, currency):
    # data-store rescales minor-unit prices to the major unit as it ingests them (IQ-1), so the
    # "values will read 100 times too high" warning this page used to show is no longer true.
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth)
    fake_portfolio.lookup_result = replace(lookup(ticker="VOD.L"), currency=currency)

    response = test_client.get("/tickers/new", params={"symbol": "vod.l"})

    assert f'<span class="mono">{currency}</span>' in text(response)
    assert "Add VOD.L" in text(response)
    assert "100 times" not in text(response)
    assert "minor units" not in text(response)


@pytest.mark.parametrize(
    "status, says",
    [
        ("ready", "already in Stakehold"),
        ("pending", "loading"),
        ("backfilling", "loading"),
        ("failed", "couldn't be loaded"),
    ],
)
def test_a_tracked_symbol_says_where_it_stands_and_offers_no_add(client, status, says):
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth)
    fake_portfolio.lookup_result = lookup("tracked", status=status)

    response = test_client.get("/tickers/new", params={"symbol": "ASML.AS"})

    assert says in text(response)
    assert 'action="/tickers"' not in text(response)


@pytest.mark.parametrize(
    "outcome, says",
    [
        ("unsupported", "can't be held"),
        ("not_found", "Delisted securities may not be available"),
        ("unavailable", "Try again in a minute"),
    ],
)
def test_a_symbol_that_cannot_be_added_says_why(client, outcome, says):
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth)
    fake_portfolio.lookup_result = lookup(outcome)

    response = test_client.get("/tickers/new", params={"symbol": "ASML.AS"})

    assert says in text(response)
    assert 'action="/tickers"' not in text(response)


def test_an_invalid_symbol_is_explained(client):
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth)
    fake_portfolio.lookup_error = InvalidTickerRequest("not a valid ticker symbol")

    response = test_client.get("/tickers/new", params={"symbol": "AS ML"})

    assert response.status_code == 422
    assert "isn't a ticker symbol" in text(response)


def test_a_lookup_outage_is_explained(client):
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth)
    fake_portfolio.lookup_error = PortfolioUnavailable("down")

    response = test_client.get("/tickers/new", params={"symbol": "ASML.AS"})

    assert response.status_code == 503
    assert "Can't reach" in text(response)


# --- add -----------------------------------------------------------------------------------------


def test_adding_sends_the_user_and_the_limit_then_lists_it(client):
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth)
    token = _csrf(test_client)

    response = test_client.post(
        "/tickers", data={"symbol": "ASML.AS", "csrf_token": token}, follow_redirects=False
    )

    assert response.status_code == 303
    # Back to the add page, so the new row shows in the list beside the form (UX-11).
    assert response.headers["location"] == "/tickers/new?added=ASML.AS"
    assert fake_portfolio.calls[-1] == ("add", "ASML.AS", "u1", True)


def test_an_admin_adds_without_the_limit(client):
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth, admin=True)
    token = _csrf(test_client)

    test_client.post("/tickers", data={"symbol": "ASML.AS", "csrf_token": token})

    assert ("add", "ASML.AS", "u1", False) in fake_portfolio.calls


def test_adding_a_tracked_ticker_says_it_was_already_there(client):
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth)
    fake_portfolio.add_result = TrackedTickerAddResult(
        ticker=tracked(status="ready"), created=False
    )
    token = _csrf(test_client)

    response = test_client.post(
        "/tickers", data={"symbol": "ASML.AS", "csrf_token": token}, follow_redirects=False
    )

    assert response.headers["location"] == "/tickers/new?already=ASML.AS"


def test_adding_without_a_csrf_token_is_refused(client):
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth)

    response = test_client.post("/tickers", data={"symbol": "ASML.AS"})

    assert response.status_code == 400
    assert not [c for c in fake_portfolio.calls if c[0] == "add"]


@pytest.mark.parametrize(
    "error, status, says",
    [
        (TickerNotOnProvider("x"), 404, "Delisted securities may not be available"),
        (InvalidTickerRequest("x"), 422, "can't be added"),
        (TickerLimitReached("x"), 429, "Try again tomorrow"),
        (TickerProviderUnavailable("x"), 503, "Try again in a minute"),
        (PortfolioUnavailable("x"), 503, "Can't reach"),
    ],
)
def test_an_add_that_fails_says_why(client, error, status, says):
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth)
    fake_portfolio.add_error = error
    token = _csrf(test_client)

    response = test_client.post("/tickers", data={"symbol": "ASML.AS", "csrf_token": token})

    assert response.status_code == status
    assert "Couldn't add the ticker." in text(response)
    assert says in text(response)


# --- the list ------------------------------------------------------------------------------------


def test_a_user_sees_the_tickers_they_added_with_their_status(client):
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth)
    fake_portfolio.list_result = [
        tracked("ASML.AS", status="backfilling"),
        tracked("GHOST", status="failed", error="no price history"),
    ]

    response = test_client.get("/tickers")

    assert ("list", "u1", None, None) in fake_portfolio.calls
    assert "ASML.AS" in text(response)
    assert ">Loading<" in text(response)
    assert ">Couldn't load<" in text(response)
    assert 'href="/tickers/new"' in text(response)
    # Admin controls stay off a user's page.
    assert "/retry" not in text(response)
    assert "/disable" not in text(response)


def test_a_user_with_no_tickers_is_told_how_to_add_one(client):
    test_client, fake_auth, _ = client
    _log_in(test_client, fake_auth)

    response = test_client.get("/tickers")

    assert "You haven't added any tickers" in text(response)


def test_the_list_after_an_add_confirms_it(client):
    test_client, fake_auth, _ = client
    _log_in(test_client, fake_auth)

    response = test_client.get("/tickers", params={"added": "ASML.AS"})

    assert "ASML.AS added." in text(response)
    assert "within a few minutes" in text(response)


def test_the_outcome_banner_only_echoes_a_ticker_shaped_value(client):
    test_client, fake_auth, _ = client
    _log_in(test_client, fake_auth)

    response = test_client.get("/tickers", params={"added": "<script>"})

    assert "added." not in text(response)


def test_the_list_degrades_when_portfolio_is_down(client):
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth)
    fake_portfolio.list_error = PortfolioUnavailable("down")

    response = test_client.get("/tickers")

    assert response.status_code == 200
    assert "Couldn't load everything." in text(response)
    assert "You haven't added any tickers" not in text(response)


def test_an_admin_sees_every_ticker_with_controls(client):
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth, admin=True)
    fake_portfolio.list_result = [
        tracked("IWDA.AS", status="ready", origin="config"),
        tracked("ASML.AS", status="ready"),
        tracked("GHOST", status="failed", error="no price history"),
    ]

    response = test_client.get("/tickers")

    assert ("list", None, None, None) in fake_portfolio.calls
    assert 'action="/tickers/GHOST/retry"' in text(response)
    assert "no price history" in text(response)
    assert 'action="/tickers/ASML.AS/disable"' in text(response)
    # A default-list ticker is turned on and off in tracked_tickers.yaml, not here.
    assert 'action="/tickers/IWDA.AS/disable"' not in text(response)
    assert "Default list" in text(response)


# --- admin changes -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action, call",
    [
        ("retry", ("update", "GHOST", None, True)),
        ("disable", ("update", "GHOST", False, False)),
        ("enable", ("update", "GHOST", True, False)),
    ],
)
def test_an_admin_change(client, action, call):
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth, admin=True)
    token = _csrf(test_client)

    response = test_client.post(
        f"/tickers/GHOST/{action}", data={"csrf_token": token}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/tickers?changed=GHOST&change={action}"
    assert fake_portfolio.calls[-1] == call


def test_an_admin_change_is_forbidden_to_a_user(client):
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth)
    token = _csrf(test_client)

    response = test_client.post("/tickers/GHOST/retry", data={"csrf_token": token})

    assert response.status_code == 403
    assert not [c for c in fake_portfolio.calls if c[0] == "update"]


def test_a_refused_admin_change_says_why(client):
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth, admin=True)
    fake_portfolio.update_error = TickerChangeRefused("x")
    token = _csrf(test_client)

    response = test_client.post("/tickers/GHOST/retry", data={"csrf_token": token})

    assert response.status_code == 409
    assert "Couldn't change the ticker." in text(response)


def test_the_list_confirms_an_admin_change(client):
    test_client, fake_auth, _ = client
    _log_in(test_client, fake_auth, admin=True)

    response = test_client.get("/tickers", params={"changed": "GHOST", "change": "retry"})

    assert "GHOST is queued to load again." in text(response)


def test_the_admin_mappings_page_links_to_adding_a_ticker(client):
    # A broker instrument can only be mapped to a ticker Stakehold prices; the link is how an
    # admin gets one that isn't priced yet.
    test_client, fake_auth, _ = client
    _log_in(test_client, fake_auth, admin=True)

    response = test_client.get("/admin/mappings")

    assert 'href="/tickers/new"' in response.text


# --- the add page: the lookup beside the user's own tickers (UX-11) ------------------------------

# The two grid children: the form column (the lookup, then its result), then the list's card.
_SPLIT = re.compile(
    r'<div class="split split-form-start">\s*<div class="split-form-column">(.*?)</div>\s*'
    r'(<section class="card card-wide"[^>]*>.*?</section>)\s*</div>',
    re.DOTALL,
)


def _columns(response) -> tuple[str, str]:
    match = _SPLIT.search(response.text)
    assert match, "no form-and-list split on the add page"
    return match.group(1), match.group(2)


def test_the_add_page_puts_the_lookup_before_the_users_tickers(client):
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth)
    fake_portfolio.list_result = [tracked("ASML.AS", status="backfilling")]

    form, tickers = _columns(test_client.get("/tickers/new", params={"symbol": "ASML.AS"}))

    assert 'action="/tickers/new"' in form
    assert "Add ASML.AS" in form
    assert '<h2 id="your-tickers">Your tickers</h2>' in tickers
    assert "ASML.AS Holding" in tickers
    assert ">Loading<" in tickers
    assert 'href="/tickers"' in tickers


@pytest.mark.parametrize("admin", [False, True])
def test_the_add_page_lists_only_the_tickers_the_viewer_added(client, admin):
    """An admin too: their /tickers lists every ticker, but beside the form only their own add
    is the context, and the default list would bury it."""
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth, admin=admin)

    test_client.get("/tickers/new")

    assert ("list", "u1", None, None) in fake_portfolio.calls


def test_a_failed_ticker_on_the_add_page_says_an_admin_can_retry(client):
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth)
    fake_portfolio.list_result = [tracked("GHOST", status="failed", error="no price history")]

    _, tickers = _columns(test_client.get("/tickers/new"))

    assert ">Couldn&#39;t load<" in tickers
    assert "An admin can retry it." in tickers
    assert "/retry" not in tickers


def test_the_add_page_says_when_the_user_has_added_none(client):
    test_client, fake_auth, _ = client
    _log_in(test_client, fake_auth)

    _, tickers = _columns(test_client.get("/tickers/new"))

    assert "You haven't added any tickers yet." in tickers


def test_the_add_page_keeps_the_form_when_the_list_fails(client):
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth)
    fake_portfolio.list_error = PortfolioUnavailable("down")

    response = test_client.get("/tickers/new", params={"symbol": "ASML.AS"})

    assert response.status_code == 200
    form, tickers = _columns(response)
    assert "Add ASML.AS" in form
    assert "Can&#39;t reach the portfolio service" in tickers
    assert "haven't added" not in tickers


@pytest.mark.parametrize(
    "param, says",
    [
        ("added", "ASML.AS added. Prices usually load within a few minutes."),
        ("already", "ASML.AS was already in Stakehold. Nothing new was added."),
    ],
)
def test_the_add_page_confirms_an_add(client, param, says):
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth)
    fake_portfolio.list_result = [tracked("ASML.AS")]

    response = test_client.get("/tickers/new", params={param: "ASML.AS"})

    form, tickers = _columns(response)
    assert says in text(response)
    assert "ASML.AS" in tickers


def test_the_add_page_banner_only_echoes_a_ticker_shaped_value(client):
    test_client, fake_auth, _ = client
    _log_in(test_client, fake_auth)

    response = test_client.get("/tickers/new", params={"added": "<script>"})

    assert "added." not in text(response)


def test_a_failed_add_keeps_the_list_beside_the_form(client):
    test_client, fake_auth, fake_portfolio = client
    _log_in(test_client, fake_auth)
    fake_portfolio.add_error = TickerLimitReached("x")
    fake_portfolio.list_result = [tracked("ASML.AS")]
    token = _csrf(test_client)

    response = test_client.post("/tickers", data={"symbol": "NEW.AS", "csrf_token": token})

    assert response.status_code == 429
    form, tickers = _columns(response)
    assert "Try again tomorrow" in form
    assert "ASML.AS Holding" in tickers


# --- copy (UX-9) ---------------------------------------------------------------------------------


def test_the_add_page_says_how_long_prices_take_once(client):
    test_client, fake_auth, _ = client
    _log_in(test_client, fake_auth)

    response = test_client.get("/tickers/new", params={"symbol": "asml.as"})

    assert "Add ASML.AS" in text(response)
    assert text(response).count("within a few minutes") == 1
    intro = re.search(r'<p class="entry-subtitle">(.*?)</p>', response.text, re.S).group(1)
    assert len([s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", intro) if s]) == 1, intro
