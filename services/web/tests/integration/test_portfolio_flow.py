"""End-to-end web <-> portfolio round trip through both services' real code.

HttpPortfolioClient is pointed at portfolio's real ASGI app via httpx.ASGITransport instead of a
network call -- the same technique test_auth_flow.py uses for auth. portfolio's create_app() has
no lifespan (api/app.py's own docstring: each job call opens/closes its own connection), so
unlike auth's app this needs no manual lifespan_context driving.

Scoped to web <-> portfolio only: require_user/optional_user are overridden to a fixed AuthUser
directly, rather than standing up a third live service for a session round trip that
test_auth_flow.py already covers -- a full three-service walk is task 13's job, not this one. The
happy-path broker-file-upload flow is deliberately not covered here either: parsing a real export
is portfolio's own tested territory (and this repo's dev venv has no broker plugin installed per
task 15), so only the reliably-reproducible BrokerParserNotFound path is exercised.
"""

import os
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from portfolio import cli as portfolio_cli
from portfolio import jobs as portfolio_jobs
from portfolio.api.app import create_app as create_portfolio_app
from portfolio.datastore_client.base import TickerInfo
from portfolio.settings import Settings as PortfolioSettings
from web.api.app import create_app as create_web_app
from web.api.deps import (
    get_auth_client,
    get_portfolio_client,
    optional_user,
    require_user,
)
from web.auth_client.base import AuthResult, AuthUser
from web.portfolio_client.http_client import HttpPortfolioClient
from web.settings import Settings as WebSettings

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

_PORTFOLIO_SECRET = "integration-test-portfolio-secret"  # pragma: allowlist secret
_BRAND_DIR = Path(__file__).resolve().parents[4] / "brand"
_USER = AuthUser(
    user_id="11111111-1111-1111-1111-111111111111", email="ann@example.com", display_name="Ann"
)


def _connect():
    return psycopg.connect(os.environ["PORTFOLIO_DATABASE_URL"])


@pytest.fixture(autouse=True)
def _clean_portfolio_tables():
    portfolio_cli.main(["db", "init"])
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE return_snapshots, position_snapshots, transactions, accounts, "
                "ticker_mappings, saved_lab_assets, saved_labs CASCADE"
            )
        conn.commit()
    yield


def _portfolio_settings(*, with_data_store: bool = False) -> PortfolioSettings:
    return PortfolioSettings(
        database_url=os.environ["PORTFOLIO_DATABASE_URL"],
        admin_database_url=os.environ["APP_POSTGRES_ADMIN_DATABASE_URL"],
        # Only the data-store-backed fixture below sets these -- and its HttpDataStoreClient is
        # replaced by _FakeDataStoreClient, so nothing is ever sent to this URL.
        datastore_api_url="https://datastore.internal" if with_data_store else None,
        internal_api_secret=(
            "unused-in-this-suite" if with_data_store else None  # pragma: allowlist secret
        ),
        api_internal_secret=_PORTFOLIO_SECRET,
        api_host="0.0.0.0",
        api_port=8002,
    )


def _web_settings() -> WebSettings:
    return WebSettings(
        auth_api_url="https://auth.internal",
        internal_secret="unused-in-this-suite",  # pragma: allowlist secret
        portfolio_api_url="https://portfolio.internal",
        portfolio_internal_secret=_PORTFOLIO_SECRET,
        brand_dir=_BRAND_DIR,
        cookie_secure=True,
        api_host="127.0.0.1",
        api_port=8000,
        http_timeout_seconds=5.0,
    )


async def _make_web_client(*, with_data_store: bool = False, user: AuthUser = _USER):
    settings = _web_settings()
    web_app = create_web_app(settings)
    portfolio_app = create_portfolio_app(_portfolio_settings(with_data_store=with_data_store))

    # https base_url: WebSettings.cookie_secure=True marks the CSRF cookie Secure, and httpx's
    # cookie jar won't store/resend a Secure cookie over plain http -- same reasoning as
    # test_auth_flow.py's own web_client fixture.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=portfolio_app), base_url=settings.portfolio_api_url
    ) as portfolio_test_client:
        real_portfolio_client = HttpPortfolioClient(portfolio_test_client, _PORTFOLIO_SECRET)
        web_app.dependency_overrides[get_portfolio_client] = lambda: real_portfolio_client
        web_app.dependency_overrides[require_user] = lambda: user
        web_app.dependency_overrides[optional_user] = lambda: user

        with TestClient(web_app, base_url="https://testserver") as test_client:
            yield test_client


@pytest.fixture
async def web_client():
    async for test_client in _make_web_client():
        yield test_client


class _FakeDataStoreClient:
    """Stands in for data-store inside the real portfolio app -- data-store isn't part of this
    suite. Knows exactly one ticker."""

    def __init__(self, base_url, internal_api_secret):
        pass

    def list_tickers(self):
        return ["IWDA.AS"]

    def get_ticker_info(self, ticker):
        known = ticker == "IWDA.AS"
        return TickerInfo(exists=known, has_returns=known)

    def list_tracked_tickers(self, *, requested_by=None, statuses=None, tickers=None):
        # Nothing is tracked directly here (no ticker onboarding in this suite), so a holding
        # without prices reads "Waiting for prices".
        return []


@pytest.fixture
async def web_client_with_data_store(monkeypatch):
    """Same wiring as web_client, with portfolio configured for a data-store that
    _FakeDataStoreClient answers for -- manual TICKER entry and the ticker picker both need
    one."""
    monkeypatch.setattr(portfolio_jobs, "HttpDataStoreClient", _FakeDataStoreClient)
    async for test_client in _make_web_client(with_data_store=True):
        yield test_client


_ADMIN = AuthUser(
    user_id="99999999-9999-7999-8999-999999999999",
    email="ada@example.com",
    display_name="Ada",
    is_admin=True,
)


@pytest.fixture
async def admin_web_client(monkeypatch):
    """web_client_with_data_store, logged in as an admin -- the mapping page checks a ticker
    against data-store when it's saved."""
    monkeypatch.setattr(portfolio_jobs, "HttpDataStoreClient", _FakeDataStoreClient)
    async for test_client in _make_web_client(with_data_store=True, user=_ADMIN):
        yield test_client


def _csrf_token(client, path, **params) -> str:
    client.get(path, params=params)
    return next(v for k, v in client.cookies.items() if "csrf" in k)


def _create_account(client, label="main", broker="acme"):
    token = _csrf_token(client, "/accounts")
    return client.post(
        "/accounts",
        data={"broker": broker, "account_label": label, "csrf_token": token},
        follow_redirects=False,
    )


def _transaction_form(csrf_token: str, **overrides) -> dict:
    data = {
        "account_label": "main",
        "broker_transaction_ref": "ref-1",
        "transaction_type": "DEPOSIT",
        "trade_datetime": "2026-01-05T12:00",
        "native_amount": "100.00",
        "native_currency": "EUR",
        "instrument_identifier": "",
        "quantity": "",
        "price": "",
        "fx_rate": "",
        "fees_native": "",
        "csrf_token": csrf_token,
    }
    data.update(overrides)
    return data


async def test_create_account_then_accounts_page_lists_it(web_client):
    response = _create_account(web_client)

    assert response.status_code == 303
    assert response.headers["location"] == "/accounts"

    list_response = web_client.get("/accounts")
    assert "main" in list_response.text
    assert "acme" in list_response.text


async def test_add_transaction_then_resubmitting_same_ref_does_not_duplicate(web_client):
    _create_account(web_client)

    first_token = _csrf_token(web_client, "/transactions/new", account="main")
    first = web_client.post(
        "/transactions/new", data=_transaction_form(first_token), follow_redirects=False
    )
    assert first.status_code == 303
    # UI-7: the redirect also carries portfolio's follow-on rebuild statuses.
    assert first.headers["location"].startswith("/transactions/new?account=main&saved=1&positions=")

    second_token = _csrf_token(web_client, "/transactions/new", account="main")
    second = web_client.post(
        "/transactions/new", data=_transaction_form(second_token), follow_redirects=False
    )
    assert second.status_code == 303
    # Nothing was saved, so portfolio ran no rebuild.
    assert second.headers["location"] == (
        "/transactions/new?account=main&saved=0&positions=skipped&returns=skipped"
    )

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM transactions WHERE broker_transaction_ref = %s", ("ref-1",)
            )
            (count,) = cur.fetchone()
    assert count == 1


async def test_add_transaction_for_unknown_account_surfaces_404(web_client):
    token = _csrf_token(web_client, "/transactions/new", account="ghost")

    response = web_client.post(
        "/transactions/new",
        data=_transaction_form(token, account_label="ghost"),
    )

    assert response.status_code == 404
    assert "doesn&#39;t exist" in response.text


@pytest.mark.parametrize(
    "importer, message",
    [("transactions", "no parser registered"), ("cashflow", "no cashflow parser registered")],
)
async def test_import_with_no_registered_parser_surfaces_broker_parser_not_found(
    web_client_with_data_store, importer, message
):
    """The importer choice reaches the matching portfolio route: each one checks its own
    registry and names it in the error. With data-store configured, since a cashflow import
    refuses to start without one before it ever looks for a parser."""
    web_client = web_client_with_data_store
    _create_account(web_client)

    token = _csrf_token(web_client, "/transactions/import", account="main")
    response = web_client.post(
        "/transactions/import",
        data={"account_label": "main", "importer": importer, "csrf_token": token},
        files={"file": ("export.csv", b"a,b\n1,2\n", "text/csv")},
    )

    assert response.status_code == 422
    assert message in response.text.lower()


# --- holdings view: GET /accounts/holdings -----------------------------------


async def test_holdings_page_shows_empty_state_before_any_rebuild(web_client):
    _create_account(web_client)

    response = web_client.get("/accounts/holdings", params={"account": "main"})

    assert response.status_code == 200
    assert "No positions yet" in response.text
    assert "No return history yet" in response.text
    assert "Can&#39;t reach the portfolio service" not in response.text


async def test_holdings_page_for_unknown_account_is_404(web_client):
    response = web_client.get("/accounts/holdings", params={"account": "ghost"})

    assert response.status_code == 404
    assert "doesn't exist" in response.text


async def test_a_ticker_picked_in_the_form_shows_up_as_a_position(web_client_with_data_store):
    """The full manual-entry path the original bug report hit: the form offers data-store's
    tickers, the picked one is saved as a TICKER, and the position appears with no ticker mapping
    and no manual rebuild. rebuild-returns needs a live data-store, so the returns section still
    shows its own empty state -- itself worth asserting: positions without computed returns must
    not look like a service failure."""
    web_client = web_client_with_data_store
    _create_account(web_client)

    form = web_client.get("/transactions/new", params={"account": "main"})
    token = next(v for k, v in web_client.cookies.items() if "csrf" in k)
    buy_response = web_client.post(
        "/transactions/new",
        data=_transaction_form(
            token,
            broker_transaction_ref="buy-1",
            transaction_type="BUY",
            instrument_identifier="IWDA.AS",
            quantity="10",
            price="95.00",
            native_amount="-950.00",
        ),
        follow_redirects=False,
    )
    response = web_client.get("/accounts/holdings", params={"account": "main"})

    assert '<option value="IWDA.AS">' in form.text
    assert buy_response.status_code == 303
    assert response.status_code == 200
    assert "IWDA.AS" in response.text
    assert "No return history yet" in response.text


def _edit_form(csrf_token: str, **overrides) -> dict:
    data = {
        "account": "main",
        "ref": "buy-1",
        "transaction_type": "BUY",
        "trade_datetime": "2026-01-05T12:00",
        "native_amount": "",
        "native_currency": "EUR",
        "instrument_identifier": "IWDA.AS",
        "original_instrument_identifier": "IWDA.AS",
        "original_instrument_identifier_kind": "TICKER",
        "quantity": "10",
        "price": "95",
        "fx_rate": "",
        "fees_native": "",
        "csrf_token": csrf_token,
    }
    data.update(overrides)
    return data


async def test_editing_a_buys_quantity_updates_the_log_and_the_holding(
    web_client_with_data_store,
):
    """The edit round trip: the edit page reads the row back from portfolio, a save goes
    through portfolio's PUT and its rebuild, and the holding follows. Saving the row unchanged
    is reported as nothing changed."""
    web_client = web_client_with_data_store
    _create_account(web_client)
    token = _csrf_token(web_client, "/transactions/new", account="main")
    web_client.post(
        "/transactions/new",
        data=_transaction_form(
            token,
            broker_transaction_ref="buy-1",
            transaction_type="BUY",
            instrument_identifier="IWDA.AS",
            quantity="10",
            price="95",
            native_amount="",
        ),
        follow_redirects=False,
    )

    page = web_client.get("/transactions/edit", params={"account": "main", "ref": "buy-1"})
    token = next(v for k, v in web_client.cookies.items() if "csrf" in k)
    unchanged = web_client.post(
        "/transactions/edit", data=_edit_form(token), follow_redirects=False
    )
    edited = web_client.post(
        "/transactions/edit", data=_edit_form(token, quantity="12"), follow_redirects=False
    )
    log = web_client.get(edited.headers["location"])
    holdings = web_client.get("/accounts/holdings", params={"account": "main"})

    assert page.status_code == 200
    assert 'name="quantity" value="10"' in page.text
    assert unchanged.headers["location"] == "/transactions?account=main&edited=0"
    assert edited.headers["location"].startswith("/transactions?account=main&edited=1&positions=")
    assert "Transaction updated." in log.text
    assert "-1,140.00 EUR" in log.text.replace("\u2212", "-")
    assert ">12<" in holdings.text


async def test_an_admin_maps_an_imported_isin_and_the_users_position_appears(admin_web_client):
    # Ann's account holds an ISIN-reported BUY -- what a broker import produces -- that nothing
    # maps yet. Entered through portfolio's CLI because web's own form only sends TICKERs.
    portfolio_cli.main(
        ["account", "add", "--user", _USER.user_id, "--broker", "acme", "--label", "main"]
    )
    portfolio_cli.main(
        [
            "add-transaction",
            "--user",
            _USER.user_id,
            "--account",
            "main",
            "--ref",
            "imported-1",
            "--type",
            "BUY",
            "--datetime",
            "2026-01-05T10:00:00+00:00",
            "--currency",
            "EUR",
            "--instrument",
            "IE00B4L5Y983",
            "--instrument-kind",
            "ISIN",
            "--quantity",
            "10",
            "--price",
            "95.00",
        ]
    )
    page = admin_web_client.get("/admin/mappings")
    assert "IE00B4L5Y983" in page.text

    response = admin_web_client.post(
        "/admin/mappings",
        data={
            "identifier_kind": "ISIN",
            "identifier": "IE00B4L5Y983",
            "broker": "",
            "canonical_ticker": "IWDA.AS",
            "csrf_token": _csrf_token(admin_web_client, "/admin/mappings"),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/mappings?saved=1&rebuilt=1&failed=0"
    after = admin_web_client.get("/admin/mappings")
    assert "Every instrument is mapped." in after.text
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT canonical_ticker FROM position_snapshots")
        assert [row[0] for row in cur.fetchall()] == ["IWDA.AS"]


# --- the combined page: GET /portfolio (PF-4) ---------------------------------------------------


class _LoggedInAuthClient:
    """/portfolio resolves its user inside the handler, so the redirect for ?account= never asks
    auth, which means overriding require_user doesn't reach it. Answering for auth does."""

    async def validate_session(self, *, cookie_header):
        return AuthResult(user=_USER, set_cookie="stakehold_session=tok; Path=/")


def _log_in_to_portfolio_page(client):
    client.app.dependency_overrides[get_auth_client] = _LoggedInAuthClient
    client.cookies.set("stakehold_session", "tok")


async def test_portfolio_page_with_no_accounts_shows_the_first_run_card(web_client):
    _log_in_to_portfolio_page(web_client)

    response = web_client.get("/portfolio")

    assert response.status_code == 200
    assert "No accounts yet" in response.text


async def test_portfolio_page_sums_a_ticker_held_in_two_accounts(web_client_with_data_store):
    """PF-3's client against portfolio's real GET /holdings: the same ticker bought in two
    accounts is one row, its units summed, with both accounts in its breakdown. No prices exist
    in this suite, so the row waits for them rather than showing a partial value -- and (PF-6)
    portfolio's real GET /returns/portfolio answers with no series yet, which the chart card
    reads as no history rather than as a failure."""
    web_client = web_client_with_data_store
    for label, ref, quantity in (("main", "buy-1", "10"), ("isa", "buy-2", "5")):
        _create_account(web_client, label=label)
        token = _csrf_token(web_client, "/transactions/new", account=label)
        buy = web_client.post(
            "/transactions/new",
            data=_transaction_form(
                token,
                account_label=label,
                broker_transaction_ref=ref,
                transaction_type="BUY",
                instrument_identifier="IWDA.AS",
                quantity=quantity,
                price="95.00",
                native_amount=f"-{Decimal(quantity) * 95}",
            ),
            follow_redirects=False,
        )
        assert buy.status_code == 303
    _log_in_to_portfolio_page(web_client)

    response = web_client.get("/portfolio")

    assert response.status_code == 200
    assert response.text.count('<tr role="row" class="holding-row">') == 1
    row = response.text.split('class="holding-row">', 1)[1].split("</tr>", 1)[0]
    assert "IWDA.AS" in row and ">15<" in row
    assert "Waiting for prices" in row
    breakdown = response.text.split('class="breakdown-row"', 1)[1].split("</table>", 1)[0]
    assert 'href="/accounts/holdings?account=isa"' in breakdown
    assert 'href="/accounts/holdings?account=main"' in breakdown
    assert "No return history yet" in response.text
    assert "Couldn't load your returns" not in response.text


def _post_form(client, path, pairs):
    """A form post with repeated keys, encoded by hand: httpx sends a list of pairs as raw
    content."""
    return client.post(
        path,
        content=urlencode(pairs),
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )


async def test_a_lab_is_saved_listed_replaced_and_deleted_through_portfolio(web_client):
    """LF-1 end to end: web's save form, portfolio's POST /labs/saved and its tables, and back.
    No data-store in this suite, so the page's backtest fails; the saved labs list doesn't
    depend on it."""
    token = _csrf_token(web_client, "/accounts")
    mix = [("ticker", "IWDA.AS"), ("weight", "60"), ("ticker", "AAPL"), ("weight", "40")]

    saved = _post_form(web_client, "/labs/saved", [("csrf_token", token), ("name", "Core"), *mix])
    assert saved.status_code == 303
    page = web_client.get(saved.headers["location"])
    assert page.status_code == 200
    assert "Saved." in page.text and "This mix is your lab Core." in page.text
    assert '<a class="saved-lab-name" href="/labs?' in page.text

    other = [("ticker", "AAPL"), ("weight", "100")]
    taken = _post_form(web_client, "/labs/saved", [("csrf_token", token), ("name", "Core"), *other])
    assert taken.status_code == 200
    assert "Replace this lab?" in taken.text
    replaced = _post_form(
        web_client,
        "/labs/saved",
        [("csrf_token", token), ("name", "Core"), ("replace", "1"), *other],
    )
    assert replaced.status_code == 303
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT a.ticker, a.weight_pct FROM portfolio.saved_labs l "
            "JOIN portfolio.saved_lab_assets a ON a.saved_lab_id = l.id WHERE l.user_id = %s",
            (_USER.user_id,),
        )
        assert cur.fetchall() == [("AAPL", Decimal("100.000000"))]

    confirm = web_client.get("/labs/saved/delete", params={"name": "Core"})
    assert confirm.status_code == 200
    assert "AAPL 100%" in confirm.text
    deleted = _post_form(
        web_client, "/labs/saved/delete", [("csrf_token", token), ("name", "Core"), ("back", "")]
    )
    assert deleted.headers["location"] == "/labs?lab_deleted=1"
    again = _post_form(
        web_client, "/labs/saved/delete", [("csrf_token", token), ("name", "Core"), ("back", "")]
    )
    assert again.headers["location"] == "/labs?lab_deleted=0"
