"""HttpPortfolioClient's saved labs calls (LF-1): GET, POST and DELETE /labs/saved. The request
each sends, how the answer is parsed, and which status becomes which error."""

import json
from decimal import Decimal

import httpx
import pytest
from web.portfolio_client.base import SavedLab, SavedLabSaveResult
from web.portfolio_client.errors import (
    InvalidPortfolioRequest,
    PortfolioUnavailable,
    SavedLabNameTaken,
    SavedLabNotFound,
)
from web.portfolio_client.http_client import HttpPortfolioClient

pytestmark = pytest.mark.anyio

INTERNAL_SECRET = "test-secret"  # pragma: allowlist secret
USER = "11111111-1111-4111-8111-111111111111"


def _client(handler) -> HttpPortfolioClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="http://portfolio.internal", transport=transport)
    return HttpPortfolioClient(http_client, INTERNAL_SECRET)


def _answer(status, body):
    return lambda request: httpx.Response(status, json=body)


async def test_list_saved_labs_sends_the_user_and_parses_each_lab():
    seen = {}

    def handler(request):
        seen["request"] = request
        return httpx.Response(
            200,
            json=[
                {
                    "name": "Core",
                    "assets": [
                        {"ticker": "VWCE.DE", "weight": "60"},
                        {"ticker": "AAPL", "weight": "33.3333"},
                    ],
                    "created_at": "2026-11-01T09:00:00Z",
                    "updated_at": "2026-11-02T09:00:00Z",
                }
            ],
        )

    labs = await _client(handler).list_saved_labs(user_id=USER)

    request = seen["request"]
    assert (request.method, request.url.path) == ("GET", "/labs/saved")
    assert request.url.params["user_id"] == USER
    assert request.headers["X-Internal-Secret"] == INTERNAL_SECRET
    assert labs == [
        SavedLab(
            name="Core",
            assets=(("VWCE.DE", "60"), ("AAPL", "33.3333")),
            updated_at="2026-11-02T09:00:00Z",
        )
    ]


async def test_list_saved_labs_raises_unavailable_on_a_malformed_body():
    with pytest.raises(PortfolioUnavailable):
        await _client(_answer(200, [{"name": "Core"}])).list_saved_labs(user_id=USER)


async def test_list_saved_labs_raises_unavailable_on_an_error_status():
    with pytest.raises(PortfolioUnavailable):
        await _client(_answer(500, {"detail": "boom"})).list_saved_labs(user_id=USER)


async def test_save_lab_sends_the_mix_with_weights_as_fixed_point_strings():
    seen = {}

    def handler(request):
        seen["request"] = request
        return httpx.Response(
            201, json={"status": "ok", "name": "Core", "created": True, "assets_saved": 2}
        )

    result = await _client(handler).save_lab(
        user_id=USER,
        name="Core",
        assets=[("VWCE.DE", Decimal("60")), ("AAPL", Decimal("4E+1"))],
        replace=False,
    )

    request = seen["request"]
    assert (request.method, request.url.path) == ("POST", "/labs/saved")
    assert json.loads(request.content) == {
        "user_id": USER,
        "name": "Core",
        "assets": [{"ticker": "VWCE.DE", "weight": "60"}, {"ticker": "AAPL", "weight": "40"}],
        "replace": False,
    }
    assert result == SavedLabSaveResult(name="Core", created=True, assets_saved=2)


async def test_save_lab_reports_a_replace():
    client = _client(
        _answer(200, {"status": "ok", "name": "Core", "created": False, "assets_saved": 1})
    )

    result = await client.save_lab(
        user_id=USER, name="Core", assets=[("MSFT", Decimal("100"))], replace=True
    )

    assert result.created is False


@pytest.mark.parametrize(
    "status, error",
    [
        (409, SavedLabNameTaken),
        (422, InvalidPortfolioRequest),
        (500, PortfolioUnavailable),
    ],
)
async def test_save_lab_maps_each_refusal_with_portfolios_message(status, error):
    client = _client(_answer(status, {"detail": "You already have a lab called 'Core'."}))

    with pytest.raises(error) as exc:
        await client.save_lab(
            user_id=USER, name="Core", assets=[("A", Decimal("1"))], replace=False
        )

    if status != 500:
        assert str(exc.value) == "You already have a lab called 'Core'."


async def test_delete_saved_lab_sends_the_user_and_name():
    seen = {}

    def handler(request):
        seen["request"] = request
        return httpx.Response(200, json={"status": "ok", "labs_deleted": 1})

    await _client(handler).delete_saved_lab(user_id=USER, name="Core & co")

    request = seen["request"]
    assert (request.method, request.url.path) == ("DELETE", "/labs/saved")
    assert request.url.params["user_id"] == USER
    assert request.url.params["name"] == "Core & co"


async def test_delete_saved_lab_raises_not_found_on_404():
    client = _client(_answer(404, {"detail": "You have no lab called 'Core'."}))

    with pytest.raises(SavedLabNotFound):
        await client.delete_saved_lab(user_id=USER, name="Core")


async def test_delete_saved_lab_raises_unavailable_on_an_error_status():
    with pytest.raises(PortfolioUnavailable):
        await _client(_answer(503, {"detail": "down"})).delete_saved_lab(user_id=USER, name="C")
