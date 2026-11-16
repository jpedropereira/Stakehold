"""The mix /labs backtests (LB-6): the user's current holdings, weighted by market value. And
the growth chart's data island (LB-8), and the periods the mix's figures leave assets out of
(LB-10).

Pure, like portfolio_view.py: the router fetches, this decides what is sent. Editing the mix
(LB-7) replaces this default; it doesn't change how a mix is backtested.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from web.portfolio_client.base import (
    BACKTEST_PERIODS,
    BacktestBenchmark,
    BacktestGrowth,
    BacktestRow,
    Holding,
)
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


@dataclass(frozen=True)
class LeftOutRun:
    """Consecutive periods, `first` to `last` in BACKTEST_PERIODS order, whose mix figure leaves
    out the same `tickers` for not covering them."""

    first: str
    last: str
    tickers: tuple[str, ...]


def mix_left_out_runs(mix: BacktestRow) -> list[LeftOutRun]:
    """The mix's left-out assets as runs of periods, for one note each under the table.

    A run ends where the set changes, a period leaving nothing out included: the notes then say
    exactly which columns each applies to. A short history is usually left out of every period
    from some length up, so this is most often one run, e.g. 3M to 5Y.
    """
    runs: list[LeftOutRun] = []
    previous: tuple[str, ...] = ()
    for period in BACKTEST_PERIODS:
        tickers = mix.periods[period].left_out
        if tickers and tickers == previous:
            runs[-1] = LeftOutRun(runs[-1].first, period, tickers)
        elif tickers:
            runs.append(LeftOutRun(period, period, tickers))
        previous = tickers
    return runs


def chart_value(value: Decimal | float) -> float:
    """A growth fraction to six significant digits, about 30% shorter in JSON than portfolio's
    eight-place strings. Not fewer: the tooltip shows hundredths of a percent, which six digits
    keep exact up to a 10,000% growth."""
    return float(f"{value:.6g}")


def build_growth_payload(
    growth: BacktestGrowth,
    benchmark: BacktestBenchmark | None = None,
    names: Mapping[str, str] | None = None,
) -> dict:
    """The growth chart's data island (static/labs_chart.js). Assets are a list, so they keep
    the table's order; a line is None when it doesn't cover the period, and a point is None
    before a late asset's Max line starts, which Chart.js draws as no line.

    The benchmark's line (RK-14), when portfolio sent one, is priced like an asset's on the
    same dates. Without one, `reason` says why (its history starts after the period, or it is
    unavailable), for the note beside its disabled checkbox. None with no risk section.

    `names` is the page's {ticker: display_name} map (ticker_names.load_names). Each line carries
    its own `name` (UX-3), None when it has none, for the tooltip's "TICKER — Name": the island
    then holds only the names of lines it draws, and the script looks nothing up."""
    names = names or {}

    def line(values):
        return None if values is None else [None if v is None else chart_value(v) for v in values]

    return {
        "period": growth.period,
        "dates": [d.isoformat() for d in growth.dates],
        "mix": line(growth.mix),
        "assets": [
            {"ticker": ticker, "name": names.get(ticker), "values": line(values)}
            for ticker, values in growth.assets.items()
        ],
        # The page names these under the chart; the script doesn't read them.
        "left_out": list(growth.left_out),
        "benchmark": (
            None
            if benchmark is None
            else {
                "ticker": benchmark.ticker,
                "name": names.get(benchmark.ticker),
                "values": line(benchmark.growth),
                "reason": (
                    None
                    if benchmark.growth is not None
                    else benchmark.growth_reason or "not available"
                ),
            }
        ),
    }
