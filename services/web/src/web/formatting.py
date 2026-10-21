"""Display formatting for figures and dates, registered as Jinja filters (UI-5).

One fixed convention, no locale switching: English digit grouping (`1,234.50`), a `€` prefix
(every aggregated figure is EUR), a true minus sign (U+2212) rather than a hyphen, ISO dates, and
half-up rounding. A value that rounds to zero carries no sign: a zero delta is neither a gain nor
a loss. See `docs/architecture.md` ("Display formatting") for why.
"""

from collections.abc import Callable
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

MISSING = "—"
MINUS = "−"

_CENTS = Decimal("0.01")


def _to_decimal(value: Decimal | int | float) -> Decimal:
    # str() first, so a float like 12.3 becomes Decimal("12.3"), not its binary expansion.
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _two_places(value: Decimal) -> tuple[str, str]:
    """Round half-up to 2 places; return (sign, grouped magnitude). Sign is "", "+" or "−"."""
    rounded = value.quantize(_CENTS, rounding=ROUND_HALF_UP)
    sign = "" if rounded.is_zero() else "+" if rounded > 0 else MINUS
    return sign, f"{abs(rounded):,.2f}"


def money(value: Decimal | int | float | None) -> str:
    """`1234.5` → `€1,234.50`; negatives get a leading `−`, positives no sign."""
    if value is None:
        return MISSING
    sign, magnitude = _two_places(_to_decimal(value))
    return f"{MINUS if sign == MINUS else ''}€{magnitude}"


def signed_money(value: Decimal | int | float | None) -> str:
    """As `money`, with an explicit `+` or `−` for deltas; zero stays unsigned."""
    if value is None:
        return MISSING
    sign, magnitude = _two_places(_to_decimal(value))
    return f"{sign}€{magnitude}"


def native_money(value: Decimal | int | float | None, currency: str, places: int = 2) -> str:
    """A figure in its own currency, code after: `-1503.5, "USD"` → `−1,503.50 USD`.

    For a transaction's own amounts and prices, which aren't EUR, so `money`'s `€` would be
    wrong. Signed like `money` (a `−` only), since the sign is a cash-flow direction, not a gain
    or loss. Rounded half-up to `places`; zeros past the second decimal are trimmed, so a price
    at `places=4` shows `112.34`, not `112.3400`.
    """
    if value is None:
        return MISSING
    rounded = _to_decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    if rounded.is_zero():
        rounded = abs(rounded)
    text = f"{abs(rounded):,f}"
    whole, _, fraction = text.partition(".")
    fraction = fraction.rstrip("0").ljust(2, "0")
    sign = MINUS if rounded < 0 else ""
    return f"{sign}{whole}.{fraction} {currency}"


def qty(value: Decimal | int | float | None) -> str:
    """Trailing zeros trimmed (`10.000000` → `10`), grouped, never scientific notation."""
    if value is None:
        return MISSING
    number = _to_decimal(value)
    if number.is_zero():
        return "0"
    # normalize() alone turns 100 into 1E+2; the "f" format spec writes it out positionally.
    text = f"{abs(number.normalize()):,f}"
    return f"{MINUS}{text}" if number < 0 else text


def pct(value: Decimal | int | float | None) -> str:
    """A fraction rendered as a signed percentage: `0.0184` → `+1.84%`."""
    if value is None:
        return MISSING
    sign, magnitude = _two_places(_to_decimal(value) * 100)
    return f"{sign}{magnitude}%"


def share(value: Decimal | int | float | None) -> str:
    """A fraction as an unsigned percentage, for a part of a whole: `0.3333` → `33.33%`.
    A negative value (a replay anomaly, never a real share) still gets its `−`."""
    if value is None:
        return MISSING
    sign, magnitude = _two_places(_to_decimal(value) * 100)
    return f"{MINUS if sign == MINUS else ''}{magnitude}%"


def _unsigned_unless_negative(value: Decimal, places: int) -> str:
    """Rounded half-up to `places`, grouped, with a `−` only: for a figure whose sign isn't a
    gain or a loss. A value that rounds to zero carries no sign."""
    rounded = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    text = f"{abs(rounded):,.{places}f}"
    return f"{MINUS}{text}" if rounded < 0 else text


def pct1(value: Decimal | int | float | None) -> str:
    """A risk figure's fraction as a percentage to 1 decimal (spec §10): `0.1534` → `15.3%`,
    a max drawdown `-0.2345` → `−23.5%`. No `+`: a volatility or a drawdown isn't a gain."""
    if value is None:
        return MISSING
    return f"{_unsigned_unless_negative(_to_decimal(value) * 100, 1)}%"


def ratio(value: Decimal | int | float | None) -> str:
    """A risk-adjusted ratio to 2 decimals (spec §10): `0.8412` → `0.84`, with a `−` only."""
    if value is None:
        return MISSING
    return _unsigned_unless_negative(_to_decimal(value), 2)


def delta_class(value: Decimal | int | float | None, kind: str = "money") -> str:
    """The CSS class for a delta: `gain`, `loss`, or `""` when it displays as zero (or `—`).

    Decided from the sign *after* the display rounding of `signed_money` (`kind="money"`) or
    `pct` (`kind="pct"`), so a figure's color never disagrees with its printed sign: `-0.004`
    prints as `€0.00` and is neither a gain nor a loss.
    """
    if kind not in ("money", "pct"):
        raise ValueError(f"unknown delta kind {kind!r}")
    if value is None:
        return ""
    number = _to_decimal(value)
    sign, _ = _two_places(number * 100 if kind == "pct" else number)
    return {"+": "gain", MINUS: "loss"}.get(sign, "")


def date_(value: date | datetime | None) -> str:
    """ISO `2026-10-06`; a datetime is shown by its date part. Registered as the `date` filter."""
    if value is None:
        return MISSING
    if isinstance(value, datetime):
        value = value.date()
    return value.isoformat()


FILTERS: dict[str, Callable[..., str]] = {
    "money": money,
    "signed_money": signed_money,
    "qty": qty,
    "pct": pct,
    "share": share,
    "delta_class": delta_class,
    "date": date_,
    "native_money": native_money,
    "pct1": pct1,
    "ratio": ratio,
}
