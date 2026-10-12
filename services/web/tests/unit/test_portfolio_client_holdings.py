"""HttpPortfolioClient.list_holdings: portfolio's GET /holdings (PF-1) -- the request it sends,
how a holding, its per-account breakdown and the totals are parsed, and which failures become
which error. The JSON is written out in the shape portfolio's HoldingsResponse serves."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
from web.portfolio_client.base import Holding, HoldingAccount, Holdings, HoldingsTotals
from web.portfolio_client.errors import InvalidPortfolioRequest, PortfolioUnavailable
from web.portfolio_client.http_client import HttpPortfolioClient

pytestmark = pytest.mark.anyio

INTERNAL_SECRET = "test-secret"  # pragma: allowlist secret
USER_ID = "11111111-1111-1111-1111-111111111111"


def _client(handler) -> HttpPortfolioClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="http://portfolio.internal", transport=transport)
    return HttpPortfolioClient(http_client, INTERNAL_SECRET)


def _account(label, quantity, average_cost, cost, value):
    return {
        "account_label": label,
        "quantity": quantity,
        "native_currency": "EUR",
        "average_cost_native": average_cost,
        "cost_basis_eur": cost,
        "market_value_native": value,
        "market_value_eur": value,
        "unrealized_return_eur": str(Decimal(value) - Decimal(cost)),
    }


def _priced_iwda():
    return {
        "canonical_ticker": "IWDA.AS",
        "quantity": "140.000000",
        "average_cost_eur": "82.142857",
        "cost_basis_eur": "11500.0000",
        "native_currency": "EUR",
        "mixed_currency": False,
        "average_cost_native": "82.142857",
        "market_value_native": "14000.0000",
        "priced": True,
        "price_date": "2026-01-06",
        "market_value_eur": "14000.0000",
        "unrealized_return_eur": "2500.0000",
        "unrealized_return_pct": "0.21739130",
        "dividends_received_eur": "0E-8",
        "total_return_eur": "2500.0000",
        "total_return_pct": "0.21739130",
        "accounts": [
            _account("A", "120.000000", "80.000000", "9600.0000", "12000.0000"),
            _account("B", "20.000000", "95.000000", "1900.0000", "2000.0000"),
        ],
    }


def _unpriced_mixed():
    return {
        "canonical_ticker": "VUSA",
        "quantity": "20.000000",
        "average_cost_eur": "49.000000",
        "cost_basis_eur": "980.0000",
        "native_currency": None,
        "mixed_currency": True,
        "average_cost_native": None,
        "market_value_native": None,
        "priced": False,
        "price_date": None,
        "market_value_eur": None,
        "unrealized_return_eur": None,
        "unrealized_return_pct": None,
        "dividends_received_eur": None,
        "total_return_eur": None,
        "total_return_pct": None,
        "accounts": [
            {
                "account_label": "A",
                "quantity": "10.000000",
                "native_currency": "EUR",
                "average_cost_native": "50.000000",
                "cost_basis_eur": "500.0000",
                "market_value_native": None,
                "market_value_eur": None,
                "unrealized_return_eur": None,
            },
            {
                "account_label": "B",
                "quantity": "10.000000",
                "native_currency": "GBP",
                "average_cost_native": "40.000000",
                "cost_basis_eur": "480.0000",
                "market_value_native": None,
                "market_value_eur": None,
                "unrealized_return_eur": None,
            },
        ],
    }


def _totals():
    return {
        "price_date": "2026-01-06",
        "market_value_eur": "14000.0000",
        "cost_basis_eur": "11500.0000",
        "dividends_received_eur": "0E-8",
        "total_return_eur": "2500.0000",
        "total_return_pct": None,
    }


async def test_list_holdings_calls_the_holdings_endpoint_and_parses_a_priced_row():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        captured["secret"] = request.headers.get("X-Internal-Secret")
        return httpx.Response(200, json={"holdings": [_priced_iwda()], "totals": _totals()})

    result = await _client(handler).list_holdings(user_id=USER_ID)

    assert captured == {
        "method": "GET",
        "path": "/holdings",
        "params": {"user_id": USER_ID},
        "secret": INTERNAL_SECRET,
    }
    assert result == Holdings(
        holdings=(
            Holding(
                canonical_ticker="IWDA.AS",
                quantity=Decimal("140"),
                average_cost_eur=Decimal("82.142857"),
                cost_basis_eur=Decimal("11500"),
                native_currency="EUR",
                mixed_currency=False,
                average_cost_native=Decimal("82.142857"),
                market_value_native=Decimal("14000"),
                priced=True,
                price_date=date(2026, 1, 6),
                market_value_eur=Decimal("14000"),
                unrealized_return_eur=Decimal("2500"),
                unrealized_return_pct=Decimal("0.21739130"),
                dividends_received_eur=Decimal("0"),
                total_return_eur=Decimal("2500"),
                total_return_pct=Decimal("0.21739130"),
                accounts=(
                    HoldingAccount(
                        account_label="A",
                        quantity=Decimal("120"),
                        native_currency="EUR",
                        average_cost_native=Decimal("80"),
                        cost_basis_eur=Decimal("9600"),
                        market_value_native=Decimal("12000"),
                        market_value_eur=Decimal("12000"),
                        unrealized_return_eur=Decimal("2400"),
                    ),
                    HoldingAccount(
                        account_label="B",
                        quantity=Decimal("20"),
                        native_currency="EUR",
                        average_cost_native=Decimal("95"),
                        cost_basis_eur=Decimal("1900"),
                        market_value_native=Decimal("2000"),
                        market_value_eur=Decimal("2000"),
                        unrealized_return_eur=Decimal("100"),
                    ),
                ),
            ),
        ),
        totals=HoldingsTotals(
            price_date=date(2026, 1, 6),
            market_value_eur=Decimal("14000"),
            cost_basis_eur=Decimal("11500"),
            dividends_received_eur=Decimal("0"),
            total_return_eur=Decimal("2500"),
            total_return_pct=None,
        ),
    )


async def test_list_holdings_keeps_an_unpriced_mixed_currency_rows_nulls():
    body = {"holdings": [_unpriced_mixed()], "totals": None}
    client = _client(lambda request: httpx.Response(200, json=body))

    row = (await client.list_holdings(user_id=USER_ID)).holdings[0]

    assert row.priced is False
    assert row.mixed_currency is True
    assert row.native_currency is None
    assert row.price_date is None
    for field in (
        "average_cost_native",
        "market_value_native",
        "market_value_eur",
        "unrealized_return_eur",
        "unrealized_return_pct",
        "dividends_received_eur",
        "total_return_eur",
        "total_return_pct",
    ):
        assert getattr(row, field) is None, field
    assert [(a.native_currency, a.average_cost_native) for a in row.accounts] == [
        ("EUR", Decimal("50")),
        ("GBP", Decimal("40")),
    ]
    assert all(a.market_value_eur is None for a in row.accounts)
    assert all(a.market_value_native is None for a in row.accounts)
    assert all(a.unrealized_return_eur is None for a in row.accounts)


async def test_list_holdings_net_zero_units_have_no_average_cost():
    row = {**_unpriced_mixed(), "quantity": "0E-6", "average_cost_eur": None}
    client = _client(lambda request: httpx.Response(200, json={"holdings": [row], "totals": None}))

    holding = (await client.list_holdings(user_id=USER_ID)).holdings[0]

    assert holding.quantity == Decimal("0")
    assert holding.average_cost_eur is None


async def test_list_holdings_keeps_portfolios_order():
    rows = [{**_priced_iwda(), "canonical_ticker": t} for t in ("B.AS", "A.AS")]
    client = _client(lambda request: httpx.Response(200, json={"holdings": rows, "totals": None}))

    result = await client.list_holdings(user_id=USER_ID)

    assert [h.canonical_ticker for h in result.holdings] == ["B.AS", "A.AS"]


async def test_list_holdings_with_an_empty_portfolio():
    client = _client(lambda request: httpx.Response(200, json={"holdings": [], "totals": None}))

    assert await client.list_holdings(user_id=USER_ID) == Holdings(holdings=(), totals=None)


async def test_list_holdings_rejected_request_raises_invalid_portfolio_request():
    client = _client(lambda request: httpx.Response(422, json={"detail": "bad user_id"}))

    with pytest.raises(InvalidPortfolioRequest):
        await client.list_holdings(user_id="not-a-uuid")


async def test_list_holdings_server_error_raises_portfolio_unavailable():
    client = _client(lambda request: httpx.Response(500, json={"detail": "boom"}))

    with pytest.raises(PortfolioUnavailable):
        await client.list_holdings(user_id=USER_ID)


async def test_list_holdings_unreachable_portfolio_raises_portfolio_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(PortfolioUnavailable):
        await _client(handler).list_holdings(user_id=USER_ID)


def _without(d, key):
    return {k: v for k, v in d.items() if k != key}


def _body_with_holding(**changes):
    return {"holdings": [{**_priced_iwda(), **changes}], "totals": _totals()}


@pytest.mark.parametrize(
    "body",
    [
        pytest.param([], id="body-is-a-list"),
        pytest.param({"holdings": []}, id="missing-totals"),
        pytest.param({"totals": None}, id="missing-holdings"),
        pytest.param(
            {"holdings": [_without(_priced_iwda(), "quantity")], "totals": None},
            id="holding-missing-quantity",
        ),
        pytest.param(
            {"holdings": [_without(_priced_iwda(), "total_return_pct")], "totals": None},
            id="holding-missing-a-nullable-field",
        ),
        pytest.param(_body_with_holding(quantity=None), id="null-quantity"),
        pytest.param(_body_with_holding(market_value_eur="lots"), id="non-numeric-decimal"),
        pytest.param(_body_with_holding(price_date="not-a-date"), id="bad-price-date"),
        pytest.param(_body_with_holding(priced="false"), id="priced-as-a-string"),
        pytest.param(_body_with_holding(mixed_currency=1), id="mixed-currency-as-a-number"),
        pytest.param(_body_with_holding(accounts=None), id="accounts-null"),
        pytest.param(
            _body_with_holding(accounts=[_without(_priced_iwda()["accounts"][0], "account_label")]),
            id="breakdown-missing-account-label",
        ),
        pytest.param(
            {"holdings": [], "totals": _without(_totals(), "price_date")},
            id="totals-missing-price-date",
        ),
    ],
)
async def test_list_holdings_malformed_body_raises_portfolio_unavailable(body):
    client = _client(lambda request: httpx.Response(200, json=body))

    with pytest.raises(PortfolioUnavailable):
        await client.list_holdings(user_id=USER_ID)


async def test_list_holdings_non_json_body_raises_portfolio_unavailable():
    client = _client(lambda request: httpx.Response(200, text="<html>oops</html>"))

    with pytest.raises(PortfolioUnavailable):
        await client.list_holdings(user_id=USER_ID)
