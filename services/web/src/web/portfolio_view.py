"""Pure, web-side display logic over portfolio's raw /positions and /returns responses.

Neither function here changes portfolio's own data -- they only reshape what routes.py already
fetched for the /portfolio view: filtering closed positions out of a "current holdings" listing,
and combining N per-ticker daily return series into one portfolio-level series for the chart
(portfolio/docs/architecture.md's "Returns are served per ticker" note explicitly leaves this to
task 12). No I/O, no FastAPI, no httpx -- mirrors how portfolio/returns/calculator.py is a pure
function tested in isolation.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from web.portfolio_client.base import Position, Return

_ZERO = Decimal("0")


def filter_open_positions(positions: Sequence[Position]) -> list[Position]:
    """Drops quantity==0 rows. positions/repository.py's list_latest_snapshots intentionally
    doesn't prune a closed position -- filtering it out of a "current holdings" view is this
    layer's job, not portfolio's. A negative quantity (a replay anomaly
    positions/calculator.py warns about but still computes, not a modeled short position) is
    kept, not hidden -- it's exactly the kind of thing a user should see, not lose.
    """
    return [p for p in positions if p.quantity != _ZERO]


@dataclass(frozen=True)
class PortfolioReturnPoint:
    """One portfolio-level day: every ticker's return_snapshots row summed, or forward-filled
    from its own last known row when it has none on this exact date."""

    price_date: date
    market_value_eur: Decimal
    cost_basis_eur: Decimal
    dividends_received_eur: Decimal
    total_return_eur: Decimal
    total_return_pct: Decimal | None


def aggregate_portfolio_returns(returns: Sequence[Return]) -> list[PortfolioReturnPoint]:
    """Combines every (account, ticker) daily series in `returns` into one portfolio-level daily
    series, ascending by price_date.

    Algorithm: group rows by canonical_ticker and sort each group ascending by price_date --
    sorted defensively here, since (unlike portfolio/returns/calculator.py's own callers) this
    consumes an HTTP response with no documented ordering guarantee. Build the sorted union of
    every price_date across every ticker, then walk each ticker's own series forward in lockstep
    with that union (one pointer per ticker, one pass): on a union date where a ticker has its
    own row, use it and advance that ticker's pointer; on a union date after a ticker's first row
    but where it lacks one of its own, forward-fill its last-seen row (a ticker not priced that
    day holds its last known value, it does not fall to zero); on a union date *before* a
    ticker's first ever row, it contributes nothing to that date at all -- not priced/held yet is
    not the same as priced at zero. Per union date, sum market_value_eur/cost_basis_eur/
    dividends_received_eur/total_return_eur across whichever tickers contributed, and recompute
    total_return_pct as sum(total_return_eur)/sum(cost_basis_eur) (None if that sum is 0) --
    never averaged or summed directly, since per-ticker percentages don't sum to a portfolio
    percentage.
    """
    by_ticker: dict[str, list[Return]] = {}
    for row in returns:
        by_ticker.setdefault(row.canonical_ticker, []).append(row)
    series = {
        ticker: sorted(rows, key=lambda r: r.price_date) for ticker, rows in by_ticker.items()
    }

    union_dates = sorted({row.price_date for row in returns})

    pointers = {ticker: -1 for ticker in series}
    points: list[PortfolioReturnPoint] = []

    for current_date in union_dates:
        market_value_eur = _ZERO
        cost_basis_eur = _ZERO
        dividends_received_eur = _ZERO
        total_return_eur = _ZERO

        for ticker, rows in series.items():
            idx = pointers[ticker]
            while idx + 1 < len(rows) and rows[idx + 1].price_date <= current_date:
                idx += 1
            pointers[ticker] = idx
            if idx == -1:
                continue  # not priced/held yet as of this date -- contributes nothing
            row = rows[idx]
            market_value_eur += row.market_value_eur
            cost_basis_eur += row.cost_basis_eur
            dividends_received_eur += row.dividends_received_eur
            total_return_eur += row.total_return_eur

        total_return_pct = None if cost_basis_eur == _ZERO else total_return_eur / cost_basis_eur

        points.append(
            PortfolioReturnPoint(
                price_date=current_date,
                market_value_eur=market_value_eur,
                cost_basis_eur=cost_basis_eur,
                dividends_received_eur=dividends_received_eur,
                total_return_eur=total_return_eur,
                total_return_pct=total_return_pct,
            )
        )

    return points
