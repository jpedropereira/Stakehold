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
import uuid
from pathlib import Path

import httpx
import psycopg
import pytest
import yaml
from fastapi.testclient import TestClient
from portfolio import cli as portfolio_cli
from portfolio.api.app import create_app as create_portfolio_app
from portfolio.jobs import run_rebuild_positions
from portfolio.settings import Settings as PortfolioSettings
from web.api.app import create_app as create_web_app
from web.api.deps import get_portfolio_client, optional_user, require_user
from web.auth_client.base import AuthUser
from web.portfolio_client.http_client import HttpPortfolioClient
from web.settings import Settings as WebSettings

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

_PORTFOLIO_SECRET = "integration-test-portfolio-secret"  # pragma: allowlist secret
_BRAND_DIR = Path(__file__).resolve().parents[4] / "brand"
_ISIN = "IE00B4L5Y983"
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
                "TRUNCATE return_snapshots, position_snapshots, transactions, accounts CASCADE"
            )
        conn.commit()
    yield


def _portfolio_settings(*, ticker_mappings_path: Path | None = None) -> PortfolioSettings:
    return PortfolioSettings(
        database_url=os.environ["PORTFOLIO_DATABASE_URL"],
        admin_database_url=os.environ["APP_POSTGRES_ADMIN_DATABASE_URL"],
        # Unused by most flows here -- only rebuild-positions needs a real mapping, and that test
        # passes its own throwaway path (the committed configs/ticker_mappings.yaml is real,
        # gitignored broker data per task 15 and doesn't exist in a dev checkout).
        ticker_mappings_path=ticker_mappings_path or Path("configs/ticker_mappings.yaml"),
        datastore_api_url=None,
        internal_api_secret=None,
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


async def _make_web_client(*, ticker_mappings_path: Path | None = None):
    settings = _web_settings()
    web_app = create_web_app(settings)
    portfolio_app = create_portfolio_app(
        _portfolio_settings(ticker_mappings_path=ticker_mappings_path)
    )

    # https base_url: WebSettings.cookie_secure=True marks the CSRF cookie Secure, and httpx's
    # cookie jar won't store/resend a Secure cookie over plain http -- same reasoning as
    # test_auth_flow.py's own web_client fixture.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=portfolio_app), base_url=settings.portfolio_api_url
    ) as portfolio_test_client:
        real_portfolio_client = HttpPortfolioClient(portfolio_test_client, _PORTFOLIO_SECRET)
        web_app.dependency_overrides[get_portfolio_client] = lambda: real_portfolio_client
        web_app.dependency_overrides[require_user] = lambda: _USER
        web_app.dependency_overrides[optional_user] = lambda: _USER

        with TestClient(web_app, base_url="https://testserver") as test_client:
            yield test_client


@pytest.fixture
async def web_client():
    async for test_client in _make_web_client():
        yield test_client


@pytest.fixture
async def web_client_with_ticker_mapping(tmp_path):
    """Same wiring as web_client, plus a throwaway ticker mapping so
    test_portfolio_page_lists_position_after_rebuild_positions' BUY transaction resolves to a
    canonical_ticker -- the same override test_rebuild_positions.py's own integration suite uses,
    since the committed configs/ticker_mappings.yaml is real, gitignored broker data (task 15)
    that doesn't exist in a dev checkout."""
    path = tmp_path / "ticker_mappings.yaml"
    path.write_text(yaml.dump({"isin": {_ISIN: "IWDA.AS"}}))
    async for test_client in _make_web_client(ticker_mappings_path=path):
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
        "instrument_identifier_kind": "",
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
    assert first.headers["location"] == "/transactions/new?account=main&saved=1"

    second_token = _csrf_token(web_client, "/transactions/new", account="main")
    second = web_client.post(
        "/transactions/new", data=_transaction_form(second_token), follow_redirects=False
    )
    assert second.status_code == 303

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


async def test_import_with_no_registered_parser_surfaces_broker_parser_not_found(web_client):
    _create_account(web_client)

    token = _csrf_token(web_client, "/transactions/import", account="main")
    response = web_client.post(
        "/transactions/import",
        data={"account_label": "main", "csrf_token": token},
        files={"file": ("export.csv", b"a,b\n1,2\n", "text/csv")},
    )

    assert response.status_code == 422
    assert "no parser registered" in response.text.lower()


# --- portfolio view: GET /portfolio -------------------------------------------


async def test_portfolio_page_shows_empty_state_before_any_rebuild(web_client):
    _create_account(web_client)

    response = web_client.get("/portfolio", params={"account": "main"})

    assert response.status_code == 200
    assert "No positions yet" in response.text
    assert "No return history yet" in response.text
    assert "Can&#39;t reach the portfolio service" not in response.text


async def test_portfolio_page_for_unknown_account_is_404(web_client):
    response = web_client.get("/portfolio", params={"account": "ghost"})

    assert response.status_code == 404
    assert "doesn't exist" in response.text


async def test_portfolio_page_lists_position_after_rebuild_positions(
    web_client_with_ticker_mapping, tmp_path
):
    """rebuild-returns needs a live data-store (unavailable in this dev environment, see the
    module docstring), so this only exercises the positions half of the view -- the returns
    section still shows its own empty state, which is itself worth asserting: a portfolio with
    positions but no computed returns yet must not look like a service failure.

    tmp_path is requested here too so it resolves to the same directory
    web_client_with_ticker_mapping already wrote the throwaway ticker-mappings file into --
    pytest caches a function-scoped fixture once per test, shared across every fixture/test that
    asks for it.
    """
    web_client = web_client_with_ticker_mapping
    _create_account(web_client)

    token = _csrf_token(web_client, "/transactions/new", account="main")
    buy_response = web_client.post(
        "/transactions/new",
        data=_transaction_form(
            token,
            broker_transaction_ref="buy-1",
            transaction_type="BUY",
            instrument_identifier=_ISIN,
            instrument_identifier_kind="ISIN",
            quantity="10",
            price="95.00",
            native_amount="-950.00",
        ),
        follow_redirects=False,
    )
    assert buy_response.status_code == 303

    run_rebuild_positions(
        user_id=uuid.UUID(_USER.user_id),
        account_label="main",
        settings=_portfolio_settings(ticker_mappings_path=tmp_path / "ticker_mappings.yaml"),
    )

    response = web_client.get("/portfolio", params={"account": "main"})

    assert response.status_code == 200
    assert "IWDA.AS" in response.text
    assert "No return history yet" in response.text
