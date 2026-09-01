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
    BrokerMismatch,
    InvalidTransaction,
    UnknownBroker,
)
from portfolio.ticker_mapping.loader import TickerMappings
from portfolio.transactions.base import CanonicalTransaction

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


@pytest.fixture
def repos(monkeypatch):
    accounts_repo = FakeAccountsRepo()
    transactions_repo = FakeTransactionsRepo(accounts_repo)
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
    _, transactions_repo = repos
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
            broker="acme",
            file_path=Path("f.csv"),
        )


def test_import_transactions_rejects_an_unknown_broker(repos, conn):
    with pytest.raises(UnknownBroker):
        pipeline.import_transactions(
            conn,
            user_id=uuid.uuid4(),
            account_label="m",
            broker="robinhood",
            file_path=Path("f.csv"),
        )


def test_import_transactions_rejects_a_broker_mismatch(repos, conn, monkeypatch):
    monkeypatch.setattr(
        pipeline, "BROKER_PARSERS", {"acme": FakeParser("acme"), "globex": FakeParser("globex")}
    )
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")

    with pytest.raises(BrokerMismatch):
        pipeline.import_transactions(
            conn, user_id=user_id, account_label="m", broker="globex", file_path=Path("f.csv")
        )


def test_import_transactions_parses_and_saves_transactions(repos, conn, monkeypatch):
    _, transactions_repo = repos
    parser = FakeParser("acme", make_transactions=_txn_factory())
    monkeypatch.setattr(pipeline, "BROKER_PARSERS", {"acme": parser})
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    commits_before = conn.commits

    saved = pipeline.import_transactions(
        conn, user_id=user_id, account_label="m", broker="acme", file_path=Path("f.csv")
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
    pipeline.import_transactions(
        conn, user_id=user_id, account_label="m", broker="acme", file_path=Path("f.csv")
    )

    saved = pipeline.import_transactions(
        conn, user_id=user_id, account_label="m", broker="acme", file_path=Path("f.csv")
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
            conn, user_id=user_id, account_label="m", broker="acme", file_path=Path("f.csv")
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
            conn, user_id=user_id, account_label="m", broker="acme", file_path=Path("f.csv")
        )


# --- list_unmapped_instruments ---


def test_list_unmapped_instruments_returns_only_unmapped_triples(repos, conn, monkeypatch):
    parser = FakeParser("acme", make_transactions=_txn_factory())
    monkeypatch.setattr(pipeline, "BROKER_PARSERS", {"acme": parser})
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    pipeline.import_transactions(
        conn, user_id=user_id, account_label="m", broker="acme", file_path=Path("f.csv")
    )
    mappings = TickerMappings(isin={}, symbol={})

    unmapped = pipeline.list_unmapped_instruments(conn, user_id=user_id, mappings=mappings)

    assert unmapped == [("ISIN", "IE00B4L5Y983", "acme")]


def test_list_unmapped_instruments_excludes_mapped_ones(repos, conn, monkeypatch):
    parser = FakeParser("acme", make_transactions=_txn_factory())
    monkeypatch.setattr(pipeline, "BROKER_PARSERS", {"acme": parser})
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    pipeline.import_transactions(
        conn, user_id=user_id, account_label="m", broker="acme", file_path=Path("f.csv")
    )
    mappings = TickerMappings(isin={"IE00B4L5Y983": "IWDA.AS"}, symbol={})

    unmapped = pipeline.list_unmapped_instruments(conn, user_id=user_id, mappings=mappings)

    assert unmapped == []


def test_list_unmapped_instruments_never_commits(repos, conn):
    mappings = TickerMappings(isin={}, symbol={})

    pipeline.list_unmapped_instruments(conn, user_id=uuid.uuid4(), mappings=mappings)

    assert conn.commits == 0


# --- import_acme_cashflows ---


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


def test_import_acme_cashflows_requires_an_existing_account(repos, conn, monkeypatch):
    monkeypatch.setattr(pipeline, "parse_cashflows", lambda *a, **kw: [])

    with pytest.raises(AccountNotFound):
        pipeline.import_acme_cashflows(
            conn,
            user_id=uuid.uuid4(),
            account_label="nonexistent",
            file_path=Path("f.csv"),
            datastore_client=FakeDataStoreClient(),
        )


def test_import_acme_cashflows_rejects_a_non_acme_account(repos, conn, monkeypatch):
    monkeypatch.setattr(pipeline, "parse_cashflows", lambda *a, **kw: [])
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="globex", account_label="m")

    with pytest.raises(BrokerMismatch):
        pipeline.import_acme_cashflows(
            conn,
            user_id=user_id,
            account_label="m",
            file_path=Path("f.csv"),
            datastore_client=FakeDataStoreClient(),
        )


def test_import_acme_cashflows_parses_and_saves_transactions(repos, conn, monkeypatch):
    _, transactions_repo = repos
    monkeypatch.setattr(
        pipeline,
        "parse_cashflows",
        lambda path, account_id, resolve: _cashflow_txn_factory()(account_id),
    )
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    commits_before = conn.commits

    saved = pipeline.import_acme_cashflows(
        conn,
        user_id=user_id,
        account_label="m",
        file_path=Path("f.csv"),
        datastore_client=FakeDataStoreClient(),
    )

    assert saved == 1
    assert transactions_repo.saved[0].broker_transaction_ref == "cashflow-1"
    assert conn.commits - commits_before == 1


def test_import_acme_cashflows_passes_a_working_fx_resolver_through_to_the_parser(
    repos, conn, monkeypatch
):
    captured = {}

    def fake_parse(path, account_id, resolve_fx_rate):
        captured["resolve_fx_rate"] = resolve_fx_rate
        return []

    monkeypatch.setattr(pipeline, "parse_cashflows", fake_parse)
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    client = FakeDataStoreClient(rates={("EUR", "PLN", date(2026, 1, 1)): Decimal("4.30")})

    pipeline.import_acme_cashflows(
        conn, user_id=user_id, account_label="m", file_path=Path("f.csv"), datastore_client=client
    )

    rate = captured["resolve_fx_rate"]("PLN", date(2026, 1, 1))
    assert rate == Decimal("4.30")
    assert client.calls == [("EUR", "PLN", date(2026, 1, 1))]


def test_import_acme_cashflows_is_idempotent_on_reimport(repos, conn, monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "parse_cashflows",
        lambda path, account_id, resolve: _cashflow_txn_factory()(account_id),
    )
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    pipeline.import_acme_cashflows(
        conn,
        user_id=user_id,
        account_label="m",
        file_path=Path("f.csv"),
        datastore_client=FakeDataStoreClient(),
    )

    saved = pipeline.import_acme_cashflows(
        conn,
        user_id=user_id,
        account_label="m",
        file_path=Path("f.csv"),
        datastore_client=FakeDataStoreClient(),
    )

    assert saved == 0


def test_import_acme_cashflows_propagates_a_broker_file_error_without_committing(
    repos, conn, monkeypatch
):
    def boom(path, account_id, resolve):
        raise BrokerFileError("bad file")

    monkeypatch.setattr(pipeline, "parse_cashflows", boom)
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    commits_before = conn.commits

    with pytest.raises(BrokerFileError):
        pipeline.import_acme_cashflows(
            conn,
            user_id=user_id,
            account_label="m",
            file_path=Path("f.csv"),
            datastore_client=FakeDataStoreClient(),
        )

    assert conn.commits == commits_before
