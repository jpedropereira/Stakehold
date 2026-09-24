from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest
from web.portfolio_client.errors import (
    AccountLabelConflict,
    AccountNotFound,
    InvalidPortfolioRequest,
    PortfolioUnavailable,
)
from web.portfolio_client.http_client import HttpPortfolioClient

pytestmark = pytest.mark.anyio

INTERNAL_SECRET = "test-secret"  # pragma: allowlist secret
USER_ID = "11111111-1111-1111-1111-111111111111"


def _client(handler) -> HttpPortfolioClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="http://portfolio.internal", transport=transport)
    return HttpPortfolioClient(http_client, INTERNAL_SECRET)


async def test_list_accounts_sends_internal_secret_and_parses_accounts():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "account_label": "main",
                    "broker": "acme",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
        )

    client = _client(handler)
    accounts = await client.list_accounts(user_id=USER_ID)

    assert captured["headers"]["x-internal-secret"] == INTERNAL_SECRET
    assert captured["params"] == {"user_id": USER_ID}
    assert len(accounts) == 1
    assert accounts[0].id == 1
    assert accounts[0].account_label == "main"
    assert accounts[0].broker == "acme"


async def test_list_brokers_returns_plain_list():
    client = _client(lambda request: httpx.Response(200, json=["acme", "globex"]))

    brokers = await client.list_brokers()

    assert brokers == ["acme", "globex"]


async def test_create_account_sends_expected_body():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(201, json={"status": "ok", "rows_saved": 1, "accounts_created": 1})

    client = _client(handler)
    result = await client.create_account(user_id=USER_ID, broker="acme", account_label="main")

    assert result.status == "ok"
    assert result.rows_saved == 1
    assert result.accounts_created == 1
    assert b'"broker":"acme"' in captured["body"] or b'"broker": "acme"' in captured["body"]


async def test_create_account_409_raises_account_label_conflict():
    client = _client(lambda request: httpx.Response(409, json={"detail": "label already used"}))

    with pytest.raises(AccountLabelConflict):
        await client.create_account(user_id=USER_ID, broker="acme", account_label="main")


async def test_create_account_422_raises_invalid_portfolio_request_with_detail():
    client = _client(
        lambda request: httpx.Response(422, json={"detail": "broker must not be blank"})
    )

    with pytest.raises(InvalidPortfolioRequest, match="broker must not be blank"):
        await client.create_account(user_id=USER_ID, broker="", account_label="main")


async def test_create_account_422_with_pydantic_validation_array_body():
    client = _client(
        lambda request: httpx.Response(
            422,
            json={
                "detail": [
                    {"loc": ["body", "account_label"], "msg": "field required", "type": "missing"}
                ]
            },
        )
    )

    with pytest.raises(InvalidPortfolioRequest, match="field required"):
        await client.create_account(user_id=USER_ID, broker="acme", account_label="")


async def test_add_transaction_serializes_decimal_and_datetime():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(201, json={"status": "ok", "rows_saved": 1, "transactions_saved": 1})

    client = _client(handler)
    result = await client.add_transaction(
        user_id=USER_ID,
        account_label="main",
        broker_transaction_ref="ref-1",
        transaction_type="DEPOSIT",
        trade_datetime=datetime(2026, 1, 5, tzinfo=timezone.utc),
        native_amount=Decimal("100.00"),
        native_currency="EUR",
    )

    assert result.transactions_saved == 1
    body = captured["body"].decode()
    assert '"native_amount":"100.00"' in body or '"native_amount": "100.00"' in body
    assert "2026-01-05" in body


async def test_add_transaction_account_not_found_raises():
    client = _client(lambda request: httpx.Response(404, json={"detail": "account not found"}))

    with pytest.raises(AccountNotFound):
        await client.add_transaction(
            user_id=USER_ID,
            account_label="missing",
            broker_transaction_ref="ref-1",
            transaction_type="DEPOSIT",
            trade_datetime=datetime(2026, 1, 5, tzinfo=timezone.utc),
            native_amount=Decimal("100.00"),
            native_currency="EUR",
        )


async def test_import_transactions_sends_multipart_file():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["content_type"] = request.headers["content-type"]
        captured["body"] = request.read()
        return httpx.Response(
            200, json={"status": "nothing_to_do", "rows_saved": 0, "transactions_saved": 0}
        )

    client = _client(handler)
    result = await client.import_transactions(
        user_id=USER_ID,
        account_label="main",
        filename="export.csv",
        content=b"col1,col2\n1,2\n",
        content_type="text/csv",
    )

    assert result.status == "nothing_to_do"
    assert "multipart/form-data" in captured["content_type"]
    assert b"export.csv" in captured["body"]
    assert b"col1,col2" in captured["body"]


async def test_import_transactions_422_broker_parser_not_found():
    client = _client(
        lambda request: httpx.Response(
            422, json={"detail": "no parser registered for broker 'acme'"}
        )
    )

    with pytest.raises(InvalidPortfolioRequest, match="no parser registered"):
        await client.import_transactions(
            user_id=USER_ID,
            account_label="main",
            filename="export.csv",
            content=b"x",
            content_type="text/csv",
        )


async def test_list_positions_sends_expected_query_params_and_parses_fields():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json=[
                {
                    "account_id": 1,
                    "canonical_ticker": "AAPL",
                    "as_of_date": "2026-01-05",
                    "quantity": "10.5",
                    "average_cost_native": "150.25",
                    "native_currency": "USD",
                    "average_cost_eur": "140.10",
                    "cost_basis_eur": "1471.05",
                }
            ],
        )

    client = _client(handler)
    positions = await client.list_positions(user_id=USER_ID, account_label="main")

    assert captured["params"] == {"user_id": USER_ID, "account_label": "main"}
    assert len(positions) == 1
    position = positions[0]
    assert position.account_id == 1
    assert position.canonical_ticker == "AAPL"
    assert position.as_of_date == date(2026, 1, 5)
    assert position.quantity == Decimal("10.5")
    assert position.average_cost_native == Decimal("150.25")
    assert position.native_currency == "USD"
    assert position.average_cost_eur == Decimal("140.10")
    assert position.cost_basis_eur == Decimal("1471.05")


async def test_list_positions_account_not_found_raises():
    client = _client(lambda request: httpx.Response(404, json={"detail": "account not found"}))

    with pytest.raises(AccountNotFound):
        await client.list_positions(user_id=USER_ID, account_label="missing")


async def test_list_positions_malformed_body_raises_portfolio_unavailable():
    client = _client(
        lambda request: httpx.Response(
            200,
            json=[
                {
                    "account_id": 1,
                    "canonical_ticker": "AAPL",
                    "as_of_date": "not-a-date",
                    "quantity": "10",
                    "average_cost_native": "1",
                    "native_currency": "USD",
                    "average_cost_eur": "1",
                    "cost_basis_eur": "1",
                }
            ],
        )
    )

    with pytest.raises(PortfolioUnavailable):
        await client.list_positions(user_id=USER_ID, account_label="main")


async def test_list_returns_sends_expected_query_params_and_parses_fields():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json=[
                {
                    "account_id": 1,
                    "canonical_ticker": "AAPL",
                    "price_date": "2026-01-05",
                    "quantity": "10",
                    "market_value_native": "1500",
                    "market_value_eur": "1400",
                    "cost_basis_eur": "1300",
                    "unrealized_return_eur": "100",
                    "unrealized_return_pct": None,
                    "dividends_received_eur": "5",
                    "total_return_eur": "105",
                    "total_return_pct": None,
                }
            ],
        )

    client = _client(handler)
    returns = await client.list_returns(user_id=USER_ID, account_label="main")

    assert captured["params"] == {"user_id": USER_ID, "account_label": "main"}
    assert len(returns) == 1
    row = returns[0]
    assert row.account_id == 1
    assert row.canonical_ticker == "AAPL"
    assert row.price_date == date(2026, 1, 5)
    assert row.quantity == Decimal("10")
    assert row.market_value_native == Decimal("1500")
    assert row.market_value_eur == Decimal("1400")
    assert row.cost_basis_eur == Decimal("1300")
    assert row.unrealized_return_eur == Decimal("100")
    assert row.unrealized_return_pct is None
    assert row.dividends_received_eur == Decimal("5")
    assert row.total_return_eur == Decimal("105")
    assert row.total_return_pct is None


async def test_list_returns_account_not_found_raises():
    client = _client(lambda request: httpx.Response(404, json={"detail": "account not found"}))

    with pytest.raises(AccountNotFound):
        await client.list_returns(user_id=USER_ID, account_label="missing")


async def test_list_returns_malformed_body_raises_portfolio_unavailable():
    client = _client(lambda request: httpx.Response(200, json=[{"account_id": 1}]))

    with pytest.raises(PortfolioUnavailable):
        await client.list_returns(user_id=USER_ID, account_label="main")


async def test_unexpected_status_raises_portfolio_unavailable():
    client = _client(lambda request: httpx.Response(500, json={"detail": "boom"}))

    with pytest.raises(PortfolioUnavailable):
        await client.list_brokers()


async def test_401_raises_portfolio_unavailable():
    client = _client(
        lambda request: httpx.Response(401, json={"detail": "invalid or missing internal secret"})
    )

    with pytest.raises(PortfolioUnavailable):
        await client.list_brokers()


async def test_connection_error_raises_portfolio_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = _client(handler)

    with pytest.raises(PortfolioUnavailable):
        await client.list_brokers()


async def test_success_with_malformed_body_raises_portfolio_unavailable():
    client = _client(lambda request: httpx.Response(200, json={"status": "ok"}))

    with pytest.raises(PortfolioUnavailable):
        await client.create_account(user_id=USER_ID, broker="acme", account_label="main")


async def test_success_with_non_json_body_raises_portfolio_unavailable():
    client = _client(lambda request: httpx.Response(200, content=b"<html>not json</html>"))

    with pytest.raises(PortfolioUnavailable):
        await client.list_brokers()
