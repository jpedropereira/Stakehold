"""risk_concentration_view (RK-23): what /risk's concentration section shows -- the coverage
split with the unmapped share and its funds, the currency table's two views side by side
(R16), each dimension's 10 heaviest known buckets with the rest held back, the top names, the
bars' widths, and what was left out."""

from datetime import date
from decimal import Decimal

from web.labs_view import DefaultMix
from web.portfolio_client.base import (
    LookThrough,
    LookThroughAsset,
    LookThroughBucket,
    LookThroughConcentration,
    LookThroughDimension,
    LookThroughExposures,
    LookThroughName,
    LookThroughNames,
    LookThroughSnapshot,
)
from web.risk_concentration_view import build_concentration_section

D = Decimal
MIX = DefaultMix(assets=(("IWDA.AS", D("0.7")),), unpriced=(), unweighted=(), dropped=())
SNAPSHOT = LookThroughSnapshot("IWDA.AS", "world", date(2026, 8, 30), 1324, D("100.03"))
NO_KNOWN = "no known exposure: every holding is unmapped or unknown"


def _concentration(**changes):
    fields = dict(
        top_shares={1: D("0.6"), 5: D("0.8"), 10: D("0.85")},
        known_share=D("0.85"),
        herfindahl=D("0.4"),
        effective_n=D("2.5"),
        reason=None,
    )
    return LookThroughConcentration(**{**fields, **changes})


def _dimension(label, *buckets, **concentration):
    return LookThroughDimension(
        label,
        tuple(LookThroughBucket(b, D(w), k) for b, w, k in buckets),
        _concentration(**concentration),
    )


def _asset(ticker, kind="look_through", weight="0.7", status="included", reason=None, note=None):
    return LookThroughAsset(
        ticker,
        status,
        reason,
        None if status != "included" else kind,
        None if status != "included" else D(weight),
        None,
        note,
    )


def _names(n=2):
    top = tuple(
        LookThroughName(f"ISSUER {i}", "United States", D("0.05") - D(i) / 1000, ("IWDA.AS",))
        for i in range(n)
    )
    return LookThroughNames(top, n_names=n + 300, concentration=_concentration())


def _exposures(**changes):
    fields = dict(
        snapshots=(SNAPSHOT,),
        coverage={
            "look_through": D("0.7"),
            "direct_matched": D("0.02"),
            "direct_unmatched": D("0"),
            "commodity": D("0.15"),
            "unmapped": D("0.13"),
        },
        dimensions={
            "currency": _dimension(
                "listing currency of holdings",
                ("USD", "0.678", "known"),
                ("EUR", "0.15", "known"),
                ("unmapped: VWCE.DE", "0.128", "unmapped"),
                ("JPY", "0.044", "known"),
            ),
            "trading_currency": _dimension(
                "trading currency of positions",
                ("EUR", "0.9", "known"),
                ("USD", "0.056", "known"),
                ("GBP", "0.044", "known"),
            ),
            "sector": _dimension("sector", ("Financials", "1", "known")),
            "country": _dimension("country", ("United States", "1", "known")),
            "asset_class": _dimension("asset class", ("Equity", "1", "known")),
        },
        names=_names(),
    )
    return LookThroughExposures(**{**fields, **changes})


def _look_through(assets=None, **changes):
    assets = assets or (
        _asset("IWDA.AS"),
        _asset("VWCE.DE", "unmapped", "0.13", note="no holdings data for this ETF"),
        _asset("EGLN.L", "commodity", "0.15"),
        _asset("BRK-B", "direct", "0.02"),
    )
    return LookThrough(assets=assets, exposures=_exposures(**changes))


def _with_dimension(key, dimension):
    exposures = _exposures()
    return LookThrough(
        assets=(_asset("IWDA.AS"),),
        exposures=LookThroughExposures(
            exposures.snapshots,
            exposures.coverage,
            {**exposures.dimensions, key: dimension},
            exposures.names,
        ),
    )


def test_coverage_lists_each_nonzero_share_in_a_fixed_order():
    section = build_concentration_section(_look_through(), MIX)

    assert [(share.key, share.weight) for share in section.coverage] == [
        ("look_through", D("0.7")),
        ("direct_matched", D("0.02")),
        ("commodity", D("0.15")),
        ("unmapped", D("0.13")),
    ]
    assert all(share.label for share in section.coverage)


def test_the_unmapped_share_names_each_fund_and_why():
    section = build_concentration_section(_look_through(), MIX)

    assert section.unmapped_share == D("0.13")
    assert [(u.ticker, u.weight, u.note) for u in section.unmapped] == [
        ("VWCE.DE", D("0.13"), "no holdings data for this ETF")
    ]


def test_nothing_unmapped_has_a_zero_share_and_no_funds():
    exposures = _exposures()
    coverage = {**exposures.coverage, "unmapped": D("0"), "look_through": D("0.83")}
    section = build_concentration_section(
        _look_through(assets=(_asset("IWDA.AS"),), coverage=coverage), MIX
    )

    assert section.unmapped_share == D("0")
    assert section.unmapped == ()
    assert "unmapped" not in [share.key for share in section.coverage]


def test_snapshots_pass_through_with_their_date():
    section = build_concentration_section(_look_through(), MIX)

    assert section.snapshots == (SNAPSHOT,)


def test_currency_table_puts_both_views_side_by_side():
    table = build_concentration_section(_look_through(), MIX).currency

    assert table.label == "listing currency of holdings"
    assert table.trading_label == "trading currency of positions"
    assert [(r.label, r.weight, r.trading) for r in table.rows] == [
        ("USD", D("0.678"), D("0.056")),
        ("EUR", D("0.15"), D("0.9")),
        ("unmapped: VWCE.DE", D("0.128"), D("0")),
        ("JPY", D("0.044"), D("0")),
        # Only in the trading view: no constituent is listed in GBP.
        ("GBP", D("0"), D("0.044")),
    ]
    assert table.rows[2].greyed
    assert not table.rows[0].greyed


def test_a_dimension_shows_ten_known_buckets_and_every_other_kind():
    buckets = [(f"Country {i:02}", "0.05", "known") for i in range(13)]
    buckets.insert(11, ("unmapped: VWCE.DE", "0.13", "unmapped"))
    buckets.append(("unknown", "0.01", "unknown"))
    section = build_concentration_section(
        _with_dimension("country", _dimension("country", *buckets)), MIX
    )

    country = next(t for t in section.dimensions if t.key == "country")
    assert [r.label for r in country.rows] == [
        *(f"Country {i:02}" for i in range(10)),
        "unmapped: VWCE.DE",
        "unknown",
    ]
    assert [r.label for r in country.more] == ["Country 10", "Country 11", "Country 12"]


def test_dimensions_are_sector_country_and_asset_class_with_their_labels():
    section = build_concentration_section(_look_through(), MIX)

    assert [(t.key, t.label) for t in section.dimensions] == [
        ("sector", "sector"),
        ("country", "country"),
        ("asset_class", "asset class"),
    ]
    assert all(t.trading_label is None for t in section.dimensions)
    assert all(r.trading is None for t in section.dimensions for r in t.rows)


def test_a_bar_is_its_share_of_the_whole_mix():
    rows = build_concentration_section(_look_through(), MIX).currency.rows

    # In percent of the mix, so bars compare across tables; the SVG's viewBox is 0 to 100.
    assert [r.bar for r in rows] == ["67.8", "15.0", "12.8", "4.4", "0.0"]


def test_concentration_passes_through_and_greys_with_its_reason():
    greyed = _dimension(
        "sector",
        ("unmapped: VWCE.DE", "1", "unmapped"),
        top_shares={1: D(0), 5: D(0), 10: D(0)},
        known_share=D(0),
        herfindahl=None,
        effective_n=None,
        reason=NO_KNOWN,
    )
    section = build_concentration_section(_with_dimension("sector", greyed), MIX)

    sector = next(t for t in section.dimensions if t.key == "sector")
    assert sector.concentration.reason == NO_KNOWN
    assert sector.concentration.effective_n is None
    assert section.currency.concentration.effective_n == D("2.5")


def test_names_show_ten_and_hold_back_the_rest():
    names = build_concentration_section(_look_through(names=_names(25)), MIX).names

    assert [r.issuer for r in names.rows] == [f"ISSUER {i}" for i in range(10)]
    assert [r.issuer for r in names.more] == [f"ISSUER {i}" for i in range(10, 25)]
    assert names.rows[0].sources == ("IWDA.AS",)
    assert names.rows[0].bar == "5.0"
    assert names.n_names == 325


def test_nothing_included_has_no_tables_but_says_what_was_left_out():
    lt = LookThrough(
        assets=(_asset("NOPE", status="not_tracked", reason="not tracked"),), exposures=None
    )

    section = build_concentration_section(lt, MIX)

    assert section.measured is False
    assert section.currency is None
    assert section.dimensions == ()
    assert section.names is None
    assert [(h.ticker, h.reason) for h in section.left_out] == [("NOPE", "not tracked")]


def test_left_out_holdings_are_portfolios_and_the_mixes_own():
    assets = (
        _asset("IWDA.AS"),
        _asset("NOPE", status="not_tracked", reason="not tracked"),
        _asset("AAPL", status="failed", reason="couldn't reach data-store"),
    )
    mix = DefaultMix(
        assets=MIX.assets, unpriced=("NEWCO.DE",), unweighted=("ZERO.DE",), dropped=("LAST.DE",)
    )

    section = build_concentration_section(_look_through(assets=assets), mix)

    assert [(h.ticker, h.reason) for h in section.left_out] == [
        ("NOPE", "not tracked"),
        ("AAPL", "couldn't reach data-store"),
        ("NEWCO.DE", "no price yet"),
        ("ZERO.DE", "worth nothing at today's price"),
        ("LAST.DE", "over the 50-holding limit"),
    ]
