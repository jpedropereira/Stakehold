"""Fakes and fixtures shared by the pipeline unit tests in this directory (test_pipeline.py,
test_pipeline_rebuilds.py, test_pipeline_deletes.py): in-memory stand-ins for the repositories
pipeline.py composes, patched onto the module by the `repos`/`returns_repo` fixtures.

They live in a conftest, not a helper module, because test directories aren't packages (see
CLAUDE.md's "Never add an `__init__.py` under a service's `tests/`"), so a sibling helper module
can't be imported. Putting them here rather than in `tests/unit/conftest.py` keeps them scoped
to the pipeline tests.
"""

from datetime import UTC, datetime

import pytest
from portfolio import pipeline
from portfolio.accounts.repository import Account

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

    def list_user_ids(self, conn):
        return sorted({u for (u, _) in self.by_key}, key=str)

    def get_by_id(self, account_id):
        return next((a for a in self.by_key.values() if a.id == account_id), None)

    def delete_account(self, conn, *, user_id, account_id):
        for key, account in list(self.by_key.items()):
            if account.id == account_id and account.user_id == user_id:
                del self.by_key[key]
                return 1
        return 0


class FakeTransactionsRepo:
    """Standing in for the real repository's SQL join to `accounts` -- given a reference to the
    fake accounts repo purely so this fake can resolve (user_id, broker) per row the same way the
    real query does. accounts/repository.py itself still has no unscoped get_by_id; this is a
    test-only convenience, not a production shortcut."""

    def __init__(self, accounts_repo):
        self.saved = []
        self._accounts_repo = accounts_repo
        self.list_by_accounts_calls: list[list[int]] = []

    def insert_transactions(self, conn, transactions):
        existing = {(t.account_id, t.broker_transaction_ref) for t in self.saved}
        new = 0
        for txn in transactions:
            if (txn.account_id, txn.broker_transaction_ref) in existing:
                continue
            self.saved.append(txn)
            new += 1
        return new

    def list_distinct_instruments(self, conn, user_id):
        pairs = set()
        for txn in self.saved:
            if txn.instrument_identifier is None:
                continue
            account = self._accounts_repo.get_by_id(txn.account_id)
            if account is None or account.user_id != user_id:
                continue
            pairs.add((txn.instrument_identifier_kind, txn.instrument_identifier, account.broker))
        return sorted(pairs)

    def list_distinct_instruments_all(self, conn):
        triples = set()
        for txn in self.saved:
            if txn.instrument_identifier is None:
                continue
            account = self._accounts_repo.get_by_id(txn.account_id)
            triples.add((txn.instrument_identifier_kind, txn.instrument_identifier, account.broker))
        return sorted(triples)

    def list_accounts_using_instrument(self, conn, *, identifier_kind, identifier, broker):
        found = set()
        for txn in self.saved:
            if (txn.instrument_identifier_kind, txn.instrument_identifier) != (
                identifier_kind,
                identifier,
            ):
                continue
            account = self._accounts_repo.get_by_id(txn.account_id)
            if broker is not None and account.broker != broker:
                continue
            found.add((account.user_id, account.account_label))
        return sorted(found, key=lambda pair: (str(pair[0]), pair[1]))

    def list_by_accounts(self, conn, account_ids, *, transaction_types=None):
        self.list_by_accounts_calls.append(list(account_ids))
        grouped: dict[int, list] = {}
        for txn in self.saved:
            if txn.account_id not in account_ids:
                continue
            if transaction_types is not None and txn.transaction_type not in transaction_types:
                continue
            grouped.setdefault(txn.account_id, []).append(txn)
        for account_id, rows in grouped.items():
            grouped[account_id] = sorted(
                rows, key=lambda t: (t.trade_datetime, t.broker_transaction_ref)
            )
        return grouped

    def delete_one(self, conn, *, account_id, broker_transaction_ref):
        before = len(self.saved)
        self.saved = [
            t
            for t in self.saved
            if (t.account_id, t.broker_transaction_ref) != (account_id, broker_transaction_ref)
        ]
        return before - len(self.saved)

    def delete_by_account(self, conn, account_id):
        before = len(self.saved)
        self.saved = [t for t in self.saved if t.account_id != account_id]
        return before - len(self.saved)

    def list_by_account(self, conn, account_id, *, transaction_types=None):
        return self.list_by_accounts(conn, [account_id], transaction_types=transaction_types).get(
            account_id, []
        )


class FakePositionsRepo:
    """Records every replace_snapshots/delete_snapshots call, keyed by (account_id,
    canonical_ticker), so tests can assert both the persisted content and how many times each
    pair's transaction boundary fired."""

    def __init__(self):
        self.snapshots: dict[tuple[int, str], list] = {}
        self.replace_calls = []
        self.delete_calls = []
        # Pairs that should raise instead of writing -- lets a test simulate a driver-level
        # failure (a constraint violation, a dropped connection) for one specific pair.
        self.failing_pairs: set[tuple[int, str]] = set()

    def replace_snapshots(self, conn, *, account_id, canonical_ticker, snapshots):
        self.replace_calls.append((account_id, canonical_ticker))
        if (account_id, canonical_ticker) in self.failing_pairs:
            raise RuntimeError(f"simulated failure for {canonical_ticker}")
        self.snapshots[(account_id, canonical_ticker)] = list(snapshots)
        return len(snapshots)

    def list_tickers(self, conn, account_id):
        return [ticker for (aid, ticker) in self.snapshots if aid == account_id]

    def delete_snapshots(self, conn, *, account_id, canonical_ticker):
        self.delete_calls.append((account_id, canonical_ticker))
        if (account_id, canonical_ticker) in self.failing_pairs:
            raise RuntimeError(f"simulated failure for {canonical_ticker}")
        existing = self.snapshots.pop((account_id, canonical_ticker), None)
        return len(existing) if existing else 0

    def delete_by_account(self, conn, account_id):
        removed = [key for key in self.snapshots if key[0] == account_id]
        return sum(len(self.snapshots.pop(key)) for key in removed)

    def list_snapshots(self, conn, *, account_id, canonical_ticker):
        return list(self.snapshots.get((account_id, canonical_ticker), []))

    def list_latest_snapshots(self, conn, account_id):
        latest = []
        for (aid, _ticker), rows in self.snapshots.items():
            if aid == account_id and rows:
                latest.append(max(rows, key=lambda s: s.as_of_date))
        return sorted(latest, key=lambda s: s.canonical_ticker)


class FakeReturnsRepo:
    """Records every replace_snapshots/delete_snapshots call, keyed by (account_id,
    canonical_ticker) -- mirrors FakePositionsRepo. Rows are held per pair by price_date, and
    replace_snapshots swaps a pair's whole set, since the real one deletes before inserting."""

    def __init__(self):
        self.snapshots: dict[tuple[int, str], dict] = {}
        self.replace_calls: list[tuple[int, str]] = []
        self.delete_calls: list[tuple[int, str]] = []
        # Pairs that should raise instead of writing, to simulate a driver-level failure.
        self.failing_pairs: set[tuple[int, str]] = set()

    def replace_snapshots(self, conn, *, account_id, canonical_ticker, snapshots):
        key = (account_id, canonical_ticker)
        self.replace_calls.append(key)
        if key in self.failing_pairs:
            raise RuntimeError(f"simulated failure for {canonical_ticker}")
        self.snapshots[key] = {snapshot.price_date: snapshot for snapshot in snapshots}
        return len(snapshots)

    def list_tickers(self, conn, account_id):
        return [ticker for (aid, ticker) in self.snapshots if aid == account_id]

    def delete_snapshots(self, conn, *, account_id, canonical_ticker):
        key = (account_id, canonical_ticker)
        self.delete_calls.append(key)
        if key in self.failing_pairs:
            raise RuntimeError(f"simulated failure for {canonical_ticker}")
        existing = self.snapshots.pop(key, None)
        return len(existing) if existing else 0

    def delete_by_account(self, conn, account_id):
        removed = [key for key in self.snapshots if key[0] == account_id]
        return sum(len(self.snapshots.pop(key)) for key in removed)

    def list_snapshots(self, conn, *, account_id, canonical_ticker=None, start=None, end=None):
        rows = []
        for (aid, ticker), by_date in self.snapshots.items():
            if aid != account_id:
                continue
            if canonical_ticker is not None and ticker != canonical_ticker:
                continue
            for price_date, snapshot in by_date.items():
                if start is not None and price_date < start:
                    continue
                if end is not None and price_date > end:
                    continue
                rows.append(snapshot)
        return sorted(rows, key=lambda s: (s.canonical_ticker, s.price_date))

    def list_latest_snapshots(self, conn, account_id):
        latest = []
        for (aid, _ticker), by_date in self.snapshots.items():
            if aid == account_id and by_date:
                latest.append(max(by_date.values(), key=lambda s: s.price_date))
        return sorted(latest, key=lambda s: s.canonical_ticker)


@pytest.fixture
def repos(monkeypatch):
    accounts_repo = FakeAccountsRepo()
    transactions_repo = FakeTransactionsRepo(accounts_repo)
    positions_repo = FakePositionsRepo()
    monkeypatch.setattr(pipeline, "accounts_repository", accounts_repo)
    monkeypatch.setattr(pipeline, "transactions_repository", transactions_repo)
    monkeypatch.setattr(pipeline, "positions_repository", positions_repo)
    return accounts_repo, transactions_repo, positions_repo


@pytest.fixture
def returns_repo(repos, monkeypatch):
    """A separate fixture, not folded into `repos`, so every existing
    `accounts_repo, transactions_repo, positions_repo = repos` unpack in this file keeps
    working unchanged -- only rebuild_returns tests need this one too."""
    fake = FakeReturnsRepo()
    monkeypatch.setattr(pipeline, "returns_repository", fake)
    return fake


@pytest.fixture
def conn():
    return FakeConnection()
