"""Pure backtest arithmetic for Labs: how a mix of assets, bought at its weights and held, would
have done over 1M, 3M, 6M, 1Y, 2Y, 5Y and the longest history every asset shares (Max), as %
returns in EUR. Nothing about the user's portfolio value enters into it.

No I/O, no datastore_client calls -- the caller fetches each asset's price series (for its
simple_return) and, for a non-EUR asset, the FX series for its currency, and hands them in as
plain data, the same split returns/calculator.py uses. Picking the FX pair (including mapping a
minor unit such as GBp to GBP, which data-store's TickerInfo.fx_currency already does) is the
caller's job too, so this module never looks at a currency code.

The rules, as agreed for the labs chunk (tasks-labs-thread.md):

- **Total return in EUR.** An asset's native growth is its simple_return chained (dividends
  included, splits already accounted for). Its EUR growth divides that by the FX rate (units of
  the currency per 1 EUR, as fx_rates stores it), taken as the latest rate on or before each
  date with no age limit. FX moves count on days the asset itself didn't trade.
- **Calendars.** Assets are lined up on the union of their trading days, each carrying its last
  value forward on days it didn't trade.
- **Periods.** Every period ends on the last day every asset has data. A month period starts
  that many calendar months earlier, the day of the month clamped as web's rangeStart does;
  each asset's base is its value on or before that start. An asset whose history starts after a
  period's start doesn't cover it. The mix then holds only the assets that do, re-weighted to
  100%, and names the rest in `left_out`; it has no figure only when no asset covers the period.
  (Until LB-9 the mix needed every asset, so one ticker onboarded a month earlier blanked the
  mix from 3M to 5Y.)
- **Max** is each asset's whole history, so its start differs per row. The mix's Max starts on
  the earliest asset's first day, and each asset joins the mix on its own first day. Shortening
  Max to the history all assets share was the alternative; it was rejected (LB-9) because a new
  ticker then cut every asset's Max to that ticker's few weeks.
- **Buy and hold.** The mix is bought at its weights on each period's own start and not
  rebalanced: V(t) = sum of w_i * G_i(t) / G_i(start). Weights are normalized to sum to 1 over
  the assets that are available; an asset with no prices or no usable FX rate is reported as
  unavailable and left out. The one exception is an asset joining Max: on its first day the
  mix's value is split again by the target weights, normalized over every asset present by then,
  and held from there. Buying the newcomer with fresh money beside the drifted holdings was the
  alternative; it would need a time-weighted return to stay a percentage, whereas rebalancing
  keeps the mix's return a plain ratio of values.
- **Gaps.** A NULL simple_return after an asset's first day is a gap: data-store either judged
  that day implausible or couldn't anchor it to a prior close. It is bridged as a flat day and
  counted in the period's `gaps`, so a caller can say so. A read-only check of data-store's dev
  database on 2026-10-13, after the September 2026 ingest gap was repaired, found 464 such NULLs
  across 9.0M rows, almost all single days. Blanking any period that contains one was the
  alternative; it was rejected because a single suppressed spike would then hide years of
  figures.
"""

import calendar
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from portfolio.datastore_client.base import FxPoint, PricePoint

# Match portfolio's other pct figures (return_snapshots' NUMERIC(18,8)). Quantized only at
# emission; the chained indexes stay at full Decimal precision.
_EIGHT_PLACES = Decimal("0.00000001")
_ONE = Decimal("1")

NO_HISTORY = "no history yet"
NO_FX_RATE = "no FX rate"


class Period(StrEnum):
    M1 = "1M"
    M3 = "3M"
    M6 = "6M"
    Y1 = "1Y"
    Y2 = "2Y"
    Y5 = "5Y"
    MAX = "Max"


_MONTHS = {
    Period.M1: 1,
    Period.M3: 3,
    Period.M6: 6,
    Period.Y1: 12,
    Period.Y2: 24,
    Period.Y5: 60,
}


@dataclass(frozen=True)
class AssetInput:
    ticker: str
    weight: Decimal  # any positive number; normalized over the available assets
    prices: Sequence[PricePoint]  # ascending by date; only simple_return is read
    # Units of the asset's currency per 1 EUR, ascending by date. None for a EUR asset; an empty
    # series means the currency has no rates, and the asset is unavailable.
    fx: Sequence[FxPoint] | None = None


@dataclass(frozen=True)
class PeriodReturn:
    start: date
    end: date
    covered: bool
    return_pct: Decimal | None  # None exactly when not covered
    gaps: int  # NULL returns bridged inside (start, end]
    # The mix's only: available assets that don't cover the period, in input order. The
    # unavailable ones are in BacktestResult.unavailable instead.
    left_out: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Row:
    ticker: str | None  # None for the mix
    weight: Decimal  # the weight actually used; 1 for the mix
    periods: dict[Period, PeriodReturn]


@dataclass(frozen=True)
class Unavailable:
    ticker: str
    reason: str


@dataclass(frozen=True)
class GrowthSeries:
    """Cumulative growth over one period: a point on its start date (0), then one per trading
    day of any asset up to its end. For Max, an asset that starts later has None points before
    its first day and 0 on it."""

    period: Period
    dates: list[date]
    mix: list[Decimal] | None  # None only when no asset covers the period
    assets: dict[str, list[Decimal | None] | None]  # None for an asset that doesn't cover it
    left_out: list[str]  # as PeriodReturn.left_out, for this period


@dataclass(frozen=True)
class BacktestResult:
    end_date: date | None  # None when no asset is available
    mix: Row | None
    assets: list[Row]  # available assets, in input order
    unavailable: list[Unavailable]
    growth: GrowthSeries | None


@dataclass(frozen=True)
class _Series:
    """One available asset: its native growth index on each of its usable days, and its FX."""

    ticker: str
    weight: Decimal
    dates: list[date]
    native: list[Decimal]
    gap_dates: list[date]
    fx_dates: list[date] | None  # None for a EUR asset
    fx_rates: list[Decimal] | None

    @property
    def first(self) -> date:
        return self.dates[0]

    @property
    def last(self) -> date:
        return self.dates[-1]

    def _native_on(self, day: date) -> Decimal:
        return self.native[bisect_right(self.dates, day) - 1]

    def _fx_on(self, day: date) -> Decimal:
        if self.fx_dates is None:
            return _ONE
        return self.fx_rates[bisect_right(self.fx_dates, day) - 1]

    def growth(self, day: date, start: date) -> Decimal:
        """G(day) / G(start), both taken on or before the date. Written as two ratios rather
        than dividing EUR values, so a flat price under a moving rate stays exact."""
        native = self._native_on(day) / self._native_on(start)
        return native * self._fx_on(start) / self._fx_on(day)

    def gaps_between(self, start: date, end: date) -> int:
        return sum(1 for day in self.gap_dates if start < day <= end)


def compute_backtest(assets: Sequence[AssetInput], *, growth_period: Period) -> BacktestResult:
    """Every period's return for each available asset and for the mix, plus the daily growth
    series for `growth_period`. Raises ValueError for a mix that can't be backtested at all (no
    assets, a duplicate ticker, a weight that isn't positive) -- the endpoint validates these
    first, so reaching here with one is a caller bug."""
    _validate(assets)

    series: list[_Series] = []
    unavailable: list[Unavailable] = []
    for asset in assets:
        built = _build_series(asset)
        if isinstance(built, Unavailable):
            unavailable.append(built)
        else:
            series.append(built)

    if not series:
        return BacktestResult(
            end_date=None, mix=None, assets=[], unavailable=unavailable, growth=None
        )

    total_weight = sum(s.weight for s in series)
    weights = {s.ticker: s.weight / total_weight for s in series}
    end = min(s.last for s in series)
    starts = {period: _period_start(period, end, series) for period in Period}

    asset_rows = []
    for s in series:
        periods = {
            period: _asset_period(s, _asset_start(period, s, starts), end) for period in Period
        }
        asset_rows.append(Row(ticker=s.ticker, weight=weights[s.ticker], periods=periods))

    mix_periods = {
        period: _mix_period(series, weights, period, starts[period], end) for period in Period
    }
    mix = Row(ticker=None, weight=_ONE, periods=mix_periods)

    growth = _growth_series(series, weights, growth_period, starts[growth_period], end)
    return BacktestResult(
        end_date=end, mix=mix, assets=asset_rows, unavailable=unavailable, growth=growth
    )


def _validate(assets: Sequence[AssetInput]) -> None:
    if not assets:
        raise ValueError("A backtest needs at least one asset.")
    tickers = [asset.ticker for asset in assets]
    duplicates = sorted({t for t in tickers if tickers.count(t) > 1})
    if duplicates:
        raise ValueError(f"Duplicate tickers in the mix: {', '.join(duplicates)}.")
    for asset in assets:
        if asset.weight <= 0:
            raise ValueError(f"{asset.ticker}'s weight must be positive, got {asset.weight}.")


def _build_series(asset: AssetInput) -> _Series | Unavailable:
    if not asset.prices:
        return Unavailable(asset.ticker, NO_HISTORY)

    # The first row is the anchor: its return (normally NULL, as data-store's first day is) is
    # ignored. Every later NULL is a gap, bridged as a flat day.
    dates = [asset.prices[0].date]
    native = [_ONE]
    gap_dates = []
    for point in asset.prices[1:]:
        if point.simple_return is None:
            gap_dates.append(point.date)
            factor = _ONE
        else:
            factor = _ONE + point.simple_return
        dates.append(point.date)
        native.append(native[-1] * factor)

    if asset.fx is None:
        return _Series(asset.ticker, asset.weight, dates, native, gap_dates, None, None)

    if not asset.fx:
        return Unavailable(asset.ticker, NO_FX_RATE)
    fx_dates = [point.date for point in asset.fx]
    fx_rates = [point.rate for point in asset.fx]
    # Usable history starts on the first day that has a rate on or before it.
    usable = bisect_left(dates, fx_dates[0])
    if usable == len(dates):
        return Unavailable(asset.ticker, NO_FX_RATE)
    return _Series(
        asset.ticker,
        asset.weight,
        dates[usable:],
        native[usable:],
        gap_dates,
        fx_dates,
        fx_rates,
    )


def _period_start(period: Period, end: date, series: list[_Series]) -> date:
    """The mix's start. For Max, that is the earliest first day; each asset's own Max starts on
    its own first day (_asset_start)."""
    if period is Period.MAX:
        return min(s.first for s in series)
    return _months_before(end, _MONTHS[period])


def _asset_start(period: Period, s: _Series, starts: dict[Period, date]) -> date:
    return s.first if period is Period.MAX else starts[period]


def _months_before(day: date, months: int) -> date:
    """`months` calendar months before `day`, the day of the month clamped to the target
    month's length -- the same rule as web's portfolio_chart.js rangeStart."""
    year, month_index = divmod(day.year * 12 + day.month - 1 - months, 12)
    last_day = calendar.monthrange(year, month_index + 1)[1]
    return date(year, month_index + 1, min(day.day, last_day))


def _covers(s: _Series, start: date, end: date) -> bool:
    # start > end happens only for an asset whose first day is after the common end, when the
    # assets' histories don't overlap at all.
    return s.first <= start <= end


def _members(series: list[_Series], period: Period, start: date, end: date) -> list[_Series]:
    """The assets the mix holds over a period: for Max, every asset with a day on or before the
    end (each joins on its first day); otherwise those covering the whole period."""
    if period is Period.MAX:
        return [s for s in series if s.first <= end]
    return [s for s in series if _covers(s, start, end)]


def _left_out(series: list[_Series], members: list[_Series]) -> list[str]:
    held = {s.ticker for s in members}
    return [s.ticker for s in series if s.ticker not in held]


# One segment of the mix: from its date, the members held at their re-normalized weights.
_Segment = tuple[date, list[_Series]]


def _entry_plan(members: list[_Series], period: Period, start: date) -> list[_Segment]:
    """Buy and hold from `start`, plus, for Max, a rebalance on each later first day."""
    if period is not Period.MAX:
        return [(start, members)]
    entries = sorted({s.first for s in members})
    return [(entry, [s for s in members if s.first <= entry]) for entry in entries]


def _segment_value(segment: _Segment, weights: dict[str, Decimal], day: date) -> Decimal:
    entry, held = segment
    total = sum(weights[s.ticker] for s in held)
    return sum(weights[s.ticker] / total * s.growth(day, entry) for s in held)


class _MixCurve:
    """V(day) / V(start) for an entry plan: each earlier segment's growth up to the next entry,
    chained once here, times the current segment's growth since its entry."""

    def __init__(self, plan: list[_Segment], weights: dict[str, Decimal]):
        self._plan = plan
        self._weights = weights
        self._entries = [entry for entry, _ in plan]
        self._bases = [_ONE]
        for segment, (following, _) in zip(plan, plan[1:]):
            self._bases.append(self._bases[-1] * _segment_value(segment, weights, following))

    def value(self, day: date) -> Decimal:
        k = bisect_right(self._entries, day) - 1
        return self._bases[k] * _segment_value(self._plan[k], self._weights, day)


def _asset_period(s: _Series, start: date, end: date) -> PeriodReturn:
    if not _covers(s, start, end):
        return PeriodReturn(start=start, end=end, covered=False, return_pct=None, gaps=0)
    return PeriodReturn(
        start=start,
        end=end,
        covered=True,
        return_pct=_quantize(s.growth(end, start) - _ONE),
        gaps=s.gaps_between(start, end),
    )


def _mix_period(
    series: list[_Series],
    weights: dict[str, Decimal],
    period: Period,
    start: date,
    end: date,
) -> PeriodReturn:
    members = _members(series, period, start, end)
    left_out = _left_out(series, members)
    if not members:
        return PeriodReturn(
            start=start, end=end, covered=False, return_pct=None, gaps=0, left_out=left_out
        )
    value = _MixCurve(_entry_plan(members, period, start), weights).value(end)
    gaps = sum(s.gaps_between(max(start, s.first), end) for s in members)
    return PeriodReturn(
        start=start,
        end=end,
        covered=True,
        return_pct=_quantize(value - _ONE),
        gaps=gaps,
        left_out=left_out,
    )


def _growth_series(
    series: list[_Series],
    weights: dict[str, Decimal],
    period: Period,
    start: date,
    end: date,
) -> GrowthSeries:
    members = _members(series, period, start, end)
    left_out = _left_out(series, members)
    trading_days = sorted({day for s in series for day in s.dates if start < day <= end})
    dates = [start, *trading_days]

    assets: dict[str, list[Decimal | None] | None] = {s.ticker: None for s in series}
    for s in members:
        own_start = max(start, s.first)
        assets[s.ticker] = [
            None if day < own_start else _quantize(s.growth(day, own_start) - _ONE) for day in dates
        ]

    mix = None
    if members:
        curve = _MixCurve(_entry_plan(members, period, start), weights)
        mix = [_quantize(curve.value(day) - _ONE) for day in dates]
    return GrowthSeries(period=period, dates=dates, mix=mix, assets=assets, left_out=left_out)


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_EIGHT_PLACES, rounding=ROUND_HALF_UP)
