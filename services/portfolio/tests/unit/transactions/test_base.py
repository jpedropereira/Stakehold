from datetime import UTC, datetime
from decimal import Decimal

import pytest
from portfolio.errors import InvalidTransaction
from portfolio.transactions.base import CanonicalTransaction, to_eur, validate

NOW = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)


def _txn(**overrides):
    values = dict(
        account_id=1,
        broker_transaction_ref="manual-1",
        transaction_type="BUY",
        trade_datetime=NOW,
        instrument_identifier="IE00B4L5Y983",
        instrument_identifier_kind="ISIN",
        quantity=Decimal("10"),
        price=Decimal("95.50"),
        native_currency="EUR",
        native_amount=Decimal("-955.00"),
        eur_amount=Decimal("-955.0000"),
        fx_rate=None,
        fx_rate_source=None,
        fees_native=None,
    )
    values.update(overrides)
    return CanonicalTransaction(**values)


# --- to_eur ---


def test_to_eur_passes_eur_amounts_through_unchanged():
    assert to_eur(Decimal("-955.00"), None) == Decimal("-955.0000")


def test_to_eur_divides_by_the_native_units_per_eur_rate():
    # fx_rate is native units per 1 EUR: USD 1.0850 per EUR -> EUR = native / rate.
    assert to_eur(Decimal("-1000.00"), Decimal("1.0850")) == Decimal("-921.6590")


def test_to_eur_rounds_half_up_to_four_decimal_places():
    assert to_eur(Decimal("100"), Decimal("3")) == Decimal("33.3333")


def test_to_eur_preserves_sign():
    assert to_eur(Decimal("1000.00"), Decimal("1.0850")) == Decimal("921.6590")


# --- validate: happy paths ---


def test_validate_accepts_a_well_formed_buy():
    validate(_txn())


def test_validate_accepts_a_cash_only_deposit():
    validate(
        _txn(
            transaction_type="DEPOSIT",
            instrument_identifier=None,
            instrument_identifier_kind=None,
            quantity=None,
            price=None,
            native_amount=Decimal("1000.00"),
            eur_amount=Decimal("1000.0000"),
        )
    )


def test_validate_accepts_a_non_eur_transaction_with_a_manual_rate():
    validate(
        _txn(
            native_currency="USD",
            native_amount=Decimal("-1000.00"),
            eur_amount=Decimal("-921.6590"),
            fx_rate=Decimal("1.0850"),
            fx_rate_source="manual",
        )
    )


def test_validate_accepts_a_sell_with_negative_quantity():
    validate(_txn(transaction_type="SELL", quantity=Decimal("-10")))


# --- validate: rejections ---


def test_validate_rejects_an_unrecognized_transaction_type():
    with pytest.raises(InvalidTransaction, match="transaction_type"):
        validate(_txn(transaction_type="TRANSFER"))


def test_validate_rejects_an_empty_ref():
    with pytest.raises(InvalidTransaction, match="broker_transaction_ref"):
        validate(_txn(broker_transaction_ref=""))


def test_validate_rejects_a_naive_datetime():
    with pytest.raises(InvalidTransaction, match="trade_datetime"):
        validate(_txn(trade_datetime=datetime(2026, 3, 1, 10, 0)))


def test_validate_rejects_an_identifier_without_a_kind():
    with pytest.raises(InvalidTransaction, match="instrument_identifier"):
        validate(_txn(instrument_identifier_kind=None))


def test_validate_rejects_a_kind_without_an_identifier():
    with pytest.raises(InvalidTransaction, match="instrument_identifier"):
        validate(_txn(instrument_identifier=None))


def test_validate_rejects_an_unrecognized_identifier_kind():
    with pytest.raises(InvalidTransaction, match="instrument_identifier_kind"):
        validate(_txn(instrument_identifier_kind="CUSIP"))


def test_validate_rejects_a_buy_missing_quantity():
    with pytest.raises(InvalidTransaction, match="quantity"):
        validate(_txn(quantity=None))


def test_validate_rejects_a_buy_missing_price():
    with pytest.raises(InvalidTransaction, match="price"):
        validate(_txn(price=None))


def test_validate_rejects_a_buy_with_non_positive_quantity():
    with pytest.raises(InvalidTransaction, match="quantity"):
        validate(_txn(quantity=Decimal("-10")))


def test_validate_rejects_a_sell_with_non_negative_quantity():
    with pytest.raises(InvalidTransaction, match="quantity"):
        validate(_txn(transaction_type="SELL", quantity=Decimal("10")))


def test_validate_rejects_a_lowercase_currency():
    with pytest.raises(InvalidTransaction, match="native_currency"):
        validate(_txn(native_currency="eur"))


def test_validate_rejects_a_currency_that_is_not_three_letters():
    with pytest.raises(InvalidTransaction, match="native_currency"):
        validate(_txn(native_currency="EURO"))


def test_validate_rejects_a_rate_without_a_source():
    with pytest.raises(InvalidTransaction, match="fx_rate"):
        validate(
            _txn(
                native_currency="USD",
                native_amount=Decimal("-1000.00"),
                eur_amount=Decimal("-921.6590"),
                fx_rate=Decimal("1.0850"),
                fx_rate_source=None,
            )
        )


def test_validate_rejects_a_source_without_a_rate():
    with pytest.raises(InvalidTransaction, match="fx_rate"):
        validate(_txn(fx_rate=None, fx_rate_source="manual"))


def test_validate_rejects_a_non_eur_transaction_missing_a_rate():
    with pytest.raises(InvalidTransaction, match="fx_rate"):
        validate(
            _txn(
                native_currency="USD",
                native_amount=Decimal("-1000.00"),
                eur_amount=Decimal("-1000.0000"),
            )
        )


def test_validate_rejects_an_unrecognized_fx_rate_source():
    with pytest.raises(InvalidTransaction, match="fx_rate_source"):
        validate(
            _txn(
                native_currency="USD",
                native_amount=Decimal("-1000.00"),
                eur_amount=Decimal("-921.6590"),
                fx_rate=Decimal("1.0850"),
                fx_rate_source="guessed",
            )
        )


def test_validate_rejects_a_non_positive_fx_rate():
    with pytest.raises(InvalidTransaction, match="fx_rate"):
        validate(
            _txn(
                native_currency="USD",
                native_amount=Decimal("-1000.00"),
                eur_amount=Decimal("-921.6590"),
                fx_rate=Decimal("0"),
                fx_rate_source="manual",
            )
        )


def test_validate_rejects_a_eur_transaction_that_carries_a_rate():
    with pytest.raises(InvalidTransaction, match="fx_rate"):
        validate(_txn(fx_rate=Decimal("1.0"), fx_rate_source="manual"))
