"""The tracked-ticker API end to end: real app, real auth, real Postgres. Only the provider is
faked -- no integration test talks to Yahoo."""

import os
from pathlib import Path

import psycopg
import pytest
from data_store import ticker_onboarding
from data_store.api.app import create_app
from data_store.tracked_tickers.base import (
    OUTCOME_FOUND,
    OUTCOME_NOT_FOUND,
    LookupResult,
)
from fastapi.testclient import TestClient
from shared.db.connection import apply_schema

pytestmark = pytest.mark.integration

SCHEMA_PATH = Path(__file__).parents[2] / "src" / "data_store" / "db" / "schema.sql"
SECRET = "integration-test-secret"  # pragma: allowlist secret
AUTH = {"Authorization": f"Bearer {SECRET}"}
ALICE = "00000000-0000-0000-0000-00000000000a"


class FakeSource:
    name = "yfinance"
    known = {"ASML.AS", "SPY"}

    def lookup(self, ticker):
        if ticker not in self.known:
            return LookupResult(OUTCOME_NOT_FOUND, ticker, None, None, None, None)
        return LookupResult(OUTCOME_FOUND, ticker, f"{ticker} name", "EUR", "Amsterdam", "equity")


@pytest.fixture
def api(monkeypatch):
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    apply_schema(conn, SCHEMA_PATH)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM tracked_tickers")
    conn.commit()

    monkeypatch.setenv("INTERNAL_API_SECRET", SECRET)
    monkeypatch.setenv("DATA_STORE_TICKER_REQUESTS_PER_DAY", "1")
    monkeypatch.setitem(ticker_onboarding.TICKER_LOOKUP_SOURCES, "yfinance", FakeSource)
    monkeypatch.setattr(ticker_onboarding, "LOOKUP_CACHE", ticker_onboarding.LookupCache())
    yield TestClient(create_app())

    with conn.cursor() as cur:
        cur.execute("DELETE FROM tracked_tickers")
    conn.commit()
    conn.close()


def add(api, ticker, *, enforce_limit=True):
    return api.post(
        "/tracked-tickers",
        json={"ticker": ticker, "requested_by": ALICE, "enforce_limit": enforce_limit},
        headers=AUTH,
    )


def test_the_tracked_ticker_routes_need_the_secret(api):
    assert api.get("/tracked-tickers").status_code == 401
    assert api.post("/tracked-tickers", json={}).status_code == 401
    assert api.get("/tickers/ASML.AS/lookup").status_code == 401


def test_add_then_look_up_then_list_then_see_it_in_ticker_info(api):
    assert api.get("/tickers/asml.as/lookup", headers=AUTH).json()["outcome"] == "available"

    response = add(api, "asml.as")
    assert response.status_code == 201
    assert response.json()["status"] == "pending"

    lookup = api.get("/tickers/ASML.AS/lookup", headers=AUTH).json()
    assert (lookup["outcome"], lookup["status"]) == ("tracked", "pending")

    listed = api.get("/tracked-tickers", params={"requested_by": ALICE}, headers=AUTH).json()
    assert [t["ticker"] for t in listed["tracked_tickers"]] == ["ASML.AS"]

    info = api.get("/tickers/ASML.AS", headers=AUTH).json()
    assert (info["exists"], info["status"]) == (False, "pending")


def test_adding_twice_is_200_and_free(api):
    add(api, "ASML.AS")

    response = add(api, "ASML.AS")

    assert response.status_code == 200
    assert response.json()["created"] is False


def test_the_daily_limit_applies_to_users_but_not_admins(api):
    assert add(api, "ASML.AS").status_code == 201

    assert add(api, "SPY").status_code == 429
    assert add(api, "SPY", enforce_limit=False).status_code == 201


def test_an_unknown_symbol_is_404_and_an_invalid_one_422(api):
    assert add(api, "NOPE").status_code == 404
    assert add(api, "AS ML").status_code == 422


def test_admin_disable_and_retry(api):
    add(api, "ASML.AS")

    disabled = api.patch("/tracked-tickers/ASML.AS", json={"enabled": False}, headers=AUTH)
    assert (disabled.status_code, disabled.json()["enabled"]) == (200, False)

    # Not failed, so there's nothing to retry.
    assert (
        api.patch("/tracked-tickers/ASML.AS", json={"retry": True}, headers=AUTH).status_code == 409
    )
    assert api.patch("/tracked-tickers/NOPE", json={"retry": True}, headers=AUTH).status_code == 404
