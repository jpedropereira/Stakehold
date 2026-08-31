import uuid
from datetime import UTC, datetime

from auth.users.repository import get_by_email, get_by_id, insert_user


class FakeCursor:
    def __init__(self, fetch_result=None):
        self.fetch_result = fetch_result
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
    def __init__(self, fetch_result=None):
        self._cursor = FakeCursor(fetch_result=fetch_result)
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def test_insert_user_executes_a_parameterized_insert_and_commits():
    conn = FakeConnection()
    user_id = uuid.uuid4()

    insert_user(
        conn,
        user_id=user_id,
        email="person@example.com",
        display_name="Person",
        password_hash="hash",  # pragma: allowlist secret
    )

    sql, params = conn._cursor.executed[0]
    assert "INSERT INTO users" in sql
    assert params == (user_id, "person@example.com", "Person", "hash")
    assert conn.committed is True


def test_get_by_email_returns_none_when_no_row():
    conn = FakeConnection(fetch_result=None)

    assert get_by_email(conn, "nobody@example.com") is None


def test_get_by_email_returns_a_user():
    user_id = uuid.uuid4()
    created_at = datetime.now(UTC)
    conn = FakeConnection(
        fetch_result=(user_id, "person@example.com", "Person", "hash", created_at)
    )

    user = get_by_email(conn, "person@example.com")

    assert user.id == user_id
    assert user.email == "person@example.com"
    assert user.display_name == "Person"
    assert user.password_hash == "hash"  # pragma: allowlist secret
    assert user.created_at == created_at


def test_get_by_id_returns_none_when_no_row():
    conn = FakeConnection(fetch_result=None)

    assert get_by_id(conn, uuid.uuid4()) is None


def test_get_by_id_returns_a_user():
    user_id = uuid.uuid4()
    created_at = datetime.now(UTC)
    conn = FakeConnection(
        fetch_result=(user_id, "person@example.com", "Person", "hash", created_at)
    )

    user = get_by_id(conn, user_id)

    assert user.id == user_id
