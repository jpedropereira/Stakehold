"""HttpPortfolioClient.list_portfolio_returns: portfolio's GET /returns/portfolio (PF-5) -- the
request it sends, how the series and the per-ticker growth are parsed, and which failures become
which error. The JSON is written out in the shape portfolio's PortfolioReturnsResponse serves."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
from web.portfolio_client.base import (
    PortfolioReturns,
    PortfolioSeriesPoint,
    TickerGrowth,
)
from web.portfolio_client.errors import InvalidPortfolioRequest, PortfolioUnavailable
from web.portfolio_client.http_client import HttpPortfolioClient

pytestmark = pytest.mark.anyio

INTERNAL_SECRET = "test-secret"  # pragma: allowlist secret
USER_ID = "11111111-1111-1111-1111-111111111111"


def _client(handler) -> HttpPortfolioClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="http://portfolio.internal", transport=transport)
    return HttpPortfolioClient(http_client, INTERNAL_SECRET)


def _point(price_date="2026-01-06", **changes):
    return {
        "price_date": price_date,
        "market_value_eur": "14000.0000",
        "cost_basis_eur": "11500.0000",
        "dividends_received_eur": "0E-8",
        "total_return_eur": "2500.0000",
        "total_return_pct": "0.21739130",
        "growth_pct": "0.18000000",
        **changes,
    }


def _body(series=None, by_ticker=None):
    return {
        "series": [_point()] if series is None else series,
        "by_ticker": (
            [{"canonical_ticker": "IWDA.AS", "growth_pct": "0.18000000"}]
            if by_ticker is None
            else by_ticker
        ),
    }


async def test_list_portfolio_returns_calls_the_endpoint_and_parses_the_series_and_growth():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        captured["secret"] = request.headers.get("X-Internal-Secret")
        return httpx.Response(200, json=_body())

    result = await _client(handler).list_portfolio_returns(user_id=USER_ID)

    # No start or end: the chart's ranges crop on the client, so it needs the whole series.
    assert captured == {
        "method": "GET",
        "path": "/returns/portfolio",
        "params": {"user_id": USER_ID},
        "secret": INTERNAL_SECRET,
    }
    assert result == PortfolioReturns(
        series=(
            PortfolioSeriesPoint(
                price_date=date(2026, 1, 6),
                market_value_eur=Decimal("14000"),
                cost_basis_eur=Decimal("11500"),
                dividends_received_eur=Decimal("0"),
                total_return_eur=Decimal("2500"),
                total_return_pct=Decimal("0.21739130"),
                growth_pct=Decimal("0.18"),
            ),
        ),
        by_ticker=(TickerGrowth(canonical_ticker="IWDA.AS", growth_pct=Decimal("0.18")),),
    )


async def test_list_portfolio_returns_keeps_a_null_total_return_pct():
    body = _body(series=[_point(total_return_pct=None)])
    client = _client(lambda request: httpx.Response(200, json=body))

    result = await client.list_portfolio_returns(user_id=USER_ID)

    assert result.series[0].total_return_pct is None


async def test_list_portfolio_returns_keeps_portfolios_order():
    body = _body(series=[_point("2026-01-05"), _point("2026-01-06")])
    client = _client(lambda request: httpx.Response(200, json=body))

    result = await client.list_portfolio_returns(user_id=USER_ID)

    assert [p.price_date for p in result.series] == [date(2026, 1, 5), date(2026, 1, 6)]


async def test_list_portfolio_returns_with_no_return_rows_yet():
    body = {"series": [], "by_ticker": []}
    client = _client(lambda request: httpx.Response(200, json=body))

    assert await client.list_portfolio_returns(user_id=USER_ID) == PortfolioReturns(
        series=(), by_ticker=()
    )


async def test_list_portfolio_returns_rejected_request_raises_invalid_portfolio_request():
    client = _client(lambda request: httpx.Response(422, json={"detail": "bad user_id"}))

    with pytest.raises(InvalidPortfolioRequest):
        await client.list_portfolio_returns(user_id="not-a-uuid")


async def test_list_portfolio_returns_server_error_raises_portfolio_unavailable():
    client = _client(lambda request: httpx.Response(500, json={"detail": "boom"}))

    with pytest.raises(PortfolioUnavailable):
        await client.list_portfolio_returns(user_id=USER_ID)


async def test_list_portfolio_returns_unreachable_portfolio_raises_portfolio_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(PortfolioUnavailable):
        await _client(handler).list_portfolio_returns(user_id=USER_ID)


def _without(d, key):
    return {k: v for k, v in d.items() if k != key}


@pytest.mark.parametrize(
    "body",
    [
        pytest.param([], id="body-is-a-list"),
        pytest.param({"series": []}, id="missing-by-ticker"),
        pytest.param({"by_ticker": []}, id="missing-series"),
        pytest.param(_body(series=[_without(_point(), "growth_pct")]), id="point-missing-growth"),
        pytest.param(
            _body(series=[_without(_point(), "total_return_pct")]),
            id="point-missing-a-nullable-field",
        ),
        pytest.param(_body(series=[_point(growth_pct=None)]), id="null-growth"),
        pytest.param(_body(series=[_point(market_value_eur="lots")]), id="non-numeric-decimal"),
        pytest.param(_body(series=[_point("not-a-date")]), id="bad-price-date"),
        pytest.param(_body(by_ticker=[{"growth_pct": "0.1"}]), id="growth-missing-ticker"),
        pytest.param(
            _body(by_ticker=[{"canonical_ticker": "IWDA.AS", "growth_pct": "x"}]),
            id="non-numeric-ticker-growth",
        ),
    ],
)
async def test_list_portfolio_returns_malformed_body_raises_portfolio_unavailable(body):
    client = _client(lambda request: httpx.Response(200, json=body))

    with pytest.raises(PortfolioUnavailable):
        await client.list_portfolio_returns(user_id=USER_ID)


async def test_list_portfolio_returns_non_json_body_raises_portfolio_unavailable():
    client = _client(lambda request: httpx.Response(200, text="<html>oops</html>"))

    with pytest.raises(PortfolioUnavailable):
        await client.list_portfolio_returns(user_id=USER_ID)
