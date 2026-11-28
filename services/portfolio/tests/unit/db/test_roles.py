"""The cross-user role's DDL, at the level a fake cursor can check: what is created and granted.
That Postgres then enforces it -- the role reads every user and writes nothing -- is proven
against a live database in tests/integration/test_row_level_security.py."""

from portfolio.db.roles import ensure_cross_user_role, grant_cross_user_reads


class _Cursor:
    def __init__(self, role_exists):
        self._role_exists = role_exists
        self.executed = []

    def execute(self, statement, params=None):
        self.executed.append(statement)

    def fetchone(self):
        return (1,) if self._role_exists else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Connection:
    def __init__(self, role_exists=False):
        self.cursor_ = _Cursor(role_exists)
        self.committed = False

    def cursor(self):
        return self.cursor_

    def commit(self):
        self.committed = True

    def rollback(self):
        pass


def _statements(conn):
    return [
        statement if isinstance(statement, str) else repr(statement)
        for statement in conn.cursor_.executed
    ]


def test_a_missing_cross_user_role_is_created_able_to_bypass_rls():
    conn = _Connection(role_exists=False)

    ensure_cross_user_role(conn, "portfolio_cross_user", "pw", "portfolio")

    create = [s for s in _statements(conn) if "CREATE ROLE" in s]
    assert len(create) == 1
    assert "LOGIN BYPASSRLS" in create[0]
    assert "portfolio_cross_user" in create[0]
    assert conn.committed is True


def test_an_existing_cross_user_role_is_not_created_again():
    conn = _Connection(role_exists=True)

    ensure_cross_user_role(conn, "portfolio_cross_user", "pw", "portfolio")

    assert not any("CREATE ROLE" in s for s in _statements(conn))


def test_the_cross_user_role_searches_the_service_schema():
    # It has no like-named schema of its own, so unqualified table names need this.
    conn = _Connection(role_exists=True)

    ensure_cross_user_role(conn, "portfolio_cross_user", "pw", "portfolio")

    search_path = [s for s in _statements(conn) if "search_path" in s]
    assert len(search_path) == 1
    assert "'portfolio'" in search_path[0] or '"portfolio"' in search_path[0]


def test_the_cross_user_role_is_granted_select_and_nothing_else():
    conn = _Connection()

    grant_cross_user_reads(conn, "portfolio_cross_user", "portfolio")

    grants = [s for s in _statements(conn) if "GRANT" in s]
    assert any("USAGE ON SCHEMA" in s for s in grants)
    table_grant = next(s for s in grants if "ON SCHEMA" not in s)
    assert "GRANT SELECT ON" in table_grant
    for table in ("accounts", "transactions", "position_snapshots", "ticker_mappings"):
        assert table in table_grant
    assert "return_snapshots" not in table_grant
    for verb in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
        assert not any(verb in s for s in grants)
    assert conn.committed is True
