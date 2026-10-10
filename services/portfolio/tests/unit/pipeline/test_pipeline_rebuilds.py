"""pipeline.rebuild_positions and pipeline.rebuild_returns against the in-memory repository
fakes in conftest.py. Split out of test_pipeline.py to keep both under the repo's 64KB limit."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from portfolio import pipeline
from portfolio.datastore_client.base import PricePoint, TickerInfo
from portfolio.errors import (
    AccountNotFound,
)
from portfolio.positions.calculator import PositionSnapshot
from portfolio.returns.calculator import ReturnSnapshot
from portfolio.ticker_mapping.base import TickerMappings
from portfolio.transactions.base import CanonicalTransaction

_ISIN = "IE00B4L5Y983"

NOW = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)


class FakeDataStoreClient:
    """The three data-store calls rebuild_returns makes, each with its own call log."""

    def __init__(
        self,
        price_series=None,
        fx_series=None,
        ticker_info=None,
        price_series_errors=None,
        ticker_info_errors=None,
    ):
        self._price_series = price_series or {}
        self._fx_series = fx_series or {}
        self._ticker_info = ticker_info or {}
        self._price_series_errors = price_series_errors or {}
        self._ticker_info_errors = ticker_info_errors or {}
        self.price_series_calls = []
        self.fx_rate_series_calls = []
        self.ticker_info_calls = []

    def get_price_series(self, ticker, start, end):
        self.price_series_calls.append((ticker, start, end))
        if ticker in self._price_series_errors:
            raise self._price_series_errors[ticker]
        return self._price_series.get(ticker, [])

    def get_fx_rate_series(self, base, quote, start, end):
        self.fx_rate_series_calls.append((base, quote, start, end))
        return self._fx_series.get((base, quote), [])

    def get_ticker_info(self, ticker):
        self.ticker_info_calls.append(ticker)
        if ticker in self._ticker_info_errors:
            raise self._ticker_info_errors[ticker]
        return self._ticker_info.get(ticker, TickerInfo(exists=True, has_returns=True))


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


def test_rebuild_positions_fetches_every_account_in_one_batched_call(repos, conn):
    """A whole-portfolio rebuild must not query each account's transactions separately -- one
    call to list_by_accounts covering every account, not one call per account (regression test
    for the N+1 query pattern list_by_account, one call per account, used to cost)."""
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    account_a = _seed_account(accounts_repo, user_id=user_id, label="a")
    account_b = _seed_account(accounts_repo, user_id=user_id, label="b")
    transactions_repo.saved.append(_position_txn(account_id=account_a.id))
    transactions_repo.saved.append(
        _position_txn(account_id=account_b.id, broker_transaction_ref="ref-2")
    )
    mappings = TickerMappings(isin={_ISIN: "IWDA.AS"}, symbol={})

    pipeline.rebuild_positions(conn, user_id=user_id, mappings=mappings)

    assert len(transactions_repo.list_by_accounts_calls) == 1
    assert sorted(transactions_repo.list_by_accounts_calls[0]) == sorted(
        [account_a.id, account_b.id]
    )


def test_rebuild_positions_ignores_non_position_moving_transaction_types(repos, conn):
    """A DIVIDEND row (or any non-BUY/SELL type) must never be checked against the ticker
    mapping at all -- not resolved, not reported as blocked -- the same as before this read
    moved its BUY/SELL filtering into the batched fetch itself."""
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    transactions_repo.saved.append(
        _position_txn(
            account_id=account.id,
            transaction_type="DIVIDEND",
            instrument_identifier="IE00UNMAPPED9",
            quantity=None,
            price=None,
        )
    )
    mappings = TickerMappings(isin={}, symbol={})  # nothing mapped

    outcome = pipeline.rebuild_positions(conn, user_id=user_id, mappings=mappings)

    assert outcome.blocked == []
    assert outcome.positions_rebuilt == 0
    assert positions_repo.snapshots == {}


def test_rebuild_positions_isolates_a_failed_pair_and_continues(repos, conn):
    """A driver-level failure saving one (account, ticker) pair must not abort the run -- every
    other pair still gets rebuilt, and the failure is reported in failed_pairs rather than
    propagating and losing whatever else the run already accomplished."""
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
    mappings = TickerMappings(isin={_ISIN: "IWDA.AS", "IE00XXXXXXX9": "VUSA.AS"}, symbol={})
    positions_repo.failing_pairs.add((account.id, "IWDA.AS"))

    outcome = pipeline.rebuild_positions(conn, user_id=user_id, mappings=mappings)

    assert outcome.failed_pairs == [
        (account.account_label, "IWDA.AS", "simulated failure for IWDA.AS")
    ]
    assert outcome.positions_rebuilt == 1
    assert (account.id, "VUSA.AS") in positions_repo.snapshots
    assert (account.id, "IWDA.AS") not in positions_repo.snapshots


def test_rebuild_positions_a_failed_prune_is_isolated_too(repos, conn):
    """Same isolation for the pruning path -- a failed delete_snapshots for one stale ticker must
    not stop the run or lose the report of what happened."""
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    transactions_repo.saved.append(_position_txn(account_id=account.id))
    positions_repo.snapshots[(account.id, "OLD.AS")] = ["stale"]
    positions_repo.failing_pairs.add((account.id, "OLD.AS"))
    mappings = TickerMappings(isin={_ISIN: "IWDA.AS"}, symbol={})

    outcome = pipeline.rebuild_positions(conn, user_id=user_id, mappings=mappings)

    assert outcome.failed_pairs == [
        (account.account_label, "OLD.AS", "simulated failure for OLD.AS")
    ]
    assert outcome.positions_removed == 0
    assert (account.id, "OLD.AS") in positions_repo.snapshots  # left in place, not lost


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
    assert outcome.positions_with_anomalies == 1


def test_rebuild_positions_counts_a_ticker_with_two_anomalies_as_one_position(repos, conn):
    """Two anomaly messages for the same (account, ticker) -- a currency mismatch and the
    negative-quantity it triggers -- must still count as one affected position, not two."""
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    transactions_repo.saved.append(_position_txn(account_id=account.id))
    transactions_repo.saved.append(
        _position_txn(
            account_id=account.id,
            broker_transaction_ref="ref-2",
            transaction_type="SELL",
            trade_datetime=datetime(2026, 3, 2, 10, 0, tzinfo=UTC),
            quantity=Decimal("-20"),
            native_currency="PLN",
            native_amount=Decimal("1900.00"),
            eur_amount=Decimal("442.0000"),
            fx_rate=Decimal("4.3000"),
            fx_rate_source="broker_reported",
        )
    )
    mappings = TickerMappings(isin={_ISIN: "IWDA.AS"}, symbol={})

    outcome = pipeline.rebuild_positions(conn, user_id=user_id, mappings=mappings)

    assert len(outcome.anomalies) == 2
    assert outcome.positions_with_anomalies == 1


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


# --- rebuild_returns ---


def _dividend_txn(**overrides):
    kwargs = dict(
        account_id=1,
        broker_transaction_ref="div-1",
        transaction_type="DIVIDEND",
        trade_datetime=NOW,
        instrument_identifier=_ISIN,
        instrument_identifier_kind="ISIN",
        quantity=None,
        price=None,
        native_currency="EUR",
        native_amount=Decimal("12.00"),
        eur_amount=Decimal("12.0000"),
        fx_rate=None,
        fx_rate_source=None,
        fees_native=None,
    )
    kwargs.update(overrides)
    return CanonicalTransaction(**kwargs)


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


def test_rebuild_returns_saves_snapshots_for_a_tracked_ticker(repos, returns_repo, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    positions_repo.snapshots[(account.id, "IWDA.AS")] = [
        _position_snapshot(account_id=account.id, as_of_date=date(2026, 1, 5)),
        _position_snapshot(
            account_id=account.id,
            as_of_date=date(2026, 1, 10),
            quantity=Decimal("0.000000"),
            average_cost_eur=Decimal("0.000000"),
            cost_basis_eur=Decimal("0.0000"),
        ),
    ]
    client = FakeDataStoreClient(
        price_series={
            "IWDA.AS": [PricePoint(date=date(2026, 1, 6), split_adjusted_close=Decimal("100.00"))]
        }
    )
    mappings = TickerMappings(isin={}, symbol={})

    outcome = pipeline.rebuild_returns(
        conn, user_id=user_id, mappings=mappings, datastore_client=client
    )

    assert outcome.blocked == []
    assert outcome.tickers_rebuilt == 1
    assert outcome.snapshots_saved == 1
    assert (account.id, "IWDA.AS") in returns_repo.snapshots


def test_rebuild_returns_reports_an_untracked_ticker_as_blocked(repos, returns_repo, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    positions_repo.snapshots[(account.id, "IWDA.AS")] = [_position_snapshot(account_id=account.id)]
    client = FakeDataStoreClient(
        ticker_info={"IWDA.AS": TickerInfo(exists=False, has_returns=False)}
    )
    mappings = TickerMappings(isin={}, symbol={})

    outcome = pipeline.rebuild_returns(
        conn, user_id=user_id, mappings=mappings, datastore_client=client
    )

    assert outcome.blocked == [(account.account_label, "IWDA.AS")]
    assert outcome.tickers_rebuilt == 0
    assert returns_repo.snapshots == {}


def test_rebuild_returns_rebuilds_every_other_ticker_when_one_is_blocked(repos, returns_repo, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    positions_repo.snapshots[(account.id, "IWDA.AS")] = [
        _position_snapshot(account_id=account.id, canonical_ticker="IWDA.AS")
    ]
    positions_repo.snapshots[(account.id, "VUSA.AS")] = [
        _position_snapshot(account_id=account.id, canonical_ticker="VUSA.AS")
    ]
    client = FakeDataStoreClient(
        ticker_info={"IWDA.AS": TickerInfo(exists=False, has_returns=False)},
        price_series={
            "VUSA.AS": [PricePoint(date=date(2026, 1, 6), split_adjusted_close=Decimal("50.00"))]
        },
    )
    mappings = TickerMappings(isin={}, symbol={})

    outcome = pipeline.rebuild_returns(
        conn, user_id=user_id, mappings=mappings, datastore_client=client
    )

    assert outcome.blocked == [(account.account_label, "IWDA.AS")]
    assert outcome.tickers_rebuilt == 1
    assert (account.id, "VUSA.AS") in returns_repo.snapshots


def test_rebuild_returns_isolates_a_failed_pair_and_continues(repos, returns_repo, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    positions_repo.snapshots[(account.id, "IWDA.AS")] = [
        _position_snapshot(account_id=account.id, canonical_ticker="IWDA.AS")
    ]
    positions_repo.snapshots[(account.id, "VUSA.AS")] = [
        _position_snapshot(account_id=account.id, canonical_ticker="VUSA.AS")
    ]
    client = FakeDataStoreClient(
        price_series={
            "VUSA.AS": [PricePoint(date=date(2026, 1, 6), split_adjusted_close=Decimal("50.00"))]
        },
        price_series_errors={"IWDA.AS": RuntimeError("data-store unreachable")},
    )
    mappings = TickerMappings(isin={}, symbol={})

    outcome = pipeline.rebuild_returns(
        conn, user_id=user_id, mappings=mappings, datastore_client=client
    )

    assert outcome.failed_pairs == [(account.account_label, "IWDA.AS", "data-store unreachable")]
    assert outcome.tickers_rebuilt == 1
    assert (account.id, "VUSA.AS") in returns_repo.snapshots
    assert (account.id, "IWDA.AS") not in returns_repo.snapshots


def test_rebuild_returns_treats_a_ticker_info_failure_as_a_failed_pair_not_blocked(
    repos, returns_repo, conn
):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    positions_repo.snapshots[(account.id, "IWDA.AS")] = [_position_snapshot(account_id=account.id)]
    client = FakeDataStoreClient(ticker_info_errors={"IWDA.AS": RuntimeError("data-store down")})
    mappings = TickerMappings(isin={}, symbol={})

    outcome = pipeline.rebuild_returns(
        conn, user_id=user_id, mappings=mappings, datastore_client=client
    )

    assert outcome.blocked == []
    assert outcome.failed_pairs == [(account.account_label, "IWDA.AS", "data-store down")]


def test_rebuild_returns_caches_ticker_info_across_accounts(repos, returns_repo, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account_a = _seed_account(accounts_repo, user_id=user_id, label="a")
    account_b = _seed_account(accounts_repo, user_id=user_id, label="b")
    positions_repo.snapshots[(account_a.id, "IWDA.AS")] = [
        _position_snapshot(account_id=account_a.id)
    ]
    positions_repo.snapshots[(account_b.id, "IWDA.AS")] = [
        _position_snapshot(account_id=account_b.id)
    ]
    client = FakeDataStoreClient(
        price_series={
            "IWDA.AS": [PricePoint(date=date(2026, 1, 6), split_adjusted_close=Decimal("100.00"))]
        }
    )
    mappings = TickerMappings(isin={}, symbol={})

    pipeline.rebuild_returns(conn, user_id=user_id, mappings=mappings, datastore_client=client)

    assert client.ticker_info_calls == ["IWDA.AS"]


def test_rebuild_returns_scopes_to_one_account_via_account_label(repos, returns_repo, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account_a = _seed_account(accounts_repo, user_id=user_id, label="a")
    account_b = _seed_account(accounts_repo, user_id=user_id, label="b")
    positions_repo.snapshots[(account_a.id, "IWDA.AS")] = [
        _position_snapshot(account_id=account_a.id)
    ]
    positions_repo.snapshots[(account_b.id, "IWDA.AS")] = [
        _position_snapshot(account_id=account_b.id)
    ]
    client = FakeDataStoreClient(
        price_series={
            "IWDA.AS": [PricePoint(date=date(2026, 1, 6), split_adjusted_close=Decimal("100.00"))]
        }
    )
    mappings = TickerMappings(isin={}, symbol={})

    outcome = pipeline.rebuild_returns(
        conn, user_id=user_id, mappings=mappings, datastore_client=client, account_label="a"
    )

    assert outcome.tickers_rebuilt == 1
    assert (account_a.id, "IWDA.AS") in returns_repo.snapshots
    assert (account_b.id, "IWDA.AS") not in returns_repo.snapshots


def test_rebuild_returns_raises_account_not_found_for_a_bad_label(repos, returns_repo, conn):
    with pytest.raises(AccountNotFound):
        pipeline.rebuild_returns(
            conn,
            user_id=uuid.uuid4(),
            mappings=TickerMappings(isin={}, symbol={}),
            datastore_client=FakeDataStoreClient(),
            account_label="nope",
        )


def test_rebuild_returns_is_isolated_per_user(repos, returns_repo, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    account_a = _seed_account(accounts_repo, user_id=user_a, label="a")
    _seed_account(accounts_repo, user_id=user_b, label="b")
    positions_repo.snapshots[(account_a.id, "IWDA.AS")] = [
        _position_snapshot(account_id=account_a.id)
    ]
    client = FakeDataStoreClient(
        price_series={
            "IWDA.AS": [PricePoint(date=date(2026, 1, 6), split_adjusted_close=Decimal("100.00"))]
        }
    )

    outcome = pipeline.rebuild_returns(
        conn, user_id=user_b, mappings=TickerMappings(isin={}, symbol={}), datastore_client=client
    )

    assert outcome.tickers_rebuilt == 0


def test_rebuild_returns_commits_once_per_pair(repos, returns_repo, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    positions_repo.snapshots[(account.id, "IWDA.AS")] = [
        _position_snapshot(account_id=account.id, canonical_ticker="IWDA.AS")
    ]
    positions_repo.snapshots[(account.id, "VUSA.AS")] = [
        _position_snapshot(account_id=account.id, canonical_ticker="VUSA.AS")
    ]
    client = FakeDataStoreClient(
        price_series={
            "IWDA.AS": [PricePoint(date=date(2026, 1, 6), split_adjusted_close=Decimal("100.00"))],
            "VUSA.AS": [PricePoint(date=date(2026, 1, 6), split_adjusted_close=Decimal("50.00"))],
        }
    )
    commits_before = conn.commits

    pipeline.rebuild_returns(
        conn,
        user_id=user_id,
        mappings=TickerMappings(isin={}, symbol={}),
        datastore_client=client,
    )

    assert conn.commits - commits_before == 2


def test_rebuild_returns_uses_todays_date_as_the_window_end_for_an_open_position(
    repos, returns_repo, conn
):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    positions_repo.snapshots[(account.id, "IWDA.AS")] = [
        _position_snapshot(
            account_id=account.id, as_of_date=date(2026, 1, 5), quantity=Decimal("10.000000")
        )
    ]
    client = FakeDataStoreClient(price_series={"IWDA.AS": []})
    mappings = TickerMappings(isin={}, symbol={})

    pipeline.rebuild_returns(conn, user_id=user_id, mappings=mappings, datastore_client=client)

    ticker, start, end = client.price_series_calls[0]
    assert ticker == "IWDA.AS"
    assert start == date(2026, 1, 5)
    assert end == date.today()


def test_rebuild_returns_uses_the_closing_date_as_the_window_end_for_a_closed_position(
    repos, returns_repo, conn
):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    positions_repo.snapshots[(account.id, "IWDA.AS")] = [
        _position_snapshot(account_id=account.id, as_of_date=date(2026, 1, 5)),
        _position_snapshot(
            account_id=account.id,
            as_of_date=date(2026, 1, 10),
            quantity=Decimal("0.000000"),
            average_cost_eur=Decimal("0.000000"),
            cost_basis_eur=Decimal("0.0000"),
        ),
    ]
    client = FakeDataStoreClient(price_series={"IWDA.AS": []})
    mappings = TickerMappings(isin={}, symbol={})

    pipeline.rebuild_returns(conn, user_id=user_id, mappings=mappings, datastore_client=client)

    _, start, end = client.price_series_calls[0]
    assert start == date(2026, 1, 5)
    assert end == date(2026, 1, 10)


def test_rebuild_returns_resolves_dividend_transactions_via_mappings(repos, returns_repo, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    positions_repo.snapshots[(account.id, "IWDA.AS")] = [
        _position_snapshot(account_id=account.id, as_of_date=date(2026, 1, 5))
    ]
    transactions_repo.saved.append(
        _dividend_txn(
            account_id=account.id,
            trade_datetime=datetime(2026, 1, 8, 10, tzinfo=UTC),
            eur_amount=Decimal("15.0000"),
        )
    )
    client = FakeDataStoreClient(
        price_series={
            "IWDA.AS": [PricePoint(date=date(2026, 1, 9), split_adjusted_close=Decimal("100.00"))]
        }
    )
    mappings = TickerMappings(isin={_ISIN: "IWDA.AS"}, symbol={})

    pipeline.rebuild_returns(conn, user_id=user_id, mappings=mappings, datastore_client=client)

    saved = returns_repo.snapshots[(account.id, "IWDA.AS")][date(2026, 1, 9)]
    assert saved.dividends_received_eur == Decimal("15.0000")


def test_rebuild_returns_excludes_an_unresolvable_dividend_without_blocking_the_pair(
    repos, returns_repo, conn
):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    positions_repo.snapshots[(account.id, "IWDA.AS")] = [_position_snapshot(account_id=account.id)]
    transactions_repo.saved.append(
        _dividend_txn(account_id=account.id, instrument_identifier="IE00UNMAPPED9")
    )
    client = FakeDataStoreClient(
        price_series={
            "IWDA.AS": [PricePoint(date=date(2026, 1, 6), split_adjusted_close=Decimal("100.00"))]
        }
    )
    mappings = TickerMappings(isin={}, symbol={})  # the dividend's own ISIN is unmapped too

    outcome = pipeline.rebuild_returns(
        conn, user_id=user_id, mappings=mappings, datastore_client=client
    )

    assert outcome.blocked == []
    saved = returns_repo.snapshots[(account.id, "IWDA.AS")][date(2026, 1, 6)]
    assert saved.dividends_received_eur == Decimal("0.0000")


def test_rebuild_returns_excludes_a_cash_only_dividend_without_aborting_the_run(
    repos, returns_repo, conn
):
    """A DIVIDEND row with no instrument attached at all (instrument_identifier and
    instrument_identifier_kind both None -- validate() permits this, cli.py's --instrument is
    optional) must be treated the same as an unresolvable one: silently excluded from
    dividends_received_eur, not a crash that aborts every other account/ticker in the run."""
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    positions_repo.snapshots[(account.id, "IWDA.AS")] = [_position_snapshot(account_id=account.id)]
    transactions_repo.saved.append(
        _dividend_txn(
            account_id=account.id, instrument_identifier=None, instrument_identifier_kind=None
        )
    )
    client = FakeDataStoreClient(
        price_series={
            "IWDA.AS": [PricePoint(date=date(2026, 1, 6), split_adjusted_close=Decimal("100.00"))]
        }
    )
    mappings = TickerMappings(isin={}, symbol={})

    outcome = pipeline.rebuild_returns(
        conn, user_id=user_id, mappings=mappings, datastore_client=client
    )

    assert outcome.blocked == []
    assert outcome.failed_pairs == []
    saved = returns_repo.snapshots[(account.id, "IWDA.AS")][date(2026, 1, 6)]
    assert saved.dividends_received_eur == Decimal("0.0000")


def test_rebuild_returns_reports_a_ticker_with_no_position_snapshots_as_blocked(
    repos, returns_repo, conn
):
    """list_tickers and list_snapshots are two separate reads of the same table -- if a ticker
    list_tickers returned has no rows left by the time list_snapshots runs (e.g. a concurrent
    rebuild-positions run), that must be reported in `blocked`, never a silent skip."""
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    positions_repo.snapshots[(account.id, "IWDA.AS")] = []
    client = FakeDataStoreClient()

    outcome = pipeline.rebuild_returns(
        conn, user_id=user_id, mappings=TickerMappings(isin={}, symbol={}), datastore_client=client
    )

    assert outcome.blocked == [(account.account_label, "IWDA.AS")]
    assert outcome.tickers_rebuilt == 0
    assert returns_repo.snapshots == {}


def test_rebuild_returns_fetches_dividend_transactions_in_one_batched_call(
    repos, returns_repo, conn
):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account_a = _seed_account(accounts_repo, user_id=user_id, label="a")
    account_b = _seed_account(accounts_repo, user_id=user_id, label="b")
    positions_repo.snapshots[(account_a.id, "IWDA.AS")] = [
        _position_snapshot(account_id=account_a.id)
    ]
    positions_repo.snapshots[(account_b.id, "IWDA.AS")] = [
        _position_snapshot(account_id=account_b.id)
    ]
    client = FakeDataStoreClient(
        price_series={
            "IWDA.AS": [PricePoint(date=date(2026, 1, 6), split_adjusted_close=Decimal("100.00"))]
        }
    )

    pipeline.rebuild_returns(
        conn, user_id=user_id, mappings=TickerMappings(isin={}, symbol={}), datastore_client=client
    )

    assert len(transactions_repo.list_by_accounts_calls) == 1
    assert sorted(transactions_repo.list_by_accounts_calls[0]) == sorted(
        [account_a.id, account_b.id]
    )


def test_rebuild_returns_with_no_tracked_tickers_reports_nothing(repos, returns_repo, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    _seed_account(accounts_repo, user_id=user_id)
    client = FakeDataStoreClient()

    outcome = pipeline.rebuild_returns(
        conn, user_id=user_id, mappings=TickerMappings(isin={}, symbol={}), datastore_client=client
    )

    assert outcome.tickers_rebuilt == 0
    assert outcome.snapshots_saved == 0
    assert outcome.blocked == []
    assert outcome.failed_pairs == []


def test_rebuild_returns_replaces_a_pairs_rows_rather_than_merging_them(repos, returns_repo, conn):
    """An earlier BUY deleted moves the window's start later, so rows from before it must go --
    an upsert would keep them and the chart would still show the deleted holding's days."""
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    stale_date = date(2026, 1, 2)
    returns_repo.snapshots[(account.id, "IWDA.AS")] = {
        stale_date: _return_snapshot(account_id=account.id, price_date=stale_date)
    }
    positions_repo.snapshots[(account.id, "IWDA.AS")] = [
        _position_snapshot(account_id=account.id, as_of_date=date(2026, 1, 5))
    ]
    client = FakeDataStoreClient(
        price_series={
            "IWDA.AS": [PricePoint(date=date(2026, 1, 6), split_adjusted_close=Decimal("100.00"))]
        }
    )

    outcome = pipeline.rebuild_returns(
        conn, user_id=user_id, mappings=TickerMappings(isin={}, symbol={}), datastore_client=client
    )

    assert outcome.tickers_rebuilt == 1
    assert list(returns_repo.snapshots[(account.id, "IWDA.AS")]) == [date(2026, 1, 6)]


def test_rebuild_returns_leaves_a_pairs_rows_alone_when_its_price_fetch_fails(
    repos, returns_repo, conn
):
    """The replacement happens only once the new rows are computed, so a data-store failure
    leaves the previous rows in place rather than an empty chart."""
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    kept = _return_snapshot(account_id=account.id, price_date=date(2026, 1, 6))
    returns_repo.snapshots[(account.id, "IWDA.AS")] = {kept.price_date: kept}
    positions_repo.snapshots[(account.id, "IWDA.AS")] = [_position_snapshot(account_id=account.id)]
    client = FakeDataStoreClient(price_series_errors={"IWDA.AS": RuntimeError("data-store down")})

    outcome = pipeline.rebuild_returns(
        conn, user_id=user_id, mappings=TickerMappings(isin={}, symbol={}), datastore_client=client
    )

    assert [pair[1] for pair in outcome.failed_pairs] == ["IWDA.AS"]
    assert returns_repo.replace_calls == []
    assert returns_repo.snapshots[(account.id, "IWDA.AS")] == {kept.price_date: kept}


def test_rebuild_returns_prunes_a_ticker_with_no_position_snapshots_left(
    repos, returns_repo, conn, caplog
):
    """Deleting a ticker's only BUY (or re-pointing its mapping) removes its position snapshots;
    its return rows must follow, or the account and home pages show a holding that's gone."""
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    positions_repo.snapshots[(account.id, "IWDA.AS")] = [_position_snapshot(account_id=account.id)]
    returns_repo.snapshots[(account.id, "GONE.AS")] = {
        date(2026, 1, 6): _return_snapshot(
            account_id=account.id, canonical_ticker="GONE.AS", price_date=date(2026, 1, 6)
        )
    }
    client = FakeDataStoreClient(
        price_series={
            "IWDA.AS": [PricePoint(date=date(2026, 1, 6), split_adjusted_close=Decimal("100.00"))]
        }
    )

    with caplog.at_level("INFO", logger="portfolio.pipeline"):
        outcome = pipeline.rebuild_returns(
            conn,
            user_id=user_id,
            mappings=TickerMappings(isin={}, symbol={}),
            datastore_client=client,
        )

    assert outcome.tickers_removed == 1
    assert (account.id, "GONE.AS") not in returns_repo.snapshots
    assert (account.id, "IWDA.AS") in returns_repo.snapshots
    assert "ticker GONE.AS -- stale return snapshot(s) removed" in caplog.text


def test_rebuild_returns_keeps_a_blocked_tickers_rows(repos, returns_repo, conn):
    """A ticker data-store doesn't track still has position snapshots, so it isn't stale: its
    rows stay, the same "blocked means don't touch" the positions rebuild follows."""
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    positions_repo.snapshots[(account.id, "IWDA.AS")] = [_position_snapshot(account_id=account.id)]
    kept = _return_snapshot(account_id=account.id, price_date=date(2026, 1, 6))
    returns_repo.snapshots[(account.id, "IWDA.AS")] = {kept.price_date: kept}
    client = FakeDataStoreClient(
        ticker_info={"IWDA.AS": TickerInfo(exists=False, has_returns=False)}
    )

    outcome = pipeline.rebuild_returns(
        conn, user_id=user_id, mappings=TickerMappings(isin={}, symbol={}), datastore_client=client
    )

    assert outcome.tickers_removed == 0
    assert returns_repo.delete_calls == []
    assert returns_repo.snapshots[(account.id, "IWDA.AS")] == {kept.price_date: kept}


def test_rebuild_returns_isolates_a_failed_prune_and_continues(repos, returns_repo, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    for ticker in ("OLD.AS", "GONE.AS"):
        returns_repo.snapshots[(account.id, ticker)] = {
            date(2026, 1, 6): _return_snapshot(
                account_id=account.id, canonical_ticker=ticker, price_date=date(2026, 1, 6)
            )
        }
    returns_repo.failing_pairs.add((account.id, "OLD.AS"))

    outcome = pipeline.rebuild_returns(
        conn,
        user_id=user_id,
        mappings=TickerMappings(isin={}, symbol={}),
        datastore_client=FakeDataStoreClient(),
    )

    assert outcome.tickers_removed == 1
    assert [pair[1] for pair in outcome.failed_pairs] == ["OLD.AS"]
    assert (account.id, "OLD.AS") in returns_repo.snapshots
    assert (account.id, "GONE.AS") not in returns_repo.snapshots


def test_rebuild_returns_prunes_only_the_rebuilt_accounts_rows(repos, returns_repo, conn):
    accounts_repo, transactions_repo, positions_repo = repos
    user_id = uuid.uuid4()
    account_a = _seed_account(accounts_repo, user_id=user_id, label="a")
    account_b = _seed_account(accounts_repo, user_id=user_id, label="b")
    for account in (account_a, account_b):
        returns_repo.snapshots[(account.id, "GONE.AS")] = {
            date(2026, 1, 6): _return_snapshot(
                account_id=account.id, canonical_ticker="GONE.AS", price_date=date(2026, 1, 6)
            )
        }

    outcome = pipeline.rebuild_returns(
        conn,
        user_id=user_id,
        mappings=TickerMappings(isin={}, symbol={}),
        datastore_client=FakeDataStoreClient(),
        account_label="a",
    )

    assert outcome.tickers_removed == 1
    assert (account_a.id, "GONE.AS") not in returns_repo.snapshots
    assert (account_b.id, "GONE.AS") in returns_repo.snapshots


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
