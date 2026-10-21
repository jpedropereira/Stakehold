from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import httpx
from web.portfolio_client.base import (
    BACKTEST_PERIODS,
    BACKTEST_RISK_WINDOWS,
    RISK_FIGURE_KEYS,
    RISK_WINDOWS,
    Account,
    AccountDeleteResult,
    AccountWriteResult,
    Backtest,
    BacktestAssetStatus,
    BacktestGrowth,
    BacktestPeriodReturn,
    BacktestRisk,
    BacktestRow,
    BacktestRowRisk,
    BacktestWindowRisk,
    Broker,
    DrawdownSeries,
    ExcludedHolding,
    ExPostRisk,
    ExPostWindow,
    Holding,
    HoldingAccount,
    Holdings,
    HoldingsTotals,
    PortfolioReturns,
    PortfolioSeriesPoint,
    Position,
    Return,
    RiskFigure,
    RiskFreeStatus,
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
                )
                for item in body["series"]
            ),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise PortfolioUnavailable(f"malformed portfolio response: {exc}") from exc


def _int(item: dict, key: str) -> int:
    value = item[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} is not an integer")
    return value


def _tickers(item: dict, key: str) -> tuple[str, ...]:
    """A required list of tickers. portfolio always sends `left_out`, so a missing key is a
    contract break: reading it as "none left out" would let the mix's figure pass for every
    asset's."""
    value = item[key]
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise TypeError(f"{key} is not a list of tickers")
    return tuple(value)


def _backtest_period(item: dict) -> BacktestPeriodReturn:
    return BacktestPeriodReturn(
        start=date.fromisoformat(item["start"]),
        end=date.fromisoformat(item["end"]),
        covered=_bool(item, "covered"),
        return_pct=_opt_decimal(item["return_pct"]),
        gaps=_int(item, "gaps"),
        left_out=_tickers(item, "left_out"),
    )


def _backtest_row(item: dict) -> BacktestRow:
    periods = item["periods"]
    missing = [p for p in BACKTEST_PERIODS if p not in periods]
    if missing:
        # The table has a column per period, so a row without one can't be shown truthfully.
        raise ValueError(f"backtest row missing period(s) {', '.join(missing)}")
    return BacktestRow(
        ticker=item["ticker"],
        weight=Decimal(item["weight"]),
        periods={p: _backtest_period(periods[p]) for p in BACKTEST_PERIODS},
    )


def _growth_line(values, length: int) -> tuple[Decimal | None, ...] | None:
    if values is None:
        return None
    # A null point is a Max asset line before its first day (LB-9).
    line = tuple(None if v is None else Decimal(v) for v in values)
    if len(line) != length:
        # One value per date, or the chart would draw each point against the wrong day.
        raise ValueError(f"growth line has {len(line)} values for {length} dates")
    return line


def _backtest_growth(item: dict) -> BacktestGrowth:
    dates = tuple(date.fromisoformat(d) for d in item["dates"])
    return BacktestGrowth(
        period=item["period"],
        dates=dates,
        mix=_growth_line(item["mix"], len(dates)),
        assets={t: _growth_line(v, len(dates)) for t, v in item["assets"].items()},
        left_out=_tickers(item, "left_out"),
    )


def _float(value, key: str) -> float:
    """A JSON number. A bool is an int to Python, and a string would format as a figure, so
    both are refused rather than shown as 1.00 or passed through."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{key} is not a number")
    return float(value)


def _opt_float(value, key: str) -> float | None:
    return None if value is None else _float(value, key)


def _risk_figure(item: dict | None) -> RiskFigure | None:
    if item is None:
        return None
    return RiskFigure(
        value=_opt_float(item["value"], "value"),
        window_start=date.fromisoformat(item["window_start"]),
        window_end=date.fromisoformat(item["window_end"]),
        frequency=item["frequency"],
        q=_float(item["q"], "q"),
        n_obs=_int(item, "n_obs"),
        n_gaps=_int(item, "n_gaps"),
        flags=_tickers(item, "flags"),
        reason=item["reason"],
        standard_error=_opt_float(item["standard_error"], "standard_error"),
    )


def _window_risk(item: dict) -> BacktestWindowRisk:
    days = item["max_days_under_water"]
    return BacktestWindowRisk(
        start=_opt_date(item["start"]),
        end=date.fromisoformat(item["end"]),
        covered=_bool(item, "covered"),
        reason=item["reason"],
        figures={key: _risk_figure(item[key]) for key in RISK_FIGURE_KEYS},
        peak_date=_opt_date(item["peak_date"]),
        trough_date=_opt_date(item["trough_date"]),
        recovery_date=_opt_date(item["recovery_date"]),
        max_days_under_water=None if days is None else _int(item, "max_days_under_water"),
    )


def _row_risk(item: dict) -> BacktestRowRisk:
    windows = item["windows"]
    missing = [w for w in BACKTEST_RISK_WINDOWS if w not in windows]
    if missing:
        # The risk table has a column per window, as the period table has one per period.
        raise ValueError(f"risk row missing window(s) {', '.join(missing)}")
    return BacktestRowRisk(
        ticker=item["ticker"],
        windows={w: _window_risk(windows[w]) for w in BACKTEST_RISK_WINDOWS},
    )


def _drawdown(item: dict) -> DrawdownSeries:
    dates = tuple(date.fromisoformat(d) for d in item["dates"])
    values = tuple(_opt_float(v, "drawdown value") for v in item["values"])
    if len(values) != len(dates):
        # One value per date, or the chart would draw each point against the wrong day.
        raise ValueError(f"drawdown has {len(values)} values for {len(dates)} dates")
    return DrawdownSeries(period=item["period"], dates=dates, values=values)


def _backtest_risk(item: dict) -> BacktestRisk:
    risk_free, drawdown = item["risk_free"], item["drawdown"]
    return BacktestRisk(
        label=item["label"],
        end_date=date.fromisoformat(item["end_date"]),
        common_start=_opt_date(item["common_start"]),
        limited_by=_tickers(item, "limited_by"),
        risk_free=RiskFreeStatus(
            available=_bool(risk_free, "available"),
            reason=risk_free["reason"],
            first_date=_opt_date(risk_free["first_date"]),
        ),
        mix=_row_risk(item["mix"]),
        assets=tuple(_row_risk(row) for row in item["assets"]),
        drawdown=None if drawdown is None else _drawdown(drawdown),
    )


def _backtest(body: dict) -> Backtest:
    try:
        # `risk` is read as required: portfolio always sends it (null with no asset included),
        # so a missing key is a contract break, not a backtest with no risk figures.
        mix, growth, risk = body["mix"], body["growth"], body["risk"]
        return Backtest(
            end_date=_opt_date(body["end_date"]),
            mix=None if mix is None else _backtest_row(mix),
            assets=tuple(_backtest_row(item) for item in body["assets"]),
            growth=None if growth is None else _backtest_growth(growth),
            statuses=tuple(
                BacktestAssetStatus(
                    ticker=item["ticker"], status=item["status"], reason=item["reason"]
                )
                for item in body["statuses"]
            ),
            risk=None if risk is None else _backtest_risk(risk),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise PortfolioUnavailable(f"malformed portfolio response: {exc}") from exc


def _ex_post_figure(item: dict, name: str) -> RiskFigure:
    """Unlike a backtest window's, an ex-post figure is never null: an uncovered window greys
    each figure with its reason instead (RK-11)."""
    figure = _risk_figure(item[name])
    if figure is None:
        raise TypeError(f"{name} is null")
    return figure


def _ex_post_window(item: dict) -> ExPostWindow:
    max_days = item["max_days_under_water"]
    return ExPostWindow(
        label=item["label"],
        start=date.fromisoformat(item["start"]),
        end=date.fromisoformat(item["end"]),
        covered=_bool(item, "covered"),
        figures={name: _ex_post_figure(item, name) for name in RISK_FIGURE_KEYS},
        peak_date=_opt_date(item["peak_date"]),
        trough_date=_opt_date(item["trough_date"]),
        recovery_date=_opt_date(item["recovery_date"]),
        max_days_under_water=None if max_days is None else _int(item, "max_days_under_water"),
    )


def _ex_post_risk(body: dict) -> ExPostRisk:
    try:
        windows = tuple(_ex_post_window(item) for item in body["windows"])
        labels = tuple(w.label for w in windows)
        if windows and labels != RISK_WINDOWS:
            # The table has a column per window, in this order: anything else would put figures
            # under the wrong heading.
            raise ValueError(f"risk windows {labels}, expected {RISK_WINDOWS}")
        return ExPostRisk(
            label=body["label"],
            base_date=_opt_date(body["base_date"]),
            end_date=_opt_date(body["end_date"]),
            rf_available=_bool(body, "rf_available"),
            windows=windows,
            excluded=tuple(
                ExcludedHolding(ticker=item["ticker"], status=item["status"], reason=item["reason"])
                for item in body["excluded"]
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
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

    async def backtest(self, *, assets: list[tuple[str, Decimal]], period: str) -> Backtest:
        response = await self._request(
            "POST",
            "/labs/backtest",
            json={
                "assets": [
                    {"ticker": ticker, "weight": _decimal_str(weight)} for ticker, weight in assets
                ],
                "period": period,
            },
        )
        if response.status_code == 422:
            raise InvalidPortfolioRequest(_error_detail(response))
        if response.status_code != 200:
            # Not _raise_for_status: its 404 means AccountNotFound, and this route has no account.
            raise PortfolioUnavailable(
                f"unexpected portfolio response {response.status_code}: {_error_detail(response)}"
            )
        return _backtest(_json_body(response))

    async def ex_post_risk(self, *, user_id: str) -> ExPostRisk:
        response = await self._request("GET", "/risk/ex-post", params={"user_id": user_id})
        if response.status_code != 200:
            # Not _raise_for_status: its 404 means AccountNotFound, and this route has no account.
            raise PortfolioUnavailable(
                f"unexpected portfolio response {response.status_code}: {_error_detail(response)}"
            )
        return _ex_post_risk(_json_body(response))

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
