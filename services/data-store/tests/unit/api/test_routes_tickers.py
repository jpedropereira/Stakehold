from datetime import date

from data_store import jobs


def test_unknown_ticker_returns_200_with_exists_false(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "get_ticker_info",
        lambda **kw: {
            "ticker": "NOPE",
            "exists": False,
            "has_returns": False,
            "first_date": None,
            "last_date": None,
        },
    )

    response = client.get("/tickers/NOPE")

    assert response.status_code == 200
    assert response.json()["exists"] is False


def test_tracked_ticker_without_returns_yet(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "get_ticker_info",
        lambda **kw: {
            "ticker": "AAPL",
            "exists": True,
            "has_returns": False,
            "first_date": None,
            "last_date": None,
        },
    )

    response = client.get("/tickers/AAPL")

    body = response.json()
    assert body["exists"] is True
    assert body["has_returns"] is False


def test_fully_tracked_ticker_reports_date_range(monkeypatch, client):
    monkeypatch.setattr(
        jobs,
        "get_ticker_info",
        lambda **kw: {
            "ticker": "AAPL",
            "exists": True,
            "has_returns": True,
            "first_date": date(2010, 1, 4),
            "last_date": date(2026, 8, 29),
        },
    )

    response = client.get("/tickers/AAPL")

    body = response.json()
    assert body["first_date"] == "2010-01-04"
    assert body["last_date"] == "2026-08-29"


def test_source_query_param_is_forwarded(monkeypatch, client):
    calls = []
    monkeypatch.setattr(
        jobs,
        "get_ticker_info",
        lambda **kw: calls.append(kw)
        or {
            "ticker": "AAPL",
            "exists": True,
            "has_returns": True,
            "first_date": None,
            "last_date": None,
        },
    )

    client.get("/tickers/AAPL?source=other")

    assert calls[0]["source"] == "other"


def test_health_endpoint_needs_no_auth():
    from data_store.api.routes import health_router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(health_router)
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200


def test_ticker_list_returns_every_ingested_ticker(monkeypatch, client):
    calls = []
    monkeypatch.setattr(
        jobs,
        "get_ticker_list",
        lambda **kw: calls.append(kw) or {"tickers": ["AAPL", "IWDA.AS"]},
    )

    response = client.get("/tickers")

    assert response.status_code == 200
    assert response.json() == {"tickers": ["AAPL", "IWDA.AS"]}
    assert calls == [{"source": "yfinance"}]


def test_ticker_list_passes_the_source_through(monkeypatch, client):
    calls = []
    monkeypatch.setattr(jobs, "get_ticker_list", lambda **kw: calls.append(kw) or {"tickers": []})

    response = client.get("/tickers", params={"source": "other"})

    assert response.json() == {"tickers": []}
    assert calls == [{"source": "other"}]
