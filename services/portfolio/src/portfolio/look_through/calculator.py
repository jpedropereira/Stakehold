"""Look-through exposures of a mix (risk spec §8): pure, no I/O.

Each held asset's weight is spread over buckets in five dimensions. The weights are Decimal,
like Labs': they are shares of a mix, not statistical estimates.
- A looked-through fund: weight x each line's weight, the snapshot rescaled to 100%.
- A direct stock: its own constituent line if any looked-through fund holds it; otherwise only
  its listing currency (and "Equity") is known.
- A commodity (an ETC, instruments.ETCS): "Commodity: <name>", in the commodity's currency.
- An unmapped ETF: one "unmapped: <ticker>" bucket in every dimension.

Every bucket has a kind. Herfindahl and effective N are measured over the `known` buckets
only, rescaled to their share, which is reported beside them (agreed for RK-22). Otherwise an
opaque 20% ETF would read as one 20% name. `other` is a real exposure that isn't one of the
dimension's categories: a commodity's country, a hedged fund's currency, a fund's cash line in
the names view.

`currency` is the constituents' listing currency, a proxy for exposure that R16 says must be
labelled "listing currency of holdings". `trading_currency` is each held ticker's own listing
currency, which R16 shows alongside.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from portfolio.datastore_client.base import EtfConstituent, EtfHoldings
from portfolio.look_through.instruments import ETCS
from portfolio.look_through.issuer import issuer_key

# A held asset's kind.
LOOK_THROUGH = "look_through"
DIRECT = "direct"
COMMODITY = "commodity"
UNMAPPED = "unmapped"

# A bucket's kind.
KNOWN = "known"
UNKNOWN_KIND = "unknown"
UNMAPPED_KIND = "unmapped"
OTHER = "other"

UNKNOWN = "unknown"
NO_COUNTRY = "n/a (commodity)"
TOP_SHARES = (1, 5, 10)
TOP_NAMES = 25
NOTHING_KNOWN = "no known exposure: every holding is unmapped or unknown"
# The asset class whose lines are single names. Cash, FX, futures and money-market lines are a
# fund's plumbing, not a company you're exposed to.
_EQUITY = "Equity"

_ZERO = Decimal("0")


@dataclass(frozen=True)
class HeldAsset:
    ticker: str
    weight: Decimal  # normalised: the mix's weights sum to 1
    kind: str  # LOOK_THROUGH, DIRECT, COMMODITY or UNMAPPED
    listing_currency: str | None  # data-store's fx_currency for the held ticker
    hedged: bool = False  # instruments.HEDGED


@dataclass(frozen=True)
class Bucket:
    label: str
    weight: Decimal
    kind: str  # KNOWN, UNKNOWN_KIND, UNMAPPED_KIND or OTHER


@dataclass(frozen=True)
class Concentration:
    # Shares of the whole mix held by the 1, 5 and 10 heaviest known buckets.
    top_shares: dict[int, Decimal]
    known_share: Decimal
    herfindahl: Decimal | None  # sum of squared weights, over the known share rescaled to 1
    effective_n: Decimal | None  # 1 / herfindahl
    reason: str | None  # why the two above are None


@dataclass(frozen=True)
class Dimension:
    buckets: tuple[Bucket, ...]  # heaviest first, ties by label; zero weights dropped
    concentration: Concentration


@dataclass(frozen=True)
class Name:
    issuer: str
    location: str | None
    weight: Decimal
    sources: tuple[str, ...]  # the held tickers it comes through, sorted


@dataclass(frozen=True)
class Names:
    top: tuple[Name, ...]  # the TOP_NAMES heaviest known names
    n_names: int
    concentration: Concentration  # over every known name, not just the top


@dataclass(frozen=True)
class AssetResult:
    ticker: str
    weight: Decimal
    kind: str
    matched_in: str | None  # a direct stock: the looked-through fund its line came from


@dataclass(frozen=True)
class Snapshot:
    ticker: str
    fund_key: str
    as_of_date: date
    n_lines: int
    raw_weight_sum: Decimal  # as stored; the look-through rescales it to 100


@dataclass(frozen=True)
class LookThrough:
    assets: tuple[AssetResult, ...]
    snapshots: tuple[Snapshot, ...]
    currency: Dimension
    trading_currency: Dimension
    sector: Dimension
    country: Dimension
    asset_class: Dimension
    names: Names


class _Accumulator:
    def __init__(self):
        self._weights: dict[tuple[str, str], Decimal] = defaultdict(lambda: _ZERO)

    def add(self, label: str, weight: Decimal, kind: str = KNOWN) -> None:
        self._weights[(label, kind)] += weight

    def add_or_unknown(self, label: str | None, weight: Decimal) -> None:
        if label is None:
            self.add(UNKNOWN, weight, UNKNOWN_KIND)
        else:
            self.add(label, weight)

    def dimension(self) -> Dimension:
        buckets = tuple(
            Bucket(label, weight, kind)
            for (label, kind), weight in sorted(
                self._weights.items(), key=lambda item: (-item[1], item[0][0])
            )
            if weight != _ZERO
        )
        known = [b.weight for b in buckets if b.kind == KNOWN]
        return Dimension(buckets=buckets, concentration=_concentration(known))


class _NameAccumulator:
    def __init__(self):
        self._weights: dict[tuple[str, str | None], Decimal] = defaultdict(lambda: _ZERO)
        self._sources: dict[tuple[str, str | None], set[str]] = defaultdict(set)

    def add(self, key: tuple[str, str | None], weight: Decimal, source: str) -> None:
        self._weights[key] += weight
        self._sources[key].add(source)

    def names(self) -> Names:
        ordered = [
            Name(issuer, location, weight, tuple(sorted(self._sources[(issuer, location)])))
            for (issuer, location), weight in sorted(
                self._weights.items(), key=lambda item: (-item[1], item[0][0], item[0][1] or "")
            )
            if weight != _ZERO
        ]
        return Names(
            top=tuple(ordered[:TOP_NAMES]),
            n_names=len(ordered),
            concentration=_concentration([name.weight for name in ordered]),
        )


def compute_look_through(
    assets: Sequence[HeldAsset], snapshots: Mapping[str, EtfHoldings]
) -> LookThrough:
    """The mix's exposures. `snapshots` holds every looked-through fund's latest snapshot, by
    the held ticker; a direct stock is matched against all of them."""
    currency, trading = _Accumulator(), _Accumulator()
    sector, country, asset_class = _Accumulator(), _Accumulator(), _Accumulator()
    names = _NameAccumulator()
    lines_by_ticker = _lines_by_yfinance_ticker(snapshots)
    results = []

    for asset in assets:
        w = asset.weight
        trading.add_or_unknown(asset.listing_currency, w)
        matched_in = None

        if asset.kind == LOOK_THROUGH:
            if asset.ticker not in snapshots:
                raise ValueError(f"no snapshot given for looked-through fund {asset.ticker}")
            for line, share in _rescaled(snapshots[asset.ticker]):
                lw = w * share
                if asset.hedged:
                    currency.add(f"hedged: {asset.ticker}", lw, OTHER)
                else:
                    currency.add_or_unknown(line.currency, lw)
                _add_line(line, lw, asset.ticker, sector, country, asset_class, names)
        elif asset.kind == DIRECT:
            match = lines_by_ticker.get(asset.ticker)
            if match is None:
                currency.add_or_unknown(asset.listing_currency, w)
                sector.add(UNKNOWN, w, UNKNOWN_KIND)
                country.add(UNKNOWN, w, UNKNOWN_KIND)
                asset_class.add(_EQUITY, w)
                names.add((asset.ticker, None), w, asset.ticker)
            else:
                matched_in, line = match
                currency.add_or_unknown(line.currency, w)
                _add_line(line, w, asset.ticker, sector, country, asset_class, names)
        elif asset.kind == COMMODITY:
            etc = ETCS[asset.ticker]
            label = f"Commodity: {etc.commodity}"
            currency.add(etc.currency, w)
            sector.add(label, w)
            country.add(NO_COUNTRY, w, OTHER)
            asset_class.add("Commodity", w)
            names.add((label, None), w, asset.ticker)
        elif asset.kind == UNMAPPED:
            label = f"unmapped: {asset.ticker}"
            for accumulator in (currency, sector, country, asset_class):
                accumulator.add(label, w, UNMAPPED_KIND)
        else:
            raise ValueError(f"unknown asset kind {asset.kind!r} for {asset.ticker}")

        results.append(AssetResult(asset.ticker, w, asset.kind, matched_in))

    return LookThrough(
        assets=tuple(results),
        snapshots=tuple(
            Snapshot(
                ticker=ticker,
                fund_key=holdings.fund_key,
                as_of_date=holdings.as_of_date,
                n_lines=len(holdings.constituents),
                raw_weight_sum=_raw_sum(holdings),
            )
            for ticker, holdings in sorted(snapshots.items())
        ),
        currency=currency.dimension(),
        trading_currency=trading.dimension(),
        sector=sector.dimension(),
        country=country.dimension(),
        asset_class=asset_class.dimension(),
        names=names.names(),
    )


def _add_line(
    line: EtfConstituent,
    weight: Decimal,
    source: str,
    sector: _Accumulator,
    country: _Accumulator,
    asset_class: _Accumulator,
    names: _NameAccumulator,
) -> None:
    sector.add_or_unknown(line.sector, weight)
    country.add_or_unknown(line.location, weight)
    asset_class.add_or_unknown(line.asset_class, weight)
    key = issuer_key(line.name, line.location)
    if line.asset_class == _EQUITY and key is not None:
        names.add(key, weight, source)


def _rescaled(holdings: EtfHoldings):
    """Each weighted line with its share of the snapshot, the weights rescaled to sum to 1."""
    total = _raw_sum(holdings)
    if total <= _ZERO:
        raise ValueError(f"snapshot of {holdings.fund_key} has no positive total weight")
    for line in holdings.constituents:
        if line.weight_percent is not None:
            yield line, line.weight_percent / total


def _raw_sum(holdings: EtfHoldings) -> Decimal:
    return sum((c.weight_percent for c in holdings.constituents if c.weight_percent), _ZERO)


def _lines_by_yfinance_ticker(
    snapshots: Mapping[str, EtfHoldings],
) -> dict[str, tuple[str, EtfConstituent]]:
    """Every equity line, by the ticker data-store prices it as. A stock in several funds takes
    the first fund by held ticker, so the match doesn't depend on request order; the lines
    describe the same listing either way."""
    found: dict[str, tuple[str, EtfConstituent]] = {}
    for ticker, holdings in sorted(snapshots.items()):
        for line in holdings.constituents:
            if line.yfinance_ticker is not None and line.yfinance_ticker not in found:
                found[line.yfinance_ticker] = (ticker, line)
    return found


def _concentration(known: Sequence[Decimal]) -> Concentration:
    known_share = sum(known, _ZERO)
    ordered = sorted(known, reverse=True)
    top_shares = {n: sum(ordered[:n], _ZERO) for n in TOP_SHARES}
    if known_share <= _ZERO:
        return Concentration(top_shares, known_share, None, None, NOTHING_KNOWN)
    herfindahl = sum(((w / known_share) ** 2 for w in known), _ZERO)
    return Concentration(top_shares, known_share, herfindahl, 1 / herfindahl, None)
