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
    pct1,
    qty,
    ratio,
    share,
    signed_money,
    signed_pct1,
    signed_points1,
    signed_ratio,
)

MINUS = "−"


@pytest.mark.parametrize(
    "fmt",
    [
        money,
        signed_money,
        qty,
        pct,
        share,
        date_,
        pct1,
        signed_pct1,
        ratio,
        signed_points1,
        signed_ratio,
    ],
)
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
        "pct1": pct1,
        "signed_pct1": signed_pct1,
        "ratio": ratio,
        "signed_points1": signed_points1,
        "signed_ratio": signed_ratio,
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
        (0.0184, "pct1", "gain"),
        (-0.0184, "pct1", "loss"),
        (-0.0004, "pct1", ""),  # shown as 0.0%, though pct would show −0.04%
        (0.0005, "pct1", "gain"),  # +0.1%, half-up
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


# --- risk figures (RK-9): one decimal for percentages, two for ratios (spec §10) ------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.184, "18.4%"),
        (-0.184, f"{MINUS}18.4%"),  # a drawdown
        (0.0, "0.0%"),
        (-0.0004, "0.0%"),  # rounds to zero, so no sign
        (0.00049999, "0.0%"),
        (0.0005, "0.1%"),  # half-up
        (12.5, "1,250.0%"),
    ],
)
def test_pct1_is_an_unsigned_percentage_to_one_decimal(value, expected):
    assert pct1(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0712, "+7.1%"),
        (-0.0712, f"{MINUS}7.1%"),
        (0.0, "0.0%"),
        (-0.0004, "0.0%"),
        (0.0005, "+0.1%"),
    ],
)
def test_signed_pct1_carries_its_sign(value, expected):
    assert signed_pct1(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.8649, "0.86"),
        (-0.124, f"{MINUS}0.12"),
        (0.005, "0.01"),  # half-up
        (-0.004, "0.00"),  # rounds to zero, so no sign
        (12.0, "12.00"),
        (1234.5, "1,234.50"),
    ],
)
def test_ratio_has_two_places_and_no_plus(value, expected):
    # A ratio isn't a gain or a loss, so it gets no "+"; a negative one keeps its "−".
    assert ratio(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (-0.002, f"{MINUS}0.2 pts"),
        (0.0123, "+1.2 pts"),
        (0.0005, "+0.1 pts"),  # half-up
        (-0.0004, "0.0 pts"),  # rounds to zero, so no sign
    ],
)
def test_signed_points1_is_a_change_in_percentage_points(value, expected):
    # A what-if's Δ volatility (RK-20): "−0.2 pts", not "−0.2%", which reads as a relative change.
    assert signed_points1(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.09, "+0.09"),
        (-0.124, f"{MINUS}0.12"),
        (0.005, "+0.01"),  # half-up
        (-0.004, "0.00"),  # rounds to zero, so no sign
    ],
)
def test_signed_ratio_carries_its_sign(value, expected):
    assert signed_ratio(value) == expected
