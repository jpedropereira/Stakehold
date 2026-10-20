"""Pure computation of one (account, ticker) pair's daily return_snapshots rows from its
position history, a price series, and (for a non-EUR position) an FX series -- plus, since
dividends are cash income no BUY/SELL-only replay ever sees, that pair's own DIVIDEND events.

No I/O, no Postgres, no datastore_client -- pipeline.py fetches everything this needs and hands
it in as plain data, the same split positions/calculator.replay uses. See
docs/architecture.md's "returns/" module note: the one thing distinguishing this module from
positions/calculator.py is the external (datastore_client) dependency, deliberately kept out of
the pure function itself.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from portfolio.datastore_client.base import FxPoint, PricePoint
from portfolio.positions.calculator import PositionSnapshot

# Match return_snapshots' own column precision (db/schema.sql): the money columns are
# NUMERIC(20,4), the two pct columns NUMERIC(18,8). Quantized only at emission, same reasoning
# positions/calculator.py's _quantize uses -- running totals stay at full Decimal precision.
_FOUR_PLACES = Decimal("0.0001")
_EIGHT_PLACES = Decimal("0.00000001")
_ZERO = Decimal("0")


@dataclass(frozen=True)
class DividendEvent:
    date: date
    eur_amount: Decimal  # already EUR, frozen at transaction time -- no FX lookup needed


@dataclass(frozen=True)
class ReturnSnapshot:
    """Mirrors return_snapshots' columns, minus calculated_at (stamped at upsert time)."""

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


def _quantize(value: Decimal, quantum: Decimal) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def _pct(numerator: Decimal, cost_basis_eur: Decimal) -> Decimal | None:
    # Undefined, not zero, when cost_basis_eur == 0 (a position closed to a zero cost basis) --
    # docs/schema.md's explicit rule, applying identically to both pct columns.
    if cost_basis_eur == _ZERO:
        return None
    return _quantize(numerator / cost_basis_eur, _EIGHT_PLACES)


def compute_returns(
    account_id: int,
    canonical_ticker: str,
    position_snapshots: Sequence[PositionSnapshot],
    prices: Sequence[PricePoint],
    fx_rates: Mapping[str, Sequence[FxPoint]],
    dividend_events: Sequence[DividendEvent],
) -> list[ReturnSnapshot]:
    """One row per price_date present in `prices`, oldest first -- the trading-day calendar
    comes entirely from data-store's series (docs/schema.md's "stable grid, not a fixed one");
    this function never invents a date of its own. pipeline.rebuild_returns fetches from the
    position's opening date to today.

    A position that is closed now (its last snapshot is zero) ends on the first price date on or
    after its closing date: that row is its zero row, and nothing follows it. When the sale fell
    on a day with no price (a weekend, a holiday, a close data-store hasn't stored yet), the zero
    row lands on the next price date. Without it, the pair's last row would still hold the units
    sold, and every reader that carries a pair's last row forward (the portfolio series, the
    latest-returns read) would keep showing them. Until that next price exists, the rows end on
    the last open day; the next rebuild after it lands writes the zero row.

    position_snapshots, prices, fx_rates' per-currency series, and dividend_events must each be
    ascending by date -- this walks all of them forward in one pass, never re-scanning.

    A price_date before the pair's first position_snapshots row is skipped (there is no known
    quantity/cost_basis for it yet). A price_date whose position is in a non-EUR currency with
    no FX point at or before it yet is also skipped -- it cannot be computed, and is never
    silently zeroed.
    """
    positions = sorted(position_snapshots, key=lambda s: s.as_of_date)
    closed_on = positions[-1].as_of_date if positions and positions[-1].quantity == 0 else None
    price_points = sorted(prices, key=lambda p: p.date)
    dividends = sorted(dividend_events, key=lambda d: d.date)
    fx_series = {
        currency: sorted(points, key=lambda p: p.date) for currency, points in fx_rates.items()
    }

    snapshots: list[ReturnSnapshot] = []
    position_idx = -1
    dividend_idx = -1
    dividends_received_eur = _ZERO
    fx_idx: dict[str, int] = {}

    for price_point in price_points:
        price_date = price_point.date
        if closed_on is not None and snapshots and snapshots[-1].price_date >= closed_on:
            break  # the zero row is written: a closed position's rows end there

        while (
            position_idx + 1 < len(positions)
            and positions[position_idx + 1].as_of_date <= price_date
        ):
            position_idx += 1
        if position_idx == -1:
            continue  # no position yet as of this date -- nothing to compute

        while dividend_idx + 1 < len(dividends) and dividends[dividend_idx + 1].date <= price_date:
            dividend_idx += 1
            dividends_received_eur += dividends[dividend_idx].eur_amount

        position = positions[position_idx]
        market_value_native = position.quantity * price_point.split_adjusted_close

        if position.native_currency == "EUR":
            market_value_eur = market_value_native
        else:
            points = fx_series.get(position.native_currency, [])
            idx = fx_idx.get(position.native_currency, -1)
            while idx + 1 < len(points) and points[idx + 1].date <= price_date:
                idx += 1
            fx_idx[position.native_currency] = idx
            if idx == -1:
                continue  # no FX rate available for this currency yet -- can't be computed
            market_value_eur = market_value_native / points[idx].rate

        cost_basis_eur = position.cost_basis_eur
        unrealized_return_eur = market_value_eur - cost_basis_eur
        total_return_eur = unrealized_return_eur + dividends_received_eur

        snapshots.append(
            ReturnSnapshot(
                account_id=account_id,
                canonical_ticker=canonical_ticker,
                price_date=price_date,
                quantity=position.quantity,
                market_value_native=_quantize(market_value_native, _FOUR_PLACES),
                market_value_eur=_quantize(market_value_eur, _FOUR_PLACES),
                cost_basis_eur=cost_basis_eur,
                unrealized_return_eur=_quantize(unrealized_return_eur, _FOUR_PLACES),
                unrealized_return_pct=_pct(unrealized_return_eur, cost_basis_eur),
                dividends_received_eur=_quantize(dividends_received_eur, _FOUR_PLACES),
                total_return_eur=_quantize(total_return_eur, _FOUR_PLACES),
                total_return_pct=_pct(total_return_eur, cost_basis_eur),
            )
        )

    return snapshots
