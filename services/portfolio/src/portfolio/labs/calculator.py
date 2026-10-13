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
  each asset's base is its value on or before that start. Max starts on the latest first day
  among the assets. An asset whose history starts after a period's start doesn't cover it, and
  the mix then has no figure for that period either.
- **Buy and hold.** The mix is bought at its weights on each period's own start and not
  rebalanced: V(t) = sum of w_i * G_i(t) / G_i(start). Weights are normalized to sum to 1 over
  the assets that are available; an asset with no prices or no usable FX rate is reported as
  unavailable and left out.
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
from dataclasses import dataclass
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
    day of any asset up to its end."""

    period: Period
    dates: list[date]
    mix: list[Decimal] | None  # None when some asset doesn't cover the period
    assets: dict[str, list[Decimal] | None]  # None for an asset that doesn't cover it


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
        periods = {period: _asset_period(s, starts[period], end) for period in Period}
        asset_rows.append(Row(ticker=s.ticker, weight=weights[s.ticker], periods=periods))

    mix_periods = {
        period: _mix_period(series, weights, starts[period], end, asset_rows, period)
        for period in Period
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
    if period is Period.MAX:
        return max(s.first for s in series)
    return _months_before(end, _MONTHS[period])


def _months_before(day: date, months: int) -> date:
    """`months` calendar months before `day`, the day of the month clamped to the target
    month's length -- the same rule as web's portfolio_chart.js rangeStart."""
    year, month_index = divmod(day.year * 12 + day.month - 1 - months, 12)
    last_day = calendar.monthrange(year, month_index + 1)[1]
    return date(year, month_index + 1, min(day.day, last_day))


def _covers(s: _Series, start: date, end: date) -> bool:
    # start > end happens only for Max when two assets' histories don't overlap at all.
    return s.first <= start <= end


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
    start: date,
    end: date,
    asset_rows: list[Row],
    period: Period,
) -> PeriodReturn:
    gaps = sum(row.periods[period].gaps for row in asset_rows)
    if not all(_covers(s, start, end) for s in series):
        return PeriodReturn(start=start, end=end, covered=False, return_pct=None, gaps=gaps)
    value = sum(weights[s.ticker] * s.growth(end, start) for s in series)
    return PeriodReturn(
        start=start, end=end, covered=True, return_pct=_quantize(value - _ONE), gaps=gaps
    )


def _growth_series(
    series: list[_Series],
    weights: dict[str, Decimal],
    period: Period,
    start: date,
    end: date,
) -> GrowthSeries:
    if start > end:
        return GrowthSeries(
            period=period, dates=[], mix=None, assets={s.ticker: None for s in series}
        )

    trading_days = sorted({day for s in series for day in s.dates if start < day <= end})
    dates = [start, *trading_days]
    covered = [s for s in series if _covers(s, start, end)]

    assets: dict[str, list[Decimal] | None] = {s.ticker: None for s in series}
    for s in covered:
        assets[s.ticker] = [_quantize(s.growth(day, start) - _ONE) for day in dates]

    mix = None
    if len(covered) == len(series):
        mix = [
            _quantize(sum(weights[s.ticker] * s.growth(day, start) for s in series) - _ONE)
            for day in dates
        ]
    return GrowthSeries(period=period, dates=dates, mix=mix, assets=assets)


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_EIGHT_PLACES, rounding=ROUND_HALF_UP)
