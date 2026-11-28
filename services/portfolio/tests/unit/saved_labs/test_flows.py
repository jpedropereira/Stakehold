"""saved_labs/flows.py: a save, a replace and a delete, each in one transaction. The repository is
an in-memory fake patched onto the module, so the transaction boundary is observed from the
connection's commits and rollbacks."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from portfolio.errors import InvalidSavedLab, SavedLabNameTaken, SavedLabNotFound
from portfolio.saved_labs import flows
from portfolio.saved_labs.base import LabAsset, SavedLab

USER = uuid.uuid4()
OTHER = uuid.uuid4()
NOW = datetime(2026, 11, 28, 9, 0, tzinfo=UTC)
MIX = (LabAsset("VWCE.DE", Decimal("60")), LabAsset("AAPL", Decimal("40")))


class FakeConnection:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeRepo:
    def __init__(self):
        self.labs: dict[tuple, int] = {}
        self.assets: dict[int, tuple[LabAsset, ...]] = {}
        self.replaced: list[int] = []
        self._next_id = 1
        self.vanish_on_replace = False

    def insert_lab(self, conn, *, user_id, name):
        if (user_id, name) in self.labs:
            return None
        self.labs[(user_id, name)] = self._next_id
        self._next_id += 1
        return self.labs[(user_id, name)]

    def replace_lab(self, conn, *, user_id, name):
        if self.vanish_on_replace:
            # Another request deleted the lab between the insert's conflict and this update.
            self.vanish_on_replace = False
            lab_id = self.labs.pop((user_id, name))
            self.assets.pop(lab_id, None)
            return None
        lab_id = self.labs.get((user_id, name))
        if lab_id is not None:
            self.replaced.append(lab_id)
            self.assets.pop(lab_id, None)
        return lab_id

    def insert_assets(self, conn, *, lab_id, assets):
        self.assets[lab_id] = tuple(assets)

    def delete_by_name(self, conn, *, user_id, name):
        lab_id = self.labs.pop((user_id, name), None)
        if lab_id is None:
            return 0
        self.assets.pop(lab_id, None)
        return 1

    def list_by_user(self, conn, user_id):
        return [
            SavedLab(name=name, assets=self.assets[lab_id], created_at=NOW, updated_at=NOW)
            for (owner, name), lab_id in sorted(self.labs.items(), key=lambda kv: kv[0][1])
            if owner == user_id
        ]


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRepo()
    monkeypatch.setattr(flows, "repository", fake)
    return fake


def _lab_assets(repo, user_id, name):
    return repo.assets[repo.labs[(user_id, name)]]


def test_save_lab_creates_a_new_lab_in_one_commit(repo):
    conn = FakeConnection()

    outcome = flows.save_lab(conn, user_id=USER, name=" Core ", assets=MIX, replace=False)

    assert outcome == flows.SaveOutcome(name="Core", created=True, assets=2)
    assert _lab_assets(repo, USER, "Core") == MIX
    assert (conn.commits, conn.rollbacks) == (1, 0)


def test_save_lab_refuses_a_taken_name_without_replace_and_rolls_back(repo):
    conn = FakeConnection()
    flows.save_lab(conn, user_id=USER, name="Core", assets=MIX, replace=False)

    with pytest.raises(SavedLabNameTaken, match="^You already have a lab called 'Core'.$"):
        flows.save_lab(
            conn,
            user_id=USER,
            name="Core",
            assets=(LabAsset("MSFT", Decimal("100")),),
            replace=False,
        )

    assert _lab_assets(repo, USER, "Core") == MIX
    assert conn.rollbacks == 1


def test_save_lab_with_replace_swaps_the_assets_of_the_same_lab(repo):
    conn = FakeConnection()
    flows.save_lab(conn, user_id=USER, name="Core", assets=MIX, replace=False)
    lab_id = repo.labs[(USER, "Core")]
    new_mix = (LabAsset("MSFT", Decimal("100")),)

    outcome = flows.save_lab(conn, user_id=USER, name="Core", assets=new_mix, replace=True)

    assert outcome == flows.SaveOutcome(name="Core", created=False, assets=1)
    assert repo.labs[(USER, "Core")] == lab_id
    assert repo.assets[lab_id] == new_mix
    assert repo.replaced == [lab_id]
    assert (conn.commits, conn.rollbacks) == (2, 0)


def test_save_lab_with_replace_creates_a_lab_that_does_not_exist_yet(repo):
    conn = FakeConnection()

    outcome = flows.save_lab(conn, user_id=USER, name="Core", assets=MIX, replace=True)

    assert outcome.created is True
    assert repo.replaced == []


def test_save_lab_with_replace_creates_it_again_if_it_was_deleted_meanwhile(repo):
    conn = FakeConnection()
    flows.save_lab(conn, user_id=USER, name="Core", assets=MIX, replace=False)
    repo.vanish_on_replace = True
    new_mix = (LabAsset("MSFT", Decimal("100")),)

    outcome = flows.save_lab(conn, user_id=USER, name="Core", assets=new_mix, replace=True)

    assert outcome.created is True
    assert _lab_assets(repo, USER, "Core") == new_mix


def test_save_lab_names_are_scoped_to_their_owner(repo):
    conn = FakeConnection()
    flows.save_lab(conn, user_id=USER, name="Core", assets=MIX, replace=False)

    outcome = flows.save_lab(conn, user_id=OTHER, name="Core", assets=MIX, replace=False)

    assert outcome.created is True


def test_save_lab_validates_before_writing_anything(repo):
    conn = FakeConnection()

    with pytest.raises(InvalidSavedLab):
        flows.save_lab(conn, user_id=USER, name="", assets=MIX, replace=False)

    assert repo.labs == {}
    assert (conn.commits, conn.rollbacks) == (0, 0)


def test_delete_lab_removes_the_owners_lab(repo):
    conn = FakeConnection()
    flows.save_lab(conn, user_id=USER, name="Core", assets=MIX, replace=False)

    flows.delete_lab(conn, user_id=USER, name="Core")

    assert repo.labs == {}
    assert conn.commits == 2


def test_delete_lab_raises_not_found_for_another_users_lab(repo):
    conn = FakeConnection()
    flows.save_lab(conn, user_id=USER, name="Core", assets=MIX, replace=False)

    with pytest.raises(SavedLabNotFound, match="^You have no lab called 'Core'.$"):
        flows.delete_lab(conn, user_id=OTHER, name="Core")

    assert (USER, "Core") in repo.labs
    assert conn.rollbacks == 1


def test_list_labs_returns_only_the_owners_labs(repo):
    conn = FakeConnection()
    flows.save_lab(conn, user_id=USER, name="Tech", assets=MIX, replace=False)
    flows.save_lab(conn, user_id=USER, name="Core", assets=MIX, replace=False)
    flows.save_lab(conn, user_id=OTHER, name="Theirs", assets=MIX, replace=False)

    labs = flows.list_labs(conn, user_id=USER)

    assert [lab.name for lab in labs] == ["Core", "Tech"]
