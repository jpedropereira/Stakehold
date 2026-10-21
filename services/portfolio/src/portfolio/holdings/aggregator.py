"""Pure aggregation of every account's latest position and return rows into one row per
canonical_ticker, plus the cross-account totals.

No I/O, no Postgres -- holdings/flows.py reads the rows and hands them in, the same split
returns/calculator.py and pipeline.py use. Rows are keyed on (account, ticker) until the final
merge, so the same ticker in two accounts can't collapse into one account's row (the bug UI-11
documented in web's aggregate_portfolio_returns). See docs/modules.md's "holdings/" note.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from portfolio.positions.calculator import PositionSnapshot
from portfolio.returns.calculator import ReturnSnapshot

# The precision of the columns these figures are derived from (db/schema.sql): average costs are
# NUMERIC(18,6), the pct columns NUMERIC(18,8). Sums of NUMERIC(20,4) values need no rounding.
_SIX_PLACES = Decimal("0.000001")
_EIGHT_PLACES = Decimal("0.00000001")
_ZERO = Decimal("0")


@dataclass(frozen=True)
class AccountHoldingInputs:
    """One account's latest rows: positions/repository and returns/repository's
    list_latest_snapshots, one row per ticker each, closed tickers included."""

    account_label: str
    positions: Sequence[PositionSnapshot]
    returns: Sequence[ReturnSnapshot]


@dataclass(frozen=True)
class HoldingAccount:
    """One account's share of a holding. The value figures are None while that account has no
    return row for the ticker yet."""

    account_label: str
    quantity: Decimal
    native_currency: str
    average_cost_native: Decimal
    cost_basis_eur: Decimal
    market_value_native: Decimal | None
    market_value_eur: Decimal | None
    unrealized_return_eur: Decimal | None


@dataclass(frozen=True)
class Holding:
    """One ticker summed across every account with an open position in it.

    The value figures are None unless `priced`: every contributing account has a return row. A
    sum over only some accounts would read like a real total. The native figures are None when
    the accounts recorded the ticker in different currencies (`mixed_currency`), since summing
    across currencies means nothing; the EUR figures are always comparable.
    """

    canonical_ticker: str
    quantity: Decimal
    average_cost_eur: Decimal | None
    cost_basis_eur: Decimal
    native_currency: str | None
    mixed_currency: bool
    average_cost_native: Decimal | None
    market_value_native: Decimal | None
    priced: bool
    price_date: date | None
    market_value_eur: Decimal | None
    unrealized_return_eur: Decimal | None
    unrealized_return_pct: Decimal | None
    dividends_received_eur: Decimal | None
    total_return_eur: Decimal | None
    total_return_pct: Decimal | None
    accounts: tuple[HoldingAccount, ...]


@dataclass(frozen=True)
class HoldingsTotals:
    """The whole portfolio's headline figures, on the home page's rule (web's
    summarize_accounts): every latest return row of every account summed, closed tickers
    included, since a closed position's dividends are still part of the return."""

    price_date: date
    market_value_eur: Decimal
    cost_basis_eur: Decimal
    dividends_received_eur: Decimal
    total_return_eur: Decimal
    total_return_pct: Decimal | None


@dataclass(frozen=True)
class HoldingsResult:
    holdings: list[Holding]
    totals: HoldingsTotals | None


def _quantize(value: Decimal, quantum: Decimal) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def _pct(numerator: Decimal, cost_basis_eur: Decimal) -> Decimal | None:
    # Undefined, not zero, when the cost is 0 -- returns/calculator.py's rule.
    if cost_basis_eur == _ZERO:
        return None
    return _quantize(numerator / cost_basis_eur, _EIGHT_PLACES)


def _per_unit(total: Decimal, quantity: Decimal) -> Decimal | None:
    if quantity == _ZERO:
        return None
    return _quantize(total / quantity, _SIX_PLACES)


def _sum(values) -> Decimal:
    return sum(values, _ZERO)


def _merge(
    ticker: str, contributions: list[tuple[str, PositionSnapshot, ReturnSnapshot | None]]
) -> Holding:
    positions = [position for _, position, _ in contributions]
    returns = [row for _, _, row in contributions if row is not None]
    priced = len(returns) == len(contributions)
    quantity = _sum(p.quantity for p in positions)
    cost_basis_eur = _sum(p.cost_basis_eur for p in positions)

    currencies = {p.native_currency for p in positions}
    mixed_currency = len(currencies) > 1
    native_currency = None if mixed_currency else currencies.pop()
    average_cost_native = (
        None
        if mixed_currency
        else _per_unit(_sum(p.quantity * p.average_cost_native for p in positions), quantity)
    )

    if priced:
        market_value_eur = _sum(r.market_value_eur for r in returns)
        unrealized_return_eur = _sum(r.unrealized_return_eur for r in returns)
        dividends_received_eur = _sum(r.dividends_received_eur for r in returns)
        total_return_eur = _sum(r.total_return_eur for r in returns)
        market_value_native = (
            None if mixed_currency else _sum(r.market_value_native for r in returns)
        )
        unrealized_return_pct = _pct(unrealized_return_eur, cost_basis_eur)
        total_return_pct = _pct(total_return_eur, cost_basis_eur)
    else:
        market_value_eur = unrealized_return_eur = dividends_received_eur = None
        total_return_eur = market_value_native = None
        unrealized_return_pct = total_return_pct = None

    return Holding(
        canonical_ticker=ticker,
        quantity=quantity,
        average_cost_eur=_per_unit(cost_basis_eur, quantity),
        cost_basis_eur=cost_basis_eur,
        native_currency=native_currency,
        mixed_currency=mixed_currency,
        average_cost_native=average_cost_native,
        market_value_native=market_value_native,
        priced=priced,
        price_date=max((r.price_date for r in returns), default=None),
        market_value_eur=market_value_eur,
        unrealized_return_eur=unrealized_return_eur,
        unrealized_return_pct=unrealized_return_pct,
        dividends_received_eur=dividends_received_eur,
        total_return_eur=total_return_eur,
        total_return_pct=total_return_pct,
        accounts=tuple(
            HoldingAccount(
                account_label=label,
                quantity=position.quantity,
                native_currency=position.native_currency,
                average_cost_native=position.average_cost_native,
                cost_basis_eur=position.cost_basis_eur,
                market_value_native=None if row is None else row.market_value_native,
                market_value_eur=None if row is None else row.market_value_eur,
                unrealized_return_eur=None if row is None else row.unrealized_return_eur,
            )
            for label, position, row in contributions
        ),
    )


def _totals(per_account: Sequence[AccountHoldingInputs]) -> HoldingsTotals | None:
    rows = [row for account in per_account for row in account.returns]
    if not rows:
        return None
    cost_basis_eur = _sum(r.cost_basis_eur for r in rows)
    total_return_eur = _sum(r.total_return_eur for r in rows)
    return HoldingsTotals(
        price_date=max(r.price_date for r in rows),
        market_value_eur=_sum(r.market_value_eur for r in rows),
        cost_basis_eur=cost_basis_eur,
        dividends_received_eur=_sum(r.dividends_received_eur for r in rows),
        total_return_eur=total_return_eur,
        total_return_pct=_pct(total_return_eur, cost_basis_eur),
    )


def aggregate_holdings(per_account: Sequence[AccountHoldingInputs]) -> HoldingsResult:
    """One Holding per ticker with an open position (quantity != 0) in at least one account,
    sorted by ticker, each with its accounts in label order; plus the totals.

    Only open positions feed a row: a ticker closed in A and open in B is B's alone, though A's
    closed row still counts in the totals. A negative quantity is kept, the same as web's
    filter_open_positions: it is a replay anomaly the user should see, not lose.
    """
    by_ticker: dict[str, list[tuple[str, PositionSnapshot, ReturnSnapshot | None]]] = {}
    for account in sorted(per_account, key=lambda a: a.account_label):
        latest = {row.canonical_ticker: row for row in account.returns}
        for position in account.positions:
            if position.quantity == _ZERO:
                continue
            by_ticker.setdefault(position.canonical_ticker, []).append(
                (account.account_label, position, latest.get(position.canonical_ticker))
            )

    return HoldingsResult(
        holdings=[_merge(ticker, by_ticker[ticker]) for ticker in sorted(by_ticker)],
        totals=_totals(per_account),
    )
