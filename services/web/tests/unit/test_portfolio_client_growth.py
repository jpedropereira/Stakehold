"""HttpPortfolioClient.list_growth: portfolio's GET /returns/growth -- the request it sends, how
each point is parsed, and which failures become which error."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
from web.portfolio_client.base import GrowthPoint
from web.portfolio_client.errors import AccountNotFound, PortfolioUnavailable
from web.portfolio_client.http_client import HttpPortfolioClient

pytestmark = pytest.mark.anyio

INTERNAL_SECRET = "test-secret"  # pragma: allowlist secret
USER_ID = "11111111-1111-1111-1111-111111111111"


def _client(handler) -> HttpPortfolioClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="http://portfolio.internal", transport=transport)
    return HttpPortfolioClient(http_client, INTERNAL_SECRET)


async def test_list_growth_calls_the_growth_endpoint_and_parses_each_point():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        captured["secret"] = request.headers.get("X-Internal-Secret")
        # "0E-8" is how portfolio sends a zero Decimal at eight places.
        return httpx.Response(
            200,
            json=[
                {"price_date": "2026-01-05", "growth_pct": "0E-8"},
                {"price_date": "2026-01-06", "growth_pct": "-0.01250000"},
            ],
        )

    points = await _client(handler).list_growth(user_id=USER_ID, account_label="main")

    assert captured == {
        "method": "GET",
        "path": "/returns/growth",
        "params": {"user_id": USER_ID, "account_label": "main"},
        "secret": INTERNAL_SECRET,
    }
    assert points == [
        GrowthPoint(price_date=date(2026, 1, 5), growth_pct=Decimal("0")),
        GrowthPoint(price_date=date(2026, 1, 6), growth_pct=Decimal("-0.0125")),
    ]


async def test_list_growth_with_no_points_is_an_empty_list():
    client = _client(lambda request: httpx.Response(200, json=[]))

    assert await client.list_growth(user_id=USER_ID, account_label="main") == []


async def test_list_growth_account_not_found_raises():
    client = _client(lambda request: httpx.Response(404, json={"detail": "account not found"}))

    with pytest.raises(AccountNotFound):
        await client.list_growth(user_id=USER_ID, account_label="missing")


async def test_list_growth_server_error_raises_portfolio_unavailable():
    client = _client(lambda request: httpx.Response(500, json={"detail": "boom"}))

    with pytest.raises(PortfolioUnavailable):
        await client.list_growth(user_id=USER_ID, account_label="main")


@pytest.mark.parametrize(
    "item",
    [
        {"price_date": "2026-01-05"},
        {"price_date": "not-a-date", "growth_pct": "0.1"},
        {"price_date": "2026-01-05", "growth_pct": "lots"},
        {"price_date": "2026-01-05", "growth_pct": None},
    ],
)
async def test_list_growth_malformed_point_raises_portfolio_unavailable(item):
    client = _client(lambda request: httpx.Response(200, json=[item]))

    with pytest.raises(PortfolioUnavailable):
        await client.list_growth(user_id=USER_ID, account_label="main")
