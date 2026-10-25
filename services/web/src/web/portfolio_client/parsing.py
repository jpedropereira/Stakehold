"""The JSON primitives every portfolio_client parser checks values with: a real boolean, an
integer, a number, a date, a list of tickers. Lifted out of http_client.py with the risk parsers
(risk_parsing.py) when it neared the repo's 64KB limit (RK-29). Private to the package: the
leading underscores stay, so the names read as they did in http_client.py."""

from datetime import date
from decimal import Decimal


def _opt_decimal(value) -> Decimal | None:
    return None if value is None else Decimal(value)


def _bool(item: dict, key: str) -> bool:
    """A flag that must be a real JSON boolean. A string "false" is truthy, and misreading
    `priced` would show a partial sum as the holding's value."""
    value = item[key]
    if not isinstance(value, bool):
        raise TypeError(f"{key} is not a boolean")
    return value


def _opt_date(value) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _int(item: dict, key: str) -> int:
    value = item[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} is not an integer")
    return value


def _tickers(item: dict, key: str) -> tuple[str, ...]:
    """A required list of tickers. portfolio always sends `left_out`, so a missing key is a
    contract break: reading it as "none left out" would let the mix's figure pass for every
    asset's."""
    value = item[key]
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise TypeError(f"{key} is not a list of tickers")
    return tuple(value)


def _float(value, key: str) -> float:
    """A JSON number. A bool is an int to Python, and a string would format as a figure, so
    both are refused rather than shown as 1.00 or passed through."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{key} is not a number")
    return float(value)


def _opt_float(value, key: str) -> float | None:
    return None if value is None else _float(value, key)


def _opt_bool(item: dict, key: str) -> bool | None:
    return None if item[key] is None else _bool(item, key)
