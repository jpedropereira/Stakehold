"""saved_labs/base.py: what a lab may be saved as. Every message is shown to the user as-is (web
displays a 422's detail), so the tests pin the wording."""

from decimal import Decimal

import pytest
from portfolio.errors import InvalidSavedLab
from portfolio.saved_labs.base import (
    MAX_LAB_ASSETS,
    MAX_LAB_NAME,
    LabAsset,
    validate_lab,
)


def _assets(*pairs):
    return [LabAsset(ticker, Decimal(weight)) for ticker, weight in pairs]


def test_validate_lab_trims_the_name_and_ticker_and_keeps_the_order():
    name, assets = validate_lab(
        "  Core and satellite ", _assets((" VWCE.DE ", "60"), ("AAPL", "40"))
    )

    assert name == "Core and satellite"
    assert assets == (
        LabAsset("VWCE.DE", Decimal("60")),
        LabAsset("AAPL", Decimal("40")),
    )


def test_validate_lab_keeps_weights_as_typed_without_scaling():
    _, assets = validate_lab("Lab", _assets(("A", "60"), ("B", "60")))

    assert [a.weight_pct for a in assets] == [Decimal("60"), Decimal("60")]


@pytest.mark.parametrize("name", ["", "   "])
def test_validate_lab_rejects_a_blank_name(name):
    with pytest.raises(InvalidSavedLab, match="^Enter a name for the lab.$"):
        validate_lab(name, _assets(("A", "100")))


def test_validate_lab_accepts_a_name_at_the_limit_and_rejects_one_over():
    validate_lab("x" * MAX_LAB_NAME, _assets(("A", "100")))

    with pytest.raises(InvalidSavedLab, match="^A lab's name can be at most 100 characters.$"):
        validate_lab("x" * (MAX_LAB_NAME + 1), _assets(("A", "100")))


def test_validate_lab_rejects_an_empty_mix():
    with pytest.raises(InvalidSavedLab, match="^A lab needs at least one asset.$"):
        validate_lab("Lab", [])


def test_validate_lab_accepts_the_asset_cap_and_rejects_one_over():
    at_cap = _assets(*((f"T{i}", "1") for i in range(MAX_LAB_ASSETS)))
    validate_lab("Lab", at_cap)

    over = _assets(*((f"T{i}", "1") for i in range(MAX_LAB_ASSETS + 1)))
    with pytest.raises(InvalidSavedLab, match="^A lab can have at most 50 assets.$"):
        validate_lab("Lab", over)


@pytest.mark.parametrize("ticker", ["", "  ", "X" * 21])
def test_validate_lab_rejects_a_blank_or_over_long_ticker(ticker):
    with pytest.raises(InvalidSavedLab, match="^Each ticker must be 1 to 20 characters.$"):
        validate_lab("Lab", _assets((ticker, "100")))


def test_validate_lab_rejects_a_repeated_ticker():
    with pytest.raises(InvalidSavedLab, match="^AAPL is in the lab twice.$"):
        validate_lab("Lab", _assets(("AAPL", "50"), ("VWCE.DE", "25"), ("AAPL", "25")))


@pytest.mark.parametrize("weight", ["0", "-1", "NaN", "Infinity"])
def test_validate_lab_rejects_a_weight_that_is_not_a_positive_number(weight):
    with pytest.raises(InvalidSavedLab, match="^Each weight must be above 0%.$"):
        validate_lab("Lab", _assets(("A", weight)))


def test_validate_lab_accepts_six_decimal_places_and_rejects_seven():
    validate_lab("Lab", _assets(("A", "33.333333")))

    with pytest.raises(InvalidSavedLab, match="^A weight can have at most 6 decimal places.$"):
        validate_lab("Lab", _assets(("A", "33.3333333")))


def test_validate_lab_rejects_a_weight_the_column_cannot_hold():
    validate_lab("Lab", _assets(("A", "999999.999999")))

    with pytest.raises(InvalidSavedLab, match="^A weight must be under 1,000,000%.$"):
        validate_lab("Lab", _assets(("A", "1000000")))
