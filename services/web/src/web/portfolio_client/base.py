"""The PortfolioClient Protocol, matching auth_client's own split (base.py Protocol +
http_client.py implementation) -- so routes.py can be tested against a fake with no live
portfolio service, and http_client.py stays the only place that knows portfolio's HTTP contract.

Field names/types mirror portfolio/api/models.py's AccountCreateRequest/TransactionCreateRequest
exactly. Unlike AuthResult, no result here carries a cookie -- portfolio calls carry no browser
session; user_id is sent explicitly as trusted input (see portfolio/docs/architecture.md's
"Conceptual entity model").
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class Account:
    id: int
    account_label: str
    broker: str
    created_at: str


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


class PortfolioClient(Protocol):
    async def list_accounts(self, *, user_id: str) -> list[Account]: ...

    async def list_brokers(self) -> list[str]: ...

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
        native_amount: Decimal,
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
