"""The normalized transaction shape every producer converges on.

Manual entry (pipeline.py's add_manual_transaction) fills this directly; an installed
broker-parser plugin (task 6) fills it from a broker export. No `broker` field: accounts.broker
is the persisted source of truth for which broker an account belongs to (schema.md dropped the
functionally-dependent transactions.broker column at sign-off), so nothing here needs to carry
one that could disagree with it. `account_id` is the resolved BIGINT accounts.id, not a
caller-supplied label -- resolving the label to an id, scoped by user, is accounts/repository.py's
job, done before a CanonicalTransaction is ever built.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from portfolio.errors import InvalidTransaction

TRANSACTION_TYPES = (
    "BUY",
    "SELL",
    "DEPOSIT",
    "WITHDRAWAL",
    "DIVIDEND",
    "FX",
    "FEE",
    "ADJUSTMENT",
)
# TICKER: the identifier already is a data-store ticker (picked from data-store's own list at
# manual entry), so it needs no mapping -- see docs/schema.md.
IDENTIFIER_KINDS = ("ISIN", "SYMBOL", "TICKER")
# "manual" is a user-typed rate -- none of the other three sources fit one, and leaving
# fx_rate_source NULL for a manual rate would collide with NULL's other meaning ("EUR, no rate
# needed"). See docs/schema.md.
FX_RATE_SOURCE_MANUAL = "manual"
FX_RATE_SOURCES = ("broker_reported", "companion_row", "datastore_series", FX_RATE_SOURCE_MANUAL)

_EUR_QUANTUM = Decimal("0.0001")


@dataclass(frozen=True)
class CanonicalTransaction:
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


def to_eur(native_amount: Decimal, fx_rate: Decimal | None) -> Decimal:
    """The EUR-equivalent of a native-currency amount, at eur_amount's own precision.

    fx_rate is native units per 1 EUR (e.g. USD/EUR = 1.0850 means EUR 1 buys USD 1.085), so
    converting *to* EUR divides. None means the amount is already EUR-denominated. Rounded here,
    explicitly and testably, rather than left to whatever the driver/server would do with a
    NUMERIC(20,4) column.

    Guards its own inputs rather than trusting validate() to have run first: pipeline.py calls
    this to build a CanonicalTransaction's eur_amount *before* validate() runs on it, so an
    unguarded division would let a zero/negative/non-finite fx_rate reach the interpreter as a
    raw decimal.DivisionByZero/InvalidOperation instead of the intended InvalidTransaction --
    and a non-finite native_amount would quantize to NaN and get persisted silently.
    """
    if not native_amount.is_finite():
        raise InvalidTransaction(f"native_amount must be a finite number: {native_amount!r}")
    if fx_rate is None:
        amount = native_amount
    else:
        if not fx_rate.is_finite() or fx_rate <= 0:
            raise InvalidTransaction(f"fx_rate must be a positive, finite number: {fx_rate!r}")
        amount = native_amount / fx_rate
    return amount.quantize(_EUR_QUANTUM, rounding=ROUND_HALF_UP)


def validate(txn: CanonicalTransaction) -> None:
    """Raise InvalidTransaction on the first broken rule. Pure -- no I/O, no Postgres."""
    if txn.transaction_type not in TRANSACTION_TYPES:
        raise InvalidTransaction(f"unrecognized transaction_type: {txn.transaction_type!r}")
    if not txn.broker_transaction_ref:
        raise InvalidTransaction("broker_transaction_ref must not be empty")
    if txn.trade_datetime.tzinfo is None:
        raise InvalidTransaction("trade_datetime must be timezone-aware")

    for field_name in ("native_amount", "quantity", "price", "fx_rate", "fees_native"):
        value = getattr(txn, field_name)
        if value is not None and not value.is_finite():
            raise InvalidTransaction(f"{field_name} must be a finite number: {value!r}")

    has_identifier = txn.instrument_identifier is not None
    has_kind = txn.instrument_identifier_kind is not None
    if has_identifier != has_kind:
        raise InvalidTransaction(
            "instrument_identifier and instrument_identifier_kind must be both set or both None"
        )
    if has_kind and txn.instrument_identifier_kind not in IDENTIFIER_KINDS:
        raise InvalidTransaction(
            f"unrecognized instrument_identifier_kind: {txn.instrument_identifier_kind!r}"
        )

    if txn.transaction_type in ("BUY", "SELL"):
        if not has_identifier:
            raise InvalidTransaction(f"{txn.transaction_type} requires an instrument_identifier")
        if txn.quantity is None:
            raise InvalidTransaction(f"{txn.transaction_type} requires a quantity")
        if txn.price is None:
            raise InvalidTransaction(f"{txn.transaction_type} requires a price")
        if txn.transaction_type == "BUY" and txn.quantity <= 0:
            raise InvalidTransaction("BUY requires a positive quantity")
        if txn.transaction_type == "SELL" and txn.quantity >= 0:
            raise InvalidTransaction("SELL requires a negative quantity")

    # isupper() alone isn't enough: it only requires the absence of lowercase letters, so a
    # digit-containing string like "1AB" passes it too (isupper() ignores uncased characters).
    if (
        len(txn.native_currency) != 3
        or not txn.native_currency.isalpha()
        or not txn.native_currency.isupper()
    ):
        raise InvalidTransaction(
            f"native_currency must be a 3-letter ISO 4217 code: {txn.native_currency!r}"
        )

    has_rate = txn.fx_rate is not None
    has_source = txn.fx_rate_source is not None
    if has_rate != has_source:
        raise InvalidTransaction("fx_rate and fx_rate_source must be both set or both None")
    # schema.md: fx_rate is "absent when native_currency = 'EUR'" -- read the other direction,
    # every non-EUR row must carry one. A producer (manual entry, or a future broker parser)
    # that skipped resolving a rate is the bug this rejects, not a state to persist and patch
    # up later.
    if txn.native_currency == "EUR":
        if has_rate:
            raise InvalidTransaction("a EUR transaction must not carry an fx_rate")
    else:
        if not has_rate:
            raise InvalidTransaction("fx_rate is required when native_currency is not EUR")
        if txn.fx_rate_source not in FX_RATE_SOURCES:
            raise InvalidTransaction(f"unrecognized fx_rate_source: {txn.fx_rate_source!r}")
        if txn.fx_rate <= 0:
            raise InvalidTransaction("fx_rate must be positive")
