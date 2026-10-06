import uuid
from datetime import UTC, datetime

from auth.users.repository import get_by_email, get_by_id, insert_user, set_admin


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


def test_insert_user_executes_a_parameterized_insert_without_committing():
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
    # The caller owns the transaction: signup has to commit this insert and the session insert
    # together, which it can't do if the repository commits on its own.
    assert conn.committed is False


def test_get_by_email_returns_none_when_no_row():
    conn = FakeConnection(fetch_result=None)

    assert get_by_email(conn, "nobody@example.com") is None


def test_get_by_email_returns_a_user():
    user_id = uuid.uuid4()
    created_at = datetime.now(UTC)
    conn = FakeConnection(
        fetch_result=(user_id, "person@example.com", "Person", "hash", created_at, False)
    )

    user = get_by_email(conn, "person@example.com")

    assert user.id == user_id
    assert user.email == "person@example.com"
    assert user.display_name == "Person"
    assert user.password_hash == "hash"  # pragma: allowlist secret
    assert user.created_at == created_at
    assert user.is_admin is False


def test_get_by_id_returns_none_when_no_row():
    conn = FakeConnection(fetch_result=None)

    assert get_by_id(conn, uuid.uuid4()) is None


def test_get_by_id_returns_a_user():
    user_id = uuid.uuid4()
    created_at = datetime.now(UTC)
    conn = FakeConnection(
        fetch_result=(user_id, "person@example.com", "Person", "hash", created_at, False)
    )

    user = get_by_id(conn, user_id)

    assert user.id == user_id


def test_get_by_id_reports_an_admin():
    user_id = uuid.uuid4()
    conn = FakeConnection(
        fetch_result=(user_id, "admin@example.com", "Admin", "hash", datetime.now(UTC), True)
    )

    assert get_by_id(conn, user_id).is_admin is True


def test_set_admin_executes_a_parameterized_update_without_committing():
    conn = FakeConnection(rowcount=1)

    set_admin(conn, "person@example.com", is_admin=True)

    sql, params = conn._cursor.executed[0]
    assert "UPDATE users" in sql
    assert "is_admin" in sql
    assert params == (True, "person@example.com")
    # Same rule as insert_user: the caller owns the transaction.
    assert conn.committed is False


def test_set_admin_returns_true_when_a_user_was_updated():
    conn = FakeConnection(rowcount=1)

    assert set_admin(conn, "person@example.com", is_admin=False) is True


def test_set_admin_returns_false_for_an_unknown_email():
    conn = FakeConnection(rowcount=0)

    assert set_admin(conn, "nobody@example.com", is_admin=True) is False
