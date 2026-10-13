"""The holding -> provider-ticker rules ingest and the read API share. Each normalizer case was
verified against real yfinance data, not guessed (see docs/architecture.md, "Ticker
resolution")."""

import logging

import pytest
from data_store.ticker_resolution import holding_symbol, resolve_yfinance_ticker


def test_resolve_yfinance_ticker_zero_pads_hong_kong_tickers():
    result = resolve_yfinance_ticker(
        "1", "Hong Kong Exchanges And Clearing Ltd", {"Hong Kong Exchanges And Clearing Ltd": ".HK"}
    )

    assert result == "0001.HK"


def test_resolve_yfinance_ticker_leaves_four_digit_hong_kong_tickers_unpadded():
    result = resolve_yfinance_ticker(
        "0700",
        "Hong Kong Exchanges And Clearing Ltd",
        {"Hong Kong Exchanges And Clearing Ltd": ".HK"},
    )

    assert result == "0700.HK"


def test_resolve_yfinance_ticker_dedupes_trailing_dot_for_london():
    result = resolve_yfinance_ticker(
        "BP.", "London Stock Exchange", {"London Stock Exchange": ".L"}
    )

    assert result == "BP.L"


def test_resolve_yfinance_ticker_leaves_non_dotted_london_tickers_alone():
    result = resolve_yfinance_ticker(
        "HSBA", "London Stock Exchange", {"London Stock Exchange": ".L"}
    )

    assert result == "HSBA.L"


def test_resolve_yfinance_ticker_hyphenates_mid_string_dot_for_london():
    # "BT.A" (BT Group's A shares) uses "." as a class-share separator, not a trailing
    # full-stop -- verified against real yfinance that this needs "-", not ".", same as the
    # Nordic/Toronto class-share cases.
    result = resolve_yfinance_ticker(
        "BT.A", "London Stock Exchange", {"London Stock Exchange": ".L"}
    )

    assert result == "BT-A.L"


@pytest.mark.parametrize(
    "exchange,suffix",
    [
        ("Nasdaq Omx Nordic", ".ST"),
        ("Nasdaq Omx Helsinki Ltd.", ".HE"),
        ("Omx Nordic Exchange Copenhagen A/S", ".CO"),
        ("Oslo Bors Asa", ".OL"),
    ],
)
def test_resolve_yfinance_ticker_hyphenates_space_for_nordic_exchanges(exchange, suffix):
    result = resolve_yfinance_ticker("VOLV B", exchange, {exchange: suffix})

    assert result == f"VOLV-B{suffix}"


def test_resolve_yfinance_ticker_hyphenates_dot_for_toronto():
    result = resolve_yfinance_ticker(
        "BBD.B", "Toronto Stock Exchange", {"Toronto Stock Exchange": ".TO"}
    )

    assert result == "BBD-B.TO"


def test_resolve_yfinance_ticker_leaves_other_exchanges_unnormalized():
    result = resolve_yfinance_ticker("AAPL", "NASDAQ", {"NASDAQ": None})

    assert result == "AAPL"


def test_resolve_yfinance_ticker_override_bypasses_exchange_resolution_entirely():
    # HEIA's real problem is a mistagged source exchange -- the override must win even when
    # the (wrong) exchange isn't in exchange_suffixes at all, which would otherwise warn+skip.
    result = resolve_yfinance_ticker("HEIA", "NASDAQ", {}, ticker_overrides={"HEIA": "HEIA.AS"})

    assert result == "HEIA.AS"


def test_resolve_yfinance_ticker_override_wins_even_when_exchange_resolves_fine():
    # BRKB's exchange (NYSE, suffix null) resolves fine on its own -- the override still wins,
    # since the raw ticker syntax itself ("BRKB") is what's wrong, not the exchange mapping.
    result = resolve_yfinance_ticker(
        "BRKB",
        "New York Stock Exchange Inc.",
        {"New York Stock Exchange Inc.": None},
        ticker_overrides={"BRKB": "BRK-B"},
    )

    assert result == "BRK-B"


def test_resolve_yfinance_ticker_no_override_falls_through_to_normal_resolution():
    result = resolve_yfinance_ticker(
        "AAPL", "NASDAQ", {"NASDAQ": None}, ticker_overrides={"BRKB": "BRK-B"}
    )

    assert result == "AAPL"


def test_resolve_yfinance_ticker_returns_none_for_an_unmapped_exchange_without_logging(caplog):
    # The read API resolves every holding on every call, so the warning belongs to the caller
    # that skips a fetch because of it -- not to the rule itself.
    caplog.set_level(logging.DEBUG)

    result = resolve_yfinance_ticker("0700", "Some Unmapped Exchange", {"NASDAQ": None})

    assert result is None
    assert caplog.records == []


@pytest.mark.parametrize("raw", [None, "", "   ", "-", " - "])
def test_holding_symbol_is_none_for_a_line_with_no_real_symbol(raw):
    assert holding_symbol(raw) is None


def test_holding_symbol_strips_a_real_symbol():
    assert holding_symbol(" HSBA ") == "HSBA"
