import os
import uuid

import psycopg
import pytest
from auth import cli

pytestmark = pytest.mark.integration


def _connect():
    return psycopg.connect(os.environ["AUTH_DATABASE_URL"])


@pytest.fixture(autouse=True)
def _clean_tables():
    cli.main(["db", "init"])
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE sessions, users CASCADE")
        conn.commit()
    yield


def test_db_init_creates_expected_tables():
    exit_code = cli.main(["db", "init"])

    assert exit_code == 0

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name IN ('users', 'sessions')
                ORDER BY table_name
                """)
            tables = [row[0] for row in cur.fetchall()]

    assert tables == ["sessions", "users"]


def test_db_init_is_idempotent():
    cli.main(["db", "init"])

    exit_code = cli.main(["db", "init"])

    assert exit_code == 0


def test_duplicate_email_rejected():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, email, display_name, password_hash) "
                "VALUES (%s, %s, %s, %s)",
                (uuid.uuid4(), "dup@example.com", "First", "hash-1"),
            )
        conn.commit()

        with pytest.raises(psycopg.errors.UniqueViolation):
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (id, email, display_name, password_hash) "
                    "VALUES (%s, %s, %s, %s)",
                    (uuid.uuid4(), "dup@example.com", "Second", "hash-2"),
                )
            conn.commit()


def test_deleting_user_cascades_to_sessions():
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, email, display_name, password_hash) "
                "VALUES (%s, %s, %s, %s)",
                (user_id, "cascade@example.com", "Cascade", "hash"),
            )
            cur.execute(
                "INSERT INTO sessions "
                "(id, token_hash, user_id, expires_at, absolute_expires_at) "
                "VALUES (%s, %s, %s, now() + interval '1 day', now() + interval '30 days')",
                (session_id, b"\x00" * 32, user_id),
            )
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM sessions WHERE id = %s", (session_id,))
            count = cur.fetchone()[0]

    assert count == 0


def test_token_hash_uniqueness_enforced():
    user_id = uuid.uuid4()
    token_hash = b"\x01" * 32

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, email, display_name, password_hash) "
                "VALUES (%s, %s, %s, %s)",
                (user_id, "unique-token@example.com", "Unique", "hash"),
            )
            cur.execute(
                "INSERT INTO sessions "
                "(id, token_hash, user_id, expires_at, absolute_expires_at) "
                "VALUES (%s, %s, %s, now() + interval '1 day', now() + interval '30 days')",
                (uuid.uuid4(), token_hash, user_id),
            )
        conn.commit()

        with pytest.raises(psycopg.errors.UniqueViolation):
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO sessions "
                    "(id, token_hash, user_id, expires_at, absolute_expires_at) "
                    "VALUES (%s, %s, %s, now() + interval '1 day', now() + interval '30 days')",
                    (uuid.uuid4(), token_hash, user_id),
                )
            conn.commit()


def test_db_init_adds_is_admin_to_a_users_table_created_before_it_existed():
    # The column arrived after users shipped: an existing database only gets it through the
    # ALTER in schema.sql, and its existing rows must come out as non-admins.
    user_id = uuid.uuid4()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE users DROP COLUMN is_admin")
            cur.execute(
                "INSERT INTO users (id, email, display_name, password_hash) "
                "VALUES (%s, 'old@example.com', 'Old', 'hash')",
                (user_id,),
            )
        conn.commit()

    assert cli.main(["db", "init"]) == 0

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT is_admin FROM users WHERE id = %s", (user_id,))
            assert cur.fetchone() == (False,)
