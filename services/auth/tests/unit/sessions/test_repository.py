import uuid
from datetime import UTC, datetime, timedelta

from auth.sessions.repository import (
    delete_by_token_hash,
    delete_expired,
    insert_session,
    renew_and_fetch,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class FakeCursor:
    def __init__(self, fetch_result=None, rowcount=0):
        self.fetch_result = fetch_result
        self.rowcount = rowcount
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.fetch_result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, fetch_result=None, rowcount=0):
        self._cursor = FakeCursor(fetch_result=fetch_result, rowcount=rowcount)
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def test_insert_session_executes_a_parameterized_insert_and_commits():
    conn = FakeConnection()
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()

    insert_session(
        conn,
        session_id=session_id,
        token_hash=b"\x00" * 32,
        user_id=user_id,
        now=NOW,
        expires_at=NOW + timedelta(days=14),
        absolute_expires_at=NOW + timedelta(days=90),
    )

    sql, params = conn._cursor.executed[0]
    assert "INSERT INTO sessions" in sql
    assert params[0] == session_id
    assert params[2] == user_id
    assert conn.committed is True


def test_renew_and_fetch_returns_none_when_no_matching_row():
    conn = FakeConnection(fetch_result=None)

    result = renew_and_fetch(
        conn, token_hash=b"\x01" * 32, now=NOW, new_expires_at=NOW + timedelta(days=14)
    )

    assert result is None


def test_renew_and_fetch_returns_the_session_user():
    user_id = uuid.uuid4()
    new_expires_at = NOW + timedelta(days=14)
    conn = FakeConnection(fetch_result=(user_id, "person@example.com", "Person", new_expires_at))

    result = renew_and_fetch(conn, token_hash=b"\x01" * 32, now=NOW, new_expires_at=new_expires_at)

    assert result.user_id == user_id
    assert result.email == "person@example.com"
    assert result.display_name == "Person"
    assert result.expires_at == new_expires_at


def test_renew_and_fetch_query_clamps_to_absolute_expiry():
    conn = FakeConnection(fetch_result=None)

    renew_and_fetch(conn, token_hash=b"\x01" * 32, now=NOW, new_expires_at=NOW + timedelta(days=14))

    sql, params = conn._cursor.executed[0]
    assert "LEAST(" in sql
    assert "absolute_expires_at" in sql
    assert params["token_hash"] == b"\x01" * 32


def test_delete_by_token_hash_is_a_plain_delete():
    conn = FakeConnection()

    delete_by_token_hash(conn, b"\x02" * 32)

    sql, params = conn._cursor.executed[0]
    assert "DELETE FROM sessions" in sql
    assert params == (b"\x02" * 32,)
    assert conn.committed is True


def test_delete_expired_returns_the_deleted_count():
    conn = FakeConnection(rowcount=3)

    deleted = delete_expired(conn, NOW)

    assert deleted == 3
    sql, params = conn._cursor.executed[0]
    assert "DELETE FROM sessions" in sql
    assert "expires_at" in sql
