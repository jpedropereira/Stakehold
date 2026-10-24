"""An edited mix on /labs (LB-7): read from the query string, checked row by row, written back.

Pure, like labs_view.py: the router fetches, this decides. Nothing is stored, so the query string
is the whole state:

- `ticker` and `weight` repeat once per row, in order, as a GET form submits them. Any `ticker`
  key at all makes the mix an edited one, so an emptied mix is `?ticker=`; no key is the default
  mix (labs_view.build_default_mix).
- `remove` is the position of a row to drop, as the form numbered them. The router answers it
  with a redirect to the canonical query, so a reload can't remove a second row.
- `last_ticker` and `last_weight` are the mix the table showed when the form was submitted.
  While the form has errors, the table keeps showing that mix instead of none.

Weights are percentages and needn't add up to 100: portfolio scales them to 100% (agreed in
LB-7), and the table shows the weights it used.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import zip_longest
from urllib.parse import urlencode

from web.labs_view import MAX_BACKTEST_ASSETS, DefaultMix

__all__ = [
    "MAX_BACKTEST_ASSETS",
    "CheckedMix",
    "CheckedRow",
    "MixForm",
    "MixRow",
    "canonical_query",
    "check_mix",
    "default_rows",
    "parse_mix",
]

_HUNDRED = Decimal("100")


@dataclass(frozen=True)
class MixRow:
    """One row as typed, trimmed: the weight is still text, so the form can show it back."""

    ticker: str
    weight: str


@dataclass(frozen=True)
class MixForm:
    """The submitted form. `positions` keep each row's place in the form, blank rows counted,
    which is what `remove` refers to."""

    rows: tuple[MixRow, ...]
    positions: tuple[int, ...]
    remove: int | None
    last: tuple[MixRow, ...]

    def without_removed(self) -> tuple[MixRow, ...]:
        return tuple(row for row, at in zip(self.rows, self.positions) if at != self.remove)


@dataclass(frozen=True)
class CheckedRow:
    """A row with each field's error, if any. `ticker` is the tracked spelling when the typed
    one matched in another case."""

    ticker: str
    weight: str
    ticker_error: str | None
    weight_error: str | None
    unknown_ticker: bool

    @property
    def errors(self) -> tuple[str, ...]:
        """The row's errors in the order they're shown: the ticker's, then the weight's."""
        return tuple(e for e in (self.ticker_error, self.weight_error) if e is not None)


@dataclass(frozen=True)
class CheckedMix:
    """`assets` is what is sent, as fractions, and is empty unless the mix is `valid`. `total`
    sums the weights that are numbers, as a fraction (1 is 100%). `error` is about the mix as
    a whole rather than a row."""

    rows: tuple[CheckedRow, ...]
    error: str | None
    total: Decimal
    assets: tuple[tuple[str, Decimal], ...]

    @property
    def valid(self) -> bool:
        return self.error is None and not any(row.errors for row in self.rows)


def _pairs(params: Iterable[tuple[str, str]], ticker_key: str, weight_key: str):
    tickers = [value for key, value in params if key == ticker_key]
    weights = [value for key, value in params if key == weight_key]
    return [(t.strip(), w.strip()) for t, w in zip_longest(tickers, weights, fillvalue="")]


def _rows(pairs) -> tuple[tuple[MixRow, ...], tuple[int, ...]]:
    kept = [(MixRow(t, w), at) for at, (t, w) in enumerate(pairs) if t or w]
    return tuple(row for row, _ in kept), tuple(at for _, at in kept)


def parse_mix(params: Sequence[tuple[str, str]]) -> MixForm | None:
    """The edited mix in `params` (a query string's key/value pairs), or None for the default
    mix. Rows with neither a ticker nor a weight are the form's empty row, and are dropped."""
    if not any(key == "ticker" for key, _ in params):
        return None
    rows, positions = _rows(_pairs(params, "ticker", "weight"))
    last, _ = _rows(_pairs(params, "last_ticker", "last_weight"))
    raw_remove = next((value for key, value in params if key == "remove"), "")
    remove = int(raw_remove) if raw_remove.isdigit() else None
    return MixForm(rows=rows, positions=positions, remove=remove, last=last)


def canonical_query(rows: Sequence[MixRow], last: Sequence[MixRow]) -> str:
    """The query string for `rows`, with `last` as the mix the table shows. Parses back to the
    same rows. An empty mix is still `ticker=`, so it doesn't read as the default."""
    params: list[tuple[str, str]] = []
    for row in rows:
        params += [("ticker", row.ticker), ("weight", row.weight)]
    if not rows:
        params.append(("ticker", ""))
    for row in last:
        params += [("last_ticker", row.ticker), ("last_weight", row.weight)]
    return urlencode(params)


def _percent_text(value: Decimal) -> str:
    """`75` for 75.00, `33.33` for 33.333: two places at most, no trailing zeros."""
    text = f"{value.quantize(Decimal('0.01')):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def default_rows(mix: DefaultMix) -> tuple[MixRow, ...]:
    """The default mix as form rows, its weights as percentages to two places. Rounded, they
    can add up to 99.99%, or to less than 100% past the cap; portfolio scales them either way."""
    return tuple(MixRow(ticker, _percent_text(weight * _HUNDRED)) for ticker, weight in mix.assets)


def parse_weight(text: str) -> Decimal | None:
    """A positive, finite percentage, or None. Takes a trailing `%` and a decimal comma. Also
    the what-if's candidate weight (risk_what_if_view.py)."""
    text = text.removesuffix("%").strip().replace(",", ".")
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value.is_finite() and value > 0 else None


def check_mix(rows: Sequence[MixRow], known_tickers: Sequence[str] | None) -> CheckedMix:
    """Each row's errors: a missing or untracked ticker, a repeat of an earlier row's ticker,
    a missing weight or one that isn't a positive number. Tickers match the tracked list in any
    case. With no list (`known_tickers` None, because it couldn't be loaded), any ticker passes
    and portfolio reports the ones it doesn't track."""
    spelling = None if known_tickers is None else {t.casefold(): t for t in known_tickers}
    seen: set[str] = set()
    checked: list[CheckedRow] = []
    assets: list[tuple[str, Decimal]] = []
    total = Decimal("0")
    for row in rows:
        ticker, ticker_error, weight_error, unknown = row.ticker, None, None, False
        if not ticker:
            ticker_error = "Enter a ticker."
        elif spelling is not None and ticker.casefold() not in spelling:
            ticker_error = "Stakehold doesn't track this ticker."
            unknown = True
        else:
            ticker = ticker if spelling is None else spelling[ticker.casefold()]
            if ticker in seen:
                ticker_error = "Already in the mix above."
            seen.add(ticker)
        weight = parse_weight(row.weight)
        if not row.weight:
            weight_error = "Enter a weight."
        elif weight is None:
            weight_error = "Enter a weight above 0%."
        else:
            total += weight
            assets.append((ticker, weight / _HUNDRED))
        checked.append(CheckedRow(ticker, row.weight, ticker_error, weight_error, unknown))
    error = (
        f"A mix can have at most {MAX_BACKTEST_ASSETS} assets."
        if len(rows) > MAX_BACKTEST_ASSETS
        else None
    )
    result = CheckedMix(rows=tuple(checked), error=error, total=total / _HUNDRED, assets=())
    if result.valid:
        result = CheckedMix(rows=result.rows, error=None, total=result.total, assets=tuple(assets))
    return result
