"""pipeline.py tested against fake repository modules -- standing in for
`portfolio.accounts.repository` / `portfolio.transactions.repository` so the orchestration
(transaction boundaries, isolation lookups, error mapping) can be exercised without a real
Postgres connection. The repository modules' own SQL is covered separately by
tests/unit/{accounts,transactions}/test_repository.py and the integration suite.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from portfolio import pipeline
from portfolio.accounts.repository import Account
from portfolio.errors import (
    AccountLabelConflict,
    AccountNotFound,
    BrokerFileError,
    BrokerParserNotFound,
    BrokerPluginError,
    InvalidBroker,
    InvalidTransaction,
)
from portfolio.ticker_mapping.loader import TickerMappings
from portfolio.transactions.base import CanonicalTransaction

_ISIN = "IE00B4L5Y983"

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

    def get_by_id(self, account_id):
        return next((a for a in self.by_key.values() if a.id == account_id), None)


class FakeTransactionsRepo:
    """Standing in for the real repository's SQL join to `accounts` -- given a reference to the
    fake accounts repo purely so this fake can resolve (user_id, broker) per row the same way the
    real query does. accounts/repository.py itself still has no unscoped get_by_id; this is a
    test-only convenience, not a production shortcut."""

    def __init__(self, accounts_repo):
        self.saved = []
        self._accounts_repo = accounts_repo

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

    def list_by_account(self, conn, account_id):
        rows = [t for t in self.saved if t.account_id == account_id]
        return sorted(rows, key=lambda t: (t.trade_datetime, t.broker_transaction_ref))


class FakePositionsRepo:
    """Records every replace_snapshots/delete_snapshots call, keyed by (account_id,
    canonical_ticker), so tests can assert both the persisted content and how many times each
    pair's transaction boundary fired."""

    def __init__(self):
        self.snapshots: dict[tuple[int, str], list] = {}
        self.replace_calls = []
        self.delete_calls = []

    def replace_snapshots(self, conn, *, account_id, canonical_ticker, snapshots):
        self.replace_calls.append((account_id, canonical_ticker))
        self.snapshots[(account_id, canonical_ticker)] = list(snapshots)
        return len(snapshots)

    def list_tickers(self, conn, account_id):
        return [ticker for (aid, ticker) in self.snapshots if aid == account_id]

    def delete_snapshots(self, conn, *, account_id, canonical_ticker):
        self.delete_calls.append((account_id, canonical_ticker))
        existing = self.snapshots.pop((account_id, canonical_ticker), None)
        return len(existing) if existing else 0


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
def conn():
    return FakeConnection()


# --- add_account ---


def test_add_account_creates_a_new_account_and_commits_once(repos, conn):
    created = pipeline.add_account(conn, user_id=uuid.uuid4(), broker="acme", account_label="m")

    assert created == 1
    assert conn.commits == 1
    assert conn.rollbacks == 0


def test_add_account_accepts_a_broker_with_no_registered_parser(repos, conn, monkeypatch):
    """Account creation (and therefore manual transaction entry) must work with zero broker-
    parser plugins installed -- broker is a free-text label, not checked against BROKER_PARSERS."""
    monkeypatch.setattr(pipeline, "BROKER_PARSERS", {})

    created = pipeline.add_account(
        conn, user_id=uuid.uuid4(), broker="robinhood", account_label="m"
    )

    assert created == 1
    assert conn.commits == 1


def test_add_account_rejects_a_blank_broker_without_touching_the_connection(repos, conn):
    with pytest.raises(InvalidBroker):
        pipeline.add_account(conn, user_id=uuid.uuid4(), broker="   ", account_label="m")

    assert conn.commits == 0


def test_add_account_rejects_a_broker_longer_than_the_column_can_hold(repos, conn):
    with pytest.raises(InvalidBroker):
        pipeline.add_account(conn, user_id=uuid.uuid4(), broker="x" * 51, account_label="m")

    assert conn.commits == 0


def test_add_account_strips_surrounding_whitespace_before_storing_the_broker(repos, conn):
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="  acme  ", account_label="m")

    account = pipeline.list_accounts(conn, user_id=user_id)[0]

    assert account.broker == "acme"


def test_add_account_is_unaffected_by_a_broker_plugin_discovery_error(repos, conn, monkeypatch):
    """account creation (and manual entry) must work even if the installed broker-parser plugin
    set is broken -- see docs/architecture.md's discovery-confinement note."""
    monkeypatch.setattr(pipeline, "_BROKER_DISCOVERY_ERROR", BrokerPluginError("boom"))

    created = pipeline.add_account(conn, user_id=uuid.uuid4(), broker="acme", account_label="m")

    assert created == 1


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


class _VanishingAccountsRepo(FakeAccountsRepo):
    """Simulates insert_account reporting a conflict for a row that get_by_label then can't
    find -- the TOCTOU race add_account must raise a clean domain error on, not crash on."""

    def insert_account(self, conn, *, user_id, broker, account_label):
        return None

    def get_by_label(self, conn, *, user_id, account_label):
        return None


def test_add_account_raises_cleanly_when_the_conflicting_row_vanishes_before_reread(
    conn, monkeypatch
):
    monkeypatch.setattr(pipeline, "accounts_repository", _VanishingAccountsRepo())

    with pytest.raises(AccountNotFound):
        pipeline.add_account(conn, user_id=uuid.uuid4(), broker="acme", account_label="m")


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


# --- list_available_brokers ---


def test_list_available_brokers_returns_sorted_registered_keys(monkeypatch):
    monkeypatch.setattr(pipeline, "BROKER_PARSERS", {"globex": object(), "acme": object()})

    assert pipeline.list_available_brokers() == ["acme", "globex"]


def test_list_available_brokers_returns_empty_list_with_no_plugins_installed(monkeypatch):
    monkeypatch.setattr(pipeline, "BROKER_PARSERS", {})

    assert pipeline.list_available_brokers() == []


def test_list_available_brokers_raises_a_stashed_discovery_error(monkeypatch):
    monkeypatch.setattr(pipeline, "_BROKER_DISCOVERY_ERROR", BrokerPluginError("boom"))

    with pytest.raises(BrokerPluginError, match="boom"):
        pipeline.list_available_brokers()


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
    accounts_repo, transactions_repo, _ = repos
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
    _, transactions_repo, _ = repos
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


def test_add_manual_transaction_rejects_a_zero_fx_rate(repos, conn):
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
            fx_rate=Decimal("0"),
        )

    assert conn.commits == commits_before


def test_add_manual_transaction_rejects_a_non_finite_amount(repos, conn):
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    commits_before = conn.commits

    with pytest.raises(InvalidTransaction):
        _add_txn(conn, user_id, "m", native_amount=Decimal("NaN"))

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
    _, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")

    _add_txn(conn, user_id, "m", fees_native=Decimal("1.50"))

    assert transactions_repo.saved[0].fees_native == Decimal("1.50")


# --- import_transactions ---


class FakeParser:
    def __init__(self, broker_key, make_transactions=None, error=None):
        self.broker_key = broker_key
        self._make_transactions = make_transactions or (lambda account_id: [])
        self._error = error
        self.calls = []

    def parse(self, file_path, account_id):
        self.calls.append((file_path, account_id))
        if self._error is not None:
            raise self._error
        return self._make_transactions(account_id)


def _txn_factory(**overrides):
    def make(account_id):
        kwargs = dict(
            account_id=account_id,
            broker_transaction_ref="import-1",
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
        kwargs.update(overrides)
        return [CanonicalTransaction(**kwargs)]

    return make


def test_import_transactions_requires_an_existing_account(repos, conn, monkeypatch):
    monkeypatch.setattr(pipeline, "BROKER_PARSERS", {"acme": FakeParser("acme")})

    with pytest.raises(AccountNotFound):
        pipeline.import_transactions(
            conn,
            user_id=uuid.uuid4(),
            account_label="nonexistent",
            file_path=Path("f.csv"),
        )


def test_import_transactions_raises_a_stashed_broker_discovery_error(repos, conn, monkeypatch):
    monkeypatch.setattr(pipeline, "_BROKER_DISCOVERY_ERROR", BrokerPluginError("boom"))

    with pytest.raises(BrokerPluginError, match="boom"):
        pipeline.import_transactions(
            conn, user_id=uuid.uuid4(), account_label="m", file_path=Path("f.csv")
        )


def test_import_transactions_rejects_a_broker_with_no_registered_parser(repos, conn, monkeypatch):
    """The account's own broker (here "robinhood") has no parser registered for it -- the
    account itself resolves fine, since account creation never checks BROKER_PARSERS."""
    monkeypatch.setattr(pipeline, "BROKER_PARSERS", {})
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="robinhood", account_label="m")

    with pytest.raises(BrokerParserNotFound):
        pipeline.import_transactions(
            conn, user_id=user_id, account_label="m", file_path=Path("f.csv")
        )


def test_import_transactions_parses_and_saves_transactions(repos, conn, monkeypatch):
    _, transactions_repo, _ = repos
    parser = FakeParser("acme", make_transactions=_txn_factory())
    monkeypatch.setattr(pipeline, "BROKER_PARSERS", {"acme": parser})
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    commits_before = conn.commits

    saved = pipeline.import_transactions(
        conn, user_id=user_id, account_label="m", file_path=Path("f.csv")
    )

    assert saved == 1
    assert transactions_repo.saved[0].broker_transaction_ref == "import-1"
    assert parser.calls == [(Path("f.csv"), transactions_repo.saved[0].account_id)]
    assert conn.commits - commits_before == 1


def test_import_transactions_is_idempotent_on_reimport(repos, conn, monkeypatch):
    parser = FakeParser("acme", make_transactions=_txn_factory())
    monkeypatch.setattr(pipeline, "BROKER_PARSERS", {"acme": parser})
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    pipeline.import_transactions(conn, user_id=user_id, account_label="m", file_path=Path("f.csv"))

    saved = pipeline.import_transactions(
        conn, user_id=user_id, account_label="m", file_path=Path("f.csv")
    )

    assert saved == 0


def test_import_transactions_propagates_a_broker_file_error_without_committing(
    repos, conn, monkeypatch
):
    parser = FakeParser("acme", error=BrokerFileError("bad file"))
    monkeypatch.setattr(pipeline, "BROKER_PARSERS", {"acme": parser})
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    commits_before = conn.commits

    with pytest.raises(BrokerFileError):
        pipeline.import_transactions(
            conn, user_id=user_id, account_label="m", file_path=Path("f.csv")
        )

    assert conn.commits == commits_before


def test_import_transactions_propagates_invalid_transaction_from_a_bad_parse(
    repos, conn, monkeypatch
):
    parser = FakeParser("acme", make_transactions=_txn_factory(native_amount=Decimal("NaN")))
    monkeypatch.setattr(pipeline, "BROKER_PARSERS", {"acme": parser})
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")

    with pytest.raises(InvalidTransaction):
        pipeline.import_transactions(
            conn, user_id=user_id, account_label="m", file_path=Path("f.csv")
        )


# --- list_unmapped_instruments ---


def test_list_unmapped_instruments_returns_only_unmapped_triples(repos, conn, monkeypatch):
    parser = FakeParser("acme", make_transactions=_txn_factory())
    monkeypatch.setattr(pipeline, "BROKER_PARSERS", {"acme": parser})
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    pipeline.import_transactions(conn, user_id=user_id, account_label="m", file_path=Path("f.csv"))
    mappings = TickerMappings(isin={}, symbol={})

    unmapped = pipeline.list_unmapped_instruments(conn, user_id=user_id, mappings=mappings)

    assert unmapped == [("ISIN", "IE00B4L5Y983", "acme")]


def test_list_unmapped_instruments_excludes_mapped_ones(repos, conn, monkeypatch):
    parser = FakeParser("acme", make_transactions=_txn_factory())
    monkeypatch.setattr(pipeline, "BROKER_PARSERS", {"acme": parser})
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    pipeline.import_transactions(conn, user_id=user_id, account_label="m", file_path=Path("f.csv"))
    mappings = TickerMappings(isin={"IE00B4L5Y983": "IWDA.AS"}, symbol={})

    unmapped = pipeline.list_unmapped_instruments(conn, user_id=user_id, mappings=mappings)

    assert unmapped == []


def test_list_unmapped_instruments_never_commits(repos, conn):
    mappings = TickerMappings(isin={}, symbol={})

    pipeline.list_unmapped_instruments(conn, user_id=uuid.uuid4(), mappings=mappings)

    assert conn.commits == 0


# --- import_cashflow ---


class FakeCashflowParser:
    def __init__(self, broker_key, make_transactions=None, error=None):
        self.broker_key = broker_key
        self._make_transactions = make_transactions or (lambda account_id: [])
        self._error = error
        self.calls = []

    def parse(self, file_path, account_id, resolve_fx_rate):
        self.calls.append((file_path, account_id, resolve_fx_rate))
        if self._error is not None:
            raise self._error
        return self._make_transactions(account_id)


class FakeDataStoreClient:
    def __init__(self, rates=None):
        self._rates = rates or {}
        self.calls = []

    def get_fx_rate(self, base, quote, on_date):
        self.calls.append((base, quote, on_date))
        return self._rates.get((base, quote, on_date))


def _cashflow_txn_factory(**overrides):
    def make(account_id):
        kwargs = dict(
            account_id=account_id,
            broker_transaction_ref="cashflow-1",
            transaction_type="DEPOSIT",
            trade_datetime=NOW,
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
        kwargs.update(overrides)
        return [CanonicalTransaction(**kwargs)]

    return make


def test_import_cashflow_raises_a_stashed_cashflow_discovery_error(repos, conn, monkeypatch):
    monkeypatch.setattr(pipeline, "_CASHFLOW_DISCOVERY_ERROR", BrokerPluginError("boom"))

    with pytest.raises(BrokerPluginError, match="boom"):
        pipeline.import_cashflow(
            conn,
            user_id=uuid.uuid4(),
            account_label="m",
            file_path=Path("f.csv"),
            datastore_client=FakeDataStoreClient(),
        )


def test_import_cashflow_rejects_a_broker_with_no_registered_parser(repos, conn, monkeypatch):
    """The account's own broker (here "acme") has no cashflow parser registered for it -- the
    account itself resolves fine, since account creation never checks CASHFLOW_PARSERS."""
    monkeypatch.setattr(pipeline, "CASHFLOW_PARSERS", {})
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")

    with pytest.raises(BrokerParserNotFound):
        pipeline.import_cashflow(
            conn,
            user_id=user_id,
            account_label="m",
            file_path=Path("f.csv"),
            datastore_client=FakeDataStoreClient(),
        )


def test_import_cashflow_requires_an_existing_account(repos, conn, monkeypatch):
    monkeypatch.setattr(pipeline, "CASHFLOW_PARSERS", {"acme": FakeCashflowParser("acme")})

    with pytest.raises(AccountNotFound):
        pipeline.import_cashflow(
            conn,
            user_id=uuid.uuid4(),
            account_label="nonexistent",
            file_path=Path("f.csv"),
            datastore_client=FakeDataStoreClient(),
        )


def test_import_cashflow_parses_and_saves_transactions(repos, conn, monkeypatch):
    _, transactions_repo, _ = repos
    parser = FakeCashflowParser("acme", make_transactions=_cashflow_txn_factory())
    monkeypatch.setattr(pipeline, "CASHFLOW_PARSERS", {"acme": parser})
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    commits_before = conn.commits

    saved = pipeline.import_cashflow(
        conn,
        user_id=user_id,
        account_label="m",
        file_path=Path("f.csv"),
        datastore_client=FakeDataStoreClient(),
    )

    assert saved == 1
    assert transactions_repo.saved[0].broker_transaction_ref == "cashflow-1"
    assert conn.commits - commits_before == 1


def test_import_cashflow_passes_a_working_fx_resolver_through_to_the_parser(
    repos, conn, monkeypatch
):
    parser = FakeCashflowParser("acme")
    monkeypatch.setattr(pipeline, "CASHFLOW_PARSERS", {"acme": parser})
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    client = FakeDataStoreClient(rates={("EUR", "PLN", date(2026, 1, 1)): Decimal("4.30")})

    pipeline.import_cashflow(
        conn,
        user_id=user_id,
        account_label="m",
        file_path=Path("f.csv"),
        datastore_client=client,
    )

    _, _, resolve_fx_rate = parser.calls[0]
    rate = resolve_fx_rate("PLN", date(2026, 1, 1))
    assert rate == Decimal("4.30")
    assert client.calls == [("EUR", "PLN", date(2026, 1, 1))]


def test_import_cashflow_memoizes_repeated_fx_rate_lookups_within_one_import(
    repos, conn, monkeypatch
):
    """A cash-ledger export commonly has several rows sharing one (currency, date) -- e.g. a
    dividend batch -- and resolve_fx_rate must only hit the datastore client once per distinct
    pair, not once per row."""
    parser = FakeCashflowParser("acme")
    monkeypatch.setattr(pipeline, "CASHFLOW_PARSERS", {"acme": parser})
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    client = FakeDataStoreClient(rates={("EUR", "PLN", date(2026, 1, 1)): Decimal("4.30")})

    pipeline.import_cashflow(
        conn,
        user_id=user_id,
        account_label="m",
        file_path=Path("f.csv"),
        datastore_client=client,
    )

    _, _, resolve_fx_rate = parser.calls[0]
    for _ in range(3):
        assert resolve_fx_rate("PLN", date(2026, 1, 1)) == Decimal("4.30")

    assert client.calls == [("EUR", "PLN", date(2026, 1, 1))]


def test_import_cashflow_is_idempotent_on_reimport(repos, conn, monkeypatch):
    parser = FakeCashflowParser("acme", make_transactions=_cashflow_txn_factory())
    monkeypatch.setattr(pipeline, "CASHFLOW_PARSERS", {"acme": parser})
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    pipeline.import_cashflow(
        conn,
        user_id=user_id,
        account_label="m",
        file_path=Path("f.csv"),
        datastore_client=FakeDataStoreClient(),
    )

    saved = pipeline.import_cashflow(
        conn,
        user_id=user_id,
        account_label="m",
        file_path=Path("f.csv"),
        datastore_client=FakeDataStoreClient(),
    )

    assert saved == 0


def test_import_cashflow_propagates_a_broker_file_error_without_committing(
    repos, conn, monkeypatch
):
    parser = FakeCashflowParser("acme", error=BrokerFileError("bad file"))
    monkeypatch.setattr(pipeline, "CASHFLOW_PARSERS", {"acme": parser})
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    commits_before = conn.commits

    with pytest.raises(BrokerFileError):
        pipeline.import_cashflow(
            conn,
            user_id=user_id,
            account_label="m",
            file_path=Path("f.csv"),
            datastore_client=FakeDataStoreClient(),
        )

    assert conn.commits == commits_before


# --- rebuild_positions ---


def _position_txn(**overrides):
    kwargs = dict(
        account_id=1,
        broker_transaction_ref="ref-1",
        transaction_type="BUY",
        trade_datetime=NOW,
        instrument_identifier=_ISIN,
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
    kwargs.update(overrides)
    return CanonicalTransaction(**kwargs)


def _seed_account(accounts_repo, *, user_id, label="m", broker="acme"):
    accounts_repo.insert_account(None, user_id=user_id, broker=broker, account_label=label)
    return accounts_repo.get_by_label(None, user_id=user_id, account_label=label)


def test_rebuild_positions_saves_snapshots_for_a_mapped_instrument(repos, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    transactions_repo.saved.append(_position_txn(account_id=account.id))
    mappings = TickerMappings(isin={_ISIN: "IWDA.AS"}, symbol={})

    outcome = pipeline.rebuild_positions(conn, user_id=user_id, mappings=mappings)

    assert outcome.blocked == []
    assert outcome.positions_rebuilt == 1
    assert outcome.snapshots_saved == 1
    assert positions_repo.snapshots[(account.id, "IWDA.AS")][0].quantity == Decimal("10.000000")


def test_rebuild_positions_reports_unmapped_instruments_as_blocked(repos, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    transactions_repo.saved.append(_position_txn(account_id=account.id))
    mappings = TickerMappings(isin={}, symbol={})

    outcome = pipeline.rebuild_positions(conn, user_id=user_id, mappings=mappings)

    assert outcome.blocked == [("ISIN", _ISIN, "acme")]
    assert outcome.positions_rebuilt == 0
    assert outcome.snapshots_saved == 0
    assert positions_repo.snapshots == {}


def test_rebuild_positions_rebuilds_every_other_instrument_when_one_is_blocked(repos, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    transactions_repo.saved.append(
        _position_txn(account_id=account.id, broker_transaction_ref="ref-1")
    )
    transactions_repo.saved.append(
        _position_txn(
            account_id=account.id,
            broker_transaction_ref="ref-2",
            instrument_identifier="IE00XXXXXXX9",
        )
    )
    mappings = TickerMappings(isin={"IE00XXXXXXX9": "VUSA.AS"}, symbol={})

    outcome = pipeline.rebuild_positions(conn, user_id=user_id, mappings=mappings)

    assert outcome.blocked == [("ISIN", _ISIN, "acme")]
    assert outcome.positions_rebuilt == 1
    assert (account.id, "VUSA.AS") in positions_repo.snapshots


def test_rebuild_positions_commits_once_per_account_ticker_pair(repos, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    transactions_repo.saved.append(
        _position_txn(account_id=account.id, broker_transaction_ref="ref-1")
    )
    transactions_repo.saved.append(
        _position_txn(
            account_id=account.id,
            broker_transaction_ref="ref-2",
            instrument_identifier="IE00XXXXXXX9",
        )
    )
    mappings = TickerMappings(isin={_ISIN: "IWDA.AS", "IE00XXXXXXX9": "VUSA.AS"}, symbol={})
    commits_before = conn.commits

    pipeline.rebuild_positions(conn, user_id=user_id, mappings=mappings)

    assert conn.commits - commits_before == 2


def test_rebuild_positions_scopes_to_one_account_via_account_label(repos, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account_a = _seed_account(accounts_repo, user_id=user_id, label="a")
    account_b = _seed_account(accounts_repo, user_id=user_id, label="b")
    transactions_repo.saved.append(_position_txn(account_id=account_a.id))
    transactions_repo.saved.append(_position_txn(account_id=account_b.id))
    mappings = TickerMappings(isin={_ISIN: "IWDA.AS"}, symbol={})

    outcome = pipeline.rebuild_positions(
        conn, user_id=user_id, mappings=mappings, account_label="a"
    )

    assert outcome.positions_rebuilt == 1
    assert (account_a.id, "IWDA.AS") in positions_repo.snapshots
    assert (account_b.id, "IWDA.AS") not in positions_repo.snapshots


def test_rebuild_positions_raises_account_not_found_for_a_bad_label(repos, conn):
    with pytest.raises(AccountNotFound):
        pipeline.rebuild_positions(
            conn,
            user_id=uuid.uuid4(),
            mappings=TickerMappings(isin={}, symbol={}),
            account_label="nope",
        )


def test_rebuild_positions_is_isolated_per_user(repos, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    account_a = _seed_account(accounts_repo, user_id=user_a, label="a")
    _seed_account(accounts_repo, user_id=user_b, label="b")
    transactions_repo.saved.append(_position_txn(account_id=account_a.id))
    mappings = TickerMappings(isin={_ISIN: "IWDA.AS"}, symbol={})

    outcome = pipeline.rebuild_positions(conn, user_id=user_b, mappings=mappings)

    assert outcome.positions_rebuilt == 0
    assert outcome.blocked == []
    assert positions_repo.snapshots == {}


def test_rebuild_positions_handles_one_instrument_in_two_accounts_two_currencies(repos, conn):
    """Each account replays independently -- no anomaly, two distinct snapshot sets. See
    docs/schema.md's note on the same instrument held in two currencies across two accounts."""
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account_eur = _seed_account(accounts_repo, user_id=user_id, label="eur")
    account_pln = _seed_account(accounts_repo, user_id=user_id, label="pln")
    transactions_repo.saved.append(_position_txn(account_id=account_eur.id))
    transactions_repo.saved.append(
        _position_txn(
            account_id=account_pln.id,
            broker_transaction_ref="ref-pln",
            native_currency="PLN",
            native_amount=Decimal("-4100.00"),
            eur_amount=Decimal("-953.4884"),
            fx_rate=Decimal("4.3000"),
            fx_rate_source="broker_reported",
        )
    )
    mappings = TickerMappings(isin={_ISIN: "IWDA.AS"}, symbol={})

    outcome = pipeline.rebuild_positions(conn, user_id=user_id, mappings=mappings)

    assert outcome.anomalies == []
    eur_snapshot = positions_repo.snapshots[(account_eur.id, "IWDA.AS")][0]
    pln_snapshot = positions_repo.snapshots[(account_pln.id, "IWDA.AS")][0]
    assert eur_snapshot.native_currency == "EUR"
    assert pln_snapshot.native_currency == "PLN"
    assert eur_snapshot.average_cost_eur != pln_snapshot.average_cost_eur


def test_rebuild_positions_surfaces_calculator_anomalies(repos, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    transactions_repo.saved.append(
        _position_txn(
            account_id=account.id,
            transaction_type="SELL",
            quantity=Decimal("-10"),
            native_amount=Decimal("950.00"),
            eur_amount=Decimal("950.0000"),
        )
    )
    mappings = TickerMappings(isin={_ISIN: "IWDA.AS"}, symbol={})

    outcome = pipeline.rebuild_positions(conn, user_id=user_id, mappings=mappings)

    assert len(outcome.anomalies) == 1
    assert "IWDA.AS" in outcome.anomalies[0]


def test_rebuild_positions_prunes_a_ticker_no_longer_produced(repos, conn):
    """A ticker mapping re-pointed to a different canonical_ticker leaves the old ticker's
    snapshots stale; a full rebuild removes them rather than letting them survive forever."""
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    transactions_repo.saved.append(_position_txn(account_id=account.id))
    positions_repo.snapshots[(account.id, "OLD.AS")] = ["stale"]
    mappings = TickerMappings(isin={_ISIN: "IWDA.AS"}, symbol={})

    outcome = pipeline.rebuild_positions(conn, user_id=user_id, mappings=mappings)

    assert outcome.positions_removed == 1
    assert (account.id, "OLD.AS") not in positions_repo.snapshots


def test_rebuild_positions_does_not_prune_when_the_account_has_a_blocked_instrument(repos, conn):
    """A blocked instrument's old canonical_ticker is indistinguishable from a genuinely stale
    one, so pruning is skipped for that account rather than risking a wrong delete."""
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    transactions_repo.saved.append(_position_txn(account_id=account.id))
    positions_repo.snapshots[(account.id, "OLD.AS")] = ["stale"]
    mappings = TickerMappings(isin={}, symbol={})  # now unmapped -> blocked

    outcome = pipeline.rebuild_positions(conn, user_id=user_id, mappings=mappings)

    assert outcome.positions_removed == 0
    assert (account.id, "OLD.AS") in positions_repo.snapshots


def test_rebuild_positions_with_no_transactions_reports_nothing(repos, conn):
    accounts_repo, _, _ = repos
    user_id = uuid.uuid4()
    _seed_account(accounts_repo, user_id=user_id)
    mappings = TickerMappings(isin={}, symbol={})

    outcome = pipeline.rebuild_positions(conn, user_id=user_id, mappings=mappings)

    assert outcome.positions_rebuilt == 0
    assert outcome.snapshots_saved == 0
    assert outcome.blocked == []
