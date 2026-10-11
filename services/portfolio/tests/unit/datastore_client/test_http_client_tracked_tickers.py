"""HttpDataStoreClient's tracked-ticker calls, against a fake requests.request. data-store's
refusals the user must see (not found, can't be held, over the daily limit, provider down) come
back as TickerRequestRefused with data-store's own status code; anything else unexpected is a
DataStoreApiError, like every other call here."""

from uuid import UUID

import pytest
import requests
from portfolio.datastore_client.base import TickerLookup, TrackedTicker
from portfolio.datastore_client.http_client import HttpDataStoreClient
from portfolio.errors import DataStoreApiError, TickerRequestRefused

ALICE = UUID("00000000-0000-0000-0000-00000000000a")

ROW = {
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
    "requested_by": str(ALICE),
    "requested_at": "2026-10-10T12:00:00Z",
    "ready_at": None,
}

EXPECTED = TrackedTicker(
    ticker="ASML.AS",
    display_name="ASML Holding N.V.",
    asset_type="equity",
    currency="EUR",
    exchange="Amsterdam",
    origin="request",
    status="pending",
    enabled=True,
    attempts=0,
    last_error=None,
    requested_by=str(ALICE),
    requested_at="2026-10-10T12:00:00Z",
    ready_at=None,
)


class FakeResponse:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self._json_body = json_body

    def json(self):
        if self._json_body is None:
            raise ValueError("no JSON")
        return self._json_body


@pytest.fixture
def client():
    return HttpDataStoreClient(
        base_url="http://datastore:8000", internal_api_secret="secret"  # pragma: allowlist secret
    )


@pytest.fixture
def respond(monkeypatch):
    captured = []

    def install(status_code=200, json_body=None, raises=None):
        def fake_request(method, url, **kwargs):
            captured.append((method, url, kwargs))
            if raises:
                raise raises
            return FakeResponse(status_code, json_body)

        monkeypatch.setattr(requests, "request", fake_request)
        return captured

    return install


def test_lookup_ticker(client, respond):
    captured = respond(
        200,
        {
            "ticker": "ASML.AS",
            "outcome": "available",
            "status": None,
            "display_name": "ASML Holding N.V.",
            "currency": "EUR",
            "exchange": "Amsterdam",
            "asset_type": "equity",
        },
    )

    result = client.lookup_ticker("asml.as")

    assert result == TickerLookup(
        ticker="ASML.AS",
        outcome="available",
        status=None,
        display_name="ASML Holding N.V.",
        currency="EUR",
        exchange="Amsterdam",
        asset_type="equity",
    )
    method, url, kwargs = captured[0]
    assert (method, url) == ("GET", "http://datastore:8000/tickers/asml.as/lookup")
    assert kwargs["headers"]["Authorization"] == "Bearer secret"
    assert kwargs["timeout"] > 0


def test_lookup_quotes_the_symbol_into_the_path(client, respond):
    # A user-typed symbol goes into a URL path; "^GSPC" or "a/b" must not change which route
    # is called. data-store refuses the symbol itself; this only keeps the path intact.
    captured = respond(422, {"detail": "not a valid ticker symbol"})

    with pytest.raises(TickerRequestRefused):
        client.lookup_ticker("../health")

    assert captured[0][1] == "http://datastore:8000/tickers/..%2Fhealth/lookup"


def test_add_tracked_ticker_201(client, respond):
    captured = respond(201, {**ROW, "created": True})

    row, created = client.add_tracked_ticker("asml.as", requested_by=ALICE, enforce_limit=True)

    assert (row, created) == (EXPECTED, True)
    method, url, kwargs = captured[0]
    assert (method, url) == ("POST", "http://datastore:8000/tracked-tickers")
    assert kwargs["json"] == {
        "ticker": "asml.as",
        "requested_by": str(ALICE),
        "enforce_limit": True,
    }


def test_add_tracked_ticker_already_tracked(client, respond):
    respond(200, {**ROW, "status": "ready", "created": False})

    row, created = client.add_tracked_ticker("ASML.AS", requested_by=ALICE, enforce_limit=False)

    assert (row.status, created) == ("ready", False)


@pytest.mark.parametrize("status_code", [404, 409, 422, 429, 503])
def test_a_refusal_keeps_data_stores_status_and_detail(client, respond, status_code):
    respond(status_code, {"detail": "daily limit of new tickers reached: limit of 10"})

    with pytest.raises(TickerRequestRefused) as exc:
        client.add_tracked_ticker("X", requested_by=ALICE, enforce_limit=True)

    assert exc.value.status_code == status_code
    assert "daily limit" in str(exc.value)


def test_a_server_error_is_a_data_store_api_error(client, respond):
    respond(500, {"detail": "boom"})

    with pytest.raises(DataStoreApiError):
        client.add_tracked_ticker("X", requested_by=ALICE, enforce_limit=True)


def test_an_unreachable_data_store_is_a_data_store_api_error(client, respond):
    respond(raises=requests.ConnectionError("refused"))

    with pytest.raises(DataStoreApiError):
        client.lookup_ticker("X")


def test_a_malformed_body_is_a_data_store_api_error(client, respond):
    respond(200, {"ticker": "X"})

    with pytest.raises(DataStoreApiError):
        client.add_tracked_ticker("X", requested_by=ALICE, enforce_limit=True)


def test_list_tracked_tickers_sends_repeated_filters(client, respond):
    captured = respond(200, {"tracked_tickers": [ROW]})

    rows = client.list_tracked_tickers(
        requested_by=ALICE, statuses=["pending", "failed"], tickers=["ASML.AS"]
    )

    assert rows == [EXPECTED]
    method, url, kwargs = captured[0]
    assert (method, url) == ("GET", "http://datastore:8000/tracked-tickers")
    assert kwargs["params"] == [
        ("requested_by", str(ALICE)),
        ("status", "pending"),
        ("status", "failed"),
        ("ticker", "ASML.AS"),
    ]


def test_list_tracked_tickers_without_filters(client, respond):
    captured = respond(200, {"tracked_tickers": []})

    assert client.list_tracked_tickers() == []
    assert captured[0][2]["params"] == []


def test_update_tracked_ticker(client, respond):
    captured = respond(200, {**ROW, "enabled": False})

    row = client.update_tracked_ticker("ASML.AS", enabled=False, retry=False)

    assert row.enabled is False
    method, url, kwargs = captured[0]
    assert (method, url) == ("PATCH", "http://datastore:8000/tracked-tickers/ASML.AS")
    assert kwargs["json"] == {"enabled": False, "retry": False}


def test_get_ticker_info_reads_status(client, monkeypatch):
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **kw: _GetResponse({"exists": False, "has_returns": False, "status": "pending"}),
    )

    info = client.get_ticker_info("ASML.AS")

    assert (info.exists, info.status) == (False, "pending")


class _GetResponse(FakeResponse):
    def __init__(self, body):
        super().__init__(200, body)

    def raise_for_status(self):
        pass
