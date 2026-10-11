"""HttpPortfolioClient's tracked-ticker calls (ticker onboarding): request shape, parsing, and
which portfolio status becomes which error -- each one a different message for the user."""

from datetime import datetime, timezone

import httpx
import pytest
from web.portfolio_client.base import TickerLookup, TrackedTicker
from web.portfolio_client.errors import (
    InvalidTickerRequest,
    PortfolioUnavailable,
    TickerChangeRefused,
    TickerLimitReached,
    TickerNotOnProvider,
    TickerProviderUnavailable,
    TrackedTickerNotFound,
)
from web.portfolio_client.http_client import HttpPortfolioClient

pytestmark = pytest.mark.anyio

USER_ID = "11111111-1111-1111-1111-111111111111"
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
    "requested_by": USER_ID,
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
    requested_by=USER_ID,
    requested_at=datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc),
    ready_at=None,
)


def _client(handler) -> HttpPortfolioClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="http://portfolio.internal", transport=transport)
    return HttpPortfolioClient(http_client, "test-secret")  # pragma: allowlist secret


def _recording(status_code, body):
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status_code, json=body)

    return handler, captured


async def test_lookup_ticker():
    handler, captured = _recording(
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

    result = await _client(handler).lookup_ticker("asml.as")

    assert result == TickerLookup(
        ticker="ASML.AS",
        outcome="available",
        status=None,
        display_name="ASML Holding N.V.",
        currency="EUR",
        exchange="Amsterdam",
        asset_type="equity",
    )
    assert captured[0].url.path == "/instruments/tickers/asml.as/lookup"
    assert captured[0].headers["x-internal-secret"] == "test-secret"  # pragma: allowlist secret


async def test_lookup_keeps_a_typed_symbol_in_one_path_segment():
    handler, captured = _recording(422, {"detail": "not a valid ticker symbol"})

    with pytest.raises(InvalidTickerRequest):
        await _client(handler).lookup_ticker("a/b")

    assert captured[0].url.raw_path == b"/instruments/tickers/a%2Fb/lookup"


async def test_add_tracked_ticker():
    handler, captured = _recording(201, {**ROW, "created": True})

    result = await _client(handler).add_tracked_ticker(
        ticker="asml.as", requested_by=USER_ID, enforce_limit=True
    )

    assert (result.ticker, result.created) == (EXPECTED, True)
    request = captured[0]
    assert (request.method, request.url.path) == ("POST", "/instruments/tracked-tickers")
    assert request.read() == (
        b'{"ticker":"asml.as","requested_by":"' + USER_ID.encode() + b'","enforce_limit":true}'
    )


async def test_add_an_already_tracked_ticker():
    handler, _ = _recording(200, {**ROW, "status": "ready", "created": False})

    result = await _client(handler).add_tracked_ticker(
        ticker="ASML.AS", requested_by=USER_ID, enforce_limit=False
    )

    assert (result.ticker.status, result.created) == ("ready", False)


@pytest.mark.parametrize(
    "status_code, error",
    [
        (404, TickerNotOnProvider),
        (422, InvalidTickerRequest),
        (429, TickerLimitReached),
        (503, TickerProviderUnavailable),
        (502, PortfolioUnavailable),
        (500, PortfolioUnavailable),
    ],
)
async def test_add_errors(status_code, error):
    handler, _ = _recording(status_code, {"detail": "nope"})

    with pytest.raises(error):
        await _client(handler).add_tracked_ticker(
            ticker="X", requested_by=USER_ID, enforce_limit=True
        )


async def test_a_malformed_add_response_is_an_outage():
    handler, _ = _recording(201, {"ticker": "X"})

    with pytest.raises(PortfolioUnavailable):
        await _client(handler).add_tracked_ticker(
            ticker="X", requested_by=USER_ID, enforce_limit=True
        )


async def test_list_tracked_tickers_with_filters():
    handler, captured = _recording(200, [ROW])

    rows = await _client(handler).list_tracked_tickers(
        requested_by=USER_ID, statuses=["pending", "failed"], tickers=["ASML.AS"]
    )

    assert rows == [EXPECTED]
    assert captured[0].url.params.multi_items() == [
        ("requested_by", USER_ID),
        ("status", "pending"),
        ("status", "failed"),
        ("ticker", "ASML.AS"),
    ]


async def test_list_tracked_tickers_without_filters():
    handler, captured = _recording(200, [])

    assert await _client(handler).list_tracked_tickers() == []
    assert captured[0].url.params.multi_items() == []


async def test_update_tracked_ticker():
    handler, captured = _recording(200, {**ROW, "status": "pending"})

    row = await _client(handler).update_tracked_ticker(ticker="ASML.AS", retry=True)

    assert row.status == "pending"
    request = captured[0]
    assert (request.method, request.url.path) == ("PATCH", "/instruments/tracked-tickers/ASML.AS")
    assert request.read() == b'{"enabled":null,"retry":true}'


@pytest.mark.parametrize(
    "status_code, error", [(404, TrackedTickerNotFound), (409, TickerChangeRefused)]
)
async def test_update_errors(status_code, error):
    handler, _ = _recording(status_code, {"detail": "nope"})

    with pytest.raises(error):
        await _client(handler).update_tracked_ticker(ticker="X", enabled=False)
