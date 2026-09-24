from datetime import date
from decimal import Decimal

from web.portfolio_client.base import Position, Return
from web.portfolio_view import (
    PortfolioReturnPoint,
    aggregate_portfolio_returns,
    filter_open_positions,
)


def _position(*, ticker: str = "AAPL", quantity: str = "10") -> Position:
    return Position(
        account_id=1,
        canonical_ticker=ticker,
        as_of_date=date(2026, 1, 1),
        quantity=Decimal(quantity),
        average_cost_native=Decimal("100"),
        native_currency="EUR",
        average_cost_eur=Decimal("100"),
        cost_basis_eur=Decimal(quantity) * Decimal("100"),
    )


def _return(
    *,
    ticker: str = "AAPL",
    price_date: date,
    market_value_eur: str,
    cost_basis_eur: str,
    dividends_received_eur: str = "0",
    total_return_eur: str,
    total_return_pct: str | None,
) -> Return:
    market_value = Decimal(market_value_eur)
    cost_basis = Decimal(cost_basis_eur)
    return Return(
        account_id=1,
        canonical_ticker=ticker,
        price_date=price_date,
        quantity=Decimal("10"),
        market_value_native=market_value,
        market_value_eur=market_value,
        cost_basis_eur=cost_basis,
        unrealized_return_eur=market_value - cost_basis,
        unrealized_return_pct=None,
        dividends_received_eur=Decimal(dividends_received_eur),
        total_return_eur=Decimal(total_return_eur),
        total_return_pct=None if total_return_pct is None else Decimal(total_return_pct),
    )


# --- filter_open_positions ----------------------------------------------------


def test_filter_open_positions_drops_closed_positions():
    open_position = _position(ticker="AAPL", quantity="10")
    closed_position = _position(ticker="MSFT", quantity="0")

    result = filter_open_positions([open_position, closed_position])

    assert result == [open_position]


def test_filter_open_positions_keeps_negative_quantity():
    # A replay anomaly (positions/calculator.py warns but still computes it), not a modeled
    # short position -- it should stay visible, not be silently hidden as if it were closed.
    anomalous = _position(ticker="AAPL", quantity="-5")

    result = filter_open_positions([anomalous])

    assert result == [anomalous]


def test_filter_open_positions_empty_input():
    assert filter_open_positions([]) == []


# --- aggregate_portfolio_returns ------------------------------------------------


def test_aggregate_empty_input_returns_empty_list():
    assert aggregate_portfolio_returns([]) == []


def test_aggregate_single_ticker_single_date_passes_through():
    row = _return(
        price_date=date(2026, 1, 2),
        market_value_eur="1000",
        cost_basis_eur="900",
        total_return_eur="100",
        total_return_pct="0.11111111",
    )

    result = aggregate_portfolio_returns([row])

    assert result == [
        PortfolioReturnPoint(
            price_date=date(2026, 1, 2),
            market_value_eur=Decimal("1000"),
            cost_basis_eur=Decimal("900"),
            dividends_received_eur=Decimal("0"),
            total_return_eur=Decimal("100"),
            total_return_pct=Decimal("100") / Decimal("900"),
        )
    ]


def test_aggregate_single_ticker_multiple_dates():
    rows = [
        _return(
            price_date=date(2026, 1, 2),
            market_value_eur="1000",
            cost_basis_eur="900",
            total_return_eur="100",
            total_return_pct=None,
        ),
        _return(
            price_date=date(2026, 1, 3),
            market_value_eur="1050",
            cost_basis_eur="900",
            total_return_eur="150",
            total_return_pct=None,
        ),
    ]

    result = aggregate_portfolio_returns(rows)

    assert [point.price_date for point in result] == [date(2026, 1, 2), date(2026, 1, 3)]
    assert [point.total_return_eur for point in result] == [Decimal("100"), Decimal("150")]


def test_aggregate_two_tickers_identical_calendars_sums_values():
    day = date(2026, 1, 2)
    rows = [
        _return(
            ticker="AAPL",
            price_date=day,
            market_value_eur="1000",
            cost_basis_eur="900",
            total_return_eur="100",
            total_return_pct=None,
        ),
        _return(
            ticker="MSFT",
            price_date=day,
            market_value_eur="500",
            cost_basis_eur="400",
            total_return_eur="100",
            total_return_pct=None,
        ),
    ]

    result = aggregate_portfolio_returns(rows)

    assert result == [
        PortfolioReturnPoint(
            price_date=day,
            market_value_eur=Decimal("1500"),
            cost_basis_eur=Decimal("1300"),
            dividends_received_eur=Decimal("0"),
            total_return_eur=Decimal("200"),
            total_return_pct=Decimal("200") / Decimal("1300"),
        )
    ]


def test_aggregate_staggered_calendars_no_phantom_zero_before_first_row():
    # AAPL starts trading on day 1; MSFT position wasn't opened/priced until day 2.
    day1, day2 = date(2026, 1, 2), date(2026, 1, 3)
    rows = [
        _return(
            ticker="AAPL",
            price_date=day1,
            market_value_eur="1000",
            cost_basis_eur="900",
            total_return_eur="100",
            total_return_pct=None,
        ),
        _return(
            ticker="AAPL",
            price_date=day2,
            market_value_eur="1010",
            cost_basis_eur="900",
            total_return_eur="110",
            total_return_pct=None,
        ),
        _return(
            ticker="MSFT",
            price_date=day2,
            market_value_eur="500",
            cost_basis_eur="400",
            total_return_eur="100",
            total_return_pct=None,
        ),
    ]

    result = aggregate_portfolio_returns(rows)

    assert result[0] == PortfolioReturnPoint(
        price_date=day1,
        market_value_eur=Decimal("1000"),
        cost_basis_eur=Decimal("900"),
        dividends_received_eur=Decimal("0"),
        total_return_eur=Decimal("100"),
        total_return_pct=Decimal("100") / Decimal("900"),
    )
    assert result[1] == PortfolioReturnPoint(
        price_date=day2,
        market_value_eur=Decimal("1510"),
        cost_basis_eur=Decimal("1300"),
        dividends_received_eur=Decimal("0"),
        total_return_eur=Decimal("210"),
        total_return_pct=Decimal("210") / Decimal("1300"),
    )


def test_aggregate_forward_fills_ticker_missing_on_a_union_date():
    # MSFT has no row on day2 (e.g. a market holiday) -- its day1 row should carry forward,
    # not drop out or count as zero.
    day1, day2, day3 = date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 4)
    rows = [
        _return(
            ticker="AAPL",
            price_date=day1,
            market_value_eur="1000",
            cost_basis_eur="900",
            total_return_eur="100",
            total_return_pct=None,
        ),
        _return(
            ticker="AAPL",
            price_date=day2,
            market_value_eur="1005",
            cost_basis_eur="900",
            total_return_eur="105",
            total_return_pct=None,
        ),
        _return(
            ticker="AAPL",
            price_date=day3,
            market_value_eur="1010",
            cost_basis_eur="900",
            total_return_eur="110",
            total_return_pct=None,
        ),
        _return(
            ticker="MSFT",
            price_date=day1,
            market_value_eur="500",
            cost_basis_eur="400",
            total_return_eur="100",
            total_return_pct=None,
        ),
        _return(
            ticker="MSFT",
            price_date=day3,
            market_value_eur="520",
            cost_basis_eur="400",
            total_return_eur="120",
            total_return_pct=None,
        ),
    ]

    result = aggregate_portfolio_returns(rows)

    assert [point.price_date for point in result] == [day1, day2, day3]
    # day2: AAPL's own day2 row (1005) + MSFT's forward-filled day1 row (500)
    assert result[1].market_value_eur == Decimal("1505")
    assert result[1].total_return_eur == Decimal("205")
    # day3: both tickers have their own row
    assert result[2].market_value_eur == Decimal("1530")
    assert result[2].total_return_eur == Decimal("230")


def test_aggregate_closed_ticker_forward_fills_its_zeroed_last_row():
    # AAPL fully sold after day1 -- portfolio/returns/calculator.py's own last row for a closed
    # position already carries market_value_eur=0/cost_basis_eur=0/total_return_eur=0, so
    # forward-filling it past that point should need no special-casing.
    day1, day2 = date(2026, 1, 2), date(2026, 1, 3)
    rows = [
        _return(
            ticker="AAPL",
            price_date=day1,
            market_value_eur="0",
            cost_basis_eur="0",
            total_return_eur="0",
            total_return_pct=None,
        ),
        _return(
            ticker="MSFT",
            price_date=day1,
            market_value_eur="500",
            cost_basis_eur="400",
            total_return_eur="100",
            total_return_pct=None,
        ),
        _return(
            ticker="MSFT",
            price_date=day2,
            market_value_eur="520",
            cost_basis_eur="400",
            total_return_eur="120",
            total_return_pct=None,
        ),
    ]

    result = aggregate_portfolio_returns(rows)

    assert result[1].market_value_eur == Decimal("520")
    assert result[1].cost_basis_eur == Decimal("400")
    assert result[1].total_return_eur == Decimal("120")


def test_aggregate_recomputes_pct_from_summed_amounts_not_average_of_per_ticker_pct():
    # AAPL: 100/900 (~11.1%), MSFT: 300/400 (75%) -- a naive average would be ~43%; the correct
    # portfolio pct is the summed eur over the summed cost basis: 400/1300 (~30.77%).
    day = date(2026, 1, 2)
    rows = [
        _return(
            ticker="AAPL",
            price_date=day,
            market_value_eur="1000",
            cost_basis_eur="900",
            total_return_eur="100",
            total_return_pct="0.11111111",
        ),
        _return(
            ticker="MSFT",
            price_date=day,
            market_value_eur="700",
            cost_basis_eur="400",
            total_return_eur="300",
            total_return_pct="0.75",
        ),
    ]

    result = aggregate_portfolio_returns(rows)

    assert result[0].total_return_pct == Decimal("400") / Decimal("1300")


def test_aggregate_pct_is_none_when_summed_cost_basis_is_zero():
    day = date(2026, 1, 2)
    rows = [
        _return(
            ticker="AAPL",
            price_date=day,
            market_value_eur="0",
            cost_basis_eur="0",
            total_return_eur="0",
            total_return_pct=None,
        ),
        _return(
            ticker="MSFT",
            price_date=day,
            market_value_eur="0",
            cost_basis_eur="0",
            total_return_eur="0",
            total_return_pct=None,
        ),
    ]

    result = aggregate_portfolio_returns(rows)

    assert result[0].total_return_pct is None


def test_aggregate_sorts_out_of_order_input():
    day1, day2 = date(2026, 1, 2), date(2026, 1, 3)
    rows = [
        _return(
            price_date=day2,
            market_value_eur="1050",
            cost_basis_eur="900",
            total_return_eur="150",
            total_return_pct=None,
        ),
        _return(
            price_date=day1,
            market_value_eur="1000",
            cost_basis_eur="900",
            total_return_eur="100",
            total_return_pct=None,
        ),
    ]

    result = aggregate_portfolio_returns(rows)

    assert [point.price_date for point in result] == [day1, day2]
