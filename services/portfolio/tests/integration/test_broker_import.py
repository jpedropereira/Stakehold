"""Broker import against live Postgres: the idempotent-reimport contract end-to-end through the
CLI (unit tests already cover it against fake repositories in tests/unit/test_pipeline.py), and
list_distinct_instruments' multi-user isolation, which only a real join against `accounts` can
prove -- see docs/architecture.md's testing table.
"""

import uuid
from pathlib import Path

import pytest
from portfolio import cli
from portfolio.transactions.repository import list_distinct_instruments

pytestmark = pytest.mark.integration

_ACME_FIXTURE = (
    Path(__file__).parents[1] / "unit" / "broker_import" / "fixtures" / "acme_sample.csv"
)


def _add_account(user_id, *, label, broker):
    exit_code = cli.main(
        ["account", "add", "--user", str(user_id), "--broker", broker, "--label", label]
    )
    assert exit_code == 0


def _import(user_id, *, label, broker="acme", file_path=_ACME_FIXTURE):
    return cli.main(
        [
            "import-transactions",
            "--user",
            str(user_id),
            "--broker",
            broker,
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

    assert result_a == [
        ("ISIN", "IE00B4L5Y983", "acme"),
        ("ISIN", "US4581401001", "acme"),
        ("ISIN", "US7134481081", "acme"),
    ]
    assert result_b == []


def test_list_distinct_instruments_carries_the_owning_accounts_broker(pg_connect):
    user_id = uuid.uuid4()
    _add_account(user_id, label="a", broker="acme")
    _import(user_id, label="a")

    with pg_connect() as conn:
        result = list_distinct_instruments(conn, user_id)

    assert all(broker == "acme" for _, _, broker in result)
