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
    "date": date_,
}
