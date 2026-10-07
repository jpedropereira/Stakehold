from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from web.formatting import (
    FILTERS,
    date_,
    delta_class,
    money,
    native_money,
    pct,
    qty,
    share,
    signed_money,
)

MINUS = "−"


@pytest.mark.parametrize("fmt", [money, signed_money, qty, pct, share, date_])
def test_none_renders_em_dash(fmt):
    assert fmt(None) == "—"


def test_filters_registry_names_every_filter():
    assert FILTERS == {
        "money": money,
        "signed_money": signed_money,
        "qty": qty,
        "pct": pct,
        "share": share,
        "delta_class": delta_class,
        "date": date_,
        "native_money": native_money,
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


# --- share -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("0.3333"), "33.33%"),
        (Decimal("1"), "100.00%"),
        (Decimal("0"), "0.00%"),
        (Decimal("0.00005"), "0.01%"),  # half-up
        (Decimal("-0.05"), f"{MINUS}5.00%"),  # a negative-quantity anomaly stays visible
    ],
)
def test_share_is_an_unsigned_percentage(value, expected):
    assert share(value) == expected


# --- delta_class ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "kind", "expected"),
    [
        (Decimal("100"), "money", "gain"),
        (Decimal("-100"), "money", "loss"),
        (Decimal("0"), "money", ""),
        (Decimal("-0.004"), "money", ""),  # shown as €0.00, so not a loss
        (Decimal("0.005"), "money", "gain"),  # shown as +€0.01
        (Decimal("0.0184"), "pct", "gain"),
        (Decimal("-0.0184"), "pct", "loss"),
        (Decimal("-0.00001"), "pct", ""),  # shown as 0.00%
        (Decimal("0.004"), "pct", "gain"),  # +0.40%, though it would round to €0.00
        (None, "money", ""),
        (None, "pct", ""),
    ],
)
def test_delta_class_follows_the_displayed_sign(value, kind, expected):
    assert delta_class(value, kind) == expected


def test_delta_class_defaults_to_money():
    assert delta_class(Decimal("-0.004")) == ""


def test_delta_class_rejects_an_unknown_kind():
    with pytest.raises(ValueError):
        delta_class(Decimal("1"), "qty")


# --- date ----------------------------------------------------------------------


def test_date_formats_date_as_iso():
    assert date_(date(2026, 10, 6)) == "2026-10-06"


def test_date_formats_datetime_by_its_date_part():
    assert date_(datetime(2026, 10, 6, 23, 59, tzinfo=timezone.utc)) == "2026-10-06"


# --- native_money (UI-8) -------------------------------------------------------


def test_native_money_renders_none_as_em_dash():
    assert native_money(None, "USD") == "—"


@pytest.mark.parametrize(
    ("value", "currency", "expected"),
    [
        (Decimal("1234.5"), "USD", "1,234.50 USD"),
        (Decimal("-1503.5"), "USD", f"{MINUS}1,503.50 USD"),
        (Decimal("0"), "EUR", "0.00 EUR"),
        (Decimal("-0.004"), "EUR", "0.00 EUR"),
        (Decimal("0.005"), "GBP", "0.01 GBP"),
        (Decimal("1234567.891"), "CHF", "1,234,567.89 CHF"),
        (12.3, "USD", "12.30 USD"),
    ],
)
def test_native_money_has_a_currency_code_suffix_and_no_plus_sign(value, currency, expected):
    assert native_money(value, currency) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("112.3400"), "112.34 USD"),
        (Decimal("112"), "112.00 USD"),
        (Decimal("0.12345"), "0.1235 USD"),
        (Decimal("0.12344"), "0.1234 USD"),
        (Decimal("1234.567"), "1,234.567 USD"),
        (Decimal("-5.1"), f"{MINUS}5.10 USD"),
    ],
)
def test_native_money_with_more_places_trims_zeros_past_two(value, expected):
    assert native_money(value, "USD", 4) == expected
