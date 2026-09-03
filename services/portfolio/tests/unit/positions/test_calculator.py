"""positions/calculator.py tested as a pure function -- no Postgres, no fakes, just
CanonicalTransaction sequences in and (PositionSnapshot, anomaly-message) sequences out.
"""

from datetime import UTC, datetime
from decimal import Decimal

from portfolio.positions.calculator import PositionSnapshot, replay
from portfolio.transactions.base import CanonicalTransaction

_D1 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
_D2 = datetime(2026, 2, 5, 10, 0, tzinfo=UTC)
_D3 = datetime(2026, 3, 5, 10, 0, tzinfo=UTC)


def _txn(**overrides):
    values = dict(
        account_id=1,
        broker_transaction_ref="ref-1",
        transaction_type="BUY",
        trade_datetime=_D1,
        instrument_identifier="IE00B4L5Y983",
        instrument_identifier_kind="ISIN",
        quantity=Decimal("10"),
        price=Decimal("95.00"),
        native_currency="EUR",
        native_amount=Decimal("-950.00"),
        eur_amount=Decimal("-950.0000"),
        fx_rate=None,
        fx_rate_source=None,
        fees_native=None,
    )
    values.update(overrides)
    return CanonicalTransaction(**values)


def test_replay_with_no_transactions_returns_nothing():
    snapshots, anomalies = replay(1, "IWDA.AS", [])

    assert snapshots == []
    assert anomalies == []


def test_replay_ignores_non_buy_sell_transaction_types():
    dividend = _txn(
        broker_transaction_ref="div-1",
        transaction_type="DIVIDEND",
        native_amount=Decimal("12.00"),
        eur_amount=Decimal("12.0000"),
        quantity=None,
        price=None,
    )

    snapshots, anomalies = replay(1, "IWDA.AS", [dividend])

    assert snapshots == []
    assert anomalies == []


def test_a_single_buy_produces_one_snapshot():
    snapshots, anomalies = replay(1, "IWDA.AS", [_txn()])

    assert anomalies == []
    assert snapshots == [
        PositionSnapshot(
            account_id=1,
            canonical_ticker="IWDA.AS",
            as_of_date=_D1.date(),
            quantity=Decimal("10.000000"),
            average_cost_native=Decimal("95.000000"),
            native_currency="EUR",
            average_cost_eur=Decimal("95.000000"),
            cost_basis_eur=Decimal("950.0000"),
        )
    ]


def test_two_buys_at_different_prices_blend_the_average_cost():
    first = _txn(broker_transaction_ref="ref-1", trade_datetime=_D1)
    second = _txn(
        broker_transaction_ref="ref-2",
        trade_datetime=_D2,
        quantity=Decimal("10"),
        price=Decimal("105.00"),
        native_amount=Decimal("-1050.00"),
        eur_amount=Decimal("-1050.0000"),
    )

    snapshots, anomalies = replay(1, "IWDA.AS", [first, second])

    assert anomalies == []
    assert len(snapshots) == 2
    second_snapshot = snapshots[1]
    assert second_snapshot.quantity == Decimal("20.000000")
    # (950 + 1050) / 20 = 100
    assert second_snapshot.average_cost_native == Decimal("100.000000")
    assert second_snapshot.average_cost_eur == Decimal("100.000000")
    assert second_snapshot.cost_basis_eur == Decimal("2000.0000")


def test_a_partial_sell_leaves_average_cost_unchanged():
    buy = _txn(broker_transaction_ref="ref-1", trade_datetime=_D1)
    sell = _txn(
        broker_transaction_ref="ref-2",
        transaction_type="SELL",
        trade_datetime=_D2,
        quantity=Decimal("-4"),
        price=Decimal("110.00"),
        native_amount=Decimal("440.00"),
        eur_amount=Decimal("440.0000"),
    )

    snapshots, anomalies = replay(1, "IWDA.AS", [buy, sell])

    assert anomalies == []
    sell_snapshot = snapshots[1]
    assert sell_snapshot.quantity == Decimal("6.000000")
    # Average cost is unchanged by a sell -- only the running cost shrinks proportionally.
    assert sell_snapshot.average_cost_native == Decimal("95.000000")
    assert sell_snapshot.average_cost_eur == Decimal("95.000000")
    assert sell_snapshot.cost_basis_eur == Decimal("570.0000")


def test_multiple_transactions_on_one_date_collapse_to_one_snapshot():
    first = _txn(broker_transaction_ref="ref-1", trade_datetime=_D1)
    second = _txn(
        broker_transaction_ref="ref-2",
        trade_datetime=_D1,
        quantity=Decimal("5"),
        price=Decimal("95.00"),
        native_amount=Decimal("-475.00"),
        eur_amount=Decimal("-475.0000"),
    )

    snapshots, anomalies = replay(1, "IWDA.AS", [first, second])

    assert anomalies == []
    assert len(snapshots) == 1
    assert snapshots[0].as_of_date == _D1.date()
    assert snapshots[0].quantity == Decimal("15.000000")


def test_selling_to_zero_resets_average_cost_and_still_emits_a_row():
    buy = _txn(broker_transaction_ref="ref-1", trade_datetime=_D1)
    sell = _txn(
        broker_transaction_ref="ref-2",
        transaction_type="SELL",
        trade_datetime=_D2,
        quantity=Decimal("-10"),
        price=Decimal("110.00"),
        native_amount=Decimal("1100.00"),
        eur_amount=Decimal("1100.0000"),
    )

    snapshots, anomalies = replay(1, "IWDA.AS", [buy, sell])

    assert anomalies == []
    close_snapshot = snapshots[1]
    assert close_snapshot.quantity == Decimal("0.000000")
    assert close_snapshot.average_cost_native == Decimal("0.000000")
    assert close_snapshot.average_cost_eur == Decimal("0.000000")
    assert close_snapshot.cost_basis_eur == Decimal("0.0000")


def test_a_rebuy_after_closing_starts_a_fresh_average_cost():
    buy1 = _txn(broker_transaction_ref="ref-1", trade_datetime=_D1)
    sell = _txn(
        broker_transaction_ref="ref-2",
        transaction_type="SELL",
        trade_datetime=_D2,
        quantity=Decimal("-10"),
        price=Decimal("110.00"),
        native_amount=Decimal("1100.00"),
        eur_amount=Decimal("1100.0000"),
    )
    buy2 = _txn(
        broker_transaction_ref="ref-3",
        trade_datetime=_D3,
        quantity=Decimal("5"),
        price=Decimal("120.00"),
        native_amount=Decimal("-600.00"),
        eur_amount=Decimal("-600.0000"),
    )

    snapshots, anomalies = replay(1, "IWDA.AS", [buy1, sell, buy2])

    assert anomalies == []
    final_snapshot = snapshots[2]
    assert final_snapshot.quantity == Decimal("5.000000")
    assert final_snapshot.average_cost_native == Decimal("120.000000")
    assert final_snapshot.average_cost_eur == Decimal("120.000000")


def test_a_non_eur_position_derives_average_cost_eur_from_eur_amount_not_fx_arithmetic():
    """Cost basis is sourced from native_amount/eur_amount directly -- the calculator never
    divides by fx_rate. eur_amount is already the frozen conversion done at transaction time."""
    buy = _txn(
        native_currency="USD",
        native_amount=Decimal("-1000.00"),
        eur_amount=Decimal("-925.9259"),
        fx_rate=Decimal("1.0800"),
        fx_rate_source="broker_reported",
    )

    snapshots, _ = replay(1, "IWDA.AS", [buy])

    assert snapshots[0].native_currency == "USD"
    assert snapshots[0].average_cost_native == Decimal("100.000000")
    assert snapshots[0].average_cost_eur == Decimal("92.592590")


def test_average_cost_eur_is_rate_blended_and_not_derivable_from_a_single_fx_rate():
    """Two non-EUR buys at different fx_rates: average_cost_eur blends the *eur_amount* history,
    not average_cost_native divided by either (or any single) fx_rate -- see docs/schema.md's
    note on the same instrument held in two currencies/two accounts."""
    first = _txn(
        broker_transaction_ref="ref-1",
        trade_datetime=_D1,
        native_currency="USD",
        native_amount=Decimal("-1000.00"),
        eur_amount=Decimal("-925.9259"),
        fx_rate=Decimal("1.0800"),
        fx_rate_source="broker_reported",
    )
    second = _txn(
        broker_transaction_ref="ref-2",
        trade_datetime=_D2,
        native_currency="USD",
        native_amount=Decimal("-1000.00"),
        eur_amount=Decimal("-1000.0000"),
        fx_rate=Decimal("1.0000"),
        fx_rate_source="broker_reported",
        quantity=Decimal("10"),
        price=Decimal("95.00"),
    )

    snapshots, anomalies = replay(1, "IWDA.AS", [first, second])

    assert anomalies == []
    final = snapshots[1]
    assert final.quantity == Decimal("20.000000")
    # average_cost_native is a plain native blend: (1000 + 1000) / 20 = 100.
    assert final.average_cost_native == Decimal("100.000000")
    # average_cost_eur blends the frozen eur_amounts instead: (925.9259 + 1000) / 20.
    expected_eur = (Decimal("925.9259") + Decimal("1000.0000")) / Decimal("20")
    assert final.average_cost_eur == expected_eur.quantize(Decimal("0.000001"))
    # Not derivable from average_cost_native by either transaction's own fx_rate.
    assert final.average_cost_eur != (final.average_cost_native / Decimal("1.0800"))
    assert final.average_cost_eur != (final.average_cost_native / Decimal("1.0000"))


def test_quantities_and_costs_are_quantized_at_their_column_precision():
    buy = _txn(
        quantity=Decimal("3"),
        price=Decimal("33.333333"),
        native_amount=Decimal("-99.999999"),
        eur_amount=Decimal("-99.9999"),
    )

    snapshots, _ = replay(1, "IWDA.AS", [buy])

    snapshot = snapshots[0]
    assert snapshot.quantity.as_tuple().exponent == -6
    assert snapshot.average_cost_native.as_tuple().exponent == -6
    assert snapshot.average_cost_eur.as_tuple().exponent == -6
    assert snapshot.cost_basis_eur.as_tuple().exponent == -4


def test_a_currency_change_within_one_account_is_reported_as_an_anomaly_but_still_computed():
    first = _txn(broker_transaction_ref="ref-1", trade_datetime=_D1, native_currency="EUR")
    second = _txn(
        broker_transaction_ref="ref-2",
        trade_datetime=_D2,
        native_currency="USD",
        fx_rate=Decimal("1.08"),
        fx_rate_source="broker_reported",
        eur_amount=Decimal("-879.6296"),
    )

    snapshots, anomalies = replay(1, "IWDA.AS", [first, second])

    assert len(anomalies) == 1
    assert "IWDA.AS" in anomalies[0]
    # Still computed -- a warning, not a block.
    assert len(snapshots) == 2
    assert snapshots[1].native_currency == "USD"


def test_a_negative_running_quantity_is_reported_as_an_anomaly_but_still_computed():
    oversell = _txn(
        transaction_type="SELL",
        quantity=Decimal("-10"),
        price=Decimal("95.00"),
        native_amount=Decimal("950.00"),
        eur_amount=Decimal("950.0000"),
    )

    snapshots, anomalies = replay(1, "IWDA.AS", [oversell])

    assert len(anomalies) == 1
    assert "IWDA.AS" in anomalies[0]
    assert snapshots[0].quantity == Decimal("-10.000000")
