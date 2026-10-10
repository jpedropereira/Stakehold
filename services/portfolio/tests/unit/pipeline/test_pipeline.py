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
from portfolio.datastore_client.base import TickerInfo
from portfolio.errors import (
    AccountLabelConflict,
    AccountNotFound,
    BrokerFileError,
    BrokerParserNotFound,
    BrokerPluginError,
    InvalidBroker,
    InvalidTickerMapping,
    InvalidTransaction,
    TickerMappingNotFound,
    UnknownTicker,
)
from portfolio.positions.calculator import PositionSnapshot
from portfolio.returns.calculator import ReturnSnapshot
from portfolio.ticker_mapping.base import TickerMappings
from portfolio.ticker_mapping.repository import TickerMappingRow
from portfolio.transactions.base import CanonicalTransaction

_ISIN = "IE00B4L5Y983"

NOW = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)


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


class _VanishingAccountsRepo:
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


def test_list_available_brokers_reports_which_imports_each_broker_supports(monkeypatch):
    """A broker key can sit in either registry or both -- each one is listed once, with every
    import kind it supports, so a caller can offer exactly those and nothing else."""
    monkeypatch.setattr(pipeline, "BROKER_PARSERS", {"globex": object(), "acme": object()})
    monkeypatch.setattr(pipeline, "CASHFLOW_PARSERS", {"initech": object(), "acme": object()})

    assert pipeline.list_available_brokers() == [
        pipeline.AvailableBroker(broker="acme", imports=("cashflow", "transactions")),
        pipeline.AvailableBroker(broker="globex", imports=("transactions",)),
        pipeline.AvailableBroker(broker="initech", imports=("cashflow",)),
    ]


def test_list_available_brokers_returns_empty_list_with_no_plugins_installed(monkeypatch):
    monkeypatch.setattr(pipeline, "BROKER_PARSERS", {})
    monkeypatch.setattr(pipeline, "CASHFLOW_PARSERS", {})

    assert pipeline.list_available_brokers() == []


def test_list_available_brokers_raises_a_stashed_discovery_error(monkeypatch):
    monkeypatch.setattr(pipeline, "_BROKER_DISCOVERY_ERROR", BrokerPluginError("boom"))

    with pytest.raises(BrokerPluginError, match="boom"):
        pipeline.list_available_brokers()


def test_list_available_brokers_raises_a_stashed_cashflow_discovery_error(monkeypatch):
    """It reads both registries now, so a broken cashflow plugin is reported too, rather than
    silently listing its brokers as having no cashflow import."""
    monkeypatch.setattr(pipeline, "_CASHFLOW_DISCOVERY_ERROR", BrokerPluginError("bust"))

    with pytest.raises(BrokerPluginError, match="bust"):
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
        price=Decimal("100"),
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

    _add_txn(conn, user_id, "m", fees_native=Decimal("1.50"), native_amount=Decimal("-956.50"))

    assert transactions_repo.saved[0].fees_native == Decimal("1.50")


# --- add_manual_transaction: the amount of a BUY/SELL is derived ---


def _account(conn, broker="acme"):
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker=broker, account_label="m")
    return user_id


def test_a_buy_without_an_amount_derives_it_as_cash_out_including_fees(repos, conn):
    _, transactions_repo, _ = repos
    user_id = _account(conn)

    _add_txn(conn, user_id, "m", native_amount=None, fees_native=Decimal("1.50"))

    # -(10 * 95.50) - 1.50
    assert transactions_repo.saved[0].native_amount == Decimal("-956.5000")
    assert transactions_repo.saved[0].eur_amount == Decimal("-956.5000")


def test_a_sell_without_an_amount_derives_it_as_cash_in_net_of_fees(repos, conn):
    _, transactions_repo, _ = repos
    user_id = _account(conn)

    _add_txn(
        conn,
        user_id,
        "m",
        transaction_type="SELL",
        quantity=Decimal("-10"),
        price=Decimal("100"),
        fees_native=Decimal("2"),
        native_amount=None,
    )

    # 10 * 100 - 2
    assert transactions_repo.saved[0].native_amount == Decimal("998.0000")


def test_a_derived_amount_is_converted_to_eur_with_the_manual_rate(repos, conn):
    _, transactions_repo, _ = repos
    user_id = _account(conn)

    _add_txn(
        conn,
        user_id,
        "m",
        native_currency="USD",
        fx_rate=Decimal("1.25"),
        quantity=Decimal("10"),
        price=Decimal("100"),
        native_amount=None,
    )

    assert transactions_repo.saved[0].native_amount == Decimal("-1000.0000")
    assert transactions_repo.saved[0].eur_amount == Decimal("-800.0000")


def test_a_derived_amount_is_rounded_half_up_to_four_places(repos, conn):
    _, transactions_repo, _ = repos
    user_id = _account(conn)

    _add_txn(
        conn, user_id, "m", quantity=Decimal("1"), price=Decimal("0.00005"), native_amount=None
    )

    assert transactions_repo.saved[0].native_amount == Decimal("-0.0001")


def test_a_typed_amount_within_a_cent_of_the_derived_one_is_kept_as_typed(repos, conn):
    """The broker's own figure can differ from price * quantity + fees by a rounding cent --
    that figure is the one to keep."""
    _, transactions_repo, _ = repos
    user_id = _account(conn)

    _add_txn(conn, user_id, "m", native_amount=Decimal("-954.99"))

    assert transactions_repo.saved[0].native_amount == Decimal("-954.99")


@pytest.mark.parametrize(
    "typed",
    [Decimal("10"), Decimal("955.00"), Decimal("-955.02")],
    ids=["typo", "wrong-sign", "two-cents-off"],
)
def test_a_typed_amount_that_disagrees_with_the_trade_is_rejected(repos, conn, typed):
    _, transactions_repo, _ = repos
    user_id = _account(conn)
    commits_before = conn.commits

    with pytest.raises(InvalidTransaction, match="-955.00"):
        _add_txn(conn, user_id, "m", native_amount=typed)

    assert transactions_repo.saved == []
    assert conn.commits == commits_before


def test_a_cash_transaction_still_requires_an_amount(repos, conn):
    user_id = _account(conn)

    with pytest.raises(InvalidTransaction, match="DEPOSIT requires an amount"):
        _add_txn(
            conn,
            user_id,
            "m",
            transaction_type="DEPOSIT",
            instrument_identifier=None,
            instrument_identifier_kind=None,
            quantity=None,
            price=None,
            native_amount=None,
        )


def test_a_buy_missing_its_price_and_amount_names_what_is_missing(repos, conn):
    user_id = _account(conn)

    with pytest.raises(InvalidTransaction, match="BUY requires a quantity and a price"):
        _add_txn(conn, user_id, "m", price=None, native_amount=None)


def test_add_manual_transaction_saves_a_ticker_data_store_knows(repos, conn):
    _, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    client = FakeDataStoreClient()

    saved = _add_txn(
        conn,
        user_id,
        "m",
        instrument_identifier="IWDA.AS",
        instrument_identifier_kind="TICKER",
        datastore_client=client,
    )

    assert saved == 1
    assert client.ticker_info_calls == ["IWDA.AS"]
    assert transactions_repo.saved[0].instrument_identifier_kind == "TICKER"


def test_add_manual_transaction_rejects_a_ticker_data_store_does_not_know(repos, conn):
    _, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    commits_before = conn.commits
    client = FakeDataStoreClient(
        ticker_info={"IWDA.AM": TickerInfo(exists=False, has_returns=False)}
    )

    with pytest.raises(UnknownTicker, match="IWDA.AM"):
        _add_txn(
            conn,
            user_id,
            "m",
            instrument_identifier="IWDA.AM",
            instrument_identifier_kind="TICKER",
            datastore_client=client,
        )

    assert transactions_repo.saved == []
    assert conn.commits == commits_before


def test_add_manual_transaction_requires_a_data_store_client_for_a_ticker(repos, conn):
    """A TICKER row is only trustworthy once data-store has confirmed it -- saving one
    unchecked is exactly how a typo like IWDA.AM got in, so a missing client is a caller bug."""
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")

    with pytest.raises(ValueError, match="datastore_client"):
        _add_txn(
            conn,
            user_id,
            "m",
            instrument_identifier="IWDA.AS",
            instrument_identifier_kind="TICKER",
        )


def test_add_manual_transaction_does_not_check_an_isin_against_data_store(repos, conn):
    user_id = uuid.uuid4()
    pipeline.add_account(conn, user_id=user_id, broker="acme", account_label="m")
    client = FakeDataStoreClient()

    _add_txn(conn, user_id, "m", datastore_client=client)

    assert client.ticker_info_calls == []


def test_list_tickers_returns_the_data_store_list():
    client = FakeDataStoreClient(tickers=["AAPL", "IWDA.AS"])

    assert pipeline.list_tickers(datastore_client=client) == ["AAPL", "IWDA.AS"]


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
    """The data-store calls manual entry, cash-ledger imports and mapping writes make.
    `.calls` is get_fx_rate's own 3-tuple log, which import_cashflow's tests depend on. The
    rebuild tests have their own fake in test_pipeline_rebuilds.py."""

    def __init__(self, rates=None, tickers=None, ticker_info=None):
        self._rates = rates or {}
        self._tickers = tickers or []
        self._ticker_info = ticker_info or {}
        self.calls = []
        self.ticker_info_calls = []

    def get_fx_rate(self, base, quote, on_date):
        self.calls.append((base, quote, on_date))
        return self._rates.get((base, quote, on_date))

    def get_ticker_info(self, ticker):
        self.ticker_info_calls.append(ticker)
        return self._ticker_info.get(ticker, TickerInfo(exists=True, has_returns=True))

    def list_tickers(self):
        return list(self._tickers)


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


# --- shared with test_pipeline_rebuilds.py, which keeps its own copies ---


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


def _position_snapshot(**overrides):
    values = dict(
        account_id=1,
        canonical_ticker="IWDA.AS",
        as_of_date=date(2026, 1, 5),
        quantity=Decimal("10.000000"),
        average_cost_native=Decimal("95.000000"),
        native_currency="EUR",
        average_cost_eur=Decimal("95.000000"),
        cost_basis_eur=Decimal("950.0000"),
    )
    values.update(overrides)
    return PositionSnapshot(**values)


# --- list_transactions ---


def _return_snapshot(**overrides):
    values = dict(
        account_id=1,
        canonical_ticker="IWDA.AS",
        price_date=date(2026, 1, 6),
        quantity=Decimal("10.000000"),
        market_value_native=Decimal("1000.0000"),
        market_value_eur=Decimal("1000.0000"),
        cost_basis_eur=Decimal("950.0000"),
        unrealized_return_eur=Decimal("50.0000"),
        unrealized_return_pct=Decimal("0.05263158"),
        dividends_received_eur=Decimal("0.0000"),
        total_return_eur=Decimal("50.0000"),
        total_return_pct=Decimal("0.05263158"),
    )
    values.update(overrides)
    return ReturnSnapshot(**values)


def test_list_transactions_returns_the_accounts_own_log(repos, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    transactions_repo.saved.append(_position_txn(account_id=account.id))

    result = pipeline.list_transactions(conn, user_id=user_id, account_label="m")

    assert result == [_position_txn(account_id=account.id)]


def test_list_transactions_raises_for_an_unknown_account_label(repos, conn):
    with pytest.raises(AccountNotFound):
        pipeline.list_transactions(conn, user_id=uuid.uuid4(), account_label="nope")


def test_list_transactions_never_returns_another_users_account(repos, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    owner, other = uuid.uuid4(), uuid.uuid4()
    _seed_account(accounts_repo, user_id=owner, label="m")

    with pytest.raises(AccountNotFound):
        pipeline.list_transactions(conn, user_id=other, account_label="m")


# --- list_positions ---


def test_list_positions_returns_the_latest_snapshot_per_ticker(repos, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    positions_repo.snapshots[(account.id, "IWDA.AS")] = [_position_snapshot(account_id=account.id)]

    result = pipeline.list_positions(conn, user_id=user_id, account_label="m")

    assert result == [_position_snapshot(account_id=account.id)]


def test_list_positions_raises_for_an_unknown_account_label(repos, conn):
    with pytest.raises(AccountNotFound):
        pipeline.list_positions(conn, user_id=uuid.uuid4(), account_label="nope")


# --- list_returns ---


def test_list_returns_returns_this_accounts_history(repos, returns_repo, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    returns_repo.snapshots[(account.id, "IWDA.AS")] = {
        date(2026, 1, 6): _return_snapshot(account_id=account.id)
    }

    result = pipeline.list_returns(conn, user_id=user_id, account_label="m")

    assert result == [_return_snapshot(account_id=account.id)]


def test_list_returns_filters_by_ticker_and_date_range(repos, returns_repo, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    returns_repo.snapshots[(account.id, "IWDA.AS")] = {
        date(2026, 1, 6): _return_snapshot(account_id=account.id, price_date=date(2026, 1, 6))
    }
    returns_repo.snapshots[(account.id, "VUSA.AS")] = {
        date(2026, 1, 6): _return_snapshot(
            account_id=account.id, canonical_ticker="VUSA.AS", price_date=date(2026, 1, 6)
        )
    }

    result = pipeline.list_returns(
        conn,
        user_id=user_id,
        account_label="m",
        canonical_ticker="IWDA.AS",
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
    )

    assert [s.canonical_ticker for s in result] == ["IWDA.AS"]


def test_list_returns_raises_for_an_unknown_account_label(repos, returns_repo, conn):
    with pytest.raises(AccountNotFound):
        pipeline.list_returns(conn, user_id=uuid.uuid4(), account_label="nope")


# --- list_latest_returns ---


def test_list_latest_returns_returns_one_row_per_ticker(repos, returns_repo, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    returns_repo.snapshots[(account.id, "IWDA.AS")] = {
        date(2026, 1, 5): _return_snapshot(account_id=account.id, price_date=date(2026, 1, 5)),
        date(2026, 1, 6): _return_snapshot(account_id=account.id, price_date=date(2026, 1, 6)),
    }

    result = pipeline.list_latest_returns(conn, user_id=user_id, account_label="m")

    assert [s.price_date for s in result] == [date(2026, 1, 6)]


def test_list_latest_returns_raises_for_an_unknown_account_label(repos, returns_repo, conn):
    with pytest.raises(AccountNotFound):
        pipeline.list_latest_returns(conn, user_id=uuid.uuid4(), account_label="nope")


# --- ticker mappings (admin) ---


class FakeTickerMappingRepo:
    """Keyed the way the real table's two partial unique indexes are: (kind, identifier) for an
    ISIN, (kind, identifier, broker) for a SYMBOL -- broker is None for every ISIN row."""

    def __init__(self):
        self.rows: dict[tuple, TickerMappingRow] = {}
        self._next_id = 1

    def upsert(self, conn, *, identifier_kind, identifier, broker, canonical_ticker, updated_by):
        key = (identifier_kind, identifier, broker)
        existing = self.rows.get(key)
        row = TickerMappingRow(
            id=existing.id if existing else self._next_id,
            identifier_kind=identifier_kind,
            identifier=identifier,
            broker=broker,
            canonical_ticker=canonical_ticker,
            updated_by=updated_by,
            created_at=existing.created_at if existing else NOW,
            updated_at=NOW,
        )
        if existing is None:
            self._next_id += 1
        self.rows[key] = row
        return row

    def delete(self, conn, mapping_id):
        for key, row in self.rows.items():
            if row.id == mapping_id:
                del self.rows[key]
                return row
        return None

    def list_all(self, conn):
        return sorted(self.rows.values(), key=lambda r: r.id)

    def load_all(self, conn):
        mappings = TickerMappings(isin={}, symbol={})
        for row in self.rows.values():
            if row.identifier_kind == "ISIN":
                mappings.isin[row.identifier] = row.canonical_ticker
            else:
                mappings.symbol.setdefault(row.broker, {})[row.identifier] = row.canonical_ticker
        return mappings


@pytest.fixture
def mapping_repo(monkeypatch):
    fake = FakeTickerMappingRepo()
    monkeypatch.setattr(pipeline, "ticker_mapping_repository", fake)
    return fake


_ADMIN = uuid.UUID("00000000-0000-7000-8000-000000000001")


def _set_mapping(conn, **overrides):
    kwargs = dict(
        identifier_kind="ISIN",
        identifier=_ISIN,
        broker=None,
        canonical_ticker="IWDA.AS",
        updated_by=_ADMIN,
        datastore_client=FakeDataStoreClient(),
    )
    kwargs.update(overrides)
    return pipeline.set_ticker_mapping(conn, **kwargs)


def test_set_ticker_mapping_saves_an_isin_mapping_in_one_transaction(mapping_repo, conn):
    row = _set_mapping(conn)

    assert (row.identifier_kind, row.identifier, row.broker, row.canonical_ticker) == (
        "ISIN",
        _ISIN,
        None,
        "IWDA.AS",
    )
    assert row.updated_by == _ADMIN
    assert conn.commits == 1


def test_set_ticker_mapping_saves_a_symbol_mapping_scoped_to_its_broker(mapping_repo, conn):
    row = _set_mapping(conn, identifier_kind="SYMBOL", identifier="IWDA", broker="globex")

    assert (row.identifier_kind, row.identifier, row.broker) == ("SYMBOL", "IWDA", "globex")


def test_set_ticker_mapping_repoints_an_existing_mapping(mapping_repo, conn):
    first = _set_mapping(conn)

    second = _set_mapping(conn, canonical_ticker="IWDA.L")

    assert second.id == first.id
    assert second.canonical_ticker == "IWDA.L"
    assert len(mapping_repo.rows) == 1


def test_set_ticker_mapping_strips_whitespace(mapping_repo, conn):
    row = _set_mapping(
        conn,
        identifier_kind="SYMBOL",
        identifier="  IWDA ",
        broker=" globex ",
        canonical_ticker=" IWDA.AS ",
    )

    assert (row.identifier, row.broker, row.canonical_ticker) == ("IWDA", "globex", "IWDA.AS")


def test_set_ticker_mapping_treats_a_blank_broker_as_none_for_an_isin(mapping_repo, conn):
    row = _set_mapping(conn, broker="  ")

    assert row.broker is None


def test_set_ticker_mapping_rejects_an_isin_mapping_with_a_broker(mapping_repo, conn):
    with pytest.raises(InvalidTickerMapping, match="every broker"):
        _set_mapping(conn, broker="globex")

    assert mapping_repo.rows == {}


def test_set_ticker_mapping_rejects_a_symbol_mapping_without_a_broker(mapping_repo, conn):
    with pytest.raises(InvalidTickerMapping, match="needs a broker"):
        _set_mapping(conn, identifier_kind="SYMBOL", identifier="IWDA", broker=None)


@pytest.mark.parametrize("kind", ["TICKER", "CUSIP", ""])
def test_set_ticker_mapping_rejects_a_kind_that_is_not_mapped(mapping_repo, conn, kind):
    # TICKER is a valid identifier kind, but it never needs a mapping: it resolves to itself.
    with pytest.raises(InvalidTickerMapping, match="ISIN or SYMBOL"):
        _set_mapping(conn, identifier_kind=kind)


def test_set_ticker_mapping_rejects_a_blank_identifier(mapping_repo, conn):
    with pytest.raises(InvalidTickerMapping, match="identifier"):
        _set_mapping(conn, identifier="   ")


def test_set_ticker_mapping_rejects_a_blank_ticker(mapping_repo, conn):
    with pytest.raises(InvalidTickerMapping, match="ticker"):
        _set_mapping(conn, canonical_ticker="  ")


@pytest.mark.parametrize(
    "field,value",
    [("identifier", "X" * 51), ("broker", "b" * 51), ("canonical_ticker", "T" * 21)],
)
def test_set_ticker_mapping_rejects_values_longer_than_their_columns(
    mapping_repo, conn, field, value
):
    overrides = {field: value}
    if field == "broker":
        overrides.update(identifier_kind="SYMBOL", identifier="IWDA")

    with pytest.raises(InvalidTickerMapping, match="too long"):
        _set_mapping(conn, **overrides)


def test_set_ticker_mapping_rejects_a_ticker_data_store_does_not_have(mapping_repo, conn):
    client = FakeDataStoreClient(
        ticker_info={"IWDA.AM": TickerInfo(exists=False, has_returns=False)}
    )

    with pytest.raises(UnknownTicker, match="IWDA.AM"):
        _set_mapping(conn, canonical_ticker="IWDA.AM", datastore_client=client)

    assert client.ticker_info_calls == ["IWDA.AM"]
    assert mapping_repo.rows == {}
    assert conn.commits == 0


def test_delete_ticker_mapping_removes_it_and_returns_the_deleted_row(mapping_repo, conn):
    saved = _set_mapping(conn)

    deleted = pipeline.delete_ticker_mapping(conn, mapping_id=saved.id)

    assert deleted == saved
    assert mapping_repo.rows == {}
    assert conn.commits == 2


def test_delete_ticker_mapping_raises_for_an_unknown_id(mapping_repo, conn):
    with pytest.raises(TickerMappingNotFound):
        pipeline.delete_ticker_mapping(conn, mapping_id=999)


def test_list_ticker_mappings_returns_every_mapping(mapping_repo, conn):
    isin = _set_mapping(conn)
    symbol = _set_mapping(conn, identifier_kind="SYMBOL", identifier="IWDA", broker="globex")

    assert pipeline.list_ticker_mappings(conn) == [isin, symbol]


def test_load_ticker_mappings_reads_the_table(mapping_repo, conn):
    _set_mapping(conn)

    mappings = pipeline.load_ticker_mappings(conn)

    assert mappings.resolve("ISIN", _ISIN, broker="anything") == "IWDA.AS"


def _seed_instrument(repos, *, user_id, label, broker, kind, identifier, ref):
    accounts_repo, transactions_repo, _ = repos
    account = accounts_repo.get_by_label(None, user_id=user_id, account_label=label)
    if account is None:
        accounts_repo.insert_account(None, user_id=user_id, broker=broker, account_label=label)
        account = accounts_repo.get_by_label(None, user_id=user_id, account_label=label)
    transactions_repo.insert_transactions(
        None,
        [
            _position_txn(
                account_id=account.id,
                broker_transaction_ref=ref,
                instrument_identifier=identifier,
                instrument_identifier_kind=kind,
            )
        ],
    )


def test_list_all_unmapped_instruments_spans_every_user(repos, conn):
    ann, bea = uuid.uuid4(), uuid.uuid4()
    _seed_instrument(
        repos, user_id=ann, label="a", broker="acme", kind="ISIN", identifier=_ISIN, ref="1"
    )
    _seed_instrument(
        repos, user_id=bea, label="b", broker="globex", kind="SYMBOL", identifier="SGLD", ref="2"
    )
    mappings = TickerMappings(isin={}, symbol={})

    unmapped = pipeline.list_all_unmapped_instruments(conn, mappings=mappings)

    assert unmapped == [("ISIN", _ISIN, None), ("SYMBOL", "SGLD", "globex")]


def test_list_all_unmapped_instruments_lists_an_isin_once_whatever_its_brokers(repos, conn):
    # An ISIN mapping covers every broker, so the brokers reporting it don't matter here.
    ann, bea = uuid.uuid4(), uuid.uuid4()
    _seed_instrument(
        repos, user_id=ann, label="a", broker="acme", kind="ISIN", identifier=_ISIN, ref="1"
    )
    _seed_instrument(
        repos, user_id=bea, label="b", broker="globex", kind="ISIN", identifier=_ISIN, ref="2"
    )

    unmapped = pipeline.list_all_unmapped_instruments(
        conn, mappings=TickerMappings(isin={}, symbol={})
    )

    assert unmapped == [("ISIN", _ISIN, None)]


def test_list_all_unmapped_instruments_excludes_mapped_and_ticker_instruments(repos, conn):
    user = uuid.uuid4()
    _seed_instrument(
        repos, user_id=user, label="a", broker="acme", kind="ISIN", identifier=_ISIN, ref="1"
    )
    _seed_instrument(
        repos, user_id=user, label="a", broker="acme", kind="TICKER", identifier="SGLD.L", ref="2"
    )

    unmapped = pipeline.list_all_unmapped_instruments(
        conn, mappings=TickerMappings(isin={_ISIN: "IWDA.AS"}, symbol={})
    )

    assert unmapped == []


def test_list_accounts_using_instrument_finds_an_isin_at_every_broker(repos, conn):
    ann, bea = uuid.uuid4(), uuid.uuid4()
    _seed_instrument(
        repos, user_id=ann, label="a", broker="acme", kind="ISIN", identifier=_ISIN, ref="1"
    )
    _seed_instrument(
        repos, user_id=bea, label="b", broker="globex", kind="ISIN", identifier=_ISIN, ref="2"
    )

    accounts = pipeline.list_accounts_using_instrument(
        conn, identifier_kind="ISIN", identifier=_ISIN, broker=None
    )

    assert sorted(accounts, key=lambda pair: pair[1]) == [(ann, "a"), (bea, "b")]


def test_list_accounts_using_instrument_scopes_a_symbol_to_its_broker(repos, conn):
    ann, bea = uuid.uuid4(), uuid.uuid4()
    _seed_instrument(
        repos, user_id=ann, label="a", broker="acme", kind="SYMBOL", identifier="IWDA", ref="1"
    )
    _seed_instrument(
        repos, user_id=bea, label="b", broker="globex", kind="SYMBOL", identifier="IWDA", ref="2"
    )

    accounts = pipeline.list_accounts_using_instrument(
        conn, identifier_kind="SYMBOL", identifier="IWDA", broker="globex"
    )

    assert accounts == [(bea, "b")]


def test_list_user_ids_returns_each_account_owner_once(repos, conn):
    ann, bea = uuid.uuid4(), uuid.uuid4()
    pipeline.add_account(conn, user_id=ann, broker="acme", account_label="a")
    pipeline.add_account(conn, user_id=ann, broker="acme", account_label="b")
    pipeline.add_account(conn, user_id=bea, broker="acme", account_label="a")

    assert sorted(pipeline.list_user_ids(conn), key=str) == sorted([ann, bea], key=str)
