"""saved_labs and saved_lab_assets. Every statement is scoped by user_id, or by a lab id that one
of the user-scoped statements here just returned: there is no unscoped read by id, for the same
reason accounts/repository.py has none. Nothing here commits; flows.py owns the transaction.
"""

from itertools import groupby
from uuid import UUID

from portfolio.saved_labs.base import LabAsset, SavedLab


def insert_lab(conn, *, user_id: UUID, name: str) -> int | None:
    """The new lab's id, or None if this user already has a lab by that name."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO saved_labs (user_id, name) VALUES (%s, %s) "
            "ON CONFLICT (user_id, name) DO NOTHING RETURNING id",
            (user_id, name),
        )
        row = cur.fetchone()
    return row[0] if row else None


def replace_lab(conn, *, user_id: UUID, name: str) -> int | None:
    """Mark this user's lab as updated and drop its assets, for insert_assets to write anew.
    Returns its id, or None if there's no such lab (deleted since the caller looked). The UPDATE
    locks the row until the caller's transaction ends, so two replaces can't interleave."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE saved_labs SET updated_at = now() "
            "WHERE user_id = %s AND name = %s RETURNING id",
            (user_id, name),
        )
        row = cur.fetchone()
        if row is None:
            return None
        cur.execute("DELETE FROM saved_lab_assets WHERE saved_lab_id = %s", (row[0],))
    return row[0]


def insert_assets(conn, *, lab_id: int, assets: tuple[LabAsset, ...]) -> None:
    """A lab's assets, each numbered by its place, so the form shows them in the order saved."""
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO saved_lab_assets (saved_lab_id, position, ticker, weight_pct) "
            "VALUES (%s, %s, %s, %s)",
            [(lab_id, at, a.ticker, a.weight_pct) for at, a in enumerate(assets)],
        )


def list_by_user(conn, user_id: UUID) -> list[SavedLab]:
    """This user's labs by name, each with its assets in their saved order."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT l.name, l.created_at, l.updated_at, a.ticker, a.weight_pct "
            "FROM saved_labs l JOIN saved_lab_assets a ON a.saved_lab_id = l.id "
            "WHERE l.user_id = %s ORDER BY l.name, a.position",
            (user_id,),
        )
        rows = cur.fetchall()
    labs = []
    for (name, created_at, updated_at), group in groupby(rows, key=lambda r: r[:3]):
        assets = tuple(LabAsset(ticker, weight) for *_, ticker, weight in group)
        labs.append(
            SavedLab(name=name, assets=assets, created_at=created_at, updated_at=updated_at)
        )
    return labs


def delete_by_name(conn, *, user_id: UUID, name: str) -> int:
    """Delete this user's lab by name; its assets go with it (ON DELETE CASCADE). Returns the
    count removed, 0 or 1."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM saved_labs WHERE user_id = %s AND name = %s", (user_id, name))
        return cur.rowcount
