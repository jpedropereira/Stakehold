from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from shared.db.connection import transaction


@dataclass(frozen=True)
class User:
    id: UUID
    email: str
    display_name: str
    password_hash: str
    created_at: datetime


def insert_user(
    conn, *, user_id: UUID, email: str, display_name: str, password_hash: str
) -> None:
    """Raises psycopg.errors.UniqueViolation on a duplicate email -- service.py translates that
    into the domain-level EmailAlreadyRegistered."""
    with transaction(conn), conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, email, display_name, password_hash) VALUES (%s, %s, %s, %s)",
            (user_id, email, display_name, password_hash),
        )


def get_by_email(conn, email: str) -> User | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, email, display_name, password_hash, created_at "
            "FROM users WHERE email = %s",
            (email,),
        )
        row = cur.fetchone()
    return User(*row) if row else None


def get_by_id(conn, user_id: UUID) -> User | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, email, display_name, password_hash, created_at "
            "FROM users WHERE id = %s",
            (user_id,),
        )
        row = cur.fetchone()
    return User(*row) if row else None
