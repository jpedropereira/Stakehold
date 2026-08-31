"""Fake in-memory repositories and connection shared by test_service.py and test_api.py.

Standing in for `auth.users.repository` / `auth.sessions.repository` so both the domain layer
and the HTTP layer can be exercised end-to-end without a real Postgres connection -- the
repository modules' own SQL shape is covered separately by
tests/unit/{users,sessions}/test_repository.py and the integration suite.
"""

from datetime import UTC, datetime

import psycopg
import pytest
from auth import service
from auth.sessions.repository import SessionUser
from auth.users.repository import User

NOW = datetime(2026, 1, 1, tzinfo=UTC)

# What /health's schema probe returns from a database that has been initialised.
HEALTHY_SCHEMA_ROW = ("users", "sessions")


class FakeCursor:
    def __init__(self, fetch_result):
        self.fetch_result = fetch_result
        self.executed: list[tuple] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.fetch_result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    """Stands in for a pooled psycopg connection.

    Counts commits and rollbacks rather than just flagging them: transaction *boundaries* are the
    behaviour under test (signup's user and session inserts have to land in one transaction, not
    two), and a boundary is only observable from the connection, never from the fake repositories.
    """

    def __init__(self, health_row=HEALTHY_SCHEMA_ROW):
        self.commits = 0
        self.rollbacks = 0
        self.cursors: list[FakeCursor] = []
        self._health_row = health_row

    def cursor(self):
        cursor = FakeCursor(self._health_row)
        self.cursors.append(cursor)
        return cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeUsersRepo:
    def __init__(self):
        self.by_email: dict[str, User] = {}
        self.by_id: dict = {}

    def insert_user(self, conn, *, user_id, email, display_name, password_hash):
        if email in self.by_email:
            raise psycopg.errors.UniqueViolation("duplicate email")
        user = User(
            id=user_id,
            email=email,
            display_name=display_name,
            password_hash=password_hash,
            created_at=NOW,
        )
        self.by_email[email] = user
        self.by_id[user_id] = user

    def get_by_email(self, conn, email):
        return self.by_email.get(email)

    def get_by_id(self, conn, user_id):
        return self.by_id.get(user_id)


class FakeSessionsRepo:
    def __init__(self, users_repo: FakeUsersRepo):
        self._users_repo = users_repo
        self.by_token_hash: dict[bytes, dict] = {}

    def insert_session(
        self, conn, *, session_id, token_hash, user_id, now, expires_at, absolute_expires_at
    ):
        self.by_token_hash[token_hash] = {
            "user_id": user_id,
            "expires_at": expires_at,
            "absolute_expires_at": absolute_expires_at,
        }

    def renew_and_fetch(self, conn, *, token_hash, now, new_expires_at):
        record = self.by_token_hash.get(token_hash)
        if record is None:
            return None
        if not (now < record["expires_at"] and now < record["absolute_expires_at"]):
            return None
        record["expires_at"] = min(new_expires_at, record["absolute_expires_at"])
        user = self._users_repo.by_id[record["user_id"]]
        return SessionUser(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            expires_at=record["expires_at"],
        )

    def delete_by_token_hash(self, conn, token_hash):
        self.by_token_hash.pop(token_hash, None)

    def delete_expired(self, conn, now):
        expired = [
            k
            for k, v in self.by_token_hash.items()
            if v["expires_at"] <= now or v["absolute_expires_at"] <= now
        ]
        for k in expired:
            del self.by_token_hash[k]
        return len(expired)


@pytest.fixture
def repos(monkeypatch):
    users_repo = FakeUsersRepo()
    sessions_repo = FakeSessionsRepo(users_repo)
    monkeypatch.setattr(service, "users_repository", users_repo)
    monkeypatch.setattr(service, "sessions_repository", sessions_repo)
    return users_repo, sessions_repo


@pytest.fixture
def conn():
    return FakeConnection()


@pytest.fixture
def make_connection():
    """Factory for a FakeConnection, for tests that need a non-default one (e.g. a database
    whose schema has not been applied). Avoids importing across the tests package, which isn't
    importable as one."""
    return FakeConnection
