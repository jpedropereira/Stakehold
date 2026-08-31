"""pipeline.py tested against fake repository modules -- standing in for
`portfolio.accounts.repository` / `portfolio.transactions.repository` so the orchestration
(transaction boundaries, isolation lookups, error mapping) can be exercised without a real
Postgres connection. The repository modules' own SQL is covered separately by
tests/unit/{accounts,transactions}/test_repository.py and the integration suite.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from portfolio import pipeline
from portfolio.accounts.repository import Account
from portfolio.errors import (
    AccountLabelConflict,
    AccountNotFound,
    InvalidTransaction,
    UnknownBroker,
)

NOW = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)


class FakeConnection:
    """Counts commits/rollbacks -- transaction *boundaries* are the behaviour under test here,
    and a boundary is only observable from the connection, never from the fake repositories."""

    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeAccountsRepo:
    def __init__(self):
        self.by_key: dict[tuple, Account] = {}
        self._next_id = 1

    def insert_account(self, conn, *, user_id, broker, account_label):
        key = (user_id, account_label)
        if key in self.by_key:
            return None
        account = Account(
            id=self._next_id,
            user_id=user_id,
            broker=broker,
            account_label=account_label,
            created_at=NOW,
        )
        self.by_key[key] = account
        self._next_id += 1
        return account.id

    def get_by_label(self, conn, *, user_id, account_label):
        return self.by_key.get((user_id, account_label))

    def list_by_user(self, conn, user_id):
        return [a for (u, _), a in self.by_key.items() if u == user_id]


class FakeTransactionsRepo:
    def __init__(self):
        self.saved = []

    def insert_transactions(self, conn, transactions):
        existing = {(t.account_id, t.broker_transaction_ref) for t in self.saved}
        new = 0
        for txn in transactions:
            if (txn.account_id, txn.broker_transaction_ref) in existing:
                continue
            self.saved.append(txn)
            new += 1
        return new


@pytest.fixture
def repos(monkeypatch):
    accounts_repo = FakeAccountsRepo()
    transactions_repo = FakeTransactionsRepo()
    monkeypatch.setattr(pipeline, "accounts_repository", accounts_repo)
    monkeypatch.setattr(pipeline, "transactions_repository", transactions_repo)
    return accounts_repo, transactions_repo


@pytest.fixture
def conn():
    return FakeConnection()


# --- add_account ---


def test_add_account_creates_a_new_account_and_commits_once(repos, conn):
    created = pipeline.add_account(conn, user_id=uuid.uuid4(), broker="acme", account_label="m")

    assert created == 1
    assert conn.commits == 1
    assert conn.rollbacks == 0


def test_add_account_rejects_an_unknown_broker_without_touching_the_connection(repos, conn):
    with pytest.raises(UnknownBroker):
        pipeline.add_account(conn, user_id=uuid.uuid4(), broker="robinhood", account_label="m")

    assert conn.commits == 0


def test_add_account_is_a_no_op_when_the_same_label_and_broker_already_exist(repos, conn):
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")

    created = pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")

    assert created == 0


def test_add_account_raises_on_label_conflict_with_a_different_broker(repos, conn):
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")

    with pytest.raises(AccountLabelConflict):
        pipeline.add_account(conn, user_id=user_id, broker="globex", account_label="m")


def test_add_account_allows_the_same_label_for_a_different_user(repos, conn):
    pipeline.add_account(conn, user_id=uuid.uuid4(), broker="acme", account_label="m")

    created = pipeline.add_account(conn, user_id=uuid.uuid4(), broker="acme", account_label="m")

    assert created == 1


# --- list_accounts ---


def test_list_accounts_returns_only_this_users_accounts(repos, conn):
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    pipeline.add_account(conn, user_id=user_a, broker="acme", account_label="a1")
    pipeline.add_account(conn, user_id=user_b, broker="acme", account_label="b1")

    accounts = pipeline.list_accounts(conn, user_id=user_a)

    assert [a.account_label for a in accounts] == ["a1"]


def test_list_accounts_never_commits(repos, conn):
    pipeline.list_accounts(conn, user_id=uuid.uuid4())

    assert conn.commits == 0


# --- add_manual_transaction ---


def _add_txn(conn, user_id, account_label, **overrides):
    kwargs = dict(
        broker_transaction_ref="ref-1",
        transaction_type="BUY",
        trade_datetime=NOW,
        instrument_identifier="IE00B4L5Y983",
        instrument_identifier_kind="ISIN",
        quantity=Decimal("10"),
        price=Decimal("95.50"),
        native_currency="EUR",
        native_amount=Decimal("-955.00"),
    )
    kwargs.update(overrides)
    return pipeline.add_manual_transaction(
        conn, user_id=user_id, account_label=account_label, **kwargs
    )


def test_add_manual_transaction_requires_an_existing_account(repos, conn):
    with pytest.raises(AccountNotFound):
        _add_txn(conn, uuid.uuid4(), "nonexistent")

    assert conn.commits == 0


def test_add_manual_transaction_scopes_account_lookup_by_user(repos, conn):
    """A label owned by a different user must not resolve -- the isolation chokepoint."""
    owner = uuid.uuid4()
    pipeline.add_account(conn, user_id=owner, broker="acme", account_label="m")

    with pytest.raises(AccountNotFound):
        _add_txn(conn, uuid.uuid4(), "m")


def test_add_manual_transaction_derives_eur_amount_for_a_eur_transaction(repos, conn):
    accounts_repo, transactions_repo = repos
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    commits_before = conn.commits

    saved = _add_txn(conn, user_id, "m")

    assert saved == 1
    txn = transactions_repo.saved[0]
    assert txn.eur_amount == Decimal("-955.0000")
    assert txn.fx_rate is None
    assert txn.fx_rate_source is None
    assert conn.commits - commits_before == 1


def test_add_manual_transaction_derives_eur_amount_from_a_manual_fx_rate(repos, conn):
    _, transactions_repo = repos
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="globex", account_label="m")

    _add_txn(
        conn,
        user_id,
        "m",
        instrument_identifier="AAPL",
        instrument_identifier_kind="SYMBOL",
        native_currency="USD",
        native_amount=Decimal("-1000.00"),
        fx_rate=Decimal("1.0850"),
    )

    txn = transactions_repo.saved[0]
    assert txn.eur_amount == Decimal("-921.6590")
    assert txn.fx_rate_source == "manual"


def test_add_manual_transaction_rejects_a_non_eur_transaction_without_a_rate(repos, conn):
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="globex", account_label="m")
    commits_before = conn.commits

    with pytest.raises(InvalidTransaction):
        _add_txn(
            conn,
            user_id,
            "m",
            instrument_identifier="AAPL",
            instrument_identifier_kind="SYMBOL",
            native_currency="USD",
            native_amount=Decimal("-1000.00"),
        )

    assert conn.commits == commits_before


def test_add_manual_transaction_rejects_an_eur_transaction_carrying_a_rate(repos, conn):
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")

    with pytest.raises(InvalidTransaction):
        _add_txn(conn, user_id, "m", fx_rate=Decimal("1.0"))


def test_add_manual_transaction_propagates_validation_errors_without_committing(repos, conn):
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    commits_before = conn.commits

    with pytest.raises(InvalidTransaction):
        _add_txn(conn, user_id, "m", quantity=None)

    assert conn.commits == commits_before


def test_add_manual_transaction_is_a_no_op_when_the_ref_already_exists(repos, conn):
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    _add_txn(conn, user_id, "m")

    saved = _add_txn(conn, user_id, "m")

    assert saved == 0


def test_add_manual_transaction_passes_through_optional_fields(repos, conn):
    _, transactions_repo = repos
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")

    _add_txn(conn, user_id, "m", fees_native=Decimal("1.50"))

    assert transactions_repo.saved[0].fees_native == Decimal("1.50")
