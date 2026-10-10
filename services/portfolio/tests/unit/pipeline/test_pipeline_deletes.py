"""pipeline.delete_transaction against the in-memory repository fakes in conftest.py: the
account lookup is the isolation chokepoint, the delete is its own transaction, and a delete
that matches nothing is an error rather than a quiet success."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from portfolio import pipeline
from portfolio.errors import AccountNotFound, TransactionNotFound
from portfolio.transactions.base import CanonicalTransaction


def _seed_account(accounts_repo, *, user_id, label="m", broker="acme"):
    accounts_repo.insert_account(None, user_id=user_id, broker=broker, account_label=label)
    return accounts_repo.get_by_label(None, user_id=user_id, account_label=label)


def _seed_txn(transactions_repo, account, ref):
    transactions_repo.saved.append(
        CanonicalTransaction(
            account_id=account.id,
            broker_transaction_ref=ref,
            transaction_type="DEPOSIT",
            trade_datetime=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            instrument_identifier=None,
            instrument_identifier_kind=None,
            quantity=None,
            price=None,
            native_currency="EUR",
            native_amount=Decimal("100.00"),
            eur_amount=Decimal("100.0000"),
            fx_rate=None,
            fx_rate_source=None,
            fees_native=None,
        )
    )


def _refs(transactions_repo):
    return sorted((t.account_id, t.broker_transaction_ref) for t in transactions_repo.saved)


def test_delete_transaction_removes_the_row_in_one_transaction(repos, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    _seed_txn(transactions_repo, account, "keep")
    _seed_txn(transactions_repo, account, "gone")

    pipeline.delete_transaction(
        conn, user_id=user_id, account_label="m", broker_transaction_ref="gone"
    )

    assert _refs(transactions_repo) == [(account.id, "keep")]
    assert conn.commits == 1


def test_delete_transaction_raises_for_an_unknown_reference(repos, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    _seed_txn(transactions_repo, account, "keep")

    with pytest.raises(TransactionNotFound):
        pipeline.delete_transaction(
            conn, user_id=user_id, account_label="m", broker_transaction_ref="missing"
        )

    assert _refs(transactions_repo) == [(account.id, "keep")]


def test_delete_transaction_raises_for_an_unknown_account(repos, conn):
    with pytest.raises(AccountNotFound):
        pipeline.delete_transaction(
            conn, user_id=uuid.uuid4(), account_label="nope", broker_transaction_ref="x"
        )

    assert conn.commits == 0


def test_delete_transaction_cannot_reach_another_users_account(repos, conn):
    """The label is resolved for the caller only, so someone else's account with the same label
    and the same reference is out of reach -- reported exactly like a missing account."""
    accounts_repo, transactions_repo, _ = repos
    owner = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=owner)
    _seed_txn(transactions_repo, account, "shared-ref")

    with pytest.raises(AccountNotFound):
        pipeline.delete_transaction(
            conn, user_id=uuid.uuid4(), account_label="m", broker_transaction_ref="shared-ref"
        )

    assert _refs(transactions_repo) == [(account.id, "shared-ref")]


def test_delete_transaction_leaves_the_same_ref_in_another_account_alone(repos, conn):
    """A reference is only unique within its account -- a content-hash ref can repeat across
    two accounts -- so the delete is scoped by account as well as by ref."""
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    a = _seed_account(accounts_repo, user_id=user_id, label="a")
    b = _seed_account(accounts_repo, user_id=user_id, label="b")
    _seed_txn(transactions_repo, a, "same")
    _seed_txn(transactions_repo, b, "same")

    pipeline.delete_transaction(
        conn, user_id=user_id, account_label="a", broker_transaction_ref="same"
    )

    assert _refs(transactions_repo) == [(b.id, "same")]
