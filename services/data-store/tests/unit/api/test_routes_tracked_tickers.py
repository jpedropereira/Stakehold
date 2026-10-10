"""The tracked-ticker routes: request shape, and which flow exception becomes which status code.
The jobs are stubbed; what they do is tested in test_jobs.py and test_ticker_onboarding_*.py."""

from datetime import datetime, timezone

import pytest
from data_store import jobs, ticker_onboarding

ALICE = "00000000-0000-0000-0000-00000000000a"


def row(**overrides):
    return {
        "ticker": "ASML.AS",
        "source": "yfinance",
        "display_name": "ASML Holding N.V.",
        "asset_type": "equity",
        "currency": "EUR",
        "exchange": "Amsterdam",
        "origin": "request",
        "status": "pending",
        "enabled": True,
        "attempts": 0,
        "last_error": None,
        "requested_by": ALICE,
        "requested_at": datetime(2026, 10, 10, tzinfo=timezone.utc),
        "ready_at": None,
        **overrides,
    }


def raising(error):
    def stub(**kwargs):
        raise error

    return stub


# --- lookup --------------------------------------------------------------------------------------


def test_lookup_returns_the_flow_answer(monkeypatch, client):
    calls = []
    monkeypatch.setattr(
        jobs,
        "get_ticker_lookup",
        lambda **kw: calls.append(kw)
        or {
            "ticker": "ASML.AS",
            "outcome": "available",
            "status": None,
            "display_name": "ASML Holding N.V.",
            "currency": "EUR",
            "exchange": "Amsterdam",
            "asset_type": "equity",
        },
    )

    response = client.get("/tickers/asml.as/lookup")

    assert response.status_code == 200
    assert response.json()["outcome"] == "available"
    assert calls == [{"ticker": "asml.as", "source": "yfinance"}]


@pytest.mark.parametrize(
    "error, status",
    [
        (ticker_onboarding.InvalidSymbol("x"), 422),
        (ticker_onboarding.UnknownLookupSource("x"), 422),
    ],
)
def test_lookup_errors(monkeypatch, client, error, status):
    monkeypatch.setattr(jobs, "get_ticker_lookup", raising(error))

    assert client.get("/tickers/X/lookup").status_code == status


# --- add -----------------------------------------------------------------------------------------


def test_adding_a_new_ticker_is_201(monkeypatch, client):
    calls = []
    monkeypatch.setattr(
        jobs, "run_add_tracked_ticker", lambda **kw: calls.append(kw) or row(created=True)
    )

    response = client.post("/tracked-tickers", json={"ticker": "asml.as", "requested_by": ALICE})

    assert response.status_code == 201
    body = response.json()
    assert (body["ticker"], body["status"], body["created"]) == ("ASML.AS", "pending", True)
    assert calls == [
        {"ticker": "asml.as", "requested_by": ALICE, "enforce_limit": True, "source": "yfinance"}
    ]


def test_adding_a_ticker_already_tracked_is_200(monkeypatch, client):
    monkeypatch.setattr(
        jobs, "run_add_tracked_ticker", lambda **kw: row(status="ready", created=False)
    )

    response = client.post(
        "/tracked-tickers",
        json={"ticker": "ASML.AS", "requested_by": ALICE, "enforce_limit": False},
    )

    assert response.status_code == 200
    assert response.json()["created"] is False


def test_adding_needs_a_requester(client):
    assert client.post("/tracked-tickers", json={"ticker": "ASML.AS"}).status_code == 422


@pytest.mark.parametrize(
    "error, status",
    [
        (ticker_onboarding.InvalidSymbol("x"), 422),
        (ticker_onboarding.UnknownLookupSource("x"), 422),
        (ticker_onboarding.CannotBeHeld("S&P 500"), 422),
        (ticker_onboarding.NotOnProvider("x"), 404),
        (ticker_onboarding.RequestLimitReached(10), 429),
        (ticker_onboarding.ProviderUnavailable("x"), 503),
    ],
)
def test_add_errors(monkeypatch, client, error, status):
    monkeypatch.setattr(jobs, "run_add_tracked_ticker", raising(error))

    response = client.post("/tracked-tickers", json={"ticker": "X", "requested_by": ALICE})

    assert response.status_code == status


# --- list / update -------------------------------------------------------------------------------


def test_list_passes_filters(monkeypatch, client):
    calls = []
    monkeypatch.setattr(
        jobs, "get_tracked_tickers", lambda **kw: calls.append(kw) or {"tracked_tickers": [row()]}
    )

    response = client.get(
        "/tracked-tickers",
        params=[
            ("requested_by", ALICE),
            ("status", "pending"),
            ("status", "failed"),
            ("ticker", "ASML.AS"),
        ],
    )

    assert response.status_code == 200
    assert [r["ticker"] for r in response.json()["tracked_tickers"]] == ["ASML.AS"]
    assert calls == [
        {
            "source": "yfinance",
            "requested_by": ALICE,
            "statuses": ["pending", "failed"],
            "tickers": ["ASML.AS"],
        }
    ]


def test_list_without_filters(monkeypatch, client):
    calls = []
    monkeypatch.setattr(
        jobs, "get_tracked_tickers", lambda **kw: calls.append(kw) or {"tracked_tickers": []}
    )

    client.get("/tracked-tickers")

    assert calls == [
        {"source": "yfinance", "requested_by": None, "statuses": None, "tickers": None}
    ]


def test_update(monkeypatch, client):
    calls = []
    monkeypatch.setattr(
        jobs, "run_update_tracked_ticker", lambda **kw: calls.append(kw) or row(enabled=False)
    )

    response = client.patch("/tracked-tickers/ASML.AS", json={"enabled": False})

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert calls == [{"ticker": "ASML.AS", "source": "yfinance", "enabled": False, "retry": False}]


@pytest.mark.parametrize(
    "error, status",
    [
        (ticker_onboarding.TrackedTickerNotFound("x"), 404),
        (ticker_onboarding.ManagedByConfig("x"), 409),
        (ticker_onboarding.NotRetryable("ready"), 409),
    ],
)
def test_update_errors(monkeypatch, client, error, status):
    monkeypatch.setattr(jobs, "run_update_tracked_ticker", raising(error))

    assert client.patch("/tracked-tickers/X", json={"retry": True}).status_code == status
