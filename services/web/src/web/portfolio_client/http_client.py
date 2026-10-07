from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx
from web.portfolio_client.base import (
    Account,
    AccountWriteResult,
    Position,
    Return,
    TickerMapping,
    TickerMappingWriteResult,
    TransactionWriteResult,
    UnmappedInstrument,
)
from web.portfolio_client.errors import (
    AccountLabelConflict,
    AccountNotFound,
    InvalidPortfolioRequest,
    PortfolioUnavailable,
    TickerMappingNotFound,
)


def _error_detail(response: httpx.Response) -> str:
    """Normalizes portfolio's two 422 body shapes -- {"detail": "<message>"} from a
    PortfolioError handler, or FastAPI's own {"detail": [{"msg": ..., ...}, ...]} from Pydantic
    request validation -- into one display string, plus a fallback for any other status."""
    try:
        body = response.json()
    except ValueError:
        return response.text or f"status {response.status_code}"
    if not isinstance(body, dict):
        return response.text or f"status {response.status_code}"
    detail = body.get("detail")
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        messages = [
            item.get("msg", str(item)) if isinstance(item, dict) else str(item) for item in detail
        ]
        if messages:
            return "; ".join(messages)
    return f"status {response.status_code}"


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code == 404:
        raise AccountNotFound(_error_detail(response))
    if response.status_code == 409:
        raise AccountLabelConflict(_error_detail(response))
    if response.status_code == 422:
        raise InvalidPortfolioRequest(_error_detail(response))
    raise PortfolioUnavailable(
        f"unexpected portfolio response {response.status_code}: " f"{_error_detail(response)}"
    )


def _json_body(response: httpx.Response):
    try:
        return response.json()
    except ValueError as exc:
        raise PortfolioUnavailable(f"malformed portfolio response: {exc}") from exc


def _account_write_result(body: dict) -> AccountWriteResult:
    try:
        return AccountWriteResult(
            status=body["status"],
            rows_saved=body["rows_saved"],
            accounts_created=body["accounts_created"],
        )
    except (KeyError, TypeError) as exc:
        raise PortfolioUnavailable(f"malformed portfolio response: {exc}") from exc


def _transaction_write_result(body: dict) -> TransactionWriteResult:
    try:
        return TransactionWriteResult(
            status=body["status"],
            rows_saved=body["rows_saved"],
            transactions_saved=body["transactions_saved"],
        )
    except (KeyError, TypeError) as exc:
        raise PortfolioUnavailable(f"malformed portfolio response: {exc}") from exc


def _decimal_str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _opt_decimal(value) -> Decimal | None:
    return None if value is None else Decimal(value)


def _position(item: dict) -> Position:
    try:
        return Position(
            account_id=item["account_id"],
            canonical_ticker=item["canonical_ticker"],
            as_of_date=date.fromisoformat(item["as_of_date"]),
            quantity=Decimal(item["quantity"]),
            average_cost_native=Decimal(item["average_cost_native"]),
            native_currency=item["native_currency"],
            average_cost_eur=Decimal(item["average_cost_eur"]),
            cost_basis_eur=Decimal(item["cost_basis_eur"]),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise PortfolioUnavailable(f"malformed portfolio response: {exc}") from exc


def _return(item: dict) -> Return:
    try:
        return Return(
            account_id=item["account_id"],
            canonical_ticker=item["canonical_ticker"],
            price_date=date.fromisoformat(item["price_date"]),
            quantity=Decimal(item["quantity"]),
            market_value_native=Decimal(item["market_value_native"]),
            market_value_eur=Decimal(item["market_value_eur"]),
            cost_basis_eur=Decimal(item["cost_basis_eur"]),
            unrealized_return_eur=Decimal(item["unrealized_return_eur"]),
            unrealized_return_pct=_opt_decimal(item["unrealized_return_pct"]),
            dividends_received_eur=Decimal(item["dividends_received_eur"]),
            total_return_eur=Decimal(item["total_return_eur"]),
            total_return_pct=_opt_decimal(item["total_return_pct"]),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise PortfolioUnavailable(f"malformed portfolio response: {exc}") from exc


def _ticker_mapping(item: dict) -> TickerMapping:
    try:
        return TickerMapping(
            id=item["id"],
            identifier_kind=item["identifier_kind"],
            identifier=item["identifier"],
            broker=item["broker"],
            canonical_ticker=item["canonical_ticker"],
            updated_at=datetime.fromisoformat(item["updated_at"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PortfolioUnavailable(f"malformed portfolio response: {exc}") from exc


def _ticker_mapping_write_result(body: dict) -> TickerMappingWriteResult:
    try:
        return TickerMappingWriteResult(
            mapping=_ticker_mapping(body["mapping"]),
            accounts_rebuilt=body["accounts_rebuilt"],
            rebuilds_failed=body["rebuilds_failed"],
        )
    except (KeyError, TypeError) as exc:
        raise PortfolioUnavailable(f"malformed portfolio response: {exc}") from exc


class HttpPortfolioClient:
    """The real PortfolioClient, calling portfolio's HTTP API (task 9).

    Every call sets X-Internal-Secret -- portfolio rejects every route but /health without it.
    Unlike HttpAuthClient, no Cookie handling is needed: portfolio calls carry no browser
    session, and the one shared httpx.AsyncClient is safe to reuse across concurrent visitors as
    a result.
    """

    def __init__(self, client: httpx.AsyncClient, internal_secret: str):
        self._client = client
        self._internal_secret = internal_secret

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = {"X-Internal-Secret": self._internal_secret}
        try:
            return await self._client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise PortfolioUnavailable(str(exc)) from exc

    async def list_accounts(self, *, user_id: str) -> list[Account]:
        response = await self._request("GET", "/accounts", params={"user_id": user_id})
        if response.status_code != 200:
            _raise_for_status(response)
        try:
            return [
                Account(
                    id=item["id"],
                    account_label=item["account_label"],
                    broker=item["broker"],
                    created_at=item["created_at"],
                )
                for item in _json_body(response)
            ]
        except (KeyError, TypeError) as exc:
            raise PortfolioUnavailable(f"malformed portfolio response: {exc}") from exc

    async def list_brokers(self) -> list[str]:
        response = await self._request("GET", "/brokers")
        if response.status_code != 200:
            _raise_for_status(response)
        return _json_body(response)

    async def list_tickers(self) -> list[str]:
        response = await self._request("GET", "/instruments/tickers")
        if response.status_code != 200:
            _raise_for_status(response)
        return _json_body(response)

    async def create_account(
        self, *, user_id: str, broker: str, account_label: str
    ) -> AccountWriteResult:
        response = await self._request(
            "POST",
            "/accounts",
            json={"user_id": user_id, "broker": broker, "account_label": account_label},
        )
        if response.status_code not in (200, 201):
            _raise_for_status(response)
        return _account_write_result(_json_body(response))

    async def add_transaction(
        self,
        *,
        user_id: str,
        account_label: str,
        broker_transaction_ref: str,
        transaction_type: str,
        trade_datetime: datetime,
        native_amount: Decimal | None,
        native_currency: str,
        instrument_identifier: str | None = None,
        instrument_identifier_kind: str | None = None,
        quantity: Decimal | None = None,
        price: Decimal | None = None,
        fx_rate: Decimal | None = None,
        fees_native: Decimal | None = None,
    ) -> TransactionWriteResult:
        payload = {
            "user_id": user_id,
            "account_label": account_label,
            "broker_transaction_ref": broker_transaction_ref,
            "transaction_type": transaction_type,
            "trade_datetime": trade_datetime.isoformat(),
            "native_amount": _decimal_str(native_amount),
            "native_currency": native_currency,
            "instrument_identifier": instrument_identifier,
            "instrument_identifier_kind": instrument_identifier_kind,
            "quantity": _decimal_str(quantity),
            "price": _decimal_str(price),
            "fx_rate": _decimal_str(fx_rate),
            "fees_native": _decimal_str(fees_native),
        }
        response = await self._request("POST", "/transactions", json=payload)
        if response.status_code not in (200, 201):
            _raise_for_status(response)
        return _transaction_write_result(_json_body(response))

    async def import_transactions(
        self,
        *,
        user_id: str,
        account_label: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> TransactionWriteResult:
        response = await self._request(
            "POST",
            "/transactions/import",
            data={"user_id": user_id, "account_label": account_label},
            files={"file": (filename, content, content_type)},
        )
        if response.status_code not in (200, 201):
            _raise_for_status(response)
        return _transaction_write_result(_json_body(response))

    async def list_positions(self, *, user_id: str, account_label: str) -> list[Position]:
        response = await self._request(
            "GET", "/positions", params={"user_id": user_id, "account_label": account_label}
        )
        if response.status_code != 200:
            _raise_for_status(response)
        return [_position(item) for item in _json_body(response)]

    async def list_returns(self, *, user_id: str, account_label: str) -> list[Return]:
        response = await self._request(
            "GET", "/returns", params={"user_id": user_id, "account_label": account_label}
        )
        if response.status_code != 200:
            _raise_for_status(response)
        return [_return(item) for item in _json_body(response)]

    async def list_latest_returns(self, *, user_id: str, account_label: str) -> list[Return]:
        response = await self._request(
            "GET", "/returns/latest", params={"user_id": user_id, "account_label": account_label}
        )
        if response.status_code != 200:
            _raise_for_status(response)
        return [_return(item) for item in _json_body(response)]

    async def list_ticker_mappings(self) -> list[TickerMapping]:
        response = await self._request("GET", "/ticker-mappings")
        if response.status_code != 200:
            _raise_for_status(response)
        return [_ticker_mapping(item) for item in _json_body(response)]

    async def list_all_unmapped_instruments(self) -> list[UnmappedInstrument]:
        response = await self._request("GET", "/instruments/unmapped/all")
        if response.status_code != 200:
            _raise_for_status(response)
        try:
            return [
                UnmappedInstrument(
                    kind=item["kind"], identifier=item["identifier"], broker=item["broker"]
                )
                for item in _json_body(response)
            ]
        except (KeyError, TypeError) as exc:
            raise PortfolioUnavailable(f"malformed portfolio response: {exc}") from exc

    async def set_ticker_mapping(
        self,
        *,
        identifier_kind: str,
        identifier: str,
        broker: str | None,
        canonical_ticker: str,
        updated_by: str,
    ) -> TickerMappingWriteResult:
        response = await self._request(
            "PUT",
            "/ticker-mappings",
            json={
                "identifier_kind": identifier_kind,
                "identifier": identifier,
                "broker": broker,
                "canonical_ticker": canonical_ticker,
                "updated_by": updated_by,
            },
        )
        if response.status_code != 200:
            _raise_for_status(response)
        return _ticker_mapping_write_result(_json_body(response))

    async def delete_ticker_mapping(
        self, *, mapping_id: int, updated_by: str
    ) -> TickerMappingWriteResult:
        response = await self._request(
            "DELETE", f"/ticker-mappings/{mapping_id}", params={"updated_by": updated_by}
        )
        if response.status_code == 404:
            raise TickerMappingNotFound(_error_detail(response))
        if response.status_code != 200:
            _raise_for_status(response)
        return _ticker_mapping_write_result(_json_body(response))
