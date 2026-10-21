"""issuer_key: the single-name overlap rule (risk spec §8, RK-22). The cases are IWDA's own."""

import pytest
from portfolio.look_through.issuer import issuer_key


def test_share_classes_of_one_company_share_a_key():
    assert issuer_key("ALPHABET CLASS A", "United States") == issuer_key(
        "ALPHABET CLASS C", "United States"
    )


def test_the_key_is_the_name_without_its_share_class_and_the_location():
    assert issuer_key("BERKSHIRE HATHAWAY INC CLASS B", "United States") == (
        "BERKSHIRE HATHAWAY INC",
        "United States",
    )


def test_two_lines_with_one_name_in_one_country_merge():
    # Lindt's registered and participation shares, both "CHOCOLADEFABRIKEN LINDT & SPRUENGL".
    name = "CHOCOLADEFABRIKEN LINDT & SPRUENGL"
    assert issuer_key(name, "Switzerland") == issuer_key(name, "Switzerland")


def test_one_name_in_two_countries_is_two_issuers():
    # EQT Corp (US) and EQT AB (Sweden).
    assert issuer_key("EQT", "United States") != issuer_key("EQT", "Sweden")


@pytest.mark.parametrize(
    "name, expected",
    [
        ("SHOPIFY SUBORDINATE VOTING CLASS A", "SHOPIFY"),
        ("ALPHABET CLASS A", "ALPHABET"),
        ("Meta Platforms  Class A", "META PLATFORMS"),
        ("ROGERS COMMUNICATIONS NON-VOTING CLASS B", "ROGERS COMMUNICATIONS"),
        ("HENKEL PREF", "HENKEL"),
        ("SCHINDLER HOLDING PARTICIPATION CERTIFICATE", "SCHINDLER HOLDING"),
        ("CLASS ACTION CORP", "CLASS ACTION CORP"),  # only a trailing class is stripped
    ],
)
def test_share_class_suffixes_are_stripped(name, expected):
    assert issuer_key(name, "X")[0] == expected


def test_a_line_with_no_name_has_no_key():
    assert issuer_key(None, "United States") is None
    assert issuer_key("  ", "United States") is None
