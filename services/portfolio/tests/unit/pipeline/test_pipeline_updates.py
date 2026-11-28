"""pipeline.update_transaction against the in-memory repository fakes in conftest.py: an edit
rewrites one row in place under the same key. An unchanged instrument or FX rate keeps the kind
and source it was recorded with, and a changed one is recorded as typed in. An edit that changes
nothing writes nothing."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from portfolio import pipeline
from portfolio.datastore_client.base import TickerInfo
from portfolio.errors import (
    AccountNotFound,
    InvalidTransaction,
    TransactionNotFound,
    UnknownTicker,
)
from portfolio.transactions.base import CanonicalTransaction

WHEN = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)


class FakeDataStoreClient:
    def __init__(self, ticker_info=None):
        self._ticker_info = ticker_info or {}
        self.ticker_info_calls = []

    def get_ticker_info(self, ticker):
        self.ticker_info_calls.append(ticker)
        return self._ticker_info.get(ticker, TickerInfo(exists=True, has_returns=True))


def _seed_account(accounts_repo, *, user_id, label="m"):
    accounts_repo.insert_account(None, user_id=user_id, broker="acme", account_label=label)
    return accounts_repo.get_by_label(None, user_id=user_id, account_label=label)


def _seed(transactions_repo, account, **overrides):
    values = dict(
        account_id=account.id,
        broker_transaction_ref="ref-1",
        transaction_type="BUY",
        trade_datetime=WHEN,
        instrument_identifier="IE00B4L5Y983",
        instrument_identifier_kind="ISIN",
        quantity=Decimal("10"),
        price=Decimal("50"),
        native_currency="USD",
        native_amount=Decimal("-500.0000"),
        eur_amount=Decimal("-462.9630"),
        fx_rate=Decimal("1.08"),
        fx_rate_source="broker_reported",
        fees_native=None,
    )
    values.update(overrides)
    txn = CanonicalTransaction(**values)
    transactions_repo.saved.append(txn)
    return txn


def _edit(conn, user_id, existing, *, datastore_client=None, **changes):
    """Submit `existing` back as an edit, with `changes` applied -- the way a form prefilled
    from the row sends every field. The amount is left blank by default, as the edit form
    leaves a BUY/SELL's amount blank for portfolio to derive."""
    fields = dict(
        transaction_type=existing.transaction_type,
        trade_datetime=existing.trade_datetime,
        native_amount=None,
        native_currency=existing.native_currency,
        instrument_identifier=existing.instrument_identifier,
        instrument_identifier_kind="TICKER",
        quantity=existing.quantity,
        price=existing.price,
        fx_rate=existing.fx_rate,
        fees_native=existing.fees_native,
    )
    fields.update(changes)
    return pipeline.update_transaction(
        conn,
        user_id=user_id,
        account_label="m",
        broker_transaction_ref=existing.broker_transaction_ref,
        datastore_client=datastore_client,
        **fields,
    )


def _only(transactions_repo):
    (txn,) = transactions_repo.saved
    return txn


def test_update_rewrites_the_row_in_place_and_rederives_a_buys_amount(repos, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    existing = _seed(transactions_repo, account)

    updated = _edit(conn, user_id, existing, quantity=Decimal("12"))

    assert updated == 1
    txn = _only(transactions_repo)
    assert txn.broker_transaction_ref == "ref-1"
    assert txn.quantity == Decimal("12")
    assert txn.native_amount == Decimal("-600.0000")
    assert txn.eur_amount == Decimal("-555.5556")
    assert conn.commits == 1


def test_an_unchanged_instrument_keeps_its_kind(repos, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    existing = _seed(transactions_repo, _seed_account(accounts_repo, user_id=user_id))

    _edit(conn, user_id, existing, price=Decimal("51"))

    assert _only(transactions_repo).instrument_identifier_kind == "ISIN"


def test_a_changed_instrument_takes_the_kind_sent_and_is_checked_with_data_store(repos, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    existing = _seed(transactions_repo, _seed_account(accounts_repo, user_id=user_id))
    client = FakeDataStoreClient()

    _edit(conn, user_id, existing, instrument_identifier="IWDA.AS", datastore_client=client)

    txn = _only(transactions_repo)
    assert (txn.instrument_identifier, txn.instrument_identifier_kind) == ("IWDA.AS", "TICKER")
    assert client.ticker_info_calls == ["IWDA.AS"]


def test_an_unchanged_ticker_is_not_checked_again(repos, conn):
    """A ticker whose loading has since failed must not block fixing the row's amount or date:
    it was checked when it was recorded."""
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    existing = _seed(
        transactions_repo,
        _seed_account(accounts_repo, user_id=user_id),
        instrument_identifier="GHOST",
        instrument_identifier_kind="TICKER",
    )
    client = FakeDataStoreClient(
        ticker_info={"GHOST": TickerInfo(exists=False, has_returns=False, status="failed")}
    )

    _edit(conn, user_id, existing, price=Decimal("51"), datastore_client=client)

    assert client.ticker_info_calls == []
    assert _only(transactions_repo).price == Decimal("51")


def test_a_changed_ticker_data_store_does_not_know_is_rejected(repos, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    existing = _seed(transactions_repo, _seed_account(accounts_repo, user_id=user_id))
    client = FakeDataStoreClient(ticker_info={"NOPE": TickerInfo(exists=False, has_returns=False)})

    with pytest.raises(UnknownTicker):
        _edit(conn, user_id, existing, instrument_identifier="NOPE", datastore_client=client)

    assert _only(transactions_repo) == existing


def test_an_unchanged_fx_rate_keeps_its_source(repos, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    existing = _seed(transactions_repo, _seed_account(accounts_repo, user_id=user_id))

    _edit(conn, user_id, existing, quantity=Decimal("11"))

    assert _only(transactions_repo).fx_rate_source == "broker_reported"


def test_a_changed_fx_rate_is_recorded_as_manual(repos, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    existing = _seed(transactions_repo, _seed_account(accounts_repo, user_id=user_id))

    _edit(conn, user_id, existing, fx_rate=Decimal("1.10"))

    txn = _only(transactions_repo)
    assert (txn.fx_rate, txn.fx_rate_source) == (Decimal("1.10"), "manual")


def test_the_same_rate_under_another_currency_is_recorded_as_manual(repos, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    existing = _seed(transactions_repo, _seed_account(accounts_repo, user_id=user_id))

    _edit(conn, user_id, existing, native_currency="GBP")

    assert _only(transactions_repo).fx_rate_source == "manual"


def test_switching_to_eur_drops_the_rate_and_its_source(repos, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    existing = _seed(transactions_repo, _seed_account(accounts_repo, user_id=user_id))

    _edit(conn, user_id, existing, native_currency="EUR", fx_rate=None)

    txn = _only(transactions_repo)
    assert (txn.fx_rate, txn.fx_rate_source, txn.eur_amount) == (None, None, txn.native_amount)


def test_an_edit_that_changes_nothing_writes_nothing(repos, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    existing = _seed(transactions_repo, _seed_account(accounts_repo, user_id=user_id))

    assert _edit(conn, user_id, existing) == 0
    assert _only(transactions_repo) == existing
    assert conn.commits == 0


def test_an_invalid_edit_is_rejected_and_leaves_the_row_alone(repos, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    existing = _seed(transactions_repo, _seed_account(accounts_repo, user_id=user_id))

    with pytest.raises(InvalidTransaction):
        _edit(conn, user_id, existing, quantity=Decimal("-3"))

    assert _only(transactions_repo) == existing
    assert conn.commits == 0


def test_a_cash_row_keeps_its_typed_amount(repos, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    existing = _seed(
        transactions_repo,
        _seed_account(accounts_repo, user_id=user_id),
        transaction_type="DEPOSIT",
        instrument_identifier=None,
        instrument_identifier_kind=None,
        quantity=None,
        price=None,
        native_currency="EUR",
        native_amount=Decimal("100.0000"),
        eur_amount=Decimal("100.0000"),
        fx_rate=None,
        fx_rate_source=None,
    )

    _edit(conn, user_id, existing, native_amount=Decimal("150"), instrument_identifier_kind=None)

    assert _only(transactions_repo).native_amount == Decimal("150")


def test_update_raises_for_an_unknown_reference(repos, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    existing = _seed(transactions_repo, _seed_account(accounts_repo, user_id=user_id))
    missing = CanonicalTransaction(**{**existing.__dict__, "broker_transaction_ref": "missing"})

    with pytest.raises(TransactionNotFound):
        _edit(conn, user_id, missing)


def test_update_raises_when_the_row_goes_before_the_write(repos, conn, monkeypatch):
    """Deleted from another tab between the read and the UPDATE: reported as not found rather
    than claimed as saved."""
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    existing = _seed(transactions_repo, _seed_account(accounts_repo, user_id=user_id))
    monkeypatch.setattr(transactions_repo, "update_one", lambda conn, txn: 0)

    with pytest.raises(TransactionNotFound):
        _edit(conn, user_id, existing, quantity=Decimal("12"))


def test_update_cannot_reach_another_users_account(repos, conn):
    accounts_repo, transactions_repo, _ = repos
    existing = _seed(transactions_repo, _seed_account(accounts_repo, user_id=uuid.uuid4()))

    with pytest.raises(AccountNotFound):
        _edit(conn, uuid.uuid4(), existing, quantity=Decimal("12"))

    assert _only(transactions_repo) == existing
