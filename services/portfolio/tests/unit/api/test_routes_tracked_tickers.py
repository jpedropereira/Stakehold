"""Route wiring only for the tracked-ticker pass-through. How TickerRequestRefused keeps
data-store's status code is covered by test_app.py's per-subclass mapping and by
test_http_client_tracked_tickers.py."""

import uuid

from portfolio import jobs

ALICE = uuid.UUID("00000000-0000-0000-0000-00000000000a")
ROW = {
    "ticker": "ASML.AS",
    "display_name": "ASML Holding N.V.",
    "asset_type": "equity",
    "currency": "EUR",
    "exchange": "Amsterdam",
    "origin": "request",
    "status": "pending",
    "enabled": True,
    "attempts": 0,
    "last_error": None,
    "requested_by": str(ALICE),
    "requested_at": "2026-10-10T12:00:00Z",
    "ready_at": None,
}


def test_lookup(monkeypatch, client):
    calls = []
    monkeypatch.setattr(
        jobs,
        "get_ticker_lookup",
        lambda **kw: calls.append(kw["ticker"])
        or {
            "ticker": "ASML.AS",
            "outcome": "tracked",
            "status": "pending",
            "display_name": "ASML Holding N.V.",
            "currency": "EUR",
            "exchange": "Amsterdam",
            "asset_type": "equity",
        },
    )

    response = client.get("/instruments/tickers/asml.as/lookup")

    assert response.status_code == 200
    assert (response.json()["outcome"], response.json()["status"]) == ("tracked", "pending")
    assert calls == ["asml.as"]


def test_add_201_when_created(monkeypatch, client):
    calls = []
    monkeypatch.setattr(
        jobs, "run_add_tracked_ticker", lambda **kw: calls.append(kw) or {**ROW, "created": True}
    )

    response = client.post(
        "/instruments/tracked-tickers",
        json={"ticker": "asml.as", "requested_by": str(ALICE), "enforce_limit": True},
    )

    assert response.status_code == 201
    assert response.json()["created"] is True
    assert calls[0]["ticker"] == "asml.as"
    assert calls[0]["requested_by"] == ALICE
    assert calls[0]["enforce_limit"] is True


def test_add_200_when_already_tracked(monkeypatch, client):
    monkeypatch.setattr(jobs, "run_add_tracked_ticker", lambda **kw: {**ROW, "created": False})

    response = client.post(
        "/instruments/tracked-tickers",
        json={"ticker": "ASML.AS", "requested_by": str(ALICE), "enforce_limit": False},
    )

    assert response.status_code == 200


def test_add_requires_enforce_limit_to_be_stated(client):
    # No default on purpose: whether the limit applies is the caller's decision to make
    # explicitly, never something an omitted field quietly decides.
    response = client.post(
        "/instruments/tracked-tickers", json={"ticker": "ASML.AS", "requested_by": str(ALICE)}
    )

    assert response.status_code == 422


def test_list_passes_filters(monkeypatch, client):
    calls = []
    monkeypatch.setattr(jobs, "get_tracked_tickers", lambda **kw: calls.append(kw) or [ROW])

    response = client.get(
        "/instruments/tracked-tickers",
        params=[("requested_by", str(ALICE)), ("status", "failed"), ("ticker", "ASML.AS")],
    )

    assert response.status_code == 200
    assert [r["ticker"] for r in response.json()] == ["ASML.AS"]
    assert calls[0]["requested_by"] == ALICE
    assert calls[0]["statuses"] == ["failed"]
    assert calls[0]["tickers"] == ["ASML.AS"]


def test_update(monkeypatch, client):
    calls = []
    monkeypatch.setattr(
        jobs,
        "run_update_tracked_ticker",
        lambda **kw: calls.append(kw) or {**ROW, "enabled": False},
    )

    response = client.patch("/instruments/tracked-tickers/ASML.AS", json={"enabled": False})

    assert response.status_code == 200
    assert calls[0]["ticker"] == "ASML.AS"
    assert (calls[0]["enabled"], calls[0]["retry"]) == (False, False)
