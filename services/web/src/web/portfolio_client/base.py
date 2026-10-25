"""The PortfolioClient Protocol, matching auth_client's own split (base.py Protocol +
http_client.py implementation) -- so routes.py can be tested against a fake with no live
portfolio service, and http_client.py stays the only place that knows portfolio's HTTP contract.

Field names/types mirror portfolio/api/models.py's AccountCreateRequest/TransactionCreateRequest
exactly. Unlike AuthResult, no result here carries a cookie -- portfolio calls carry no browser
session; user_id is sent explicitly as trusted input (see portfolio/docs/architecture.md's
"Conceptual entity model").
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class Account:
    id: int
    account_label: str
    broker: str
    created_at: str


@dataclass(frozen=True)
class Broker:
    """A broker with at least one importer installed in portfolio, and which import kinds it
    has: "transactions" (a trades export) and/or "cashflow" (an account statement)."""

    broker: str
    imports: tuple[str, ...]


@dataclass(frozen=True)
class Position:
    account_id: int
    canonical_ticker: str
    as_of_date: date
    quantity: Decimal
    average_cost_native: Decimal
    native_currency: str
    average_cost_eur: Decimal
    cost_basis_eur: Decimal


@dataclass(frozen=True)
class Return:
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


@dataclass(frozen=True)
class HoldingAccount:
    """One account's share of a Holding, mirroring portfolio's HoldingAccountResponse. The value
    fields are None while that account has no return row for the ticker yet."""

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
    """One ticker summed across every account with an open position in it, mirroring
    portfolio's HoldingResponse. The value fields are None unless `priced` (every account has a
    return row, so no partial sum is ever shown); the native ones are None when
    `mixed_currency`. The pcts are fractions."""

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
    """Every account's latest return rows summed, closed tickers included, mirroring portfolio's
    HoldingsTotalsResponse: the home page's rule, so the two pages' totals agree."""

    price_date: date
    market_value_eur: Decimal
    cost_basis_eur: Decimal
    dividends_received_eur: Decimal
    total_return_eur: Decimal
    total_return_pct: Decimal | None


@dataclass(frozen=True)
class Holdings:
    """portfolio's HoldingsResponse. `totals` is None while no account has a return row."""

    holdings: tuple[Holding, ...]
    totals: HoldingsTotals | None


@dataclass(frozen=True)
class PortfolioSeriesPoint:
    """One day of the whole portfolio across every account, mirroring portfolio's
    PortfolioReturnPointResponse. total_return_pct is a fraction."""

    price_date: date
    market_value_eur: Decimal
    cost_basis_eur: Decimal
    dividends_received_eur: Decimal
    total_return_eur: Decimal
    total_return_pct: Decimal | None


@dataclass(frozen=True)
class PortfolioReturns:
    """portfolio's PortfolioReturnsResponse (PF-5). Empty while no account has a return row."""

    series: tuple[PortfolioSeriesPoint, ...]


@dataclass(frozen=True)
class Transaction:
    """One row of an account's transaction log, mirroring portfolio's TransactionResponse.
    Amounts are signed cash flows (a BUY's is negative), quantity is signed (a SELL's is
    negative), and every instrument field is None on a cash-only row."""

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
    # The follow-on rebuilds portfolio runs after the write (ok / partial / nothing_to_do /
    # skipped / failed), reported apart from `status`, which is the write's own. None when an
    # older portfolio response doesn't carry them.
    positions_rebuild: str | None = None
    returns_rebuild: str | None = None


@dataclass(frozen=True)
class AccountDeleteResult:
    status: str
    accounts_deleted: int
    # Every transaction recorded in the account went with it. Nothing is rebuilt afterwards:
    # the account's holdings and returns are deleted too.
    transactions_deleted: int


@dataclass(frozen=True)
class TransactionDeleteResult:
    status: str
    transactions_deleted: int
    # The rebuilds portfolio runs after the delete, reported as on TransactionWriteResult.
    positions_rebuild: str | None = None
    returns_rebuild: str | None = None


@dataclass(frozen=True)
class TickerMapping:
    id: int
    identifier_kind: str
    identifier: str
    broker: str | None
    canonical_ticker: str
    updated_at: datetime


@dataclass(frozen=True)
class UnmappedInstrument:
    kind: str
    identifier: str
    # None for an ISIN: one ISIN mapping covers every broker, so the admin list doesn't split it.
    broker: str | None


@dataclass(frozen=True)
class TickerMappingWriteResult:
    mapping: TickerMapping
    accounts_rebuilt: int
    rebuilds_failed: int


@dataclass(frozen=True)
class TickerLookup:
    """What adding a ticker would do -- see data-store's GET /tickers/{ticker}/lookup."""

    ticker: str
    # "tracked" (see status), "available", "unsupported", "not_found" or "unavailable".
    outcome: str
    status: str | None
    display_name: str | None
    currency: str | None
    exchange: str | None
    asset_type: str | None


@dataclass(frozen=True)
class TrackedTicker:
    """A ticker data-store prices directly, and how far its loading has got: status is
    "pending", "backfilling", "ready" or "failed"."""

    ticker: str
    display_name: str
    asset_type: str
    currency: str | None
    exchange: str | None
    # "config" (the platform's default list) or "request" (added by a user or admin).
    origin: str
    status: str
    enabled: bool
    attempts: int
    last_error: str | None
    requested_by: str | None
    requested_at: datetime
    ready_at: datetime | None


@dataclass(frozen=True)
class TrackedTickerAddResult:
    ticker: TrackedTicker
    # False when it was already tracked: nothing was added, and nothing counted to the limit.
    created: bool


# portfolio's labs Period values, in the table's column order. web can't import portfolio's enum.
BACKTEST_PERIODS = ("1M", "3M", "6M", "1Y", "2Y", "5Y", "Max")


@dataclass(frozen=True)
class BacktestPeriodReturn:
    """One period's figure, mirroring portfolio's PeriodReturnResponse. return_pct is a fraction,
    and None exactly when not `covered` ("Not enough history"), never 0."""

    start: date
    end: date
    covered: bool
    return_pct: Decimal | None
    gaps: int  # NULL returns bridged as flat days inside (start, end]
    # The assets the mix's figure leaves out for not covering this period, in the mix's order
    # (LB-9). Only ever non-empty on the mix's row; unavailable assets are in statuses instead.
    left_out: tuple[str, ...] = ()


@dataclass(frozen=True)
class BacktestRow:
    """The mix (ticker None, weight 1) or one included asset, with the weight actually used
    after normalizing. `periods` has every key in BACKTEST_PERIODS, in that order."""

    ticker: str | None
    weight: Decimal
    periods: dict[str, BacktestPeriodReturn]


@dataclass(frozen=True)
class BacktestGrowth:
    """Cumulative growth over `period`, as fractions: 0 on its first date. Every line has one
    value per date. An asset's line is None when it doesn't cover the period, and the mix's only
    when no asset does. For Max, an asset that starts later has None points until its first
    day (LB-9)."""

    period: str
    dates: tuple[date, ...]
    mix: tuple[Decimal, ...] | None
    assets: dict[str, tuple[Decimal | None, ...] | None]
    left_out: tuple[str, ...] = ()  # as BacktestPeriodReturn.left_out, for `period`


@dataclass(frozen=True)
class BacktestAssetStatus:
    ticker: str
    # "included", "not_tracked", "no_history", "no_fx_rate" or "failed".
    status: str
    reason: str | None  # None when included


# portfolio's standard risk windows (StandardWindow, spec §4.5), in a risk table's column order.
RISK_WINDOWS = ("1Y", "3Y", "5Y", "10Y", "Max")
# The backtest's risk table (RK-8) and the ex-post summary (RK-11) both use them.
BACKTEST_RISK_WINDOWS = RISK_WINDOWS

# The figures each risk window carries, as portfolio names them.
RISK_FIGURE_KEYS = (
    "cumulative_return",
    "cagr",
    "volatility",
    "downside_deviation",
    "sharpe",
    "sortino",
    "calmar",
    "max_drawdown",
    "var_95",
    "cvar_95",
    "var_99",
    "cvar_99",
)

# portfolio's default benchmark (RK-13, spec §5.6), sent when the page names none.
DEFAULT_BENCHMARK = "VWCE.DE"

# The figures each window measures against the benchmark (RK-13, spec §5.6), all weekly, as
# portfolio names them in its RelativeRiskResponse.
RELATIVE_FIGURE_KEYS = ("beta", "correlation", "tracking_error", "information_ratio")


@dataclass(frozen=True)
class RiskFigure:
    """One risk figure (RK-8), mirroring portfolio's RiskFigureResponse. A statistical estimate,
    so a float, not a Decimal. A fraction, like the backtest's returns; VaR and CVaR are positive
    losses. `value` is None exactly when the figure is greyed, and `reason` then says why (R9)."""

    value: float | None
    window_start: date  # the figure's own window: a weekly one is the whole weeks ending in it
    window_end: date
    frequency: str  # "daily" or "weekly"
    q: float  # periods per year
    n_obs: int
    n_gaps: int
    flags: tuple[str, ...]  # sorted
    reason: str | None
    standard_error: float | None  # a Sharpe's 1 s.e.; None on every other figure


@dataclass(frozen=True)
class BacktestWindowRisk:
    """One standard window of one row. Not covered: `reason` says why and every figure is None.
    `start` is None only for Max when the assets' histories don't overlap. `figures` has every
    key in RISK_FIGURE_KEYS."""

    start: date | None
    end: date
    covered: bool
    reason: str | None
    figures: dict[str, RiskFigure | None]
    peak_date: date | None
    trough_date: date | None
    recovery_date: date | None  # None when not recovered, or the drawdown is greyed
    max_days_under_water: int | None
    # Against the benchmark (RK-13): every RELATIVE_FIGURE_KEYS key, in that order. None exactly
    # when the window isn't covered, like the other figures.
    relative: dict[str, RiskFigure] | None


@dataclass(frozen=True)
class BacktestRowRisk:
    """The mix (ticker None) or one included asset. `windows` has every key in
    BACKTEST_RISK_WINDOWS, in that order."""

    ticker: str | None
    windows: dict[str, BacktestWindowRisk]


@dataclass(frozen=True)
class RiskFreeStatus:
    available: bool
    reason: str | None  # why not, when it isn't: downside deviation, Sharpe, Sortino greyed
    first_date: date | None


@dataclass(frozen=True)
class BenchmarkStatus:
    """What the relative figures measure against (RK-13). Not `available`: `reason` says why
    ("not tracked", "couldn't reach data-store", ...) and every relative figure is greyed."""

    ticker: str
    available: bool
    reason: str | None
    first_date: date | None  # its first day, which a window it doesn't cover names


@dataclass(frozen=True)
class BacktestBenchmark(BenchmarkStatus):
    """The backtest's benchmark, with its growth line: one value per BacktestGrowth.dates entry,
    from 0, priced as an asset's line is. None, with `growth_reason`, when it is unavailable or
    starts after the growth period."""

    growth: tuple[Decimal, ...] | None
    growth_reason: str | None


@dataclass(frozen=True)
class DrawdownSeries:
    """The mix's drawdown over the growth period: one value per date (the growth series' own),
    each 0 or below, None where a member's return was NULL."""

    period: str
    dates: tuple[date, ...]
    values: tuple[float | None, ...]


@dataclass(frozen=True)
class BacktestRisk:
    """portfolio's BacktestRiskResponse (RK-8). Every row is measured on one common window:
    `common_start` is the youngest asset's first day, and `limited_by` names it (R10)."""

    label: str  # "buy & hold (drifting)" (R3)
    end_date: date
    common_start: date | None  # None when the assets' histories don't overlap
    limited_by: tuple[str, ...]
    risk_free: RiskFreeStatus
    benchmark: BacktestBenchmark
    mix: BacktestRowRisk
    assets: tuple[BacktestRowRisk, ...]  # included assets, in request order
    drawdown: DrawdownSeries | None


@dataclass(frozen=True)
class Backtest:
    """portfolio's BacktestResponse (LB-4). With no asset included, end_date, mix, growth and
    risk are None and assets is empty; statuses still lists every requested asset, in order."""

    end_date: date | None
    mix: BacktestRow | None
    assets: tuple[BacktestRow, ...]
    growth: BacktestGrowth | None
    statuses: tuple[BacktestAssetStatus, ...]
    risk: BacktestRisk | None = None


@dataclass(frozen=True)
class ExPostWindow:
    """One standard window, ending at ExPostRisk.end_date. Not `covered`: the history starts
    after the window does, and every figure is greyed. `figures` has every RISK_FIGURE_KEYS key, in
    that order. The max drawdown's dates are None when it's greyed; a None recovery_date
    otherwise means not recovered by the window's end."""

    label: str
    start: date
    end: date
    covered: bool
    figures: dict[str, RiskFigure]
    peak_date: date | None
    trough_date: date | None
    recovery_date: date | None
    max_days_under_water: int | None
    # Against the benchmark (RK-13): every RELATIVE_FIGURE_KEYS key, in that order. Never None:
    # an uncovered window greys them like its other figures.
    relative: dict[str, RiskFigure]


@dataclass(frozen=True)
class ExcludedHolding:
    """A held ticker the TWR left out; the rest were renormalised to 100%."""

    ticker: str
    status: str  # not_tracked, no_history, no_fx_rate or failed
    reason: str


@dataclass(frozen=True)
class RollingRisk:
    """Rolling 1Y volatility and Sharpe, stepped monthly (RK-24, spec §4.5): one figure per
    month end, in date order, from the first full year, the two of the same length and on the
    same windows. A window below its minimum is a gap: a greyed figure with its reason, kept in
    place, so a chart breaks its line there. The last point is the 1Y figure. Both empty: the
    history is under a year."""

    window: str  # "1Y"
    step: str  # "monthly"
    volatility: tuple[RiskFigure, ...]
    sharpe: tuple[RiskFigure, ...]


@dataclass(frozen=True)
class RollingCorrelation:
    """A pair's rolling 52-week correlation (RK-24, spec §7.1), stepped weekly, always EUR. `b`
    is "portfolio" for the constant-weight mix. `points` is empty exactly when `reason` says
    why (a side left out, an unavailable benchmark); a greyed point keeps its place."""

    a: str
    b: str
    window: str  # "52W"
    step: str  # "weekly"
    reason: str | None
    points: tuple[RiskFigure, ...]


@dataclass(frozen=True)
class ExPostRisk:
    """portfolio's ExPostRiskResponse (RK-11), measured on the TWR of invested positions. With
    nothing to measure, the dates are None and `windows` is empty; otherwise it holds every
    RISK_WINDOWS entry, in that order. Not `rf_available`: downside deviation, Sharpe and Sortino
    are greyed."""

    label: str
    base_date: date | None
    end_date: date | None
    rf_available: bool
    benchmark: BenchmarkStatus  # not fetched when there's nothing to measure: not available
    windows: tuple[ExPostWindow, ...]
    excluded: tuple[ExcludedHolding, ...]
    rolling: RollingRisk | None = None  # on the TWR (RK-24); None exactly when there are no windows


@dataclass(frozen=True)
class ExAnteWindow:
    """The requested standard window (RK-17), ending on the holdings' latest common date. The
    measured one starts where every holding has weekly returns (R12): later than requested when
    a holding starts later, which `limited_by` names. `start` is None when the holdings'
    histories don't overlap."""

    label: str
    requested_start: date
    start: date | None
    end: date
    limited_by: tuple[str, ...]


@dataclass(frozen=True)
class ExAnteHolding:
    """One holding's row of the decomposition (spec §6.3). The figures are None exactly when the
    diversification ratio is greyed (under 104 weeks); the weight, normalised over the included
    holdings, is always there. Fractions: the PCRs sum to 1, negative for a hedge."""

    ticker: str
    weight: float
    volatility: float | None
    mcr: float | None
    cr: float | None
    pcr: float | None
    corr_with_portfolio: float | None


@dataclass(frozen=True)
class SeriesRisk:
    """One series' twelve figures over one window: here, ex-ante's constant-weight mix.
    `figures` has every RISK_FIGURE_KEYS key, in that order, none None (greyed with a reason)."""

    figures: dict[str, RiskFigure]
    peak_date: date | None
    trough_date: date | None
    recovery_date: date | None
    max_days_under_water: int | None


@dataclass(frozen=True)
class CorrelationCell:
    a: str
    b: str
    figure: RiskFigure  # over the weeks this pair shares inside the window (R13)


@dataclass(frozen=True)
class CorrelationMatrix:
    """Pairwise, for display only (R13). `cells` is the upper triangle, diagonal included, row
    by row in `keys` order: the included holdings in request order, then the benchmark."""

    keys: tuple[str, ...]
    benchmark_key: str | None  # the benchmark's row, when it has one
    cells: tuple[CorrelationCell, ...]


@dataclass(frozen=True)
class FxDecompositionRow:
    """One non-EUR holding's FX decomposition (RK-26, spec §6.4): the weekly volatility of its
    local-currency returns, of its currency against the EUR, their correlation, and its EUR
    volatility. All four are on the decomposition's weeks, so `eur_volatility` is its
    `ExAnteHolding.volatility` whenever both are shown. None is ever None: a figure is greyed
    with a reason instead."""

    ticker: str
    currency: str  # e.g. "USD"
    local_volatility: RiskFigure
    fx_volatility: RiskFigure
    correlation: RiskFigure  # corr(r_local, r_fx)
    eur_volatility: RiskFigure


@dataclass(frozen=True)
class ExAnteRisk:
    """portfolio's ExAnteRiskResponse (RK-17): a mix held at constant weights through history.
    Every section but `statuses` and `benchmark` is None exactly when no asset was included.
    Statuses add "excluded" to the backtest's."""

    label: str  # "ex-ante · constant weights"
    currency: str  # the matrix's returns: "eur" or "local"; σ_p is always EUR
    rf_available: bool
    window: ExAnteWindow | None
    benchmark: BenchmarkStatus
    statuses: tuple[BacktestAssetStatus, ...]  # every requested asset, in request order
    sigma_p: RiskFigure | None  # greyed under 52 weeks
    diversification_ratio: RiskFigure | None  # greyed under 104 weeks
    effective_n: float | None
    holdings: tuple[ExAnteHolding, ...] | None
    constant_weight: SeriesRisk | None  # its weekly volatility is σ_p
    correlation: CorrelationMatrix | None
    # Each included non-EUR holding, in request order; () when every one is EUR (RK-26).
    fx_decomposition: tuple[FxDecompositionRow, ...] | None
    # RK-24: over the mix's whole history, whatever `window` is; the pair only when one was sent.
    rolling: RollingRisk | None = None
    rolling_correlation: RollingCorrelation | None = None


# The what-if's rows (RK-19, spec §7.2), as portfolio names and orders them: the risk deltas
# first, ΔSharpe (with its s.e.) after them.
WHAT_IF_ROW_KEYS = (
    "sigma_p",
    "diversification_ratio",
    "candidate_pcr",
    "candidate_correlation",
    "sharpe",
    "max_drawdown",
)


@dataclass(frozen=True)
class WhatIfCandidate:
    """The candidate (RK-19). `status` is a holding's ("included", "no_fx_rate", ...).
    `weight` is after funding: the replaced holding's own weight when the request exceeded it
    (`capped`). Both are None when there's nothing to compare. `survivorship` is R14: it's
    priced only through a fund's holdings, so its history comes from today's index members."""

    ticker: str
    status: str
    reason: str | None
    requested_weight: float
    weight: float | None
    capped: bool | None
    survivorship: bool


@dataclass(frozen=True)
class WhatIfFunding:
    rule: str  # "pro_rata" or "replace"
    ticker: str | None  # the replaced holding
    reason: str | None  # why it can't fund the candidate: it can't be measured


@dataclass(frozen=True)
class WhatIfWindow(ExAnteWindow):
    """Before and after share one listwise window (R12). `shortened`: the candidate's history
    made it start later than the holdings alone would (the `_without_candidate` dates)."""

    shortened: bool
    start_without_candidate: date | None
    end_without_candidate: date | None


@dataclass(frozen=True)
class WhatIfRow:
    """One row of the before/after table. `before` is None on the candidate's own rows, and
    `delta` (after − before) when either side is greyed or there's no before. `delta_se` is
    ΔSharpe's 1 s.e. (Memmel), None on every other row."""

    before: RiskFigure | None
    after: RiskFigure
    delta: float | None
    delta_se: float | None


@dataclass(frozen=True)
class WhatIfThreshold:
    """A small amount of the candidate raises the historical Sharpe if SR_c > ρ(c, p) × SR_p
    (spec §7.2). Not `applicable` under replace funding; applicable but greyed (figures None,
    `reason` says why) without overlap, a risk-free rate or 104 weeks."""

    applicable: bool
    reason: str | None
    candidate_sharpe: RiskFigure | None
    correlation: float | None
    portfolio_sharpe: float | None
    threshold: float | None  # ρ(c, p) × SR_p
    improves: bool | None


@dataclass(frozen=True)
class WhatIf:
    """portfolio's WhatIfResponse (RK-19): today's mix before and after adding a candidate.
    `window`, `rows` and `threshold` are None exactly when there's nothing to compare: see
    `candidate`, `funding.reason` or `statuses`. The before/after weights aren't parsed: the
    page shows only the candidate's own."""

    label: str  # "ex-ante · constant weights"
    rf_available: bool
    statuses: tuple[BacktestAssetStatus, ...]  # every requested holding, in request order
    candidate: WhatIfCandidate
    funding: WhatIfFunding
    window: WhatIfWindow | None
    rows: dict[str, WhatIfRow] | None  # every WHAT_IF_ROW_KEYS key, in that order
    threshold: WhatIfThreshold | None


# The ranking's rows per candidate (RK-29): the what-if's, without max drawdown, in portfolio's
# order. Each is a WhatIfRow; only "sharpe" has a delta_se.
RANKING_ROW_KEYS = (
    "sigma_p",
    "diversification_ratio",
    "candidate_pcr",
    "candidate_correlation",
    "sharpe",
)


@dataclass(frozen=True)
class RankingWindow:
    """The holdings' own window, which every ranked candidate is measured on. `start` is None
    when the holdings' histories don't overlap."""

    label: str
    requested_start: date
    start: date | None
    end: date


@dataclass(frozen=True)
class RankedCandidate:
    """One tracked ticker's what-if (RK-19) at the ranking's weight, funded pro-rata."""

    ticker: str
    name: str  # its tracked display name
    history_start: date
    n_weeks: int  # weekly returns over its whole history
    rows: dict[str, WhatIfRow]  # every RANKING_ROW_KEYS key, in that order


@dataclass(frozen=True)
class UnrankedCandidate:
    """A tracked ticker that wasn't ranked: "short_history" (it doesn't cover the holdings'
    window), or the fetch's status ("no_fx_rate", "failed", ...). The history is None when it
    couldn't be fetched."""

    ticker: str
    name: str
    status: str
    reason: str
    history_start: date | None
    n_weeks: int | None


@dataclass(frozen=True)
class CandidateRanking:
    """portfolio's RankingResponse (RK-29): the tracked tickers outside the mix, each scored as
    a what-if candidate at one weight. `ranked` is in rank order (Δσ_p ascending or Δ
    diversification ratio descending); never by Sharpe. `window`, `ranked` and `greyed` are
    None exactly when `nothing` says why there's no ranking."""

    label: str  # "ex-ante · constant weights"
    rf_available: bool
    statuses: tuple[BacktestAssetStatus, ...]  # every requested holding, in request order
    nothing: str | None
    universe: int  # tracked candidates outside the mix, before the cap
    cap: int
    over_cap: tuple[str, ...]  # past the cap: not scored
    weight: float
    rank_by: str  # "sigma_p" or "diversification_ratio"
    window: RankingWindow | None
    ranked: tuple[RankedCandidate, ...] | None
    greyed: tuple[RankedCandidate, ...] | None  # a rank key greyed; by ticker
    unranked: tuple[UnrankedCandidate, ...]  # by ticker


# The look-through's dimensions and coverage shares (RK-22), as portfolio names them. Every
# response has each, so the section can lay out a table per dimension in a fixed place.
LOOK_THROUGH_DIMENSIONS = ("currency", "trading_currency", "sector", "country", "asset_class")
LOOK_THROUGH_COVERAGE = (
    "look_through",
    "direct_matched",
    "direct_unmatched",
    "commodity",
    "unmapped",
)


@dataclass(frozen=True)
class LookThroughAsset:
    """One requested asset, in request order. `kind` is look_through, direct, commodity or
    unmapped, and None, like `weight`, when it was left out (status not_tracked or failed)."""

    ticker: str
    status: str
    reason: str | None
    kind: str | None
    weight: Decimal | None  # normalised over the included assets
    matched_in: str | None  # a direct stock: the fund whose line describes it
    note: str | None  # an unmapped asset: why it isn't looked through


@dataclass(frozen=True)
class LookThroughSnapshot:
    ticker: str
    fund_key: str
    as_of_date: date  # data-store's scrape date, not the fund's own
    n_lines: int
    raw_weight_sum: Decimal  # as stored, in percent; the look-through rescales it to 100


@dataclass(frozen=True)
class LookThroughBucket:
    label: str
    weight: Decimal
    kind: str  # known, unknown, unmapped or other; only known ones are in the concentration


@dataclass(frozen=True)
class LookThroughConcentration:
    """Over the known share rescaled to 1 (spec §8, amended by RK-22). `herfindahl` and
    `effective_n` are None, with a reason, when nothing is known. `top_shares` (the 1, 5 and
    10 heaviest known buckets) are shares of the whole mix."""

    top_shares: dict[int, Decimal]
    known_share: Decimal
    herfindahl: Decimal | None
    effective_n: Decimal | None
    reason: str | None


@dataclass(frozen=True)
class LookThroughDimension:
    label: str  # what to call it: currency's is R16's "listing currency of holdings"
    buckets: tuple[LookThroughBucket, ...]  # heaviest first
    concentration: LookThroughConcentration


@dataclass(frozen=True)
class LookThroughName:
    issuer: str
    location: str | None
    weight: Decimal
    sources: tuple[str, ...]  # the held tickers it comes through


@dataclass(frozen=True)
class LookThroughNames:
    top: tuple[LookThroughName, ...]  # the 25 heaviest
    n_names: int
    concentration: LookThroughConcentration  # over every name, not only the top


@dataclass(frozen=True)
class LookThroughExposures:
    snapshots: tuple[LookThroughSnapshot, ...]
    coverage: dict[str, Decimal]  # LOOK_THROUGH_COVERAGE's keys, summing to 1
    dimensions: dict[str, LookThroughDimension]  # LOOK_THROUGH_DIMENSIONS' keys, in order
    names: LookThroughNames


@dataclass(frozen=True)
class LookThrough:
    """portfolio's LookThroughResponse (RK-22): where a mix is concentrated, by currency,
    sector, country, asset class and single name, through the funds' holdings. Weights are
    fractions of the mix. `exposures` is None exactly when no asset was included."""

    assets: tuple[LookThroughAsset, ...]
    exposures: LookThroughExposures | None


class PortfolioClient(Protocol):
    async def list_accounts(self, *, user_id: str) -> list[Account]: ...

    async def list_brokers(self) -> list[Broker]: ...

    async def list_tickers(self) -> list[str]: ...

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
        native_amount: Decimal | None,
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

    async def import_cashflow(
        self,
        *,
        user_id: str,
        account_label: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> TransactionWriteResult: ...

    # Deletes the account and everything recorded in it. Raises AccountNotFound when the label
    # doesn't resolve for this user.
    async def delete_account(self, *, user_id: str, account_label: str) -> AccountDeleteResult: ...

    # Raises TransactionNotFound when the account or the reference doesn't resolve for this user.
    async def delete_transaction(
        self, *, user_id: str, account_label: str, broker_transaction_ref: str
    ) -> TransactionDeleteResult: ...

    # Oldest first, as portfolio returns it.
    async def list_transactions(self, *, user_id: str, account_label: str) -> list[Transaction]: ...

    async def list_positions(self, *, user_id: str, account_label: str) -> list[Position]: ...

    async def list_returns(self, *, user_id: str, account_label: str) -> list[Return]: ...

    # One row per ticker, its most recent price_date. Includes closed tickers.
    async def list_latest_returns(self, *, user_id: str, account_label: str) -> list[Return]: ...

    # Every account's open positions summed per ticker, by ticker, plus totals on the home
    # page's rule. Empty (no holdings, totals None) for a user with no accounts.
    async def list_holdings(self, *, user_id: str) -> Holdings: ...

    # The whole portfolio's daily series, across every account. The full history: the chart's
    # ranges crop it on the client. Empty for a user with no accounts.
    async def list_portfolio_returns(self, *, user_id: str) -> PortfolioReturns: ...

    # A Labs backtest of (ticker, weight) pairs; weights are normalized by portfolio. Read-only
    # despite the POST. An asset portfolio can't backtest is a status in the result, not an
    # error; InvalidPortfolioRequest means the mix itself was rejected. An unavailable
    # `benchmark` isn't an error either: the result's risk.benchmark says why.
    async def backtest(
        self, *, assets: list[tuple[str, Decimal]], period: str, benchmark: str
    ) -> Backtest: ...

    # This user's ex-post risk figures over the standard windows (RK-11), and against
    # `benchmark` (RK-13). Read-only.
    async def ex_post_risk(self, *, user_id: str, benchmark: str) -> ExPostRisk: ...

    # The ex-ante risk of (ticker, weight) pairs held at constant weights (RK-17), over one
    # standard window, with `exclude` left out of the mix. Read-only despite the POST; the
    # matrix is in EUR. InvalidPortfolioRequest means the mix itself was rejected.
    # `rolling_pair` (RK-24) asks for the rolling correlation of two tickers from the mix or the
    # benchmark, or of one against the portfolio (None); a pair portfolio rejects is a 422 too.
    async def ex_ante_risk(
        self,
        *,
        assets: list[tuple[str, Decimal]],
        window: str,
        benchmark: str,
        exclude: list[str],
        rolling_pair: tuple[str, str | None] | None = None,
    ) -> ExAnteRisk: ...

    # The look-through exposures of (ticker, weight) pairs (RK-22): by currency, sector,
    # country, asset class and single name. Read-only despite the POST. An asset portfolio
    # can't place is a status in the result; InvalidPortfolioRequest means the mix was rejected.
    async def look_through(self, *, assets: list[tuple[str, Decimal]]) -> LookThrough: ...

    # The mix before and after adding `candidate` (ticker, weight as a fraction of the mix after
    # it) (RK-19), funded by `funding` ("pro_rata" or "replace:<ticker>"), over one standard
    # window with `exclude` left out. Read-only despite the POST. A candidate that can't be
    # measured is in the result; InvalidPortfolioRequest means the request was rejected.
    async def what_if(
        self,
        *,
        assets: list[tuple[str, Decimal]],
        candidate: tuple[str, Decimal],
        funding: str,
        window: str,
        exclude: list[str],
    ) -> WhatIf: ...

    # Every tracked ticker outside the mix, scored as a pro-rata what-if candidate at `weight` (a
    # fraction of the mix after it) (RK-29), over one standard window (3Y or more) with
    # `exclude` left out, ranked by `rank_by` ("sigma_p" or "diversification_ratio"). Read-only
    # despite the POST. InvalidPortfolioRequest means the request was rejected.
    async def rank_candidates(
        self,
        *,
        assets: list[tuple[str, Decimal]],
        weight: Decimal,
        window: str,
        exclude: list[str],
        rank_by: str,
    ) -> CandidateRanking: ...

    async def list_ticker_mappings(self) -> list[TickerMapping]: ...

    # This user's instruments with no ticker mapping, across all their accounts.
    async def list_unmapped_instruments(self, *, user_id: str) -> list[UnmappedInstrument]: ...

    async def list_all_unmapped_instruments(self) -> list[UnmappedInstrument]: ...

    async def set_ticker_mapping(
        self,
        *,
        identifier_kind: str,
        identifier: str,
        broker: str | None,
        canonical_ticker: str,
        updated_by: str,
    ) -> TickerMappingWriteResult: ...

    async def delete_ticker_mapping(
        self, *, mapping_id: int, updated_by: str
    ) -> TickerMappingWriteResult: ...

    async def lookup_ticker(self, ticker: str) -> TickerLookup: ...

    async def add_tracked_ticker(
        self, *, ticker: str, requested_by: str, enforce_limit: bool
    ) -> TrackedTickerAddResult: ...

    async def list_tracked_tickers(
        self,
        *,
        requested_by: str | None = None,
        statuses: list[str] | None = None,
        tickers: list[str] | None = None,
    ) -> list[TrackedTicker]: ...

    async def update_tracked_ticker(
        self, *, ticker: str, enabled: bool | None = None, retry: bool = False
    ) -> TrackedTicker: ...
