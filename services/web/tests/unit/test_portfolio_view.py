from datetime import date
from decimal import Decimal

from web.portfolio_client.base import Position, Return, UnmappedInstrument
from web.portfolio_view import (
    HoldingRow,
    PortfolioReturnPoint,
    aggregate_portfolio_returns,
    build_holdings,
    filter_open_positions,
    summarize_latest_returns,
    unmapped_for_broker,
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


# --- summarize_latest_returns ---------------------------------------------------


def test_summarize_empty_input_is_none():
    assert summarize_latest_returns([]) is None


def test_summarize_sums_every_ticker_and_dates_it_by_the_newest_price():
    latest = [
        _return(
            ticker="AAPL",
            price_date=date(2026, 10, 6),
            market_value_eur="1600",
            cost_basis_eur="1500",
            dividends_received_eur="10",
            total_return_eur="110",
            total_return_pct="0.07333333",
        ),
        # Stale: its last price is older. It still counts, at its last known value.
        _return(
            ticker="MSFT",
            price_date=date(2026, 10, 2),
            market_value_eur="900",
            cost_basis_eur="1000",
            total_return_eur="-100",
            total_return_pct="-0.1",
        ),
    ]

    summary = summarize_latest_returns(latest)

    assert summary == PortfolioReturnPoint(
        price_date=date(2026, 10, 6),
        market_value_eur=Decimal("2500"),
        cost_basis_eur=Decimal("2500"),
        dividends_received_eur=Decimal("10"),
        total_return_eur=Decimal("10"),
        total_return_pct=Decimal("10") / Decimal("2500"),
    )


def test_summarize_counts_a_closed_tickers_dividends():
    latest = [
        _return(
            ticker="AAPL",
            price_date=date(2026, 10, 6),
            market_value_eur="1000",
            cost_basis_eur="1000",
            total_return_eur="0",
            total_return_pct="0",
        ),
        _return(
            ticker="MSFT",
            price_date=date(2026, 5, 1),
            market_value_eur="0",
            cost_basis_eur="0",
            dividends_received_eur="25",
            total_return_eur="25",
            total_return_pct=None,
        ),
    ]

    summary = summarize_latest_returns(latest)

    assert summary.dividends_received_eur == Decimal("25")
    assert summary.total_return_eur == Decimal("25")
    assert summary.total_return_pct == Decimal("0.025")


def test_summarize_pct_is_none_when_cost_basis_is_zero():
    latest = [
        _return(
            price_date=date(2026, 5, 1),
            market_value_eur="0",
            cost_basis_eur="0",
            dividends_received_eur="5",
            total_return_eur="5",
            total_return_pct=None,
        )
    ]

    assert summarize_latest_returns(latest).total_return_pct is None


# --- build_holdings -------------------------------------------------------------


def _latest(ticker: str, market_value_eur: str, cost_basis_eur: str) -> Return:
    market_value, cost_basis = Decimal(market_value_eur), Decimal(cost_basis_eur)
    return Return(
        account_id=1,
        canonical_ticker=ticker,
        price_date=date(2026, 10, 6),
        quantity=Decimal("10"),
        market_value_native=market_value,
        market_value_eur=market_value,
        cost_basis_eur=cost_basis,
        unrealized_return_eur=market_value - cost_basis,
        unrealized_return_pct=None if cost_basis == 0 else (market_value - cost_basis) / cost_basis,
        dividends_received_eur=Decimal("0"),
        total_return_eur=market_value - cost_basis,
        total_return_pct=None,
    )


def test_build_holdings_joins_each_position_to_its_latest_return():
    positions = [_position(ticker="AAPL", quantity="10"), _position(ticker="MSFT", quantity="5")]
    latest = [_latest("MSFT", "1000", "500"), _latest("AAPL", "3000", "1000")]

    holdings = build_holdings(positions, latest)

    assert holdings == [
        HoldingRow(
            canonical_ticker="AAPL",
            quantity=Decimal("10"),
            average_cost_eur=Decimal("100"),
            market_value_eur=Decimal("3000"),
            weight=Decimal("0.75"),
            unrealized_return_eur=Decimal("2000"),
            unrealized_return_pct=Decimal("2"),
        ),
        HoldingRow(
            canonical_ticker="MSFT",
            quantity=Decimal("5"),
            average_cost_eur=Decimal("100"),
            market_value_eur=Decimal("1000"),
            weight=Decimal("0.25"),
            unrealized_return_eur=Decimal("500"),
            unrealized_return_pct=Decimal("1"),
        ),
    ]


def test_build_holdings_leaves_a_position_with_no_return_row_blank_and_out_of_the_weights():
    positions = [_position(ticker="AAPL"), _position(ticker="NEW")]
    latest = [_latest("AAPL", "1000", "1000")]

    aapl, new = build_holdings(positions, latest)

    assert aapl.weight == Decimal("1")
    assert new.canonical_ticker == "NEW"
    assert new.quantity == Decimal("10")
    assert new.average_cost_eur == Decimal("100")
    assert new.market_value_eur is None
    assert new.weight is None
    assert new.unrealized_return_eur is None
    assert new.unrealized_return_pct is None
    assert new.awaiting_prices is True
    assert aapl.awaiting_prices is False


def test_build_holdings_ignores_return_rows_for_tickers_not_held():
    # A closed ticker keeps a latest row; it isn't a holding and doesn't dilute the weights.
    positions = [_position(ticker="AAPL")]
    latest = [_latest("AAPL", "1000", "800"), _latest("SOLD", "0", "0")]

    holdings = build_holdings(positions, latest)

    assert [h.canonical_ticker for h in holdings] == ["AAPL"]
    assert holdings[0].weight == Decimal("1")


def test_build_holdings_weights_are_none_when_total_market_value_is_zero():
    positions = [_position(ticker="AAPL"), _position(ticker="MSFT")]
    latest = [_latest("AAPL", "0", "100"), _latest("MSFT", "0", "100")]

    holdings = build_holdings(positions, latest)

    assert [h.weight for h in holdings] == [None, None]
    assert holdings[0].market_value_eur == Decimal("0")


def test_build_holdings_keeps_full_precision_weights_for_display_rounding():
    positions = [_position(ticker=t) for t in ("A", "B", "C")]
    latest = [_latest(t, "100", "100") for t in ("A", "B", "C")]

    weights = [h.weight for h in build_holdings(positions, latest)]

    assert weights == [Decimal("100") / Decimal("300")] * 3


def test_build_holdings_empty_inputs():
    assert build_holdings([], []) == []
    assert build_holdings([], [_latest("AAPL", "1", "1")]) == []


# --- unmapped_for_broker (UI-7) ----------------------------------------------------------


def _unmapped(identifier: str, broker: str, kind: str = "ISIN") -> UnmappedInstrument:
    return UnmappedInstrument(kind=kind, identifier=identifier, broker=broker)


def test_unmapped_for_broker_keeps_only_the_accounts_broker_sorted():
    unmapped = [
        _unmapped("SGLD", "acme", kind="SYMBOL"),
        _unmapped("IE00B4L5Y983", "globex"),
        _unmapped("IE00BK5BQT80", "acme"),
    ]

    assert unmapped_for_broker(unmapped, "acme") == [
        _unmapped("IE00BK5BQT80", "acme"),
        _unmapped("SGLD", "acme", kind="SYMBOL"),
    ]


def test_unmapped_for_broker_with_nothing_unmapped():
    assert unmapped_for_broker([], "acme") == []


def test_unmapped_for_broker_with_no_known_broker_shows_nothing():
    # The account header degraded and the broker isn't known: no guess across brokers.
    assert unmapped_for_broker([_unmapped("IE00B4L5Y983", "acme")], None) == []
