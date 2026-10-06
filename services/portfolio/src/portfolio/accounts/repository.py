"""The multi-user isolation chokepoint.

Every other domain module reaches its rows only by first resolving an account through
`get_by_label`, scoped to a `user_id`. There is deliberately no unscoped `get_by_id(account_id)`
here -- that absence is the design, not an oversight: it removes the one shortcut that would let
a caller skip the ownership check by accident. See docs/architecture.md's cross-cutting
decision on this.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class Account:
    id: int
    user_id: UUID
    broker: str
    account_label: str
    created_at: datetime


def insert_account(conn, *, user_id: UUID, broker: str, account_label: str) -> int | None:
    """Insert one account, doing nothing on a duplicate (user_id, account_label).

    Returns the new id, or None on conflict -- the caller (pipeline.py) re-reads via
    get_by_label to decide whether the existing row agrees with what was asked for. Does not
    commit: the calling pipeline owns the transaction, same as auth's repositories.
    """
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (user_id, broker, account_label) VALUES (%s, %s, %s) "
            "ON CONFLICT (user_id, account_label) DO NOTHING RETURNING id",
            (user_id, broker, account_label),
        )
        row = cur.fetchone()
    return row[0] if row else None


def get_by_label(conn, *, user_id: UUID, account_label: str) -> Account | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, user_id, broker, account_label, created_at FROM accounts "
            "WHERE user_id = %s AND account_label = %s",
            (user_id, account_label),
        )
        row = cur.fetchone()
    return Account(*row) if row else None


def list_by_user(conn, user_id: UUID) -> list[Account]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, user_id, broker, account_label, created_at FROM accounts "
            "WHERE user_id = %s ORDER BY account_label",
            (user_id,),
        )
        rows = cur.fetchall()
    return [Account(*row) for row in rows]


def list_user_ids(conn) -> list[UUID]:
    """Every user who owns at least one account. Unscoped by design: the scheduled returns
    refresh rebuilds every user, and goes back through the user-scoped reads for each one."""
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT user_id FROM accounts ORDER BY user_id")
        return [row[0] for row in cur.fetchall()]
