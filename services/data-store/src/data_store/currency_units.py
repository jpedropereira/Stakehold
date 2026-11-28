"""The minor currency units yfinance quotes some listings in, and the major units they belong to.

A leaf module: both ingestion (`market_data/yfinance_source.py`, which rescales minor-unit
prices to the major unit as they arrive) and onboarding and the read API (which pick FX pairs)
need the same map, and neither should import the other to get it.
"""

import re

# Verified for GBp (TK-0); ZAc and ILA are yfinance's documented equivalents for Johannesburg and
# Tel Aviv, and the same three yfinance 1.7.0 itself converts (`_CURRENCY_CONVERSIONS` in
# scrapers/history.py). Each is 1/100 of its major unit.
_MINOR_UNITS = {"GBp": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}
_CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")


def major_currency(code: str | None) -> str | None:
    """The ISO code FX rates are tracked under for a provider currency, or None if it isn't one."""
    if code is None:
        return None
    code = _MINOR_UNITS[code][0] if code in _MINOR_UNITS else code
    return code if _CURRENCY_CODE.fullmatch(code) else None


def minor_unit_divisor(code: str | None) -> int:
    """What a price quoted in `code` must be divided by to be in its major unit: 100 for a
    minor unit, 1 for anything else (a major currency, an unknown code, or None)."""
    return _MINOR_UNITS[code][1] if code in _MINOR_UNITS else 1
