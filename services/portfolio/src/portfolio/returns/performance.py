"""Pure computation of time-weighted growth since inception, one figure per priced day, from
return_snapshots rows and the money their BUY/SELL transactions moved -- for one account
(GET /returns/growth) or for every account a user holds at once (GET /returns/portfolio).

Time-weighted means money going in or out never moves the figure by itself -- only what the
holdings did with it does. Each day's return is

    r_d = (MV_d + Div_d + Out_d - MV_{d-1} - In_d) / (MV_{d-1} + In_d)

where MV is the summed market value, Div the dividends that arrived that day, In the money spent
on buys (fees included) and Out the proceeds of sells. Growth on day d is the product of
(1 + r) up to d, minus 1. Buys count at the start of the day and sells at its end: the day a
position sells out has a zero market value, and treating its proceeds as start-of-day money
would leave nothing (or less than nothing) to divide by. A day with nothing invested at its
start and nothing bought holds growth flat.

Rows and flows are keyed on (account_id, canonical_ticker), never on the ticker alone. The same
ticker in two accounts is two series, each forward-filled from its own last row, and each flow
lands on its own account's rows. Keyed on the ticker alone, two accounts' rows would collapse
into one series -- the bug UI-11 documented in web's aggregate_portfolio_returns. A sell in one
account and a buy in another are money out and money in, as they should be for a time-weighted
return. For one account every key shares one account_id, so its growth is what it always was.

compute_series also sums the EUR figures in the same pass, so the portfolio chart gets one series
with the euros and growth together. The forward-fill is done here in Python rather than in SQL:
the pass already exists for growth, flows have to land on each (account, ticker)'s own next row,
which SQL would need a calendar join per pair to express, and one implementation means the
chart's euros and its growth can never disagree about which row a day holds.

Calculated on read, never stored -- see docs/architecture.md's "Growth since inception is
computed on read". No I/O, no Postgres: pipeline.py resolves each BUY/SELL to its ticker and
hands the flows in as plain data, the same split returns/calculator.py uses.
"""

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from portfolio.returns.calculator import ReturnSnapshot

# The precision return_snapshots' own pct columns use (NUMERIC(18,8)), applied only at emission
# so compounding runs at full Decimal precision.
_EIGHT_PLACES = Decimal("0.00000001")
_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True)
class TradeFlow:
    """The money one BUY or SELL moved, resolved to the account and ticker it traded."""

    account_id: int
    canonical_ticker: str
    date: date  # trade_datetime's UTC date, the same date dividends are bucketed by
    eur_amount: (
        Decimal  # transactions.eur_amount as stored: negative for a buy, positive for a sell
    )


@dataclass(frozen=True)
class GrowthPoint:
    price_date: date
    growth_pct: Decimal  # a fraction: 0.1240 is +12.40%


@dataclass(frozen=True)
class PortfolioPoint:
    """One day of every (account, ticker) row summed, forward-filled where a pair has no row of
    its own that day, plus growth since inception up to that day."""

    price_date: date
    market_value_eur: Decimal
    cost_basis_eur: Decimal
    dividends_received_eur: Decimal
    total_return_eur: Decimal
    # sum(total_return_eur) / sum(cost_basis_eur), never an average of the rows' own pcts; None
    # when nothing has a cost.
    total_return_pct: Decimal | None
    growth_pct: Decimal  # a fraction: 0.1240 is +12.40%


@dataclass(frozen=True)
class TickerGrowth:
    canonical_ticker: str
    growth_pct: Decimal  # the ticker's latest growth across every account holding it


_Key = tuple[int, str]  # (account_id, canonical_ticker)


def compute_series(
    snapshots: Sequence[ReturnSnapshot], flows: Sequence[TradeFlow]
) -> list[PortfolioPoint]:
    """One point per date any (account, ticker) has a row for, oldest first.

    A pair with no row on a date holds its last row's value, the same forward-fill web's chart
    uses; before its first row it contributes nothing. A flow lands on its own pair's first row
    dated on or after the trade -- the first day that pair's market value reflects it, so a
    weekend trade or one on a day only another pair was priced still balances. A flow with no
    such row (its pair has none, or the trade is newer than its last) is left out: no market
    value reflects it yet, so counting it would read as a loss.
    """
    by_key: dict[_Key, list[ReturnSnapshot]] = {}
    for row in snapshots:
        by_key.setdefault((row.account_id, row.canonical_ticker), []).append(row)
    series = {key: sorted(rows, key=lambda r: r.price_date) for key, rows in by_key.items()}
    row_dates = {key: [r.price_date for r in rows] for key, rows in series.items()}

    money_in: dict[date, Decimal] = {}
    money_out: dict[date, Decimal] = {}
    for flow in flows:
        dates = row_dates.get((flow.account_id, flow.canonical_ticker))
        if not dates:
            continue
        idx = bisect_left(dates, flow.date)
        if idx == len(dates):
            continue
        lands_on = dates[idx]
        if flow.eur_amount < _ZERO:
            money_in[lands_on] = money_in.get(lands_on, _ZERO) - flow.eur_amount
        else:
            money_out[lands_on] = money_out.get(lands_on, _ZERO) + flow.eur_amount

    pointers = {key: -1 for key in series}
    previous_value = _ZERO
    previous_dividends = _ZERO
    growth_factor = _ONE
    points: list[PortfolioPoint] = []

    for current_date in sorted({row.price_date for row in snapshots}):
        value = _ZERO
        cost = _ZERO
        dividends = _ZERO
        total_return = _ZERO
        for key, rows in series.items():
            idx = pointers[key]
            while idx + 1 < len(rows) and rows[idx + 1].price_date <= current_date:
                idx += 1
            pointers[key] = idx
            if idx == -1:
                continue  # not held yet as of this date -- contributes nothing
            row = rows[idx]
            value += row.market_value_eur
            cost += row.cost_basis_eur
            dividends += row.dividends_received_eur
            total_return += row.total_return_eur

        invested = previous_value + money_in.get(current_date, _ZERO)
        if invested > _ZERO:
            gain = (
                value
                + (dividends - previous_dividends)
                + money_out.get(current_date, _ZERO)
                - invested
            )
            growth_factor *= _ONE + gain / invested

        points.append(
            PortfolioPoint(
                price_date=current_date,
                market_value_eur=value,
                cost_basis_eur=cost,
                dividends_received_eur=dividends,
                total_return_eur=total_return,
                total_return_pct=(None if cost == _ZERO else _round(total_return / cost)),
                growth_pct=_round(growth_factor - _ONE),
            )
        )
        previous_value = value
        previous_dividends = dividends

    return points


def compute_growth(
    snapshots: Sequence[ReturnSnapshot], flows: Sequence[TradeFlow]
) -> list[GrowthPoint]:
    """compute_series' growth alone -- what GET /returns/growth serves for one account."""
    return [
        GrowthPoint(price_date=point.price_date, growth_pct=point.growth_pct)
        for point in compute_series(snapshots, flows)
    ]


def compute_ticker_growth(
    snapshots: Sequence[ReturnSnapshot], flows: Sequence[TradeFlow]
) -> list[TickerGrowth]:
    """Each ticker's latest growth since inception across every account holding it, sorted by
    ticker: compute_series over that ticker's rows and flows alone (still keyed per account), and
    its last point. A ticker sold out and bought back holds flat while nothing is held, as an
    account's growth does. Closed tickers are included; a caller shows the ones it needs."""
    rows_by_ticker: dict[str, list[ReturnSnapshot]] = {}
    for row in snapshots:
        rows_by_ticker.setdefault(row.canonical_ticker, []).append(row)
    flows_by_ticker: dict[str, list[TradeFlow]] = {}
    for flow in flows:
        flows_by_ticker.setdefault(flow.canonical_ticker, []).append(flow)
    return [
        TickerGrowth(
            canonical_ticker=ticker,
            growth_pct=compute_series(rows, flows_by_ticker.get(ticker, []))[-1].growth_pct,
        )
        for ticker, rows in sorted(rows_by_ticker.items())
    ]


def _round(value: Decimal) -> Decimal:
    return value.quantize(_EIGHT_PLACES, rounding=ROUND_HALF_UP)
