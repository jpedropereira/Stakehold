"""The PortfolioClient Protocol, matching auth_client's own split (base.py Protocol +
http_client.py implementation) -- so routes.py can be tested against a fake with no live
portfolio service, and http_client.py stays the only place that knows portfolio's HTTP contract.

Field names/types mirror portfolio/api/models.py's AccountCreateRequest/TransactionCreateRequest
exactly. Unlike AuthResult, no result here carries a cookie -- portfolio calls carry no browser
session; user_id is sent explicitly as trusted input (see portfolio/docs/architecture.md's
"Conceptual entity model").
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class Account:
    id: int
    account_label: str
    broker: str
    created_at: str


@dataclass(frozen=True)
class Broker:
    """A broker with at least one importer installed in portfolio, and which import kinds it
    has: "transactions" (a trades export) and/or "cashflow" (an account statement)."""

    broker: str
    imports: tuple[str, ...]


@dataclass(frozen=True)
class Position:
    account_id: int
    canonical_ticker: str
    as_of_date: date
    quantity: Decimal
    average_cost_native: Decimal
    native_currency: str
    average_cost_eur: Decimal
    cost_basis_eur: Decimal


@dataclass(frozen=True)
class Return:
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


@dataclass(frozen=True)
class AccountWriteResult:
    status: str
    rows_saved: int
    accounts_created: int


@dataclass(frozen=True)
class TransactionWriteResult:
    status: str
    rows_saved: int
    transactions_saved: int
    # The follow-on rebuilds portfolio runs after the write (ok / partial / nothing_to_do /
    # skipped / failed), reported apart from `status`, which is the write's own. None when an
    # older portfolio response doesn't carry them.
    positions_rebuild: str | None = None
    returns_rebuild: str | None = None


@dataclass(frozen=True)
class TickerMapping:
    id: int
    identifier_kind: str
    identifier: str
    broker: str | None
    canonical_ticker: str
    updated_at: datetime


@dataclass(frozen=True)
class UnmappedInstrument:
    kind: str
    identifier: str
    # None for an ISIN: one ISIN mapping covers every broker, so the admin list doesn't split it.
    broker: str | None


@dataclass(frozen=True)
class TickerMappingWriteResult:
    mapping: TickerMapping
    accounts_rebuilt: int
    rebuilds_failed: int


class PortfolioClient(Protocol):
    async def list_accounts(self, *, user_id: str) -> list[Account]: ...

    async def list_brokers(self) -> list[Broker]: ...

    async def list_tickers(self) -> list[str]: ...

    async def create_account(
        self, *, user_id: str, broker: str, account_label: str
    ) -> AccountWriteResult: ...

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
    ) -> TransactionWriteResult: ...

    async def import_transactions(
        self,
        *,
        user_id: str,
        account_label: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> TransactionWriteResult: ...

    async def import_cashflow(
        self,
        *,
        user_id: str,
        account_label: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> TransactionWriteResult: ...

    async def list_positions(self, *, user_id: str, account_label: str) -> list[Position]: ...

    async def list_returns(self, *, user_id: str, account_label: str) -> list[Return]: ...

    # One row per ticker, its most recent price_date. Includes closed tickers.
    async def list_latest_returns(self, *, user_id: str, account_label: str) -> list[Return]: ...

    async def list_ticker_mappings(self) -> list[TickerMapping]: ...

    # This user's instruments with no ticker mapping, across all their accounts.
    async def list_unmapped_instruments(self, *, user_id: str) -> list[UnmappedInstrument]: ...

    async def list_all_unmapped_instruments(self) -> list[UnmappedInstrument]: ...

    async def set_ticker_mapping(
        self,
        *,
        identifier_kind: str,
        identifier: str,
        broker: str | None,
        canonical_ticker: str,
        updated_by: str,
    ) -> TickerMappingWriteResult: ...

    async def delete_ticker_mapping(
        self, *, mapping_id: int, updated_by: str
    ) -> TickerMappingWriteResult: ...
