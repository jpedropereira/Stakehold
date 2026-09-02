"""rebuild-positions against live Postgres: the delete-then-insert replacement semantics a fake
repository can't prove (a re-run replaces rows rather than duplicating them), and multi-user
isolation through the real accounts/transactions join -- see docs/architecture.md's testing
section.

Seeds transactions via `add-transaction` (manual entry) through the real CLI/pipeline/repository
stack -- this test is about positions/repository.py's delete-then-insert contract and
pipeline.rebuild_positions' account/user scoping, not about any particular broker's parsing.
"""

import uuid

import pytest
import yaml
from portfolio import cli

pytestmark = pytest.mark.integration

_ISIN = "IE00B4L5Y983"


@pytest.fixture(autouse=True)
def _ticker_mappings(monkeypatch, tmp_path):
    """The default configs/ticker_mappings.yaml is real, gitignored broker data (task 15) and
    doesn't exist in a dev checkout -- point PORTFOLIO_TICKER_MAPPINGS_PATH at a throwaway file
    with just what this test needs, the same absolute-path override settings.py already supports.
    """
    path = tmp_path / "ticker_mappings.yaml"
    path.write_text(yaml.dump({"isin": {_ISIN: "IWDA.AS"}}))
    monkeypatch.setenv("PORTFOLIO_TICKER_MAPPINGS_PATH", str(path))
    return path


def _add_account(user_id, *, label, broker="acme"):
    exit_code = cli.main(
        ["account", "add", "--user", str(user_id), "--broker", broker, "--label", label]
    )
    assert exit_code == 0


def _add_buy(user_id, *, account_label, ref, currency="EUR", amount="-950.00", identifier=_ISIN):
    exit_code = cli.main(
        [
            "add-transaction",
            "--user",
            str(user_id),
            "--account",
            account_label,
            "--ref",
            ref,
            "--type",
            "BUY",
            "--datetime",
            "2026-01-05T10:00:00+00:00",
            "--instrument",
            identifier,
            "--instrument-kind",
            "ISIN",
            "--quantity",
            "10",
            "--price",
            "95.00",
            "--amount",
            amount,
            "--currency",
            currency,
        ]
    )
    assert exit_code == 0


def _rebuild(user_id, *, account_label=None):
    args = ["rebuild-positions", "--user", str(user_id)]
    if account_label is not None:
        args += ["--account", account_label]
    return cli.main(args)


def test_rebuilding_writes_a_position_snapshot(pg_connect, capsys):
    user_id = uuid.uuid4()
    _add_account(user_id, label="m")
    _add_buy(user_id, account_label="m", ref="t1")
    capsys.readouterr()

    exit_code = _rebuild(user_id)
    out = capsys.readouterr().out

    assert exit_code == 0
    assert out == "1 position snapshot(s) saved\n"
    with pg_connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT quantity, average_cost_native, native_currency, average_cost_eur, "
            "cost_basis_eur FROM position_snapshots"
        )
        rows = cur.fetchall()
    assert rows == [(10, 95, "EUR", 95, 950)]


def test_rebuilding_twice_replaces_rather_than_duplicates(pg_connect):
    """The behaviour a fake repository can't prove: delete-then-insert, not upsert."""
    user_id = uuid.uuid4()
    _add_account(user_id, label="m")
    _add_buy(user_id, account_label="m", ref="t1")
    _rebuild(user_id)

    _add_buy(user_id, account_label="m", ref="t2", amount="-1050.00")
    exit_code = _rebuild(user_id)

    with pg_connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM position_snapshots")
        (count,) = cur.fetchone()
    assert exit_code == 0
    assert count == 1  # one date, one row -- not two from the first rebuild plus the second


def test_rebuild_is_scoped_to_one_account_via_the_account_flag(pg_connect):
    user_id = uuid.uuid4()
    _add_account(user_id, label="a")
    _add_account(user_id, label="b")
    _add_buy(user_id, account_label="a", ref="t1")
    _add_buy(user_id, account_label="b", ref="t2")

    exit_code = _rebuild(user_id, account_label="a")

    assert exit_code == 0
    with pg_connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM position_snapshots")
        (count,) = cur.fetchone()
    assert count == 1


def test_rebuild_is_isolated_per_user(pg_connect):
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    _add_account(user_a, label="a")
    _add_account(user_b, label="b")
    _add_buy(user_a, account_label="a", ref="t1")
    _add_buy(user_b, account_label="b", ref="t2")

    exit_code = _rebuild(user_a)

    assert exit_code == 0
    with pg_connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT account_id FROM position_snapshots")
        rows = cur.fetchall()
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM accounts WHERE user_id = %s AND account_label = %s",
                (user_a, "a"),
            )
            (account_a_id,) = cur.fetchone()
    assert rows == [(account_a_id,)]


def test_an_unmapped_instrument_blocks_its_rebuild_and_exits_nonzero(
    monkeypatch, tmp_path, pg_connect, capsys
):
    empty_mappings = tmp_path / "empty.yaml"
    empty_mappings.write_text(yaml.dump({"isin": {}}))
    monkeypatch.setenv("PORTFOLIO_TICKER_MAPPINGS_PATH", str(empty_mappings))
    user_id = uuid.uuid4()
    _add_account(user_id, label="m")
    _add_buy(user_id, account_label="m", ref="t1")
    capsys.readouterr()

    exit_code = _rebuild(user_id)
    out = capsys.readouterr().out

    assert exit_code == 1
    assert f"ISIN\t{_ISIN}\tacme" in out
    with pg_connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM position_snapshots")
        (count,) = cur.fetchone()
    assert count == 0
