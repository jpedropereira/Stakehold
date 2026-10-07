import json
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest
from web.portfolio_client.errors import (
    AccountLabelConflict,
    AccountNotFound,
    InvalidPortfolioRequest,
    PortfolioUnavailable,
    TickerMappingNotFound,
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


async def test_list_tickers_returns_plain_list_from_the_tickers_endpoint():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, json=["AAPL", "IWDA.AS"])

    client = _client(handler)

    tickers = await client.list_tickers()

    assert tickers == ["AAPL", "IWDA.AS"]
    assert captured["path"] == "/instruments/tickers"


async def test_list_tickers_raises_unavailable_on_a_server_error():
    client = _client(lambda request: httpx.Response(502, json={"detail": "data-store down"}))

    with pytest.raises(PortfolioUnavailable):
        await client.list_tickers()


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


async def test_list_latest_returns_calls_the_latest_endpoint_and_parses_fields():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        captured["secret"] = request.headers.get("X-Internal-Secret")
        return httpx.Response(
            200,
            json=[
                {
                    "account_id": 1,
                    "canonical_ticker": "IWDA.AS",
                    "price_date": "2026-10-06",
                    "quantity": "12.5",
                    "market_value_native": "1250",
                    "market_value_eur": "1250",
                    "cost_basis_eur": "1000",
                    "unrealized_return_eur": "250",
                    "unrealized_return_pct": "0.25",
                    "dividends_received_eur": "0",
                    "total_return_eur": "250",
                    "total_return_pct": None,
                }
            ],
        )

    client = _client(handler)
    returns = await client.list_latest_returns(user_id=USER_ID, account_label="main")

    assert captured == {
        "path": "/returns/latest",
        "params": {"user_id": USER_ID, "account_label": "main"},
        "secret": INTERNAL_SECRET,
    }
    assert len(returns) == 1
    row = returns[0]
    assert row.canonical_ticker == "IWDA.AS"
    assert row.price_date == date(2026, 10, 6)
    assert row.quantity == Decimal("12.5")
    assert row.market_value_eur == Decimal("1250")
    assert row.unrealized_return_pct == Decimal("0.25")
    assert row.total_return_pct is None


async def test_list_latest_returns_account_not_found_raises():
    client = _client(lambda request: httpx.Response(404, json={"detail": "account not found"}))

    with pytest.raises(AccountNotFound):
        await client.list_latest_returns(user_id=USER_ID, account_label="missing")


async def test_list_latest_returns_malformed_body_raises_portfolio_unavailable():
    client = _client(lambda request: httpx.Response(200, json=[{"account_id": 1}]))

    with pytest.raises(PortfolioUnavailable):
        await client.list_latest_returns(user_id=USER_ID, account_label="main")


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


async def test_add_transaction_sends_a_missing_amount_as_null():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(201, json={"status": "ok", "rows_saved": 1, "transactions_saved": 1})

    client = _client(handler)
    await client.add_transaction(
        user_id=USER_ID,
        account_label="main",
        broker_transaction_ref="ref-1",
        transaction_type="BUY",
        trade_datetime=datetime(2026, 1, 5, tzinfo=timezone.utc),
        native_amount=None,
        native_currency="EUR",
        quantity=Decimal("5"),
        price=Decimal("128.65"),
    )

    body = captured["body"].decode()
    assert '"native_amount":null' in body or '"native_amount": null' in body


# --- ticker mappings (admin) ---

ADMIN_ID = "00000000-0000-7000-8000-000000000001"
_MAPPING_JSON = {
    "id": 7,
    "identifier_kind": "SYMBOL",
    "identifier": "IWDA",
    "broker": "globex",
    "canonical_ticker": "IWDA.AS",
    "updated_by": ADMIN_ID,
    "created_at": "2026-10-01T00:00:00Z",
    "updated_at": "2026-10-06T09:30:00Z",
}
_WRITE_JSON = {
    "status": "ok",
    "rows_saved": 1,
    "accounts_rebuilt": 2,
    "rebuilds_failed": 1,
    "mapping": _MAPPING_JSON,
}


async def test_list_ticker_mappings_parses_each_mapping():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, json=[_MAPPING_JSON, {**_MAPPING_JSON, "broker": None}])

    mappings = await _client(handler).list_ticker_mappings()

    assert captured["path"] == "/ticker-mappings"
    assert mappings[0].id == 7
    assert mappings[0].identifier_kind == "SYMBOL"
    assert mappings[0].broker == "globex"
    assert mappings[0].canonical_ticker == "IWDA.AS"
    assert mappings[0].updated_at == datetime(2026, 10, 6, 9, 30, tzinfo=timezone.utc)
    assert mappings[1].broker is None


async def test_list_ticker_mappings_raises_unavailable_on_a_malformed_body():
    client = _client(lambda request: httpx.Response(200, json=[{"id": 7}]))

    with pytest.raises(PortfolioUnavailable):
        await client.list_ticker_mappings()


async def test_set_ticker_mapping_puts_the_mapping_and_parses_the_result():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = request.read()
        return httpx.Response(200, json=_WRITE_JSON)

    result = await _client(handler).set_ticker_mapping(
        identifier_kind="SYMBOL",
        identifier="IWDA",
        broker="globex",
        canonical_ticker="IWDA.AS",
        updated_by=ADMIN_ID,
    )

    assert (captured["method"], captured["path"]) == ("PUT", "/ticker-mappings")
    assert json.loads(captured["body"]) == {
        "identifier_kind": "SYMBOL",
        "identifier": "IWDA",
        "broker": "globex",
        "canonical_ticker": "IWDA.AS",
        "updated_by": ADMIN_ID,
    }
    assert result.mapping.id == 7
    assert result.accounts_rebuilt == 2
    assert result.rebuilds_failed == 1


async def test_set_ticker_mapping_raises_invalid_request_with_portfolios_message():
    client = _client(
        lambda request: httpx.Response(422, json={"detail": "A SYMBOL mapping needs a broker."})
    )

    with pytest.raises(InvalidPortfolioRequest, match="needs a broker"):
        await client.set_ticker_mapping(
            identifier_kind="SYMBOL",
            identifier="IWDA",
            broker=None,
            canonical_ticker="IWDA.AS",
            updated_by=ADMIN_ID,
        )


async def test_delete_ticker_mapping_sends_the_admin_and_parses_the_result():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=_WRITE_JSON)

    result = await _client(handler).delete_ticker_mapping(mapping_id=7, updated_by=ADMIN_ID)

    assert (captured["method"], captured["path"]) == ("DELETE", "/ticker-mappings/7")
    assert captured["params"] == {"updated_by": ADMIN_ID}
    assert result.mapping.identifier == "IWDA"


async def test_delete_ticker_mapping_maps_404_to_ticker_mapping_not_found():
    # Not AccountNotFound: on this route a 404 means the mapping is already gone.
    client = _client(lambda request: httpx.Response(404, json={"detail": "No ticker mapping"}))

    with pytest.raises(TickerMappingNotFound):
        await client.delete_ticker_mapping(mapping_id=7, updated_by=ADMIN_ID)


async def test_list_all_unmapped_instruments_parses_instruments_with_optional_broker():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(
            200,
            json=[
                {"kind": "ISIN", "identifier": "IE00B4L5Y983", "broker": None},
                {"kind": "SYMBOL", "identifier": "SGLD", "broker": "globex"},
            ],
        )

    unmapped = await _client(handler).list_all_unmapped_instruments()

    assert captured["path"] == "/instruments/unmapped/all"
    assert [(u.kind, u.identifier, u.broker) for u in unmapped] == [
        ("ISIN", "IE00B4L5Y983", None),
        ("SYMBOL", "SGLD", "globex"),
    ]


# --- UI-7: rebuild statuses on a write, and the user's own unmapped instruments ---


async def _add_deposit(client: HttpPortfolioClient):
    return await client.add_transaction(
        user_id=USER_ID,
        account_label="main",
        broker_transaction_ref="ref-1",
        transaction_type="DEPOSIT",
        trade_datetime=datetime(2026, 1, 5, tzinfo=timezone.utc),
        native_amount=Decimal("100.00"),
        native_currency="EUR",
    )


async def test_add_transaction_parses_the_rebuild_statuses():
    body = {
        "status": "ok",
        "rows_saved": 1,
        "transactions_saved": 1,
        "positions_rebuild": "partial",
        "returns_rebuild": "failed",
    }
    client = _client(lambda request: httpx.Response(201, json=body))

    result = await _add_deposit(client)

    assert result.positions_rebuild == "partial"
    assert result.returns_rebuild == "failed"


async def test_add_transaction_tolerates_an_older_response_without_rebuild_statuses():
    body = {"status": "ok", "rows_saved": 1, "transactions_saved": 1}
    client = _client(lambda request: httpx.Response(201, json=body))

    result = await _add_deposit(client)

    assert result.positions_rebuild is None
    assert result.returns_rebuild is None


async def test_import_transactions_parses_the_rebuild_statuses():
    body = {
        "status": "ok",
        "rows_saved": 3,
        "transactions_saved": 3,
        "positions_rebuild": "ok",
        "returns_rebuild": "skipped",
    }
    client = _client(lambda request: httpx.Response(200, json=body))

    result = await client.import_transactions(
        user_id=USER_ID,
        account_label="main",
        filename="export.csv",
        content=b"a,b\n",
        content_type="text/csv",
    )

    assert (result.positions_rebuild, result.returns_rebuild) == ("ok", "skipped")


async def test_a_non_string_rebuild_status_is_a_malformed_response():
    body = {
        "status": "ok",
        "rows_saved": 1,
        "transactions_saved": 1,
        "positions_rebuild": 3,
        "returns_rebuild": "ok",
    }
    client = _client(lambda request: httpx.Response(201, json=body))

    with pytest.raises(PortfolioUnavailable, match="malformed"):
        await _add_deposit(client)


async def test_list_unmapped_instruments_is_scoped_to_the_user():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200, json=[{"kind": "ISIN", "identifier": "IE00B4L5Y983", "broker": "acme"}]
        )

    unmapped = await _client(handler).list_unmapped_instruments(user_id=USER_ID)

    assert captured["path"] == "/instruments/unmapped"
    assert captured["params"] == {"user_id": USER_ID}
    assert [(u.kind, u.identifier, u.broker) for u in unmapped] == [
        ("ISIN", "IE00B4L5Y983", "acme")
    ]


async def test_list_unmapped_instruments_raises_unavailable_on_a_server_error():
    client = _client(lambda request: httpx.Response(500, text="boom"))

    with pytest.raises(PortfolioUnavailable):
        await client.list_unmapped_instruments(user_id=USER_ID)


async def test_list_unmapped_instruments_raises_unavailable_on_a_malformed_body():
    client = _client(lambda request: httpx.Response(200, json=[{"kind": "ISIN"}]))

    with pytest.raises(PortfolioUnavailable, match="malformed"):
        await client.list_unmapped_instruments(user_id=USER_ID)
