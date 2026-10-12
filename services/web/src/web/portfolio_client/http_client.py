from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import httpx
from web.portfolio_client.base import (
    Account,
    AccountDeleteResult,
    AccountWriteResult,
    Broker,
    GrowthPoint,
    Holding,
    HoldingAccount,
    Holdings,
    HoldingsTotals,
    PortfolioReturns,
    PortfolioSeriesPoint,
    Position,
    Return,
    TickerGrowth,
    TickerLookup,
    TickerMapping,
    TickerMappingWriteResult,
    TrackedTicker,
    TrackedTickerAddResult,
    Transaction,
    TransactionDeleteResult,
    TransactionWriteResult,
    UnmappedInstrument,
)
from web.portfolio_client.errors import (
    AccountLabelConflict,
    AccountNotFound,
    InvalidPortfolioRequest,
    InvalidTickerRequest,
    PortfolioUnavailable,
    TickerChangeRefused,
    TickerLimitReached,
    TickerMappingNotFound,
    TickerNotOnProvider,
    TickerProviderUnavailable,
    TrackedTickerNotFound,
    TransactionNotFound,
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


# The tracked-ticker routes' refusals, each its own sentence for the user. A 404 means "not on
# the provider" on add and "not tracked" on an admin change.
_TICKER_ERRORS = {
    409: TickerChangeRefused,
    422: InvalidTickerRequest,
    429: TickerLimitReached,
    503: TickerProviderUnavailable,
}


def _raise_for_ticker_status(response: httpx.Response, *, not_found) -> None:
    if response.status_code == 404:
        raise not_found(_error_detail(response))
    error = _TICKER_ERRORS.get(response.status_code)
    if error is not None:
        raise error(_error_detail(response))
    raise PortfolioUnavailable(
        f"unexpected portfolio response {response.status_code}: {_error_detail(response)}"
    )


def _tracked_ticker(item: dict) -> TrackedTicker:
    try:
        return TrackedTicker(
            ticker=item["ticker"],
            display_name=item["display_name"],
            asset_type=item["asset_type"],
            currency=item["currency"],
            exchange=item["exchange"],
            origin=item["origin"],
            status=item["status"],
            enabled=item["enabled"],
            attempts=item["attempts"],
            last_error=item["last_error"],
            requested_by=item["requested_by"],
            requested_at=datetime.fromisoformat(item["requested_at"]),
            ready_at=datetime.fromisoformat(item["ready_at"]) if item["ready_at"] else None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PortfolioUnavailable(f"malformed portfolio response: {exc}") from exc


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


def _broker(body: dict) -> Broker:
    try:
        return Broker(broker=body["broker"], imports=tuple(body["imports"]))
    except (KeyError, TypeError) as exc:
        raise PortfolioUnavailable(f"malformed portfolio response: {exc}") from exc


def _transaction_write_result(body: dict) -> TransactionWriteResult:
    try:
        return TransactionWriteResult(
            status=body["status"],
            rows_saved=body["rows_saved"],
            transactions_saved=body["transactions_saved"],
            positions_rebuild=_opt_status(body, "positions_rebuild"),
            returns_rebuild=_opt_status(body, "returns_rebuild"),
        )
    except (KeyError, TypeError) as exc:
        raise PortfolioUnavailable(f"malformed portfolio response: {exc}") from exc


def _account_delete_result(body: dict) -> AccountDeleteResult:
    try:
        return AccountDeleteResult(
            status=body["status"],
            accounts_deleted=body["accounts_deleted"],
            transactions_deleted=body["transactions_deleted"],
        )
    except (KeyError, TypeError) as exc:
        raise PortfolioUnavailable(f"malformed portfolio response: {exc}") from exc


def _transaction_delete_result(body: dict) -> TransactionDeleteResult:
    try:
        return TransactionDeleteResult(
            status=body["status"],
            transactions_deleted=body["transactions_deleted"],
            positions_rebuild=_opt_status(body, "positions_rebuild"),
            returns_rebuild=_opt_status(body, "returns_rebuild"),
        )
    except (KeyError, TypeError) as exc:
        raise PortfolioUnavailable(f"malformed portfolio response: {exc}") from exc


def _opt_status(body: dict, key: str) -> str | None:
    """A rebuild status, or None when an older portfolio response doesn't carry it."""
    value = body.get(key)
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{key} is not a string")
    return value


def _unmapped_instrument(item: dict) -> UnmappedInstrument:
    return UnmappedInstrument(
        kind=item["kind"], identifier=item["identifier"], broker=item["broker"]
    )


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


def _growth_point(item: dict) -> GrowthPoint:
    try:
        return GrowthPoint(
            price_date=date.fromisoformat(item["price_date"]),
            growth_pct=Decimal(item["growth_pct"]),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise PortfolioUnavailable(f"malformed portfolio response: {exc}") from exc


def _bool(item: dict, key: str) -> bool:
    """A flag that must be a real JSON boolean. A string "false" is truthy, and misreading
    `priced` would show a partial sum as the holding's value."""
    value = item[key]
    if not isinstance(value, bool):
        raise TypeError(f"{key} is not a boolean")
    return value


def _opt_date(value) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _holding_account(item: dict) -> HoldingAccount:
    return HoldingAccount(
        account_label=item["account_label"],
        quantity=Decimal(item["quantity"]),
        native_currency=item["native_currency"],
        average_cost_native=Decimal(item["average_cost_native"]),
        cost_basis_eur=Decimal(item["cost_basis_eur"]),
        market_value_native=_opt_decimal(item["market_value_native"]),
        market_value_eur=_opt_decimal(item["market_value_eur"]),
        unrealized_return_eur=_opt_decimal(item["unrealized_return_eur"]),
    )


def _holding(item: dict) -> Holding:
    return Holding(
        canonical_ticker=item["canonical_ticker"],
        quantity=Decimal(item["quantity"]),
        average_cost_eur=_opt_decimal(item["average_cost_eur"]),
        cost_basis_eur=Decimal(item["cost_basis_eur"]),
        native_currency=item["native_currency"],
        mixed_currency=_bool(item, "mixed_currency"),
        average_cost_native=_opt_decimal(item["average_cost_native"]),
        market_value_native=_opt_decimal(item["market_value_native"]),
        priced=_bool(item, "priced"),
        price_date=_opt_date(item["price_date"]),
        market_value_eur=_opt_decimal(item["market_value_eur"]),
        unrealized_return_eur=_opt_decimal(item["unrealized_return_eur"]),
        unrealized_return_pct=_opt_decimal(item["unrealized_return_pct"]),
        dividends_received_eur=_opt_decimal(item["dividends_received_eur"]),
        total_return_eur=_opt_decimal(item["total_return_eur"]),
        total_return_pct=_opt_decimal(item["total_return_pct"]),
        accounts=tuple(_holding_account(a) for a in item["accounts"]),
    )


def _holdings_totals(item: dict) -> HoldingsTotals:
    return HoldingsTotals(
        price_date=date.fromisoformat(item["price_date"]),
        market_value_eur=Decimal(item["market_value_eur"]),
        cost_basis_eur=Decimal(item["cost_basis_eur"]),
        dividends_received_eur=Decimal(item["dividends_received_eur"]),
        total_return_eur=Decimal(item["total_return_eur"]),
        total_return_pct=_opt_decimal(item["total_return_pct"]),
    )


def _holdings(body: dict) -> Holdings:
    try:
        totals = body["totals"]
        return Holdings(
            holdings=tuple(_holding(item) for item in body["holdings"]),
            totals=None if totals is None else _holdings_totals(totals),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise PortfolioUnavailable(f"malformed portfolio response: {exc}") from exc


def _portfolio_returns(body: dict) -> PortfolioReturns:
    try:
        return PortfolioReturns(
            series=tuple(
                PortfolioSeriesPoint(
                    price_date=date.fromisoformat(item["price_date"]),
                    market_value_eur=Decimal(item["market_value_eur"]),
                    cost_basis_eur=Decimal(item["cost_basis_eur"]),
                    dividends_received_eur=Decimal(item["dividends_received_eur"]),
                    total_return_eur=Decimal(item["total_return_eur"]),
                    total_return_pct=_opt_decimal(item["total_return_pct"]),
                    growth_pct=Decimal(item["growth_pct"]),
                )
                for item in body["series"]
            ),
            by_ticker=tuple(
                TickerGrowth(
                    canonical_ticker=item["canonical_ticker"],
                    growth_pct=Decimal(item["growth_pct"]),
                )
                for item in body["by_ticker"]
            ),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise PortfolioUnavailable(f"malformed portfolio response: {exc}") from exc


def _transaction(item: dict) -> Transaction:
    try:
        return Transaction(
            account_id=item["account_id"],
            broker_transaction_ref=item["broker_transaction_ref"],
            transaction_type=item["transaction_type"],
            trade_datetime=datetime.fromisoformat(item["trade_datetime"]),
            instrument_identifier=item["instrument_identifier"],
            instrument_identifier_kind=item["instrument_identifier_kind"],
            quantity=_opt_decimal(item["quantity"]),
            price=_opt_decimal(item["price"]),
            native_currency=item["native_currency"],
            native_amount=Decimal(item["native_amount"]),
            eur_amount=Decimal(item["eur_amount"]),
            fx_rate=_opt_decimal(item["fx_rate"]),
            fx_rate_source=item["fx_rate_source"],
            fees_native=_opt_decimal(item["fees_native"]),
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

    async def list_brokers(self) -> list[Broker]:
        response = await self._request("GET", "/brokers")
        if response.status_code != 200:
            _raise_for_status(response)
        return [_broker(item) for item in _json_body(response)]

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
        return await self._upload(
            "/transactions/import",
            user_id=user_id,
            account_label=account_label,
            filename=filename,
            content=content,
            content_type=content_type,
        )

    async def import_cashflow(
        self,
        *,
        user_id: str,
        account_label: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> TransactionWriteResult:
        return await self._upload(
            "/cashflows/import",
            user_id=user_id,
            account_label=account_label,
            filename=filename,
            content=content,
            content_type=content_type,
        )

    async def _upload(
        self,
        path: str,
        *,
        user_id: str,
        account_label: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> TransactionWriteResult:
        """The two broker-file imports share portfolio's multipart contract and differ only in
        which route (and so which parser registry) they hit."""
        response = await self._request(
            "POST",
            path,
            data={"user_id": user_id, "account_label": account_label},
            files={"file": (filename, content, content_type)},
        )
        if response.status_code not in (200, 201):
            _raise_for_status(response)
        return _transaction_write_result(_json_body(response))

    async def list_transactions(self, *, user_id: str, account_label: str) -> list[Transaction]:
        response = await self._request(
            "GET", "/transactions", params={"user_id": user_id, "account_label": account_label}
        )
        if response.status_code != 200:
            _raise_for_status(response)
        return [_transaction(item) for item in _json_body(response)]

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

    async def list_growth(self, *, user_id: str, account_label: str) -> list[GrowthPoint]:
        response = await self._request(
            "GET", "/returns/growth", params={"user_id": user_id, "account_label": account_label}
        )
        if response.status_code != 200:
            _raise_for_status(response)
        return [_growth_point(item) for item in _json_body(response)]

    async def list_holdings(self, *, user_id: str) -> Holdings:
        response = await self._request("GET", "/holdings", params={"user_id": user_id})
        if response.status_code != 200:
            _raise_for_status(response)
        return _holdings(_json_body(response))

    async def list_portfolio_returns(self, *, user_id: str) -> PortfolioReturns:
        response = await self._request("GET", "/returns/portfolio", params={"user_id": user_id})
        if response.status_code != 200:
            _raise_for_status(response)
        return _portfolio_returns(_json_body(response))

    async def list_ticker_mappings(self) -> list[TickerMapping]:
        response = await self._request("GET", "/ticker-mappings")
        if response.status_code != 200:
            _raise_for_status(response)
        return [_ticker_mapping(item) for item in _json_body(response)]

    async def list_unmapped_instruments(self, *, user_id: str) -> list[UnmappedInstrument]:
        response = await self._request("GET", "/instruments/unmapped", params={"user_id": user_id})
        if response.status_code != 200:
            _raise_for_status(response)
        try:
            return [_unmapped_instrument(item) for item in _json_body(response)]
        except (KeyError, TypeError) as exc:
            raise PortfolioUnavailable(f"malformed portfolio response: {exc}") from exc

    async def list_all_unmapped_instruments(self) -> list[UnmappedInstrument]:
        response = await self._request("GET", "/instruments/unmapped/all")
        if response.status_code != 200:
            _raise_for_status(response)
        try:
            return [_unmapped_instrument(item) for item in _json_body(response)]
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

    async def delete_account(self, *, user_id: str, account_label: str) -> AccountDeleteResult:
        response = await self._request(
            "DELETE", "/accounts", params={"user_id": user_id, "account_label": account_label}
        )
        if response.status_code != 200:
            _raise_for_status(response)
        return _account_delete_result(_json_body(response))

    async def delete_transaction(
        self, *, user_id: str, account_label: str, broker_transaction_ref: str
    ) -> TransactionDeleteResult:
        response = await self._request(
            "DELETE",
            "/transactions",
            params={
                "user_id": user_id,
                "account_label": account_label,
                "broker_transaction_ref": broker_transaction_ref,
            },
        )
        if response.status_code == 404:
            raise TransactionNotFound(_error_detail(response))
        if response.status_code != 200:
            _raise_for_status(response)
        return _transaction_delete_result(_json_body(response))

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

    # --- tracked tickers (ticker onboarding) -----------------------------------------------------

    async def lookup_ticker(self, ticker: str) -> TickerLookup:
        # Quoted: a typed symbol must stay one path segment ("a/b" can't reach another route).
        response = await self._request(
            "GET", f"/instruments/tickers/{quote(ticker, safe='')}/lookup"
        )
        if response.status_code != 200:
            _raise_for_ticker_status(response, not_found=TickerNotOnProvider)
        body = _json_body(response)
        try:
            return TickerLookup(
                ticker=body["ticker"],
                outcome=body["outcome"],
                status=body["status"],
                display_name=body["display_name"],
                currency=body["currency"],
                exchange=body["exchange"],
                asset_type=body["asset_type"],
            )
        except (KeyError, TypeError) as exc:
            raise PortfolioUnavailable(f"malformed portfolio response: {exc}") from exc

    async def add_tracked_ticker(
        self, *, ticker: str, requested_by: str, enforce_limit: bool
    ) -> TrackedTickerAddResult:
        response = await self._request(
            "POST",
            "/instruments/tracked-tickers",
            json={"ticker": ticker, "requested_by": requested_by, "enforce_limit": enforce_limit},
        )
        if response.status_code not in (200, 201):
            _raise_for_ticker_status(response, not_found=TickerNotOnProvider)
        body = _json_body(response)
        if not isinstance(body, dict) or not isinstance(body.get("created"), bool):
            raise PortfolioUnavailable("malformed portfolio response: no created flag")
        return TrackedTickerAddResult(ticker=_tracked_ticker(body), created=body["created"])

    async def list_tracked_tickers(
        self,
        *,
        requested_by: str | None = None,
        statuses: list[str] | None = None,
        tickers: list[str] | None = None,
    ) -> list[TrackedTicker]:
        params = []
        if requested_by is not None:
            params.append(("requested_by", requested_by))
        params.extend(("status", status) for status in statuses or [])
        params.extend(("ticker", ticker) for ticker in tickers or [])
        response = await self._request("GET", "/instruments/tracked-tickers", params=params)
        if response.status_code != 200:
            _raise_for_ticker_status(response, not_found=TrackedTickerNotFound)
        body = _json_body(response)
        if not isinstance(body, list):
            raise PortfolioUnavailable("malformed portfolio response: not a list")
        return [_tracked_ticker(item) for item in body]

    async def update_tracked_ticker(
        self, *, ticker: str, enabled: bool | None = None, retry: bool = False
    ) -> TrackedTicker:
        response = await self._request(
            "PATCH",
            f"/instruments/tracked-tickers/{quote(ticker, safe='')}",
            json={"enabled": enabled, "retry": retry},
        )
        if response.status_code != 200:
            _raise_for_ticker_status(response, not_found=TrackedTickerNotFound)
        return _tracked_ticker(_json_body(response))
