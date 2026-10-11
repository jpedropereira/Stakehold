import uuid
from datetime import UTC, datetime

from portfolio.accounts.repository import (
    Account,
    delete_account,
    get_by_label,
    insert_account,
    list_by_user,
)


class FakeCursor:
    def __init__(self, fetch_result=None, fetchall_result=None):
        self.fetch_result = fetch_result
        self.fetchall_result = fetchall_result if fetchall_result is not None else []
        self.executed = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.fetch_result

    def fetchall(self):
        return self.fetchall_result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, fetch_result=None, fetchall_result=None):
        self._cursor = FakeCursor(fetch_result=fetch_result, fetchall_result=fetchall_result)
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def test_insert_account_executes_a_parameterized_insert_without_committing():
    conn = FakeConnection(fetch_result=(1,))
    user_id = uuid.uuid4()

    account_id = insert_account(conn, user_id=user_id, broker="acme", account_label="main")

    sql, params = conn._cursor.executed[0]
    assert "INSERT INTO accounts" in sql
    assert "ON CONFLICT" in sql
    assert "DO NOTHING" in sql
    assert params == (user_id, "acme", "main")
    assert account_id == 1
    # The caller (pipeline.py) owns the transaction, same as auth's repositories.
    assert conn.committed is False


def test_insert_account_returns_none_on_conflict():
    conn = FakeConnection(fetch_result=None)

    account_id = insert_account(conn, user_id=uuid.uuid4(), broker="acme", account_label="main")

    assert account_id is None


def test_get_by_label_scopes_the_query_by_user_id():
    """The multi-user isolation chokepoint: the query is unusable without a user_id filter."""
    conn = FakeConnection(fetch_result=None)
    user_id = uuid.uuid4()

    get_by_label(conn, user_id=user_id, account_label="main")

    sql, params = conn._cursor.executed[0]
    assert "user_id" in sql
    assert params == (user_id, "main")


def test_get_by_label_returns_none_when_no_row():
    conn = FakeConnection(fetch_result=None)

    assert get_by_label(conn, user_id=uuid.uuid4(), account_label="main") is None


def test_get_by_label_returns_an_account():
    user_id = uuid.uuid4()
    created_at = datetime.now(UTC)
    conn = FakeConnection(fetch_result=(1, user_id, "acme", "main", created_at))

    account = get_by_label(conn, user_id=user_id, account_label="main")

    assert account == Account(
        id=1, user_id=user_id, broker="acme", account_label="main", created_at=created_at
    )


def test_list_by_user_maps_every_row_in_order():
    user_id = uuid.uuid4()
    created_at = datetime.now(UTC)
    conn = FakeConnection(
        fetchall_result=[
            (1, user_id, "acme", "acme-main", created_at),
            (2, user_id, "globex", "globex-main", created_at),
        ]
    )

    accounts = list_by_user(conn, user_id)

    assert [a.account_label for a in accounts] == ["acme-main", "globex-main"]


def test_list_by_user_scopes_the_query_by_user_id():
    conn = FakeConnection(fetchall_result=[])
    user_id = uuid.uuid4()

    list_by_user(conn, user_id)

    sql, params = conn._cursor.executed[0]
    assert "user_id" in sql
    assert params == (user_id,)


def test_list_by_user_returns_empty_list_when_no_accounts():
    conn = FakeConnection(fetchall_result=[])

    assert list_by_user(conn, uuid.uuid4()) == []


def test_delete_account_is_scoped_to_the_owner_and_does_not_commit():
    """Scoped by user_id as well as id, even though the caller resolved the id through
    get_by_label: this module is the isolation chokepoint, so a delete never trusts an id alone."""
    conn = FakeConnection()
    conn._cursor.rowcount = 1
    user_id = uuid.uuid4()

    deleted = delete_account(conn, user_id=user_id, account_id=7)

    [(sql, params)] = conn._cursor.executed
    assert "DELETE FROM accounts" in sql
    assert "id = %s" in sql and "user_id = %s" in sql
    assert params == (7, user_id)
    assert deleted == 1
    assert conn.committed is False
