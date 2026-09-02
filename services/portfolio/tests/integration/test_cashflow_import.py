"""import_cashflow against live Postgres: the idempotent-reimport contract and broker-mismatch
rejection -- see docs/architecture.md's testing section.

Uses a synthetic CashflowParser (monkeypatched into pipeline.CASHFLOW_PARSERS) and a stub
DataStoreClient, not a real broker plugin or a live data-store instance -- this test is really
about transactions/repository.py's idempotent-insert semantics and import_cashflow's own
account-resolution/broker-mismatch logic, not about parsing any particular broker's cash-ledger
format. A real plugin (e.g. stakehold-brokers, installed separately) isn't a dependency of this
suite; jobs.run_import_cashflow's own construction of the real HttpDataStoreClient from settings
is covered by tests/unit/test_jobs.py instead.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from portfolio import cli, pipeline
from portfolio.errors import BrokerMismatch
from portfolio.transactions.base import CanonicalTransaction

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
_RATES = {("EUR", "PLN", date(2026, 3, 1)): Decimal("4.30")}


class StubDataStoreClient:
    def get_fx_rate(self, base, quote, on_date):
        return _RATES.get((base, quote, on_date))


class _FakeCashflowParser:
    """Two synthetic cash events -- one EUR (no FX needed), one PLN (exercises the
    resolve_fx_rate plumbing end-to-end against the stub client above)."""

    broker_key = "acme"

    def parse(self, file_path, account_id, resolve_fx_rate):
        pln_rate = resolve_fx_rate("PLN", _NOW.date())
        return [
            CanonicalTransaction(
                account_id=account_id,
                broker_transaction_ref="synthetic-cashflow-1",
                transaction_type="DEPOSIT",
                trade_datetime=_NOW,
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
            ),
            CanonicalTransaction(
                account_id=account_id,
                broker_transaction_ref="synthetic-cashflow-2",
                transaction_type="DEPOSIT",
                trade_datetime=_NOW,
                instrument_identifier=None,
                instrument_identifier_kind=None,
                quantity=None,
                price=None,
                native_currency="PLN",
                native_amount=Decimal("430.00"),
                eur_amount=Decimal("430.00") / pln_rate,
                fx_rate=pln_rate,
                fx_rate_source="datastore_series",
                fees_native=None,
            ),
        ]


@pytest.fixture(autouse=True)
def _fake_cashflow_parser(monkeypatch):
    monkeypatch.setattr(pipeline, "CASHFLOW_PARSERS", {"acme": _FakeCashflowParser()})


def _add_account(user_id, *, label="acme-main", broker="acme"):
    exit_code = cli.main(
        ["account", "add", "--user", str(user_id), "--broker", broker, "--label", label]
    )
    assert exit_code == 0


def _import(pg_connect, user_id, *, label="acme-main"):
    with pg_connect() as conn:
        saved = pipeline.import_cashflow(
            conn,
            user_id=user_id,
            account_label=label,
            broker="acme",
            file_path=Path("synthetic_cashflow.csv"),
            datastore_client=StubDataStoreClient(),
        )
        conn.commit()
    return saved


def test_importing_a_cashflow_file_twice_is_idempotent(pg_connect):
    user_id = uuid.uuid4()
    _add_account(user_id)

    first = _import(pg_connect, user_id)
    second = _import(pg_connect, user_id)

    assert first == 2
    assert second == 0


def test_cashflow_import_requires_an_acme_account(pg_connect):
    user_id = uuid.uuid4()
    _add_account(user_id, label="globex-main", broker="globex")

    with pg_connect() as conn:
        with pytest.raises(BrokerMismatch):
            pipeline.import_cashflow(
                conn,
                user_id=user_id,
                account_label="globex-main",
                broker="acme",
                file_path=Path("synthetic_cashflow.csv"),
                datastore_client=StubDataStoreClient(),
            )
