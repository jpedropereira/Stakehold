"""Broker import against live Postgres: the idempotent-reimport contract end-to-end through the
CLI, and list_distinct_instruments' multi-user isolation, which only a real join against
`accounts` can prove -- see docs/architecture.md's testing table.

Uses a synthetic BrokerParser (monkeypatched into pipeline.BROKER_PARSERS), not a real broker
plugin -- this test is really about transactions/repository.py's idempotent-insert and
multi-user-isolation semantics, not about parsing any particular broker's export file. A real
plugin (e.g. stakehold-brokers, installed separately) isn't a dependency of this suite.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from portfolio import cli, pipeline
from portfolio.transactions.base import CanonicalTransaction
from portfolio.transactions.repository import list_distinct_instruments

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
_ISINS = ("TEST0000AAA1", "TEST0000AAA2", "TEST0000AAA3")


class _FakeParser:
    """Four transactions across three distinct instruments -- enough to exercise both the
    idempotent-reimport contract and list_distinct_instruments' isolation/broker-attribution,
    without depending on any real broker's export shape."""

    broker_key = "acme"

    def parse(self, file_path, account_id):
        return [
            CanonicalTransaction(
                account_id=account_id,
                broker_transaction_ref=f"synthetic-ref-{i}",
                transaction_type="BUY",
                trade_datetime=_NOW,
                instrument_identifier=isin,
                instrument_identifier_kind="ISIN",
                quantity=Decimal("1"),
                price=Decimal("10.00"),
                native_currency="EUR",
                native_amount=Decimal("-10.00"),
                eur_amount=Decimal("-10.0000"),
                fx_rate=None,
                fx_rate_source=None,
                fees_native=None,
            )
            for i, isin in enumerate([_ISINS[0], _ISINS[0], _ISINS[1], _ISINS[2]])
        ]


@pytest.fixture(autouse=True)
def _fake_broker_parser(monkeypatch):
    monkeypatch.setattr(pipeline, "BROKER_PARSERS", {"acme": _FakeParser()})


def _add_account(user_id, *, label, broker):
    exit_code = cli.main(
        ["account", "add", "--user", str(user_id), "--broker", broker, "--label", label]
    )
    assert exit_code == 0


def _import(user_id, *, label, file_path=Path("synthetic.csv")):
    return cli.main(
        [
            "import-transactions",
            "--user",
            str(user_id),
            "--file",
            str(file_path),
            "--account",
            label,
        ]
    )


def test_importing_a_file_twice_is_idempotent(capsys):
    user_id = uuid.uuid4()
    _add_account(user_id, label="m", broker="acme")
    capsys.readouterr()

    first_exit = _import(user_id, label="m")
    first_out = capsys.readouterr().out

    second_exit = _import(user_id, label="m")
    second_out = capsys.readouterr().out

    assert first_exit == 0
    assert first_out == "4 transaction(s) saved\n"
    assert second_exit == 0
    assert second_out == "0 transaction(s) saved\n"


def test_list_distinct_instruments_is_isolated_per_user(pg_connect):
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    _add_account(user_a, label="a", broker="acme")
    _add_account(user_b, label="b", broker="acme")

    _import(user_a, label="a")

    with pg_connect() as conn:
        result_a = list_distinct_instruments(conn, user_a)
        result_b = list_distinct_instruments(conn, user_b)

    assert result_a == [("ISIN", isin, "acme") for isin in _ISINS]
    assert result_b == []


def test_list_distinct_instruments_carries_the_owning_accounts_broker(pg_connect):
    user_id = uuid.uuid4()
    _add_account(user_id, label="a", broker="acme")
    _import(user_id, label="a")

    with pg_connect() as conn:
        result = list_distinct_instruments(conn, user_id)

    assert all(broker == "acme" for _, _, broker in result)
