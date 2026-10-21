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
