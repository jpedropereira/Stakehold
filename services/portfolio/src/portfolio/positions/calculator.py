"""Pure replay of one account's transaction log into position snapshots.

No I/O, no Postgres, no datastore_client -- quantity and average cost are pure functions of the
transaction log (docs/architecture.md's "everything except returns/ can be built before
data-store's read API exists"). Only BUY/SELL move a position: every other transaction_type is a
cash event that validate() doesn't even guarantee carries a quantity or price, so DIVIDEND/FEE/
ADJUSTMENT rows are left entirely to returns (task 8).

Cost basis is sourced from native_amount/eur_amount -- the broker's own frozen cash-flow figures,
schema.md's "immutable source of truth" -- not quantity * price. The consequence worth keeping in
mind: average_cost_eur needs no FX arithmetic here (it never divides by fx_rate), and it is a
blend of each purchase's own frozen eur_amount, not derivable from average_cost_native by any
single fx_rate. See docs/schema.md's note on the same instrument held in two currencies.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date
from decimal import ROUND_HALF_UP, Decimal

from portfolio.transactions.base import CanonicalTransaction

# Match position_snapshots' own column precision (db/schema.sql): quantity/average_cost_native/
# average_cost_eur are NUMERIC(*, 6), cost_basis_eur is NUMERIC(*, 4). Running totals are kept at
# full Decimal precision and quantized only here, at emission -- quantizing on every transaction
# would compound rounding error across a multi-year replay.
_SIX_PLACES = Decimal("0.000001")
_FOUR_PLACES = Decimal("0.0001")
_ZERO = Decimal("0")

_POSITION_MOVING_TYPES = ("BUY", "SELL")


@dataclass(frozen=True)
class PositionSnapshot:
    """Mirrors position_snapshots' columns, minus calculated_at (stamped at insert time)."""

    account_id: int
    canonical_ticker: str
    as_of_date: date
    quantity: Decimal
    average_cost_native: Decimal
    native_currency: str
    average_cost_eur: Decimal
    cost_basis_eur: Decimal


def _quantize(value: Decimal, quantum: Decimal) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def replay(
    account_id: int, canonical_ticker: str, transactions: Sequence[CanonicalTransaction]
) -> tuple[list[PositionSnapshot], list[str]]:
    """One row per date a BUY/SELL changed this position, oldest first.

    Returns (snapshots, anomaly_messages) rather than logging directly, so this stays pure and
    cheaply unit-testable -- pipeline.py is where a message becomes a `logger.warning`. An
    anomaly (the same ticker traded in two currencies within this account, or the running
    quantity going negative) never blocks the replay -- docs/schema.md's "warn but compute"
    policy -- it is only ever reported, never silently absorbed.
    """
    relevant = sorted(
        (t for t in transactions if t.transaction_type in _POSITION_MOVING_TYPES),
        key=lambda t: (t.trade_datetime, t.broker_transaction_ref),
    )

    rows_by_date: dict[date, list[CanonicalTransaction]] = {}
    for txn in relevant:
        as_of_date = txn.trade_datetime.astimezone(UTC).date()
        rows_by_date.setdefault(as_of_date, []).append(txn)

    anomalies: list[str] = []
    snapshots: list[PositionSnapshot] = []

    quantity = _ZERO
    cost_native = _ZERO
    cost_eur = _ZERO
    native_currency: str | None = None

    for as_of_date in sorted(rows_by_date):
        for txn in rows_by_date[as_of_date]:
            if native_currency is not None and txn.native_currency != native_currency:
                anomalies.append(
                    f"{canonical_ticker} in account {account_id}: traded in both "
                    f"{native_currency!r} and {txn.native_currency!r} -- "
                    "position_snapshots.native_currency assumes one currency per instrument "
                    "per account (docs/schema.md)"
                )
            native_currency = txn.native_currency

            if txn.transaction_type == "BUY":
                cost_native += abs(txn.native_amount)
                cost_eur += abs(txn.eur_amount)
                quantity += txn.quantity
            else:  # SELL
                previous_quantity = quantity
                quantity += txn.quantity
                if previous_quantity != 0:
                    # Shrink the running cost proportionally so cost == average_cost * quantity
                    # still holds -- this is what makes the method "blended average": a sale
                    # never changes average cost, only the smaller quantity it now applies to.
                    fraction = quantity / previous_quantity
                    cost_native *= fraction
                    cost_eur *= fraction

            if quantity < 0:
                anomalies.append(
                    f"{canonical_ticker} in account {account_id}: quantity went negative "
                    f"({quantity}) as of {as_of_date}"
                )

            if quantity == 0:
                # A closed position resets to a clean basis -- a later BUY starts fresh rather
                # than carrying forward a cost/quantity ratio that's now 0/0.
                cost_native = _ZERO
                cost_eur = _ZERO

        average_cost_native = _ZERO if quantity == 0 else cost_native / quantity
        average_cost_eur = _ZERO if quantity == 0 else cost_eur / quantity
        snapshots.append(
            PositionSnapshot(
                account_id=account_id,
                canonical_ticker=canonical_ticker,
                as_of_date=as_of_date,
                quantity=_quantize(quantity, _SIX_PLACES),
                average_cost_native=_quantize(average_cost_native, _SIX_PLACES),
                native_currency=native_currency,
                average_cost_eur=_quantize(average_cost_eur, _SIX_PLACES),
                cost_basis_eur=_quantize(quantity * average_cost_eur, _FOUR_PLACES),
            )
        )

    return snapshots, anomalies
