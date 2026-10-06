from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from web.formatting import FILTERS, date_, money, pct, qty, signed_money

MINUS = "−"


@pytest.mark.parametrize("fmt", [money, signed_money, qty, pct, date_])
def test_none_renders_em_dash(fmt):
    assert fmt(None) == "—"


def test_filters_registry_names_every_filter():
    assert FILTERS == {
        "money": money,
        "signed_money": signed_money,
        "qty": qty,
        "pct": pct,
        "date": date_,
    }


# --- money ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("1234.5"), "€1,234.50"),
        (Decimal("0"), "€0.00"),
        (Decimal("-1234.5"), f"{MINUS}€1,234.50"),
        (Decimal("1234567.891"), "€1,234,567.89"),
        (Decimal("150.12345678"), "€150.12"),
        (Decimal("0.005"), "€0.01"),  # half-up, not banker's rounding
        (Decimal("0.015"), "€0.02"),
        (Decimal("-0.001"), "€0.00"),  # rounds to zero, so no minus sign
        (1500, "€1,500.00"),
        (12.3, "€12.30"),
    ],
)
def test_money(value, expected):
    assert money(value) == expected


def test_money_never_uses_ascii_hyphen():
    assert "-" not in money(Decimal("-5"))


# --- signed_money --------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("1234.5"), "+€1,234.50"),
        (Decimal("-1234.5"), f"{MINUS}€1,234.50"),
        (Decimal("0"), "€0.00"),
        (Decimal("-0.004"), "€0.00"),
        (Decimal("0.004"), "€0.00"),
        (Decimal("9876543.21"), "+€9,876,543.21"),
        (Decimal("100.00000001"), "+€100.00"),
    ],
)
def test_signed_money(value, expected):
    assert signed_money(value) == expected


# --- qty -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("10.000000"), "10"),
        (Decimal("0.125000"), "0.125"),
        (Decimal("0"), "0"),
        (Decimal("0.000000"), "0"),
        (Decimal("1200"), "1,200"),
        (Decimal("1E+2"), "100"),  # positive exponent: never scientific notation
        (Decimal("100"), "100"),
        (Decimal("1234567.12345678"), "1,234,567.12345678"),
        (Decimal("-3.50"), f"{MINUS}3.5"),
        (5, "5"),
    ],
)
def test_qty(value, expected):
    assert qty(value) == expected


# --- pct -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("0.0184"), "+1.84%"),
        (Decimal("-0.0184"), f"{MINUS}1.84%"),
        (Decimal("0"), "0.00%"),
        (Decimal("0.06666667"), "+6.67%"),
        (Decimal("12.5"), "+1,250.00%"),
        (Decimal("0.00005"), "+0.01%"),  # half-up
        (Decimal("-0.00001"), "0.00%"),
    ],
)
def test_pct(value, expected):
    assert pct(value) == expected


# --- date ----------------------------------------------------------------------


def test_date_formats_date_as_iso():
    assert date_(date(2026, 10, 6)) == "2026-10-06"


def test_date_formats_datetime_by_its_date_part():
    assert date_(datetime(2026, 10, 6, 23, 59, tzinfo=timezone.utc)) == "2026-10-06"
