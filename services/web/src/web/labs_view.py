"""The mix /labs backtests (LB-6): the user's current holdings, weighted by market value.

Pure, like portfolio_view.py: the router fetches, this decides what is sent. Editing the mix
(LB-7) replaces this default; it doesn't change how a mix is backtested.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from web.portfolio_client.base import Holding
from web.portfolio_view import build_portfolio_holdings

# portfolio's POST /labs/backtest rejects a larger mix with a 422 (LB-4). Mirrored here so a
# portfolio with more holdings than that still gets figures, for its largest ones.
MAX_BACKTEST_ASSETS = 50


@dataclass(frozen=True)
class DefaultMix:
    """`assets` is what is sent, largest weight first; each other tuple names holdings left
    out, and why: no price yet, a market value of zero or less, or over MAX_BACKTEST_ASSETS."""

    assets: tuple[tuple[str, Decimal], ...]
    unpriced: tuple[str, ...]
    unweighted: tuple[str, ...]
    dropped: tuple[str, ...]


def build_default_mix(holdings: Sequence[Holding]) -> DefaultMix:
    """Each priced holding with /portfolio's weight: its share of the priced holdings' market
    value.

    A holding whose weight isn't positive (worth zero, or every priced holding summing to zero)
    is left out: portfolio rejects the whole mix over one weight <= 0. Past the cap, the
    smallest holdings go; the kept weights are still shares of the whole portfolio, and
    portfolio re-normalizes them to 100%. Ties go by ticker, so the same holdings always give
    the same mix.
    """
    rows = build_portfolio_holdings(holdings)
    unpriced = tuple(r.holding.canonical_ticker for r in rows if not r.holding.priced)
    weighted = [
        (r.holding.canonical_ticker, r.weight)
        for r in rows
        if r.holding.priced and r.weight is not None and r.weight > 0
    ]
    unweighted = tuple(
        r.holding.canonical_ticker
        for r in rows
        if r.holding.priced and (r.weight is None or r.weight <= 0)
    )
    weighted.sort(key=lambda asset: (-asset[1], asset[0]))
    return DefaultMix(
        assets=tuple(weighted[:MAX_BACKTEST_ASSETS]),
        unpriced=unpriced,
        unweighted=unweighted,
        dropped=tuple(ticker for ticker, _ in weighted[MAX_BACKTEST_ASSETS:]),
    )
