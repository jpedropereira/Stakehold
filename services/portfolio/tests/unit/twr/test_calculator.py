"""twr/calculator.py tested as a pure function: every account's return_snapshots rows and each
ticker's daily EUR returns in, the portfolio's holdings-based daily TWR out (RK-10).

r_t = sum_i w_(i,t-1) x r_eur,i,t, where w is ticker i's share of the EUR market value at the
previous date of the union calendar. Every expected value is worked out by hand in the comment
beside it. Returns are fractions (0.02 is +2%); fx is units of the currency per 1 EUR.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from portfolio.datastore_client.base import FxPoint, PricePoint
from portfolio.eur_returns.calculator import eur_daily_returns
from portfolio.returns.calculator import ReturnSnapshot
from portfolio.twr.calculator import TwrPoint, TwrSeries, compute_twr

_EIGHT_PLACES = Decimal("0.00000001")

D1 = date(2026, 1, 5)  # a Monday
D2 = date(2026, 1, 6)
D3 = date(2026, 1, 7)
D4 = date(2026, 1, 8)
D5 = date(2026, 1, 9)


def _row(price_date, market_value_eur, *, ticker="A", account=1, dividends="0", quantity="10"):
    # Only market_value_eur feeds the weights. The rest are filled with plausible values so the
    # rows look like real return_snapshots rows.
    mv = Decimal(market_value_eur)
    return ReturnSnapshot(
        account_id=account,
        canonical_ticker=ticker,
        price_date=price_date,
        quantity=Decimal(quantity),
        market_value_native=mv,
        market_value_eur=mv,
        cost_basis_eur=Decimal("1000"),
        unrealized_return_eur=mv - Decimal("1000"),
        unrealized_return_pct=None,
        dividends_received_eur=Decimal(dividends),
        total_return_eur=mv - Decimal("1000") + Decimal(dividends),
        total_return_pct=None,
    )


def _eur(anchor, *points):
    """A EUR asset's returns: its anchor day, then (date, simple_return or None) per later day.
    Built through eur_daily_returns, so the TWR is tested on the shape it really receives."""
    prices = [PricePoint(anchor, Decimal("100"), None)] + [
        PricePoint(d, Decimal("100"), None if r is None else Decimal(r)) for d, r in points
    ]
    return eur_daily_returns(prices, None)


def _q(value):
    return Decimal(value).quantize(_EIGHT_PLACES, rounding=ROUND_HALF_UP)


def _returns(series):
    return [(p.price_date, p.twr_return) for p in series.points]


def _growth(series):
    return [(p.price_date, p.twr_growth) for p in series.points]


def test_no_rows_means_no_series():
    assert compute_twr([], {}, closed=set()) == TwrSeries(base_date=None, points=[], n_gaps=0)


def test_two_holdings_are_weighted_by_their_previous_close_market_value():
    # D1: A 600, B 400 (weights 0.6 / 0.4).
    # D2: A +10% -> 660, B -5% -> 380. r = 0.6 x 0.10 + 0.4 x -0.05 = 0.04.
    # D3: A 0%, B +5% -> 399. Weights 660/1040, 380/1040, so r = 380 x 0.05 / 1040 = 19/1040.
    # Growth: 1.04, then 1059/1000 = 1.059 (the market value's own growth, with no flows).
    snapshots = [
        _row(D1, "600"),
        _row(D2, "660"),
        _row(D3, "660"),
        _row(D1, "400", ticker="B"),
        _row(D2, "380", ticker="B"),
        _row(D3, "399", ticker="B"),
    ]
    returns = {
        "A": _eur(D1, (D2, "0.10"), (D3, "0")),
        "B": _eur(D1, (D2, "-0.05"), (D3, "0.05")),
    }

    series = compute_twr(snapshots, returns, closed=set())

    assert series == TwrSeries(
        base_date=D1,
        points=[
            TwrPoint(D2, _q("0.04"), _q("1.04")),
            TwrPoint(D3, _q(Decimal(19) / Decimal(1040)), _q("1.059")),
        ],
        n_gaps=0,
    )


def test_a_buy_mid_series_does_not_move_the_return():
    # D1: A 1000. D2: +2% -> 1020, then a buy of 500 -> 1520. D3: +1% -> 1535.2.
    # The buy only raises D2's market value; the returns are A's own: 0.02, then 0.01.
    # Growth: 1.02, then 1.02 x 1.01 = 1.0302.
    snapshots = [_row(D1, "1000"), _row(D2, "1520", quantity="15"), _row(D3, "1535.2")]
    returns = {"A": _eur(D1, (D2, "0.02"), (D3, "0.01"))}

    series = compute_twr(snapshots, returns, closed=set())

    assert _returns(series) == [(D2, _q("0.02")), (D3, _q("0.01"))]
    assert _growth(series) == [(D2, _q("1.02")), (D3, _q("1.0302"))]


def test_a_sell_only_changes_the_next_days_weights():
    # D1: A 500, B 500. D2: A +2%, B +4%, and all of B is sold that day (its D2 row is 0).
    # r(D2) = 0.5 x 0.02 + 0.5 x 0.04 = 0.03: B ran close to close on its last day.
    # D3: only A is held. r = 0.01, and B's +10% plays no part. Growth 1.03 x 1.01 = 1.0403.
    snapshots = [
        _row(D1, "500"),
        _row(D2, "510"),
        _row(D3, "515.1"),
        _row(D1, "500", ticker="B"),
        _row(D2, "0", ticker="B", quantity="0"),
    ]
    returns = {
        "A": _eur(D1, (D2, "0.02"), (D3, "0.01")),
        "B": _eur(D1, (D2, "0.04"), (D3, "0.10")),
    }

    series = compute_twr(snapshots, returns, closed={(1, "B")})

    assert _returns(series) == [(D2, _q("0.03")), (D3, _q("0.01"))]
    assert _growth(series) == [(D2, _q("1.03")), (D3, _q("1.0403"))]


def test_a_dividend_counts_on_its_ex_date_and_its_pay_date_shows_no_swing():
    # A pays a 2% dividend: ex-date D2, paid D4. The close doesn't move, so the price-only market
    # value stays 1000 throughout, and the 20 paid lands only in dividends_received_eur on D4.
    # simple_return is a total return, with the dividend on its ex-date: D2 +2%, D3 and D4 0%.
    # r = 0.02, 0, 0: the gain on the ex-date, and no false gain on the pay date.
    snapshots = [
        _row(D1, "1000"),
        _row(D2, "1000"),
        _row(D3, "1000"),
        _row(D4, "1000", dividends="20"),
    ]
    returns = {"A": _eur(D1, (D2, "0.02"), (D3, "0"), (D4, "0"))}

    series = compute_twr(snapshots, returns, closed=set())

    assert _returns(series) == [(D2, _q("0.02")), (D3, _q("0")), (D4, _q("0"))]
    assert _growth(series)[-1] == (D4, _q("1.02"))


def test_a_full_exit_leaves_the_empty_days_out_and_the_chain_resumes_on_re_entry():
    # D1: A 1000. D2: +1%, then sold (0). D3: still 0. D4: bought again, 800. D5: +2%.
    # D2: r = 0.01. D3 and D4: nothing was held at the previous close, so no point at all.
    # D5: r = 0.02 on the re-entered position. Growth 1.01, then 1.01 x 1.02 = 1.0302.
    snapshots = [
        _row(D1, "1000"),
        _row(D2, "0", quantity="0"),
        _row(D3, "0", quantity="0"),
        _row(D4, "800", quantity="8"),
        _row(D5, "816", quantity="8"),
    ]
    returns = {"A": _eur(D1, (D2, "0.01"), (D3, "0.03"), (D4, "0.05"), (D5, "0.02"))}

    series = compute_twr(snapshots, returns, closed=set())

    assert series == TwrSeries(
        base_date=D1,
        points=[TwrPoint(D2, _q("0.01"), _q("1.01")), TwrPoint(D5, _q("0.02"), _q("1.0302"))],
        n_gaps=0,
    )


def test_one_ticker_in_two_accounts_is_summed_before_weighting():
    # D1: A is 300 in account 1 and 700 in account 2, so 1000 in all; B is 1000 in account 1.
    # D2: A +10%, B 0%. r = 0.5 x 0.10 = 0.05. Keyed on the ticker alone, one account's row
    # would replace the other's and A's weight would be 300/1300 or 700/1700.
    snapshots = [
        _row(D1, "300"),
        _row(D2, "330"),
        _row(D1, "700", account=2),
        _row(D2, "770", account=2),
        _row(D1, "1000", ticker="B"),
        _row(D2, "1000", ticker="B"),
    ]
    returns = {"A": _eur(D1, (D2, "0.10")), "B": _eur(D1, (D2, "0"))}

    series = compute_twr(snapshots, returns, closed=set())

    assert _returns(series) == [(D2, _q("0.05"))]


def test_an_fx_move_on_a_usd_holding_counts_in_eur():
    # U is in USD. Its price is flat on D2, but USD strengthens: 1.10 -> 1.00 per EUR, so
    # r_eur = 1.10 / 1.00 - 1 = +10%. A (EUR) is flat. D1 weights 0.5 / 0.5: r = 0.05.
    prices = [PricePoint(D1, Decimal("100"), None), PricePoint(D2, Decimal("100"), Decimal("0"))]
    fx = [FxPoint(D1, Decimal("1.10")), FxPoint(D2, Decimal("1.00"))]
    snapshots = [
        _row(D1, "500"),
        _row(D2, "500"),
        _row(D1, "500", ticker="U"),
        _row(D2, "550", ticker="U"),
    ]
    returns = {"A": _eur(D1, (D2, "0")), "U": eur_daily_returns(prices, fx)}

    series = compute_twr(snapshots, returns, closed=set())

    assert _returns(series) == [(D2, _q("0.05"))]


def test_a_holding_whose_exchange_was_closed_carries_forward_and_its_next_return_has_the_move():
    # U (USD) doesn't trade on D2, when A (EUR) does; the union calendar is D1, D2, D3.
    # D2: A +4% -> 520; U has no row and no return, so it carries forward at 500 and adds 0.
    #     r = 0.5 x 0.04 = 0.02.
    # D3: U's return spans D1 to D3, FX move included: (1 + 0.02) x 1.10 / 1.00 - 1 = 0.122.
    #     Weights at D2: A 520/1020, U 500/1020. r = 500 x 0.122 / 1020 = 61/1020.
    prices = [
        PricePoint(D1, Decimal("100"), None),
        PricePoint(D3, Decimal("102"), Decimal("0.02")),
    ]
    fx = [FxPoint(D1, Decimal("1.10")), FxPoint(D2, Decimal("1.00")), FxPoint(D3, Decimal("1.00"))]
    snapshots = [
        _row(D1, "500"),
        _row(D2, "520"),
        _row(D3, "520"),
        _row(D1, "500", ticker="U"),
        _row(D3, "561", ticker="U"),
    ]
    returns = {"A": _eur(D1, (D2, "0.04"), (D3, "0")), "U": eur_daily_returns(prices, fx)}

    series = compute_twr(snapshots, returns, closed=set())

    assert _returns(series) == [(D2, _q("0.02")), (D3, _q(Decimal(61) / Decimal(1020)))]


def test_returns_between_two_calendar_dates_are_compounded_into_the_later_one():
    # A has a return on D2, but no snapshot row does (the snapshots predate D2's price). Its D2
    # and D3 returns, +10% each, both fall in (D1, D3]: r(D3) = 1.1 x 1.1 - 1 = 0.21.
    snapshots = [_row(D1, "1000"), _row(D3, "1210")]
    returns = {"A": _eur(D1, (D2, "0.10"), (D3, "0.10"))}

    series = compute_twr(snapshots, returns, closed=set())

    assert _returns(series) == [(D3, _q("0.21"))]


def test_a_null_return_on_a_held_ticker_makes_that_day_a_gap():
    # A's D2 return is NULL (R5): the day is a gap, never 0, and growth stays flat across it.
    # D3: +1% -> growth 1.01.
    snapshots = [_row(D1, "1000"), _row(D2, "1000"), _row(D3, "1010")]
    returns = {"A": _eur(D1, (D2, None), (D3, "0.01"))}

    series = compute_twr(snapshots, returns, closed=set())

    assert series == TwrSeries(
        base_date=D1,
        points=[TwrPoint(D2, None, _q("1")), TwrPoint(D3, _q("0.01"), _q("1.01"))],
        n_gaps=1,
    )


def test_a_null_return_on_a_ticker_not_held_at_the_previous_close_is_not_a_gap():
    # B was sold on D2 (row 0), so its NULL on D3 doesn't matter: r(D3) = A's 0.01.
    snapshots = [
        _row(D1, "500"),
        _row(D2, "500"),
        _row(D3, "505"),
        _row(D1, "500", ticker="B"),
        _row(D2, "0", ticker="B", quantity="0"),
    ]
    returns = {"A": _eur(D1, (D2, "0"), (D3, "0.01")), "B": _eur(D1, (D2, "0"), (D3, None))}

    series = compute_twr(snapshots, returns, closed={(1, "B")})

    assert series.n_gaps == 0
    assert _returns(series) == [(D2, _q("0")), (D3, _q("0.01"))]


def test_a_ticker_with_no_returns_is_excluded_and_the_rest_are_renormalised():
    # B has no EUR returns (e.g. no FX series), so it's left out of `returns`. D1: A 600, B 400.
    # A's weight is renormalised to 1: r(D2) = 0.05, not 0.6 x 0.05 = 0.03.
    snapshots = [
        _row(D1, "600"),
        _row(D2, "630"),
        _row(D1, "400", ticker="B"),
        _row(D2, "400", ticker="B"),
    ]
    returns = {"A": _eur(D1, (D2, "0.05"))}

    series = compute_twr(snapshots, returns, closed=set())

    assert _returns(series) == [(D2, _q("0.05"))]


def test_a_closed_pair_whose_last_row_is_not_zero_leaves_no_stale_weight():
    # B was sold on a day with no price row, after D1, so its last row (D1) still holds 500.
    # Forward-filled, B would keep that weight for good. It counts through its last row only:
    # D2: r = 0.5 x 0 + 0.5 x 0.02 = 0.01 (its last day runs close to close).
    # D3: only A is held, flat: r = 0, and B's +10% plays no part.
    snapshots = [
        _row(D1, "500"),
        _row(D2, "500"),
        _row(D3, "500"),
        _row(D1, "500", ticker="B"),
    ]
    returns = {
        "A": _eur(D1, (D2, "0"), (D3, "0")),
        "B": _eur(D1, (D2, "0.02"), (D3, "0.10")),
    }

    series = compute_twr(snapshots, returns, closed={(1, "B")})

    assert _returns(series) == [(D2, _q("0.01")), (D3, _q("0"))]


def test_an_open_pair_without_a_row_on_a_date_carries_forward():
    # The same rows, but B is still open: its D1 value carries forward, and its D3 return counts.
    # D3 weights: A 500/1000, B 500/1000 (forward-filled). r = 0.5 x 0.10 = 0.05.
    snapshots = [
        _row(D1, "500"),
        _row(D2, "500"),
        _row(D3, "500"),
        _row(D1, "500", ticker="B"),
    ]
    returns = {
        "A": _eur(D1, (D2, "0"), (D3, "0")),
        "B": _eur(D1, (D2, "0.02"), (D3, "0.10")),
    }

    series = compute_twr(snapshots, returns, closed=set())

    assert _returns(series) == [(D2, _q("0.01")), (D3, _q("0.05"))]


# --- days: the tickers behind each point, and whether they all traded on it (RK-11) ---
#
# Ex-post VaR keeps only the days on which every included asset's exchange traded (R7, §5.5).
# The holdings change over time, so that's judged per point: the tickers weighted at the previous
# date, each with a return row dated on the point itself.


def _days(series):
    return [(d.price_date, d.tickers, d.traded) for d in series.days]


def test_each_point_names_the_tickers_weighted_at_the_previous_date():
    # B is bought on D2 (no D1 row), so D2's point is A's alone and D3's is A's and B's.
    snapshots = [
        _row(D1, "1000"),
        _row(D2, "1000"),
        _row(D3, "1000"),
        _row(D2, "500", ticker="B"),
        _row(D3, "500", ticker="B"),
    ]
    returns = {"A": _eur(D1, (D2, "0"), (D3, "0")), "B": _eur(D1, (D2, "0"), (D3, "0"))}

    series = compute_twr(snapshots, returns, closed=set())

    assert _days(series) == [
        (D2, frozenset({"A"}), True),
        (D3, frozenset({"A", "B"}), True),
    ]


def test_a_day_on_which_a_held_ticker_has_no_row_did_not_trade():
    # The closed-exchange fixture: U has no price row on D2, so D2 is a carry-forward 0% for U
    # and fails the mask. D3 has a row for both.
    prices = [
        PricePoint(D1, Decimal("100"), None),
        PricePoint(D3, Decimal("102"), Decimal("0.02")),
    ]
    fx = [FxPoint(D1, Decimal("1.10")), FxPoint(D2, Decimal("1.00")), FxPoint(D3, Decimal("1.00"))]
    snapshots = [
        _row(D1, "500"),
        _row(D2, "520"),
        _row(D3, "520"),
        _row(D1, "500", ticker="U"),
        _row(D3, "561", ticker="U"),
    ]
    returns = {"A": _eur(D1, (D2, "0.04"), (D3, "0")), "U": eur_daily_returns(prices, fx)}

    series = compute_twr(snapshots, returns, closed=set())

    assert _days(series) == [
        (D2, frozenset({"A", "U"}), False),
        (D3, frozenset({"A", "U"}), True),
    ]


def test_returns_compounded_into_one_point_did_not_trade_on_it():
    # A's D2 and D3 returns both land on D3, so D3's return isn't one day's move.
    snapshots = [_row(D1, "1000"), _row(D3, "1210")]
    returns = {"A": _eur(D1, (D2, "0.10"), (D3, "0.10"))}

    series = compute_twr(snapshots, returns, closed=set())

    assert _days(series) == [(D3, frozenset({"A"}), False)]


def test_a_ticker_not_held_at_the_previous_date_does_not_affect_trading():
    # B was sold on D2, and has no price row on D3. Only A is weighted at D2, so D3 traded.
    snapshots = [
        _row(D1, "500"),
        _row(D2, "500"),
        _row(D3, "505"),
        _row(D1, "500", ticker="B"),
        _row(D2, "0", ticker="B", quantity="0"),
    ]
    returns = {"A": _eur(D1, (D2, "0"), (D3, "0.01")), "B": _eur(D1, (D2, "0"))}

    series = compute_twr(snapshots, returns, closed={(1, "B")})

    assert _days(series) == [
        (D2, frozenset({"A", "B"}), True),
        (D3, frozenset({"A"}), True),
    ]


def test_a_gap_day_still_traded():
    # A NULL return is a lost move on a day the exchange traded (R5): the measure drops it, not
    # the mask.
    snapshots = [_row(D1, "1000"), _row(D2, "1000"), _row(D3, "1010")]
    returns = {"A": _eur(D1, (D2, None), (D3, "0.01"))}

    series = compute_twr(snapshots, returns, closed=set())

    assert _days(series) == [(D2, frozenset({"A"}), True), (D3, frozenset({"A"}), True)]
