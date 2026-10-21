"""The look-through calculator (risk spec §8, RK-22), on hand-computed fixtures.

The main fixture: a mix of 50% WORLD.AS (a EUR-listed world fund, looked through), 20% AAA held
directly (also a WORLD.AS constituent), 20% VWCE.DE (an ETF with no holdings data) and 10%
EGLN.L (a gold ETC listed in GBP). WORLD.AS's lines sum to 100:

    AAA CLASS A  USD  Tech         United States  40
    AAA CLASS C  USD  Tech         United States  10
    BBB          EUR  Financials   Germany        30
    CCC          JPY  Industrials  Japan          19
    USD CASH     USD  Cash...      United States   1   (asset class Cash)
    EUR/USD FX   EUR  Cash...      European Union  0   (asset class FX)
"""

from datetime import date
from decimal import Decimal

import pytest
from portfolio.datastore_client.base import EtfConstituent, EtfHoldings
from portfolio.look_through.calculator import (
    COMMODITY,
    DIRECT,
    LOOK_THROUGH,
    UNMAPPED,
    HeldAsset,
    compute_look_through,
)


def _line(ticker, yf, name, currency, sector, location, weight, asset_class="Equity"):
    return EtfConstituent(
        ticker=ticker,
        yfinance_ticker=yf,
        name=name,
        sector=sector,
        asset_class=asset_class,
        location=location,
        exchange="X",
        currency=currency,
        weight_percent=None if weight is None else Decimal(weight),
    )


WORLD = EtfHoldings(
    fund_key="world",
    ticker="WORLD.AS",
    as_of_date=date(2026, 8, 30),
    constituents=[
        _line("AAA", "AAA", "AAA CLASS A", "USD", "Tech", "United States", "40"),
        _line("AAAC", "AAAC", "AAA CLASS C", "USD", "Tech", "United States", "10"),
        _line("BBB", "BBB.DE", "BBB", "EUR", "Financials", "Germany", "30"),
        _line("CCC", "7203.T", "CCC", "JPY", "Industrials", "Japan", "19"),
        _line(
            "USD", None, "USD CASH", "USD", "Cash and/or Derivatives", "United States", "1", "Cash"
        ),
        _line(None, None, "EUR/USD", "EUR", "Cash and/or Derivatives", "European Union", "0", "FX"),
    ],
)

MIX = [
    HeldAsset("WORLD.AS", Decimal("0.5"), LOOK_THROUGH, listing_currency="EUR"),
    HeldAsset("AAA", Decimal("0.2"), DIRECT, listing_currency="USD"),
    HeldAsset("VWCE.DE", Decimal("0.2"), UNMAPPED, listing_currency="EUR"),
    HeldAsset("EGLN.L", Decimal("0.1"), COMMODITY, listing_currency="GBP"),
]


def _buckets(dimension):
    return {b.label: (b.weight, b.kind) for b in dimension.buckets}


@pytest.fixture
def result():
    return compute_look_through(MIX, {"WORLD.AS": WORLD})


def test_currency_is_the_constituents_listing_currency(result):
    assert _buckets(result.currency) == {
        # WORLD 0.5 x (40 + 10 + 1)% + AAA 0.2 + gold's USD 0.1
        "USD": (Decimal("0.555"), "known"),
        "EUR": (Decimal("0.15"), "known"),
        "JPY": (Decimal("0.095"), "known"),
        "unmapped: VWCE.DE": (Decimal("0.2"), "unmapped"),
    }


def test_buckets_are_heaviest_first_and_zero_weights_are_dropped(result):
    assert [b.label for b in result.currency.buckets] == [
        "USD",
        "unmapped: VWCE.DE",
        "EUR",
        "JPY",
    ]


def test_a_eur_listed_world_fund_is_mostly_usd_exposure():
    result = compute_look_through(
        [HeldAsset("WORLD.AS", Decimal("1"), LOOK_THROUGH, listing_currency="EUR")],
        {"WORLD.AS": WORLD},
    )
    assert _buckets(result.trading_currency) == {"EUR": (Decimal("1"), "known")}
    assert result.currency.buckets[0].label == "USD"
    assert result.currency.buckets[0].weight == Decimal("0.51")


def test_trading_currency_is_each_held_tickers_own_listing_currency(result):
    assert _buckets(result.trading_currency) == {
        "EUR": (Decimal("0.7"), "known"),
        "USD": (Decimal("0.2"), "known"),
        "GBP": (Decimal("0.1"), "known"),
    }


def test_sector_looks_through_and_names_the_commodity(result):
    assert _buckets(result.sector) == {
        # WORLD 0.5 x 50% + AAA 0.2, which takes its line's sector
        "Tech": (Decimal("0.45"), "known"),
        "Financials": (Decimal("0.15"), "known"),
        "Industrials": (Decimal("0.095"), "known"),
        "Cash and/or Derivatives": (Decimal("0.005"), "known"),
        "Commodity: gold": (Decimal("0.1"), "known"),
        "unmapped: VWCE.DE": (Decimal("0.2"), "unmapped"),
    }


def test_country_puts_a_commodity_outside_any_country(result):
    assert _buckets(result.country) == {
        "United States": (Decimal("0.455"), "known"),
        "Germany": (Decimal("0.15"), "known"),
        "Japan": (Decimal("0.095"), "known"),
        "n/a (commodity)": (Decimal("0.1"), "other"),
        "unmapped: VWCE.DE": (Decimal("0.2"), "unmapped"),
    }


def test_asset_class(result):
    assert _buckets(result.asset_class) == {
        "Equity": (Decimal("0.695"), "known"),
        "Cash": (Decimal("0.005"), "known"),
        "Commodity": (Decimal("0.1"), "known"),
        "unmapped: VWCE.DE": (Decimal("0.2"), "unmapped"),
    }


def test_every_dimension_sums_to_the_whole_mix(result):
    for dimension in (
        result.currency,
        result.trading_currency,
        result.sector,
        result.country,
        result.asset_class,
    ):
        assert sum(b.weight for b in dimension.buckets) == Decimal("1")


def test_a_stock_held_directly_and_through_the_fund_is_one_name(result):
    top = result.names.top[0]
    # Both share classes through WORLD (0.5 x 50%) plus the direct 0.2.
    assert (top.issuer, top.location, top.weight) == ("AAA", "United States", Decimal("0.45"))
    assert top.sources == ("AAA", "WORLD.AS")


def test_names_are_the_known_single_names_heaviest_first(result):
    assert [(n.issuer, n.weight) for n in result.names.top] == [
        ("AAA", Decimal("0.45")),
        ("BBB", Decimal("0.15")),
        ("Commodity: gold", Decimal("0.1")),
        ("CCC", Decimal("0.095")),
    ]
    assert result.names.n_names == 4


def test_names_known_share_leaves_out_unmapped_funds_and_cash_lines(result):
    assert result.names.concentration.known_share == Decimal("0.795")


def test_herfindahl_and_effective_n_are_over_the_known_share_only(result):
    concentration = result.currency.concentration
    assert concentration.known_share == Decimal("0.8")
    # (0.555/0.8)^2 + (0.15/0.8)^2 + (0.095/0.8)^2 = 0.69375^2 + 0.1875^2 + 0.11875^2
    assert concentration.herfindahl == Decimal("0.530546875")
    assert concentration.effective_n == pytest.approx(Decimal("1.8848476"), abs=Decimal("1e-7"))
    assert concentration.reason is None


def test_top_shares_are_of_the_whole_mix_over_known_buckets(result):
    assert result.currency.concentration.top_shares == {
        1: Decimal("0.555"),
        5: Decimal("0.8"),
        10: Decimal("0.8"),
    }


def test_a_mix_with_nothing_known_greys_the_concentration():
    result = compute_look_through(
        [HeldAsset("VWCE.DE", Decimal("1"), UNMAPPED, listing_currency="EUR")], {}
    )
    concentration = result.currency.concentration
    assert concentration.known_share == Decimal("0")
    assert concentration.herfindahl is None
    assert concentration.effective_n is None
    assert concentration.reason == "no known exposure: every holding is unmapped or unknown"
    assert result.names.top == ()


def test_a_direct_stock_outside_every_snapshot_is_unknown_but_keeps_its_currency():
    result = compute_look_through(
        [HeldAsset("BABA", Decimal("1"), DIRECT, listing_currency="USD")], {"WORLD.AS": WORLD}
    )
    assert _buckets(result.currency) == {"USD": (Decimal("1"), "known")}
    assert _buckets(result.sector) == {"unknown": (Decimal("1"), "unknown")}
    assert _buckets(result.country) == {"unknown": (Decimal("1"), "unknown")}
    assert _buckets(result.asset_class) == {"Equity": (Decimal("1"), "known")}
    name = result.names.top[0]
    assert (name.issuer, name.location, name.sources) == ("BABA", None, ("BABA",))
    assert result.assets[0].matched_in is None


def test_a_direct_stock_reports_the_fund_it_was_matched_in(result):
    assert {a.ticker: a.matched_in for a in result.assets} == {
        "WORLD.AS": None,
        "AAA": "WORLD.AS",
        "VWCE.DE": None,
        "EGLN.L": None,
    }


def test_a_direct_stock_with_no_listing_currency_is_unknown_currency():
    result = compute_look_through(
        [HeldAsset("ZZZ", Decimal("1"), DIRECT, listing_currency=None)], {}
    )
    assert _buckets(result.currency) == {"unknown": (Decimal("1"), "unknown")}
    assert _buckets(result.trading_currency) == {"unknown": (Decimal("1"), "unknown")}


def test_a_hedged_fund_is_kept_out_of_the_constituent_currencies():
    result = compute_look_through(
        [
            HeldAsset("WORLD.AS", Decimal("0.6"), LOOK_THROUGH, "EUR", hedged=True),
            HeldAsset("AAA", Decimal("0.4"), DIRECT, "USD"),
        ],
        {"WORLD.AS": WORLD},
    )
    assert _buckets(result.currency) == {
        "hedged: WORLD.AS": (Decimal("0.6"), "other"),
        "USD": (Decimal("0.4"), "known"),
    }
    # Only the currency view: the hedge doesn't change what the fund holds.
    assert _buckets(result.sector)["Financials"] == (Decimal("0.18"), "known")


def test_two_etcs_on_one_commodity_are_one_name():
    result = compute_look_through(
        [
            HeldAsset("EGLN.L", Decimal("0.5"), COMMODITY, "GBP"),
            HeldAsset("SGLD.MI", Decimal("0.3"), COMMODITY, "EUR"),
            HeldAsset("ISLN.L", Decimal("0.2"), COMMODITY, "USD"),
        ],
        {},
    )
    assert [(n.issuer, n.weight, n.sources) for n in result.names.top] == [
        ("Commodity: gold", Decimal("0.8"), ("EGLN.L", "SGLD.MI")),
        ("Commodity: silver", Decimal("0.2"), ("ISLN.L",)),
    ]
    assert _buckets(result.currency) == {"USD": (Decimal("1"), "known")}


def test_a_snapshot_is_rescaled_to_100_and_its_raw_sum_reported():
    fund = EtfHoldings(
        fund_key="f",
        ticker="F.AS",
        as_of_date=date(2026, 8, 30),
        constituents=[
            _line("A", "A", "A", "USD", "Tech", "United States", "60.03"),
            _line("B", "B", "B", "EUR", "Tech", "Germany", "40"),
            _line("C", "C", "C", "EUR", "Tech", "Germany", None),  # no weight: skipped
        ],
    )
    result = compute_look_through(
        [HeldAsset("F.AS", Decimal("1"), LOOK_THROUGH, "EUR")], {"F.AS": fund}
    )
    assert _buckets(result.currency) == {
        "USD": (Decimal("60.03") / Decimal("100.03"), "known"),
        "EUR": (Decimal("40") / Decimal("100.03"), "known"),
    }
    (snapshot,) = result.snapshots
    assert (snapshot.ticker, snapshot.fund_key, snapshot.as_of_date) == (
        "F.AS",
        "f",
        date(2026, 8, 30),
    )
    assert (snapshot.n_lines, snapshot.raw_weight_sum) == (3, Decimal("100.03"))


def test_missing_constituent_fields_are_unknown():
    fund = EtfHoldings(
        fund_key="f",
        ticker="F.AS",
        as_of_date=date(2026, 8, 30),
        constituents=[_line("A", None, None, None, None, None, "100", asset_class=None)],
    )
    result = compute_look_through(
        [HeldAsset("F.AS", Decimal("1"), LOOK_THROUGH, "EUR")], {"F.AS": fund}
    )
    for dimension in (result.currency, result.sector, result.country, result.asset_class):
        assert _buckets(dimension) == {"unknown": (Decimal("1"), "unknown")}
    assert result.names.top == ()


def test_a_look_through_asset_without_its_snapshot_raises():
    with pytest.raises(ValueError, match="WORLD.AS"):
        compute_look_through([HeldAsset("WORLD.AS", Decimal("1"), LOOK_THROUGH, "EUR")], {})


def test_only_the_top_25_names_are_listed_but_all_are_measured():
    lines = [
        _line(f"S{i}", f"S{i}", f"STOCK {i:02d}", "USD", "Tech", "United States", "2")
        for i in range(50)
    ]
    fund = EtfHoldings(
        fund_key="f", ticker="F.AS", as_of_date=date(2026, 8, 30), constituents=lines
    )
    result = compute_look_through(
        [HeldAsset("F.AS", Decimal("1"), LOOK_THROUGH, "EUR")], {"F.AS": fund}
    )
    assert len(result.names.top) == 25
    assert result.names.n_names == 50
    assert result.names.concentration.effective_n == Decimal("50")
    # Ties go by name.
    assert result.names.top[0].issuer == "STOCK 00"
