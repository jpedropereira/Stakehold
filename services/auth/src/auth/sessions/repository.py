from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class SessionUser:
    user_id: UUID
    email: str
    display_name: str
    is_admin: bool
    expires_at: datetime


def insert_session(
    conn,
    *,
    session_id: UUID,
    token_hash: bytes,
    user_id: UUID,
    now: datetime,
    expires_at: datetime,
    absolute_expires_at: datetime,
) -> None:
    """Does not commit -- the calling service owns the transaction. See architecture.md."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sessions "
            "(id, token_hash, user_id, created_at, last_seen_at, expires_at, absolute_expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (session_id, token_hash, user_id, now, now, expires_at, absolute_expires_at),
        )


def renew_and_fetch(
    conn, *, token_hash: bytes, now: datetime, new_expires_at: datetime
) -> SessionUser | None:
    """Validate, slide expiry (clamped to absolute_expires_at) and fetch the owning user, in one
    round trip -- the hottest call in the service, made on every session-validate request.

    Clamping to absolute_expires_at here (not just at login) is what keeps a continuously-renewed
    session from ever living past its absolute cap -- see schema.md.

    is_admin is read from users on every call rather than cached anywhere, so revoking admin
    takes effect on the caller's very next request.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH renewed AS (
                UPDATE sessions
                SET expires_at = LEAST(%(new_expires_at)s, absolute_expires_at),
                    last_seen_at = %(now)s
                WHERE token_hash = %(token_hash)s
                  AND %(now)s < expires_at
                  AND %(now)s < absolute_expires_at
                RETURNING user_id, expires_at
            )
            SELECT users.id, users.email, users.display_name, users.is_admin, renewed.expires_at
            FROM renewed
            JOIN users ON users.id = renewed.user_id
            """,
            {"token_hash": token_hash, "now": now, "new_expires_at": new_expires_at},
        )
        row = cur.fetchone()
    return SessionUser(*row) if row else None


def delete_by_token_hash(conn, token_hash: bytes) -> None:
    """Idempotent -- logging out an already-gone/unknown session is not an error."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM sessions WHERE token_hash = %s", (token_hash,))


def delete_expired(conn, now: datetime) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM sessions WHERE expires_at <= %s OR absolute_expires_at <= %s",
            (now, now),
        )
        return cur.rowcount
