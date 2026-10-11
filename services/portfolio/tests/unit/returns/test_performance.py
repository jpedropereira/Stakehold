"""returns/performance.py tested as a pure function -- ReturnSnapshot rows and TradeFlow
sequences in, one time-weighted growth figure per date out.

The expected figures are worked by hand from the daily-return rule the module documents:
r_d = (MV_d + Div_d + Out_d - MV_{d-1} - In_d) / (MV_{d-1} + In_d), growth = prod(1 + r_d) - 1.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from portfolio.returns.calculator import ReturnSnapshot
from portfolio.returns.performance import GrowthPoint, TradeFlow, compute_growth

_EIGHT_PLACES = Decimal("0.00000001")

D1 = date(2026, 1, 5)  # a Monday
D2 = date(2026, 1, 6)
D3 = date(2026, 1, 7)
D4 = date(2026, 1, 8)
D5 = date(2026, 1, 9)
D6 = date(2026, 1, 12)


def _row(price_date, market_value_eur, *, ticker="IWDA.AS", dividends="0"):
    # Only market_value_eur, dividends_received_eur, the ticker and the date feed growth; the
    # rest are filled with plausible values so the rows look like real return_snapshots rows.
    mv = Decimal(market_value_eur)
    return ReturnSnapshot(
        account_id=1,
        canonical_ticker=ticker,
        price_date=price_date,
        quantity=Decimal("10.000000"),
        market_value_native=mv,
        market_value_eur=mv,
        cost_basis_eur=Decimal("1000.0000"),
        unrealized_return_eur=mv - Decimal("1000.0000"),
        unrealized_return_pct=None,
        dividends_received_eur=Decimal(dividends),
        total_return_eur=mv - Decimal("1000.0000") + Decimal(dividends),
        total_return_pct=None,
    )


def _buy(on, amount, *, ticker="IWDA.AS"):
    # transactions.eur_amount's own sign: money leaving the account's cash is negative.
    return TradeFlow(canonical_ticker=ticker, date=on, eur_amount=-Decimal(amount))


def _sell(on, amount, *, ticker="IWDA.AS"):
    return TradeFlow(canonical_ticker=ticker, date=on, eur_amount=Decimal(amount))


def _q(value):
    return Decimal(value).quantize(_EIGHT_PLACES, rounding=ROUND_HALF_UP)


def _growth(points):
    return [(p.price_date, p.growth_pct) for p in points]


def test_no_rows_means_no_points():
    assert compute_growth([], []) == []


def test_flows_alone_produce_no_points():
    assert compute_growth([], [_buy(D1, "1000")]) == []


def test_a_single_buy_grows_with_the_price():
    points = compute_growth([_row(D1, "1000"), _row(D2, "1100")], [_buy(D1, "1000")])

    assert points == [
        GrowthPoint(price_date=D1, growth_pct=_q("0")),
        GrowthPoint(price_date=D2, growth_pct=_q("0.1")),
    ]


def test_a_buy_fee_shows_as_a_loss_on_the_day():
    points = compute_growth([_row(D1, "1000")], [_buy(D1, "1001")])

    assert _growth(points) == [(D1, _q(Decimal("1000") / Decimal("1001") - 1))]


def test_a_later_larger_buy_does_not_move_the_line():
    rows = [_row(D1, "1000"), _row(D2, "1100"), _row(D3, "11100"), _row(D4, "11211")]
    flows = [_buy(D1, "1000"), _buy(D3, "10000")]

    points = compute_growth(rows, flows)

    # D3: (11100 - 1100 - 10000) / (1100 + 10000) = 0. D4: 1.1 * 1.01 - 1.
    assert _growth(points) == [
        (D1, _q("0")),
        (D2, _q("0.1")),
        (D3, _q("0.1")),
        (D4, _q("0.111")),
    ]


def test_selling_out_keeps_the_gain_and_a_rebuy_carries_on_from_it():
    # A sell-out's own row has a zero market value; the proceeds are money out, not a loss.
    rows = [
        _row(D1, "1000"),
        _row(D2, "1200"),
        _row(D3, "0"),
        _row(D5, "500"),
        _row(D6, "550"),
    ]
    flows = [_buy(D1, "1000"), _sell(D3, "1200"), _buy(D5, "500")]

    points = compute_growth(rows, flows)

    assert _growth(points) == [
        (D1, _q("0")),
        (D2, _q("0.2")),
        (D3, _q("0.2")),
        (D5, _q("0.2")),
        (D6, _q("0.32")),
    ]


def test_a_sale_for_more_than_the_last_value_is_a_gain_that_day():
    rows = [_row(D1, "1000"), _row(D2, "0")]
    flows = [_buy(D1, "1000"), _sell(D2, "1050")]

    assert _growth(compute_growth(rows, flows)) == [(D1, _q("0")), (D2, _q("0.05"))]


def test_a_dividend_counts_once_on_the_day_it_arrives():
    rows = [
        _row(D1, "1000"),
        _row(D2, "1000", dividends="20"),
        _row(D3, "1000", dividends="20"),
    ]

    points = compute_growth(rows, [_buy(D1, "1000")])

    # D3: the cumulative figure hasn't moved, so no new income. Base is still 1000.
    assert _growth(points) == [(D1, _q("0")), (D2, _q("0.02")), (D3, _q("0.02"))]


def test_a_weekend_trade_lands_on_the_tickers_next_priced_day():
    saturday = date(2026, 1, 3)

    points = compute_growth([_row(D1, "1000"), _row(D2, "1100")], [_buy(saturday, "1000")])

    assert _growth(points) == [(D1, _q("0")), (D2, _q("0.1"))]


def test_a_trade_after_the_tickers_last_row_is_ignored():
    # Its row doesn't exist yet, so no market value reflects it either.
    rows = [_row(D1, "1000"), _row(D2, "1100")]

    points = compute_growth(rows, [_buy(D1, "1000"), _buy(D3, "5000")])

    assert _growth(points) == [(D1, _q("0")), (D2, _q("0.1"))]


def test_a_flow_for_a_ticker_with_no_rows_is_ignored():
    # e.g. an untracked ticker: it has no market value anywhere to balance its flow against.
    rows = [_row(D1, "1000"), _row(D2, "1100")]
    flows = [_buy(D1, "1000"), _buy(D1, "700", ticker="NOPE")]

    assert _growth(compute_growth(rows, flows)) == [(D1, _q("0")), (D2, _q("0.1"))]


def test_a_flow_on_another_tickers_priced_day_waits_for_its_own():
    # VWCE isn't priced on D2, so its D2 buy lands on D3, the day its value first shows it.
    rows = [
        _row(D1, "1000"),
        _row(D2, "1000"),
        _row(D3, "1000"),
        _row(D3, "500", ticker="VWCE.DE"),
    ]
    flows = [_buy(D1, "1000"), _buy(D2, "500", ticker="VWCE.DE")]

    assert _growth(compute_growth(rows, flows)) == [
        (D1, _q("0")),
        (D2, _q("0")),
        (D3, _q("0")),
    ]


def test_a_ticker_missing_a_day_holds_its_last_value():
    rows = [
        _row(D1, "1000"),
        _row(D2, "1100"),
        _row(D3, "1100"),
        _row(D1, "1000", ticker="VWCE.DE"),
        _row(D3, "1100", ticker="VWCE.DE"),
    ]
    flows = [_buy(D1, "1000"), _buy(D1, "1000", ticker="VWCE.DE")]

    points = compute_growth(rows, flows)

    # D2: VWCE holds 1000, so 100 / 2000. D3: 100 / 2100. 1.05 * 2200 / 2100 - 1 = 0.1.
    assert _growth(points) == [(D1, _q("0")), (D2, _q("0.05")), (D3, _q("0.1"))]


def test_a_day_with_nothing_invested_holds_growth_flat():
    # No value on the previous day and no money in: there's nothing to divide by.
    rows = [_row(D1, "1000"), _row(D2, "0"), _row(D3, "0")]
    flows = [_buy(D1, "1000"), _sell(D2, "1000")]

    assert _growth(compute_growth(rows, flows)) == [
        (D1, _q("0")),
        (D2, _q("0")),
        (D3, _q("0")),
    ]


def test_input_order_does_not_matter():
    rows = [_row(D2, "1100"), _row(D1, "1000")]

    points = compute_growth(rows, [_buy(D1, "1000")])

    assert _growth(points) == [(D1, _q("0")), (D2, _q("0.1"))]


def test_growth_is_rounded_to_eight_places_only_at_the_end():
    # Two days of +1/3 compounded, then x4; rounding each day's growth would drift.
    rows = [_row(D1, "3"), _row(D2, "4"), _row(D3, "16"), _row(D4, "64")]
    flows = [_buy(D1, "3"), _buy(D3, "8")]

    points = compute_growth(rows, flows)

    # D3: (16 - 4 - 8) / (4 + 8) = 1/3. D4: (64 - 16) / 16 = 3.
    third = Decimal(1) / Decimal(3)
    expected_d3 = (1 + third) * (1 + third) - 1
    expected_d4 = (1 + third) * (1 + third) * 4 - 1
    assert _growth(points) == [
        (D1, _q("0")),
        (D2, _q(third)),
        (D3, _q(expected_d3)),
        (D4, _q(expected_d4)),
    ]
