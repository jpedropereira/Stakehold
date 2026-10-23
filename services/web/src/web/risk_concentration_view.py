"""The /risk page's concentration section (RK-23): where today's mix is concentrated, by
currency, sector, country, asset class and single name, looked through the funds' holdings
(spec §8, portfolio's RK-22).

Pure, like risk_ex_ante_view.py: the router fetches, this decides what is shown. Its parts:
- the coverage: how much of the mix was looked through, held directly, a commodity or
  unmapped. The unmapped share is shown prominently, with each fund it covers and why (§8): a
  table that sums to 100% would otherwise read as complete;
- the currency table: both of R16's views side by side, the constituents' listing currency
  (the look-through, the headline) and each held ticker's own trading currency. A currency
  missing from one view is 0 there, not blank: it's a real 0% of the mix;
- sector, country and asset class, a table each;
- the top single names, a stock held directly and through a fund counting once.

Each table shows its 10 heaviest known buckets, and every unknown, unmapped or other bucket,
since those say what the table doesn't know; the rest are held back for a "show all". Each
has its concentration line (top shares, effective number, Herfindahl over the known share).

A row's bar is its weight in percent of the whole mix, so bars compare across tables. It is
an SVG width, not a style: the CSP has no 'unsafe-inline' for styles.

The section measures the whole default mix: `?exclude=` is ex-ante's, to lengthen its
window (R12), and concentration has no window.
"""

from dataclasses import dataclass
from decimal import Decimal

from web.labs_view import DefaultMix
from web.portfolio_client.base import (
    LOOK_THROUGH_COVERAGE,
    LookThrough,
    LookThroughBucket,
    LookThroughConcentration,
    LookThroughDimension,
    LookThroughExposures,
    LookThroughName,
    LookThroughSnapshot,
)
from web.risk_ex_ante_view import LeftOut, mix_left_out

VISIBLE_KNOWN = 10  # known buckets, and names, shown before "show all"

COVERAGE_LABELS = {
    "look_through": "Looked through a fund's holdings",
    "direct_matched": "Stocks held directly, described by a fund's holdings",
    "direct_unmatched": "Stocks held directly, sector and country unknown",
    "commodity": "Commodities",
    "unmapped": "Unmapped: funds with no holdings data",
}
_TABLE_DIMENSIONS = ("sector", "country", "asset_class")
_GREYED_KINDS = frozenset({"unknown", "unmapped"})
_ZERO = Decimal(0)


@dataclass(frozen=True)
class CoverageShare:
    key: str
    label: str
    weight: Decimal


@dataclass(frozen=True)
class UnmappedFund:
    ticker: str
    weight: Decimal | None
    note: str | None  # why it isn't looked through


@dataclass(frozen=True)
class BucketRow:
    label: str
    weight: Decimal
    kind: str
    trading: Decimal | None  # the currency table's trading-currency view; None elsewhere
    bar: str  # the bar's SVG width: percent of the mix, to 1 decimal

    @property
    def greyed(self) -> bool:
        """An unknown or unmapped bucket is what the table can't place: shown in the caption
        tone, never a category of its own."""
        return self.kind in _GREYED_KINDS


@dataclass(frozen=True)
class DimensionTable:
    key: str
    label: str
    trading_label: str | None  # the currency table's second column
    rows: tuple[BucketRow, ...]  # shown
    more: tuple[BucketRow, ...]  # held back for "show all"
    concentration: LookThroughConcentration


@dataclass(frozen=True)
class NameRow:
    issuer: str
    location: str | None
    weight: Decimal
    sources: tuple[str, ...]
    bar: str


@dataclass(frozen=True)
class NamesTable:
    rows: tuple[NameRow, ...]
    more: tuple[NameRow, ...]
    n_names: int
    concentration: LookThroughConcentration


@dataclass(frozen=True)
class ConcentrationSection:
    measured: bool  # False when nothing could be included: only `left_out` then
    coverage: tuple[CoverageShare, ...]  # the nonzero shares, in LOOK_THROUGH_COVERAGE order
    unmapped_share: Decimal
    unmapped: tuple[UnmappedFund, ...]
    snapshots: tuple[LookThroughSnapshot, ...]
    currency: DimensionTable | None
    dimensions: tuple[DimensionTable, ...]  # sector, country, asset class
    names: NamesTable | None
    left_out: tuple[LeftOut, ...]


def build_concentration_section(lt: LookThrough, mix: DefaultMix) -> ConcentrationSection:
    left_out = (
        *(LeftOut(a.ticker, a.reason or a.status) for a in lt.assets if a.status != "included"),
        *mix_left_out(mix),
    )
    exposures = lt.exposures
    if exposures is None:
        return ConcentrationSection(
            measured=False,
            coverage=(),
            unmapped_share=_ZERO,
            unmapped=(),
            snapshots=(),
            currency=None,
            dimensions=(),
            names=None,
            left_out=left_out,
        )
    return ConcentrationSection(
        measured=True,
        coverage=tuple(
            CoverageShare(key, COVERAGE_LABELS[key], exposures.coverage[key])
            for key in LOOK_THROUGH_COVERAGE
            if exposures.coverage[key] > 0
        ),
        unmapped_share=exposures.coverage["unmapped"],
        unmapped=tuple(
            UnmappedFund(a.ticker, a.weight, a.note) for a in lt.assets if a.kind == "unmapped"
        ),
        snapshots=exposures.snapshots,
        currency=_currency_table(exposures),
        dimensions=tuple(
            _table(key, exposures.dimensions[key], _rows(exposures.dimensions[key].buckets))
            for key in _TABLE_DIMENSIONS
        ),
        names=_names(exposures),
        left_out=left_out,
    )


def _bar(weight: Decimal) -> str:
    return str((weight * 100).quantize(Decimal("0.1")))


def _rows(buckets, trading: dict[str, Decimal] | None = None) -> tuple[BucketRow, ...]:
    return tuple(
        BucketRow(
            b.label,
            b.weight,
            b.kind,
            None if trading is None else trading.get(b.label, _ZERO),
            _bar(b.weight),
        )
        for b in buckets
    )


def _currency_table(exposures: LookThroughExposures) -> DimensionTable:
    """The listing-currency view's buckets, each with its trading-currency weight, then the
    currencies only the trading view has (a EUR-listed fund whose constituents are all
    elsewhere), at 0 in the first column."""
    listing = exposures.dimensions["currency"]
    trading = exposures.dimensions["trading_currency"]
    trading_weights = {b.label: b.weight for b in trading.buckets}
    listed = {b.label for b in listing.buckets}
    only_trading = [
        LookThroughBucket(b.label, _ZERO, b.kind) for b in trading.buckets if b.label not in listed
    ]
    rows = _rows((*listing.buckets, *only_trading), trading_weights)
    return _table("currency", listing, rows, trading_label=trading.label)


def _table(
    key: str,
    dimension: LookThroughDimension,
    rows: tuple[BucketRow, ...],
    trading_label: str | None = None,
) -> DimensionTable:
    shown, more, known = [], [], 0
    for row in rows:
        if row.kind != "known":
            shown.append(row)
        elif known < VISIBLE_KNOWN:
            shown.append(row)
            known += 1
        else:
            more.append(row)
    return DimensionTable(
        key=key,
        label=dimension.label,
        trading_label=trading_label,
        rows=tuple(shown),
        more=tuple(more),
        concentration=dimension.concentration,
    )


def _name_row(name: LookThroughName) -> NameRow:
    return NameRow(name.issuer, name.location, name.weight, name.sources, _bar(name.weight))


def _names(exposures: LookThroughExposures) -> NamesTable:
    names = exposures.names
    rows = tuple(_name_row(n) for n in names.top)
    return NamesTable(
        rows=rows[:VISIBLE_KNOWN],
        more=rows[VISIBLE_KNOWN:],
        n_names=names.n_names,
        concentration=names.concentration,
    )
