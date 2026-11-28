import pytest
from data_store import currency_units


@pytest.mark.parametrize(
    "code, expected",
    [
        ("EUR", "EUR"),
        ("USD", "USD"),
        ("GBp", "GBP"),
        ("ZAc", "ZAR"),
        ("ILA", "ILS"),
        (None, None),
        ("", None),
        ("usd", None),
        ("EURO", None),
    ],
)
def test_major_currency(code, expected):
    assert currency_units.major_currency(code) == expected


@pytest.mark.parametrize(
    "code, expected",
    [
        ("GBp", 100),
        ("ZAc", 100),
        ("ILA", 100),
        # GBP is the major unit: case matters, it's how yfinance tells pence from pounds.
        ("GBP", 1),
        ("ZAR", 1),
        ("USD", 1),
        (None, 1),
        ("", 1),
        ("XYZ", 1),
    ],
)
def test_minor_unit_divisor(code, expected):
    assert currency_units.minor_unit_divisor(code) == expected
