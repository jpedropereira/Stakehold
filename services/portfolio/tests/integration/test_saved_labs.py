"""Saved labs (LF-1) against a real Postgres: the constraints, the cascade, a replace in one
transaction, and that one user's labs never reach another's."""

import uuid
from decimal import Decimal

import psycopg
import pytest
from portfolio import cli
from portfolio.errors import SavedLabNameTaken, SavedLabNotFound
from portfolio.saved_labs import flows
from portfolio.saved_labs.base import LabAsset

pytestmark = pytest.mark.integration

MIX = (LabAsset("VWCE.DE", Decimal("60")), LabAsset("AAPL", Decimal("33.333333")))


def test_a_saved_lab_lists_back_with_its_weights_as_typed(pg_connect):
    user = uuid.uuid4()
    with pg_connect() as conn:
        flows.save_lab(conn, user_id=user, name="Core", assets=MIX, replace=False)
        labs = flows.list_labs(conn, user_id=user)

    assert len(labs) == 1
    assert labs[0].name == "Core"
    assert labs[0].assets == MIX
    assert labs[0].created_at == labs[0].updated_at


def test_labs_list_by_name_with_assets_in_saved_order(pg_connect):
    user = uuid.uuid4()
    reversed_mix = tuple(reversed(MIX))
    with pg_connect() as conn:
        flows.save_lab(conn, user_id=user, name="Tech", assets=reversed_mix, replace=False)
        flows.save_lab(conn, user_id=user, name="Core", assets=MIX, replace=False)
        labs = flows.list_labs(conn, user_id=user)

    assert [(lab.name, lab.assets) for lab in labs] == [("Core", MIX), ("Tech", reversed_mix)]


def test_a_taken_name_is_refused_and_leaves_the_lab_alone(pg_connect):
    user = uuid.uuid4()
    with pg_connect() as conn:
        flows.save_lab(conn, user_id=user, name="Core", assets=MIX, replace=False)
        with pytest.raises(SavedLabNameTaken):
            flows.save_lab(
                conn,
                user_id=user,
                name="Core",
                assets=(LabAsset("MSFT", Decimal("100")),),
                replace=False,
            )
        labs = flows.list_labs(conn, user_id=user)

    assert labs[0].assets == MIX


def test_replace_swaps_the_assets_and_bumps_updated_at(pg_connect):
    user = uuid.uuid4()
    new_mix = (LabAsset("MSFT", Decimal("100")),)
    with pg_connect() as conn:
        flows.save_lab(conn, user_id=user, name="Core", assets=MIX, replace=False)
        outcome = flows.save_lab(conn, user_id=user, name="Core", assets=new_mix, replace=True)
        (lab,) = flows.list_labs(conn, user_id=user)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM saved_labs WHERE user_id = %s", (user,))
            (count,) = cur.fetchone()

    assert outcome.created is False
    assert count == 1
    assert lab.assets == new_mix
    assert lab.updated_at > lab.created_at


def test_a_failed_replace_rolls_back_to_the_old_assets(pg_connect, monkeypatch):
    user = uuid.uuid4()
    with pg_connect() as conn:
        flows.save_lab(conn, user_id=user, name="Core", assets=MIX, replace=False)

        def fail(*args, **kwargs):
            raise RuntimeError("insert failed")

        monkeypatch.setattr(flows.repository, "insert_assets", fail)
        with pytest.raises(RuntimeError):
            flows.save_lab(
                conn,
                user_id=user,
                name="Core",
                assets=(LabAsset("MSFT", Decimal("100")),),
                replace=True,
            )
        monkeypatch.undo()
        (lab,) = flows.list_labs(conn, user_id=user)

    assert lab.assets == MIX


def test_names_are_scoped_to_their_owner(pg_connect):
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    with pg_connect() as conn:
        flows.save_lab(conn, user_id=mine, name="Core", assets=MIX, replace=False)
        flows.save_lab(conn, user_id=theirs, name="Core", assets=MIX[:1], replace=False)
        with pytest.raises(SavedLabNotFound):
            flows.delete_lab(conn, user_id=theirs, name="Mine only")
        mine_labs = flows.list_labs(conn, user_id=mine)
        their_labs = flows.list_labs(conn, user_id=theirs)

    assert mine_labs[0].assets == MIX
    assert their_labs[0].assets == MIX[:1]


def test_deleting_a_lab_cascades_to_its_assets(pg_connect):
    user = uuid.uuid4()
    with pg_connect() as conn:
        flows.save_lab(conn, user_id=user, name="Core", assets=MIX, replace=False)
        flows.delete_lab(conn, user_id=user, name="Core")
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM saved_lab_assets")
            (assets,) = cur.fetchone()
        labs = flows.list_labs(conn, user_id=user)

    assert labs == []
    assert assets == 0


def test_another_users_lab_cannot_be_deleted(pg_connect):
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    with pg_connect() as conn:
        flows.save_lab(conn, user_id=mine, name="Core", assets=MIX, replace=False)
        with pytest.raises(SavedLabNotFound):
            flows.delete_lab(conn, user_id=theirs, name="Core")
        labs = flows.list_labs(conn, user_id=mine)

    assert len(labs) == 1


_ASSET_INSERT = (
    "INSERT INTO saved_lab_assets (saved_lab_id, position, ticker, weight_pct) VALUES (%s, {})"
)


@pytest.mark.parametrize(
    "values",
    [
        "1, 'VWCE.DE', 10",  # a ticker twice in one lab
        "1, 'MSFT', 0",  # a weight that isn't above 0
        "0, 'MSFT', 10",  # two assets in one place
    ],
)
def test_the_assets_table_constrains_what_code_would_also_refuse(pg_connect, values):
    user = uuid.uuid4()
    with pg_connect() as conn:
        flows.save_lab(conn, user_id=user, name="Core", assets=MIX[:1], replace=False)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM saved_labs WHERE user_id = %s", (user,))
            (lab_id,) = cur.fetchone()
            with pytest.raises(psycopg.errors.IntegrityError):
                cur.execute(_ASSET_INSERT.format(values), (lab_id,))
        conn.rollback()


def test_the_cli_saves_lists_replaces_and_deletes_a_lab(capsys):
    user = str(uuid.uuid4())
    save = ["lab", "save", "--user", user, "--name", "Core", "--asset", "VWCE.DE=60"]

    assert cli.main([*save, "--asset", "AAPL=40"]) == 0
    assert cli.main(save) == 1  # taken, no --replace
    assert cli.main([*save, "--replace"]) == 0
    assert cli.main(["lab", "list", "--user", user]) == 0
    assert cli.main(["lab", "delete", "--user", user, "--name", "Core"]) == 0
    assert cli.main(["lab", "delete", "--user", user, "--name", "Core"]) == 1

    out, err = capsys.readouterr()
    assert "Lab 'Core' created with 2 asset(s)" in out
    assert "Lab 'Core' replaced with 1 asset(s)" in out
    assert "Core\tVWCE.DE=60\t" in out
    assert "1 lab(s) deleted" in out
    assert "error: You already have a lab called 'Core'." in err
    assert "error: You have no lab called 'Core'." in err
