"""Request/response models for the portfolio HTTP API.

Kept separate from routes.py, matching data-store's own split -- the response contract, what a
future web.PortfolioClient will consume, is visible in one place. Numeric fields stay `Decimal`,
never `float`: Pydantic v2 serializes `Decimal` to a JSON string in JSON mode, preserving a
NUMERIC column's exact precision the way a float round-trip would not (data-store's own
models.py docstring). Request models mirror cli.py's argparse coercers -- a UUID, a Decimal, a
tz-aware datetime -- so a malformed request 422s the same way a malformed CLI flag would exit 1.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from portfolio.benchmark.base import DEFAULT_BENCHMARK
from portfolio.labs.calculator import Period
from portfolio.risk.windows import StandardWindow
from portfolio.transactions.base import TRANSACTION_TYPES
from pydantic import BaseModel, Field, PlainSerializer, field_validator, model_validator


class AccountCreateRequest(BaseModel):
    user_id: UUID
    broker: str
    account_label: str


class RebuildRequest(BaseModel):
    user_id: UUID
    account_label: str | None = None


class AccountResponse(BaseModel):
    id: int
    account_label: str
    broker: str
    created_at: datetime


class TransactionCreateRequest(BaseModel):
    user_id: UUID
    account_label: str
    broker_transaction_ref: str
    transaction_type: str
    trade_datetime: datetime
    native_currency: str
    # Optional for a BUY/SELL, which derives it from price * quantity + fees -- see
    # pipeline.add_manual_transaction. Required (by the pipeline) for every other type.
    native_amount: Decimal | None = None
    instrument_identifier: str | None = None
    instrument_identifier_kind: str | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    fx_rate: Decimal | None = None
    fees_native: Decimal | None = None

    @field_validator("transaction_type")
    @classmethod
    def _check_transaction_type(cls, value: str) -> str:
        if value not in TRANSACTION_TYPES:
            raise ValueError(f"transaction_type must be one of {TRANSACTION_TYPES}")
        return value

    @field_validator("trade_datetime")
    @classmethod
    def _require_utc_offset(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("trade_datetime must include a UTC offset")
        return value


class TransactionResponse(BaseModel):
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


class PositionResponse(BaseModel):
    account_id: int
    canonical_ticker: str
    as_of_date: date
    quantity: Decimal
    average_cost_native: Decimal
    native_currency: str
    average_cost_eur: Decimal
    cost_basis_eur: Decimal


class ReturnResponse(BaseModel):
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


class PortfolioReturnPointResponse(BaseModel):
    """Every account's (account, ticker) rows summed for one day. The pcts are fractions, as in
    ReturnResponse; total_return_pct is null when nothing has a cost."""

    price_date: date
    market_value_eur: Decimal
    cost_basis_eur: Decimal
    dividends_received_eur: Decimal
    total_return_eur: Decimal
    total_return_pct: Decimal | None


class PortfolioReturnsResponse(BaseModel):
    # Cropped to start/end.
    series: list[PortfolioReturnPointResponse]


class TwrPointResponse(BaseModel):
    """One day of the portfolio's TWR of invested positions (RK-10). twr_return is a fraction,
    null on a gap; twr_growth is 1 at base_date and flat across a gap."""

    price_date: date
    twr_return: Decimal | None
    twr_growth: Decimal


class ExcludedHoldingResponse(BaseModel):
    ticker: str
    # not_tracked, no_history, no_fx_rate or failed.
    status: str
    reason: str


class PortfolioTwrResponse(BaseModel):
    base_date: date | None  # null when there's no series
    points: list[TwrPointResponse]
    n_gaps: int
    n_excluded: int
    # Held tickers left out of the weights, by ticker; the rest were renormalised to 100%.
    excluded: list[ExcludedHoldingResponse]


class HoldingAccountResponse(BaseModel):
    """One account's share of a holding. The value fields are null while that account has no
    return row for the ticker yet."""

    account_label: str
    quantity: Decimal
    native_currency: str
    average_cost_native: Decimal
    cost_basis_eur: Decimal
    market_value_native: Decimal | None
    market_value_eur: Decimal | None
    unrealized_return_eur: Decimal | None


class HoldingResponse(BaseModel):
    """One ticker summed across every account with an open position in it. The value fields are
    null unless `priced` (every account has a return row); the native ones are null when
    `mixed_currency`. The pcts are fractions, as in ReturnResponse."""

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
    accounts: list[HoldingAccountResponse]


class HoldingsTotalsResponse(BaseModel):
    """Every account's latest return rows summed, closed tickers included -- the home page's
    rule, so the two pages' totals agree."""

    price_date: date
    market_value_eur: Decimal
    cost_basis_eur: Decimal
    dividends_received_eur: Decimal
    total_return_eur: Decimal
    total_return_pct: Decimal | None


class HoldingsResponse(BaseModel):
    holdings: list[HoldingResponse]
    # Null when no account has a return row yet.
    totals: HoldingsTotalsResponse | None


class BrokerResponse(BaseModel):
    broker: str
    # Which import routes this broker has a parser for: "transactions" (POST
    # /transactions/import) and/or "cashflow" (POST /cashflows/import).
    imports: list[str]


class UnmappedInstrumentResponse(BaseModel):
    kind: str
    identifier: str
    broker: str


class UnmappedInstrumentAnyBrokerResponse(BaseModel):
    """The admin's cross-user view: an ISIN carries no broker, since one mapping covers them all."""

    kind: str
    identifier: str
    broker: str | None


class TickerMappingSetRequest(BaseModel):
    # Plain strings: pipeline.set_ticker_mapping owns every rule (kind, broker-vs-kind,
    # lengths), so its message reaches the admin rather than a Pydantic one.
    identifier_kind: str
    identifier: str
    broker: str | None = None
    canonical_ticker: str
    updated_by: UUID


class TickerMappingResponse(BaseModel):
    id: int
    identifier_kind: str
    identifier: str
    broker: str | None
    canonical_ticker: str
    updated_by: UUID
    created_at: datetime
    updated_at: datetime


class TickerMappingWriteResponse(BaseModel):
    status: str
    rows_saved: int
    # How the follow-on positions rebuilds went, beside `status` rather than folded into it --
    # see jobs._mapping_write_result.
    accounts_rebuilt: int
    rebuilds_failed: int
    returns_rebuilds_failed: int
    mapping: TickerMappingResponse


class TickerLookupResponse(BaseModel):
    ticker: str
    # "tracked" (see status), "available", "unsupported", "not_found" or "unavailable".
    outcome: str
    status: str | None
    display_name: str | None
    currency: str | None
    exchange: str | None
    asset_type: str | None


class TrackedTickerAddRequest(BaseModel):
    ticker: str
    requested_by: UUID
    # Required, no default: whether the daily limit applies (it doesn't for an admin) is the
    # caller's decision, never one an omitted field makes quietly.
    enforce_limit: bool


class TrackedTickerUpdateRequest(BaseModel):
    enabled: bool | None = None
    retry: bool = False


class TrackedTickerResponse(BaseModel):
    ticker: str
    display_name: str
    asset_type: str
    currency: str | None
    exchange: str | None
    origin: str
    status: str
    enabled: bool
    attempts: int
    last_error: str | None
    requested_by: UUID | None
    requested_at: datetime
    ready_at: datetime | None


class TrackedTickerAddResponse(TrackedTickerResponse):
    created: bool


class FailedPairResponse(BaseModel):
    account_label: str
    ticker: str
    error: str


class BlockedTickerResponse(BaseModel):
    account_label: str
    ticker: str


class AccountWriteResponse(BaseModel):
    status: str
    rows_saved: int
    accounts_created: int


class AccountDeleteResponse(BaseModel):
    status: str
    accounts_deleted: int
    # What went with the account, so a caller can say so without having counted beforehand.
    transactions_deleted: int


class TransactionDeleteResponse(BaseModel):
    status: str
    transactions_deleted: int
    # Reported the same way as TransactionsWriteResponse's: the follow-on rebuilds' own statuses,
    # kept apart from `status`, which describes only the delete.
    positions_rebuild: str
    returns_rebuild: str


class TransactionsWriteResponse(BaseModel):
    status: str
    rows_saved: int
    transactions_saved: int
    # The follow-on positions rebuild's own status (ok / partial / nothing_to_do), or `skipped`
    # / `failed` -- kept apart from `status`, which describes only the write. See
    # jobs._rebuild_after_write.
    positions_rebuild: str
    # The returns rebuild chained after it, reported the same way -- also `skipped` when the
    # positions rebuild failed or data-store isn't configured.
    returns_rebuild: str


class RebuildPositionsResponse(BaseModel):
    status: str
    rows_saved: int
    positions_rebuilt: int
    positions_removed: int
    instruments_blocked: int
    positions_with_anomalies: int
    pairs_failed: int
    blocked_instruments: list[UnmappedInstrumentResponse]
    failed_pairs: list[FailedPairResponse]


class RebuildReturnsResponse(BaseModel):
    status: str
    rows_saved: int
    tickers_rebuilt: int
    tickers_removed: int
    tickers_blocked: int
    pairs_failed: int
    blocked_tickers: list[BlockedTickerResponse]
    failed_pairs: list[FailedPairResponse]


# --- labs (LB-4) ---

# Above any realistic personal portfolio, since web's default mix is the user's whole holdings,
# while bounding one request's fan-out (about two data-store calls per asset, plus one per
# currency) and its growth payload (one point per trading day per asset for Max).
MAX_BACKTEST_ASSETS = 50
# A benchmark is a ticker, so it takes data-store's 20-character limit too (RK-13).
BenchmarkTicker = Annotated[str, Field(min_length=1, max_length=20)]

# Fixed-point, never scientific notation: a quantized zero is Decimal("0E-8"), and str() would
# write 0.00000012 as "1.2E-7". Every figure in a backtest is a fraction (0.10 is +10%).
BacktestDecimal = Annotated[
    Decimal, PlainSerializer(lambda value: format(value, "f"), return_type=str, when_used="json")
]


class BacktestAssetRequest(BaseModel):
    # 20 is data-store's own limit on the ticker in /tickers/{ticker} and /prices/{ticker}.
    ticker: str = Field(min_length=1, max_length=20)
    # Any positive number: the weights are normalized over the assets that can be backtested.
    weight: Decimal = Field(gt=0, allow_inf_nan=False)


class BacktestRequest(BaseModel):
    assets: list[BacktestAssetRequest] = Field(min_length=1, max_length=MAX_BACKTEST_ASSETS)
    # The period the growth series is for; the table always has every period.
    period: Period
    # What the risk table's relative figures and the benchmark line measure against (RK-13).
    benchmark: BenchmarkTicker = DEFAULT_BENCHMARK

    @model_validator(mode="after")
    def _no_duplicate_tickers(self):
        _check_unique([asset.ticker for asset in self.assets])
        return self


def _check_unique(tickers: list[str]) -> None:
    duplicates = sorted({t for t in tickers if tickers.count(t) > 1})
    if duplicates:
        raise ValueError(f"duplicate tickers in the mix: {', '.join(duplicates)}")


class PeriodReturnResponse(BaseModel):
    start: date
    end: date
    covered: bool
    return_pct: BacktestDecimal | None  # null exactly when not covered
    gaps: int  # NULL returns bridged as flat days inside (start, end]
    # The mix's only: included assets that don't cover the period, so the mix left them out
    # and re-weighted the rest. Always empty on an asset's row.
    left_out: list[str] = []


class BacktestRowResponse(BaseModel):
    ticker: str | None  # null for the mix
    weight: BacktestDecimal  # the weight actually used, normalized; 1 for the mix
    periods: dict[Period, PeriodReturnResponse]


class GrowthSeriesResponse(BaseModel):
    """Cumulative growth over `period`: 0 on its start date, then one point per trading day of
    any asset. An asset's line is null when it doesn't cover the period, and the mix's only when
    no asset does. For Max, an asset that starts later has null points until its first day."""

    period: Period
    dates: list[date]
    mix: list[BacktestDecimal] | None
    assets: dict[str, list[BacktestDecimal | None] | None]
    left_out: list[str] = []  # as PeriodReturnResponse.left_out, for `period`


class AssetStatusResponse(BaseModel):
    ticker: str
    # included, not_tracked, no_history, no_fx_rate or failed; ex-ante also has excluded.
    status: str
    reason: str | None  # null when included


# --- labs risk (RK-8) ---
# Risk figures are statistical estimates, not ledger amounts, so they are JSON numbers rather
# than BacktestDecimal strings. Fractions, like the backtest's: 0.10 is 10%.


class RiskFigureResponse(BaseModel):
    value: float | None  # null exactly when greyed, and `reason` says why (R9)
    window_start: date  # the figure's own window: a weekly one is the whole weeks ending in it
    window_end: date
    frequency: str  # daily or weekly
    q: float  # periods per year
    n_obs: int
    n_gaps: int
    flags: list[str]  # sorted
    reason: str | None
    standard_error: float | None  # a Sharpe's 1 s.e.; null on every other figure


class RelativeRiskResponse(BaseModel):
    """Against the benchmark, on weekly returns (RK-13, spec §5.6). Each figure is greyed with a
    reason when the benchmark doesn't cover the window ("benchmark history too short (from
    <date>)") or is unavailable ("benchmark unavailable: not tracked")."""

    beta: RiskFigureResponse
    correlation: RiskFigureResponse
    tracking_error: RiskFigureResponse
    information_ratio: RiskFigureResponse


class WindowRiskResponse(BaseModel):
    """One standard window of one row. Not covered: `reason` says why and every figure is null.
    `start` is null only for Max when the assets' histories don't overlap."""

    start: date | None
    end: date
    covered: bool
    reason: str | None
    cumulative_return: RiskFigureResponse | None
    cagr: RiskFigureResponse | None
    volatility: RiskFigureResponse | None
    downside_deviation: RiskFigureResponse | None
    sharpe: RiskFigureResponse | None
    sortino: RiskFigureResponse | None
    calmar: RiskFigureResponse | None
    max_drawdown: RiskFigureResponse | None
    peak_date: date | None
    trough_date: date | None
    recovery_date: date | None  # null when not recovered, or the drawdown is greyed
    max_days_under_water: int | None
    var_95: RiskFigureResponse | None
    cvar_95: RiskFigureResponse | None
    var_99: RiskFigureResponse | None
    cvar_99: RiskFigureResponse | None
    relative: RelativeRiskResponse | None  # null exactly when the window isn't covered


class RowRiskResponse(BaseModel):
    ticker: str | None  # null for the mix
    windows: dict[StandardWindow, WindowRiskResponse]


class RiskFreeStatusResponse(BaseModel):
    available: bool
    reason: str | None  # why not, when it isn't: downside deviation, Sharpe, Sortino greyed
    first_date: date | None


class BenchmarkStatusResponse(BaseModel):
    ticker: str
    available: bool
    reason: str | None  # why not, when it isn't: "not tracked", "couldn't reach data-store", ...
    first_date: date | None  # its first day, which a greyed window names


class BacktestBenchmarkResponse(BenchmarkStatusResponse):
    # Its cumulative growth on each `growth.dates` entry, from 0, priced as an asset's line is.
    # Null, with growth_reason, when it is unavailable or starts after the growth period.
    growth: list[BacktestDecimal] | None
    growth_reason: str | None


class DrawdownSeriesResponse(BaseModel):
    """The mix's drawdown over the growth period, one value per `growth.dates` entry, null
    where a member's return was NULL."""

    period: Period
    dates: list[date]
    values: list[float | None]


class BacktestRiskResponse(BaseModel):
    label: str  # "buy & hold (drifting)" (R3)
    end_date: date
    common_start: date | None  # null when the assets' histories don't overlap
    limited_by: list[str]
    risk_free: RiskFreeStatusResponse
    benchmark: BacktestBenchmarkResponse
    mix: RowRiskResponse
    assets: list[RowRiskResponse]  # included assets, in request order
    drawdown: DrawdownSeriesResponse | None


class BacktestResponse(BaseModel):
    end_date: date | None  # null when no asset could be included
    mix: BacktestRowResponse | None
    assets: list[BacktestRowResponse]  # included assets, in request order
    growth: GrowthSeriesResponse | None
    statuses: list[AssetStatusResponse]  # every requested asset, in request order
    risk: BacktestRiskResponse | None = None  # null exactly when no asset could be included


# --- ex-post risk (RK-11) ---
# Same RiskFigureResponse as Labs' risk table above.


class SeriesRiskResponse(BaseModel):
    """One series' §5.1-5.5 figures over one window: an ex-post window's, or ex-ante's
    constant-weight mix."""

    cumulative_return: RiskFigureResponse
    cagr: RiskFigureResponse
    volatility: RiskFigureResponse
    downside_deviation: RiskFigureResponse
    sharpe: RiskFigureResponse
    sortino: RiskFigureResponse
    max_drawdown: RiskFigureResponse
    # The max drawdown's dates, null when it's greyed; recovery_date null also means "not
    # recovered" by the window's end.
    peak_date: date | None
    trough_date: date | None
    recovery_date: date | None
    max_days_under_water: int | None
    calmar: RiskFigureResponse
    var_95: RiskFigureResponse
    cvar_95: RiskFigureResponse
    var_99: RiskFigureResponse
    cvar_99: RiskFigureResponse


class ExPostWindowResponse(SeriesRiskResponse):
    """One standard window (1Y, 3Y, 5Y, 10Y, Max), all ending at end_date. covered is false when
    the history starts after the window does: every figure is then greyed."""

    label: str
    start: date
    end: date
    covered: bool
    # Against the benchmark (RK-13): greyed like the rest when the window isn't covered.
    relative: RelativeRiskResponse


class ExPostRiskResponse(BaseModel):
    """Ex-post risk (RK-11), measured on the TWR of invested positions."""

    label: str  # "ex-post · invested assets"
    base_date: date | None  # null, with no windows, when there's nothing to measure
    end_date: date | None
    # false: downside deviation, Sharpe and Sortino are greyed, "no risk-free rate data".
    rf_available: bool
    # Not fetched when there's nothing to measure: available false, "not fetched: ...".
    benchmark: BenchmarkStatusResponse
    windows: list[ExPostWindowResponse]
    n_excluded: int
    # Held tickers left out of the TWR, by ticker; the rest were renormalised to 100%.
    excluded: list[ExcludedHoldingResponse]


# --- ex-ante risk (RK-17) ---
# Same RiskFigureResponse as Labs' and ex-post's. Values are fractions (0.14 is 14%).

# The correlation matrix's returns: EUR (the default, a EUR investor's risk) or each ticker's
# own currency (spec §7.1's toggle). σ_p and the decomposition are always EUR.
CorrelationCurrency = Literal["eur", "local"]


class ExAnteRiskRequest(BaseModel):
    # web's default mix is the user's holdings with their weights (labs_view.build_default_mix);
    # weights are normalised over the holdings that can be measured.
    assets: list[BacktestAssetRequest] = Field(min_length=1, max_length=MAX_BACKTEST_ASSETS)
    window: StandardWindow = StandardWindow.Y3  # spec §7.1's default
    benchmark: BenchmarkTicker = DEFAULT_BENCHMARK
    currency: CorrelationCurrency = "eur"
    # Holdings to leave out, typically the one that limits the window (R12); the rest are
    # renormalised.
    exclude: list[str] = Field(default_factory=list, max_length=MAX_BACKTEST_ASSETS)

    @model_validator(mode="after")
    def _a_valid_mix(self):
        _check_exclude([asset.ticker for asset in self.assets], self.exclude)
        return self


def _check_exclude(tickers: list[str], exclude: list[str]) -> None:
    _check_unique(tickers)
    unknown = sorted(set(exclude) - set(tickers))
    if unknown:
        raise ValueError(f"exclude names tickers not in the mix: {', '.join(unknown)}")
    if set(tickers) <= set(exclude):
        raise ValueError("exclude leaves nothing in the mix")


class ExAnteWindowResponse(BaseModel):
    """The requested standard window ends on the holdings' latest common date. The measured one
    starts where every holding has weekly returns (R12): later than requested when a holding
    starts later (`limited_by`), or up to six days earlier, on the Friday its first week starts.
    `start` is null when the holdings' histories don't overlap."""

    label: StandardWindow
    requested_start: date
    start: date | None
    end: date
    limited_by: list[str]


class ExAnteHoldingResponse(BaseModel):
    """One holding's row of the decomposition (spec §6.3). Every figure is null exactly when
    `diversification_ratio` is greyed (under 104 weeks); the weight is always there."""

    ticker: str
    weight: float  # normalised over the included holdings
    volatility: float | None
    mcr: float | None
    cr: float | None  # the CRs sum to σ_p
    pcr: float | None  # the PCRs sum to 1, negative for a hedge
    corr_with_portfolio: float | None


class CorrelationCellResponse(BaseModel):
    a: str
    b: str
    figure: RiskFigureResponse  # over the weeks this pair shares inside the window


class CorrelationMatrixResponse(BaseModel):
    """Pairwise, for display only (R13): each cell over its own overlap inside the requested
    window. `cells` is the upper triangle, diagonal included, row by row in `keys` order."""

    keys: list[str]  # the included holdings in request order, then the benchmark
    benchmark_key: str | None  # the benchmark's key, when it has a row
    cells: list[CorrelationCellResponse]


class ExAnteRiskResponse(BaseModel):
    """Ex-ante risk of a mix held at constant weights (RK-17, spec §6.2-6.3, §7.1). Every section
    but `statuses` and `benchmark` is null exactly when no asset could be included."""

    label: str  # "ex-ante · constant weights"
    currency: CorrelationCurrency
    rf_available: bool
    window: ExAnteWindowResponse | None
    benchmark: BenchmarkStatusResponse
    statuses: list[AssetStatusResponse]  # every requested asset, in request order
    sigma_p: RiskFigureResponse | None  # greyed under 52 weeks
    diversification_ratio: RiskFigureResponse | None  # greyed under 104 weeks
    effective_n: float | None  # 1 / Σ w², from the weights alone
    holdings: list[ExAnteHoldingResponse] | None
    # "Today's mix" through history over the measured window; its volatility is σ_p.
    constant_weight: SeriesRiskResponse | None
    correlation: CorrelationMatrixResponse | None


# --- what-if (RK-19) ---
# Same RiskFigureResponse as ex-ante's. Values are fractions (0.14 is 14%).

# Pro-rata (spec §7.2's default) or replacing one holding; "from cash" waits for cash tracking.
_FUNDING_PATTERN = r"^(pro_rata|replace:.{1,20})$"


class WhatIfCandidateRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    # A fraction of the whole mix after the change (0.05 is 5%), not a weight to normalise.
    weight: Decimal = Field(gt=0, lt=1, allow_inf_nan=False)


class WhatIfRequest(BaseModel):
    # The mix as for ex-ante: web's default is the user's holdings with their weights.
    assets: list[BacktestAssetRequest] = Field(min_length=1, max_length=MAX_BACKTEST_ASSETS)
    candidate: WhatIfCandidateRequest
    funding: str = Field(default="pro_rata", pattern=_FUNDING_PATTERN)
    window: StandardWindow = StandardWindow.Y3
    exclude: list[str] = Field(default_factory=list, max_length=MAX_BACKTEST_ASSETS)

    @property
    def replaced(self) -> str | None:
        """The holding `replace:<ticker>` names; None under pro-rata."""
        return self.funding.removeprefix("replace:") if self.funding != "pro_rata" else None

    @model_validator(mode="after")
    def _a_valid_what_if(self):
        tickers = [asset.ticker for asset in self.assets]
        _check_exclude(tickers, self.exclude)
        if self.candidate.ticker in tickers:
            raise ValueError(f"the candidate {self.candidate.ticker} is already in the mix")
        if self.replaced is not None:
            if self.replaced not in tickers:
                raise ValueError(f"replace names a ticker not in the mix: {self.replaced}")
            if self.replaced in self.exclude:
                raise ValueError(f"replace names an excluded holding: {self.replaced}")
        return self


class WhatIfCandidateResponse(BaseModel):
    ticker: str
    # included, not_tracked, no_history, no_fx_rate or failed, as for a holding.
    status: str
    reason: str | None
    requested_weight: float
    # After funding: the replaced holding's weight when the request exceeded it (`capped`).
    # Null, like `capped`, when there's nothing to compare.
    weight: float | None
    capped: bool | None
    # R14: data-store prices the candidate only through a fund's holdings, so its history comes
    # from today's index members. Every "after" figure carries the flag too.
    survivorship: bool


class WhatIfFundingResponse(BaseModel):
    rule: Literal["pro_rata", "replace"]
    ticker: str | None  # the replaced holding
    reason: str | None  # why it can't fund the candidate (it can't be measured)


class WhatIfWindowResponse(BaseModel):
    """Before and after share one listwise window (R12). `shortened` says the candidate changed
    it: the holdings alone would have had the `_without_candidate` one."""

    label: StandardWindow
    requested_start: date
    start: date | None  # null when the histories don't overlap
    end: date
    limited_by: list[str]
    shortened: bool
    start_without_candidate: date | None
    end_without_candidate: date | None


class WhatIfWeightsResponse(BaseModel):
    before: dict[str, float]  # the included holdings', normalised
    after: dict[str, float]  # with the candidate, funded


class WhatIfRowResponse(BaseModel):
    before: RiskFigureResponse | None  # null on the candidate's own rows
    after: RiskFigureResponse
    delta: float | None  # after − before; null when either is greyed, or there's no before


class WhatIfSharpeRowResponse(WhatIfRowResponse):
    delta_se: float | None  # Memmel's SE of the delta (1 s.e.), on the same weeks


class WhatIfRowsResponse(BaseModel):
    """Spec §7.2's table, risk first: the risk deltas are the reliable outputs, ΔSharpe is
    secondary and shown with its SE."""

    sigma_p: WhatIfRowResponse
    diversification_ratio: WhatIfRowResponse
    candidate_pcr: WhatIfRowResponse
    # The candidate's correlation with the portfolio before it joins.
    candidate_correlation: WhatIfRowResponse
    sharpe: WhatIfSharpeRowResponse
    max_drawdown: WhatIfRowResponse


class WhatIfThresholdResponse(BaseModel):
    """A small amount of the candidate raises the historical Sharpe if SR_c > ρ(c, p) × SR_p
    (spec §7.2): weekly, on the shared window. Pro-rata funding only."""

    applicable: bool  # false under replace funding
    reason: str | None  # why it isn't shown
    candidate_sharpe: RiskFigureResponse | None
    correlation: float | None
    portfolio_sharpe: float | None
    threshold: float | None  # ρ(c, p) × SR_p
    improves: bool | None


class WhatIfResponse(BaseModel):
    """The mix before and after adding a candidate (RK-19, spec §7.2). `window`, `weights`,
    `rows` and `threshold` are null exactly when there's nothing to compare: the candidate or
    every holding can't be measured (see `statuses` and `candidate`), or the replaced holding
    can't (`funding.reason`)."""

    label: str  # "ex-ante · constant weights"
    rf_available: bool
    statuses: list[AssetStatusResponse]  # every requested holding, in request order
    candidate: WhatIfCandidateResponse
    funding: WhatIfFundingResponse
    window: WhatIfWindowResponse | None
    weights: WhatIfWeightsResponse | None
    rows: WhatIfRowsResponse | None
    threshold: WhatIfThresholdResponse | None


# --- look-through (RK-22) ---
# Weights are fractions of the mix (0.10 is 10%), as fixed-point strings like the backtest's:
# they are shares that sum to 1, not statistical estimates.


class LookThroughRequest(BaseModel):
    # web's default mix is the user's holdings with their weights (labs_view.build_default_mix);
    # weights are normalised over the holdings data-store knows.
    assets: list[BacktestAssetRequest] = Field(min_length=1, max_length=MAX_BACKTEST_ASSETS)

    @model_validator(mode="after")
    def _no_duplicate_tickers(self):
        _check_unique([asset.ticker for asset in self.assets])
        return self


class LookThroughAssetResponse(BaseModel):
    ticker: str
    status: str  # included, not_tracked or failed
    reason: str | None  # null when included
    # look_through, direct, commodity or unmapped; null when left out.
    kind: str | None
    weight: BacktestDecimal | None  # normalised over the included assets
    matched_in: str | None  # a direct stock: the fund whose line describes it
    note: str | None  # an unmapped asset: why it isn't looked through


class LookThroughSnapshotResponse(BaseModel):
    ticker: str
    fund_key: str
    as_of_date: date  # data-store's scrape date
    n_lines: int
    raw_weight_sum: BacktestDecimal  # as stored; the look-through rescales it to 100


class LookThroughBucketResponse(BaseModel):
    label: str
    weight: BacktestDecimal
    # known, unknown, unmapped or other. Only known buckets are in the concentration figures.
    kind: str


class LookThroughConcentrationResponse(BaseModel):
    top_shares: dict[int, BacktestDecimal]  # the 1, 5 and 10 heaviest known buckets' share
    known_share: BacktestDecimal
    # Over the known share rescaled to 1. Null, with a reason, when nothing is known.
    herfindahl: BacktestDecimal | None
    effective_n: BacktestDecimal | None
    reason: str | None


class LookThroughDimensionResponse(BaseModel):
    label: str  # what to call it: the currency one is R16's "listing currency of holdings"
    buckets: list[LookThroughBucketResponse]  # heaviest first
    concentration: LookThroughConcentrationResponse


class LookThroughNameResponse(BaseModel):
    issuer: str
    location: str | None
    weight: BacktestDecimal
    sources: list[str]  # the held tickers it comes through


class LookThroughNamesResponse(BaseModel):
    top: list[LookThroughNameResponse]  # the 25 heaviest
    n_names: int
    concentration: LookThroughConcentrationResponse  # over every name, not only the top


class LookThroughExposuresResponse(BaseModel):
    snapshots: list[LookThroughSnapshotResponse]
    # look_through, direct_matched, direct_unmatched, commodity, unmapped: sums to 1.
    coverage: dict[str, BacktestDecimal]
    # currency, trading_currency, sector, country, asset_class
    dimensions: dict[str, LookThroughDimensionResponse]
    names: LookThroughNamesResponse


class LookThroughResponse(BaseModel):
    assets: list[LookThroughAssetResponse]  # every requested asset, in request order
    exposures: LookThroughExposuresResponse | None  # null exactly when nothing was included
