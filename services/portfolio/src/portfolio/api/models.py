"""Request/response models for the portfolio HTTP API.

Kept separate from routes.py, matching data-store's own split -- the response contract, what a
future web.PortfolioClient will consume, is visible in one place. Numeric fields stay `Decimal`,
never `float`: Pydantic v2 serializes `Decimal` to a JSON string in JSON mode, preserving a
NUMERIC column's exact precision the way a float round-trip would not (data-store's own
models.py docstring). Request models mirror cli.py's argparse coercers -- a UUID, a Decimal, a
tz-aware datetime -- so a malformed request 422s the same way a malformed CLI flag would exit 1.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from portfolio.transactions.base import TRANSACTION_TYPES
from pydantic import BaseModel, field_validator


class AccountCreateRequest(BaseModel):
    user_id: UUID
    broker: str
    account_label: str


class RebuildRequest(BaseModel):
    user_id: UUID
    account_label: str | None = None


class AccountResponse(BaseModel):
    id: int
    account_label: str
    broker: str
    created_at: datetime


class TransactionCreateRequest(BaseModel):
    user_id: UUID
    account_label: str
    broker_transaction_ref: str
    transaction_type: str
    trade_datetime: datetime
    native_currency: str
    # Optional for a BUY/SELL, which derives it from price * quantity + fees -- see
    # pipeline.add_manual_transaction. Required (by the pipeline) for every other type.
    native_amount: Decimal | None = None
    instrument_identifier: str | None = None
    instrument_identifier_kind: str | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    fx_rate: Decimal | None = None
    fees_native: Decimal | None = None

    @field_validator("transaction_type")
    @classmethod
    def _check_transaction_type(cls, value: str) -> str:
        if value not in TRANSACTION_TYPES:
            raise ValueError(f"transaction_type must be one of {TRANSACTION_TYPES}")
        return value

    @field_validator("trade_datetime")
    @classmethod
    def _require_utc_offset(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("trade_datetime must include a UTC offset")
        return value


class TransactionResponse(BaseModel):
    account_id: int
    broker_transaction_ref: str
    transaction_type: str
    trade_datetime: datetime
    instrument_identifier: str | None
    instrument_identifier_kind: str | None
    quantity: Decimal | None
    price: Decimal | None
    native_currency: str
    native_amount: Decimal
    eur_amount: Decimal
    fx_rate: Decimal | None
    fx_rate_source: str | None
    fees_native: Decimal | None


class PositionResponse(BaseModel):
    account_id: int
    canonical_ticker: str
    as_of_date: date
    quantity: Decimal
    average_cost_native: Decimal
    native_currency: str
    average_cost_eur: Decimal
    cost_basis_eur: Decimal


class ReturnResponse(BaseModel):
    account_id: int
    canonical_ticker: str
    price_date: date
    quantity: Decimal
    market_value_native: Decimal
    market_value_eur: Decimal
    cost_basis_eur: Decimal
    unrealized_return_eur: Decimal
    unrealized_return_pct: Decimal | None
    dividends_received_eur: Decimal
    total_return_eur: Decimal
    total_return_pct: Decimal | None


class GrowthResponse(BaseModel):
    price_date: date
    # A fraction, not a percentage: 0.1234 is +12.34%, the same convention as total_return_pct.
    growth_pct: Decimal


class BrokerResponse(BaseModel):
    broker: str
    # Which import routes this broker has a parser for: "transactions" (POST
    # /transactions/import) and/or "cashflow" (POST /cashflows/import).
    imports: list[str]


class UnmappedInstrumentResponse(BaseModel):
    kind: str
    identifier: str
    broker: str


class UnmappedInstrumentAnyBrokerResponse(BaseModel):
    """The admin's cross-user view: an ISIN carries no broker, since one mapping covers them all."""

    kind: str
    identifier: str
    broker: str | None


class TickerMappingSetRequest(BaseModel):
    # Plain strings: pipeline.set_ticker_mapping owns every rule (kind, broker-vs-kind,
    # lengths), so its message reaches the admin rather than a Pydantic one.
    identifier_kind: str
    identifier: str
    broker: str | None = None
    canonical_ticker: str
    updated_by: UUID


class TickerMappingResponse(BaseModel):
    id: int
    identifier_kind: str
    identifier: str
    broker: str | None
    canonical_ticker: str
    updated_by: UUID
    created_at: datetime
    updated_at: datetime


class TickerMappingWriteResponse(BaseModel):
    status: str
    rows_saved: int
    # How the follow-on positions rebuilds went, beside `status` rather than folded into it --
    # see jobs._mapping_write_result.
    accounts_rebuilt: int
    rebuilds_failed: int
    returns_rebuilds_failed: int
    mapping: TickerMappingResponse


class TickerLookupResponse(BaseModel):
    ticker: str
    # "tracked" (see status), "available", "unsupported", "not_found" or "unavailable".
    outcome: str
    status: str | None
    display_name: str | None
    currency: str | None
    exchange: str | None
    asset_type: str | None


class TrackedTickerAddRequest(BaseModel):
    ticker: str
    requested_by: UUID
    # Required, no default: whether the daily limit applies (it doesn't for an admin) is the
    # caller's decision, never one an omitted field makes quietly.
    enforce_limit: bool


class TrackedTickerUpdateRequest(BaseModel):
    enabled: bool | None = None
    retry: bool = False


class TrackedTickerResponse(BaseModel):
    ticker: str
    display_name: str
    asset_type: str
    currency: str | None
    exchange: str | None
    origin: str
    status: str
    enabled: bool
    attempts: int
    last_error: str | None
    requested_by: UUID | None
    requested_at: datetime
    ready_at: datetime | None


class TrackedTickerAddResponse(TrackedTickerResponse):
    created: bool


class FailedPairResponse(BaseModel):
    account_label: str
    ticker: str
    error: str


class BlockedTickerResponse(BaseModel):
    account_label: str
    ticker: str


class AccountWriteResponse(BaseModel):
    status: str
    rows_saved: int
    accounts_created: int


class AccountDeleteResponse(BaseModel):
    status: str
    accounts_deleted: int
    # What went with the account, so a caller can say so without having counted beforehand.
    transactions_deleted: int


class TransactionDeleteResponse(BaseModel):
    status: str
    transactions_deleted: int
    # Reported the same way as TransactionsWriteResponse's: the follow-on rebuilds' own statuses,
    # kept apart from `status`, which describes only the delete.
    positions_rebuild: str
    returns_rebuild: str


class TransactionsWriteResponse(BaseModel):
    status: str
    rows_saved: int
    transactions_saved: int
    # The follow-on positions rebuild's own status (ok / partial / nothing_to_do), or `skipped`
    # / `failed` -- kept apart from `status`, which describes only the write. See
    # jobs._rebuild_after_write.
    positions_rebuild: str
    # The returns rebuild chained after it, reported the same way -- also `skipped` when the
    # positions rebuild failed or data-store isn't configured.
    returns_rebuild: str


class RebuildPositionsResponse(BaseModel):
    status: str
    rows_saved: int
    positions_rebuilt: int
    positions_removed: int
    instruments_blocked: int
    positions_with_anomalies: int
    pairs_failed: int
    blocked_instruments: list[UnmappedInstrumentResponse]
    failed_pairs: list[FailedPairResponse]


class RebuildReturnsResponse(BaseModel):
    status: str
    rows_saved: int
    tickers_rebuilt: int
    tickers_removed: int
    tickers_blocked: int
    pairs_failed: int
    blocked_tickers: list[BlockedTickerResponse]
    failed_pairs: list[FailedPairResponse]
