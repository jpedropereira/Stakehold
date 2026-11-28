"""Saving, listing and deleting a user's labs (LF-1). Each write is one transaction, so a replace
never leaves a lab with no assets or with half of the new ones.

A save without `replace` never overwrites: a taken name raises SavedLabNameTaken, and web asks
the user before saving again with `replace`.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from portfolio.errors import SavedLabNameTaken, SavedLabNotFound
from portfolio.saved_labs import repository
from portfolio.saved_labs.base import LabAsset, SavedLab, validate_lab
from shared.db.connection import transaction


@dataclass(frozen=True)
class SaveOutcome:
    name: str  # as stored, trimmed
    created: bool  # False when an existing lab was replaced
    assets: int


def save_lab(
    conn, *, user_id: UUID, name: str, assets: Iterable[LabAsset], replace: bool
) -> SaveOutcome:
    """Save `assets` as this user's lab `name`. Raises InvalidSavedLab before writing anything,
    and SavedLabNameTaken if the name is taken and `replace` is False.

    With `replace`, a lab deleted by another request between the insert's conflict and the
    replace is created again, since that is what the user asked for."""
    name, checked = validate_lab(name, assets)
    with transaction(conn):
        lab_id = repository.insert_lab(conn, user_id=user_id, name=name)
        created = lab_id is not None
        if not created:
            if not replace:
                raise SavedLabNameTaken(f"You already have a lab called {name!r}.")
            lab_id = repository.replace_lab(conn, user_id=user_id, name=name)
            if lab_id is None:
                lab_id = repository.insert_lab(conn, user_id=user_id, name=name)
                created = True
            if lab_id is None:
                raise SavedLabNameTaken(f"You already have a lab called {name!r}.")
        repository.insert_assets(conn, lab_id=lab_id, assets=checked)
    return SaveOutcome(name=name, created=created, assets=len(checked))


def delete_lab(conn, *, user_id: UUID, name: str) -> None:
    """Delete this user's lab `name`. Raises SavedLabNotFound if there's none, which is also
    the answer for another user's lab."""
    with transaction(conn):
        if not repository.delete_by_name(conn, user_id=user_id, name=name):
            raise SavedLabNotFound(f"You have no lab called {name!r}.")


def list_labs(conn, *, user_id: UUID) -> list[SavedLab]:
    return repository.list_by_user(conn, user_id)
