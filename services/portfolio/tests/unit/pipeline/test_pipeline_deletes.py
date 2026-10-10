"""pipeline.delete_transaction and pipeline.delete_account against the in-memory repository
fakes in conftest.py: the account lookup is the isolation chokepoint, the delete is its own
transaction, and a delete that matches nothing is an error rather than a quiet success. An
account delete also takes every row derived from the account with it, in the same transaction."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from portfolio import pipeline
from portfolio.errors import AccountNotFound, TransactionNotFound
from portfolio.positions.calculator import PositionSnapshot
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


# --- delete_account ---


def _seed_position(positions_repo, account, ticker):
    positions_repo.snapshots[(account.id, ticker)] = [
        PositionSnapshot(
            account_id=account.id,
            canonical_ticker=ticker,
            as_of_date=date(2026, 3, 1),
            quantity=Decimal("10"),
            average_cost_native=Decimal("95"),
            native_currency="EUR",
            average_cost_eur=Decimal("95"),
            cost_basis_eur=Decimal("950"),
        )
    ]


def _seed_returns(returns_repo, account, ticker, days):
    # The fake only keys rows by price_date, so a placeholder stands in for each ReturnSnapshot.
    returns_repo.snapshots[(account.id, ticker)] = {date(2026, 3, d): object() for d in days}


def test_delete_account_removes_the_account_and_everything_recorded_in_it(
    repos, returns_repo, conn
):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    gone = _seed_account(accounts_repo, user_id=user_id, label="gone")
    kept = _seed_account(accounts_repo, user_id=user_id, label="kept")
    for account in (gone, kept):
        _seed_txn(transactions_repo, account, "t1")
        _seed_position(positions_repo, account, "IWDA.AS")
        _seed_returns(returns_repo, account, "IWDA.AS", days=[2, 3])
    _seed_txn(transactions_repo, gone, "t2")

    outcome = pipeline.delete_account(conn, user_id=user_id, account_label="gone")

    assert outcome == pipeline.AccountDeleteOutcome(
        transactions_deleted=2, position_snapshots_deleted=1, return_snapshots_deleted=2
    )
    assert accounts_repo.get_by_label(None, user_id=user_id, account_label="gone") is None
    assert _refs(transactions_repo) == [(kept.id, "t1")]
    assert list(positions_repo.snapshots) == [(kept.id, "IWDA.AS")]
    assert list(returns_repo.snapshots) == [(kept.id, "IWDA.AS")]
    assert conn.commits == 1


def test_delete_account_with_nothing_recorded_in_it_still_deletes_it(repos, returns_repo, conn):
    accounts_repo, _, _ = repos
    user_id = uuid.uuid4()
    _seed_account(accounts_repo, user_id=user_id)

    outcome = pipeline.delete_account(conn, user_id=user_id, account_label="m")

    assert outcome.transactions_deleted == 0
    assert accounts_repo.get_by_label(None, user_id=user_id, account_label="m") is None
    assert conn.commits == 1


def test_delete_account_raises_for_an_unknown_label(repos, returns_repo, conn):
    with pytest.raises(AccountNotFound):
        pipeline.delete_account(conn, user_id=uuid.uuid4(), account_label="nope")

    assert conn.commits == 0


def test_delete_account_cannot_reach_another_users_account(repos, returns_repo, conn):
    accounts_repo, transactions_repo, _ = repos
    owner = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=owner)
    _seed_txn(transactions_repo, account, "t1")

    with pytest.raises(AccountNotFound):
        pipeline.delete_account(conn, user_id=uuid.uuid4(), account_label="m")

    assert accounts_repo.get_by_label(None, user_id=owner, account_label="m") == account
    assert _refs(transactions_repo) == [(account.id, "t1")]


def test_delete_account_rolls_back_everything_if_a_step_fails(
    repos, returns_repo, conn, monkeypatch
):
    """One transaction for the lot: a failure part-way (here, deleting the account row itself)
    must not leave the account with its snapshots or transactions already gone."""
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    _seed_account(accounts_repo, user_id=user_id)

    def boom(conn, **kw):
        raise RuntimeError("foreign key violation")

    monkeypatch.setattr(accounts_repo, "delete_account", boom)

    with pytest.raises(RuntimeError):
        pipeline.delete_account(conn, user_id=user_id, account_label="m")

    assert (conn.commits, conn.rollbacks) == (0, 1)


def test_delete_account_raises_if_the_account_vanished_before_its_row_was_deleted(
    repos, returns_repo, conn, monkeypatch
):
    """Another request deleted it between the lookup and the delete: report it as not found and
    roll back, rather than claim a delete that didn't happen here."""
    accounts_repo, _, _ = repos
    user_id = uuid.uuid4()
    _seed_account(accounts_repo, user_id=user_id)
    monkeypatch.setattr(accounts_repo, "delete_account", lambda conn, **kw: 0)

    with pytest.raises(AccountNotFound):
        pipeline.delete_account(conn, user_id=user_id, account_label="m")

    assert (conn.commits, conn.rollbacks) == (0, 1)
