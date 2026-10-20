"""Pure computation of the whole portfolio's daily series (GET /returns/portfolio): every
account's return_snapshots rows summed per date.

Rows are keyed on (account_id, canonical_ticker), never on the ticker alone. The same ticker in
two accounts is two series, each forward-filled from its own last row. Keyed on the ticker
alone, two accounts' rows would collapse into one series -- the bug UI-11 documented in web's
aggregate_portfolio_returns.

The forward-fill is done here in Python rather than in SQL, which would need a calendar join per
(account, ticker) pair to express. total_return_pct is recomputed from the sums, never averaged
from the rows' own pcts.

Calculated on read, never stored. No I/O, no Postgres: holdings/flows.py reads the rows and hands
them in as plain data, the same split returns/calculator.py uses.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from portfolio.returns.calculator import ReturnSnapshot

# The precision return_snapshots' own pct columns use (NUMERIC(18,8)).
_EIGHT_PLACES = Decimal("0.00000001")
_ZERO = Decimal("0")


@dataclass(frozen=True)
class PortfolioPoint:
    """One day of every (account, ticker) row summed, forward-filled where a pair has no row of
    its own that day."""

    price_date: date
    market_value_eur: Decimal
    cost_basis_eur: Decimal
    dividends_received_eur: Decimal
    total_return_eur: Decimal
    # sum(total_return_eur) / sum(cost_basis_eur), never an average of the rows' own pcts; None
    # when nothing has a cost.
    total_return_pct: Decimal | None


Key = tuple[int, str]  # (account_id, canonical_ticker)


def forward_filled(
    snapshots: Sequence[ReturnSnapshot],
) -> Iterator[tuple[date, Mapping[Key, ReturnSnapshot]]]:
    """Every date any (account, ticker) has a row for, oldest first, each with every pair's
    latest row on or before it. A pair with no row on a date holds its last row; before its
    first row it is absent. The mapping is rebuilt per date, so it can be kept.

    twr/calculator.py walks the same calendar for its weights (RK-10)."""
    by_key: dict[Key, list[ReturnSnapshot]] = {}
    for row in snapshots:
        by_key.setdefault((row.account_id, row.canonical_ticker), []).append(row)
    series = {key: sorted(rows, key=lambda r: r.price_date) for key, rows in by_key.items()}

    pointers = {key: -1 for key in series}
    for current_date in sorted({row.price_date for row in snapshots}):
        current: dict[Key, ReturnSnapshot] = {}
        for key, rows in series.items():
            idx = pointers[key]
            while idx + 1 < len(rows) and rows[idx + 1].price_date <= current_date:
                idx += 1
            pointers[key] = idx
            if idx != -1:
                current[key] = rows[idx]
        yield current_date, current


def compute_series(snapshots: Sequence[ReturnSnapshot]) -> list[PortfolioPoint]:
    """One point per date any (account, ticker) has a row for, oldest first.

    A pair with no row on a date holds its last row's value, the same forward-fill web's chart
    uses; before its first row it contributes nothing.
    """
    points: list[PortfolioPoint] = []
    for current_date, rows in forward_filled(snapshots):
        value = sum((row.market_value_eur for row in rows.values()), _ZERO)
        cost = sum((row.cost_basis_eur for row in rows.values()), _ZERO)
        dividends = sum((row.dividends_received_eur for row in rows.values()), _ZERO)
        total_return = sum((row.total_return_eur for row in rows.values()), _ZERO)
        points.append(
            PortfolioPoint(
                price_date=current_date,
                market_value_eur=value,
                cost_basis_eur=cost,
                dividends_received_eur=dividends,
                total_return_eur=total_return,
                total_return_pct=(None if cost == _ZERO else _round(total_return / cost)),
            )
        )

    return points


def _round(value: Decimal) -> Decimal:
    return value.quantize(_EIGHT_PLACES, rounding=ROUND_HALF_UP)
