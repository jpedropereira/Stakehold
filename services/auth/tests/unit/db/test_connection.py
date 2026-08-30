from auth.db.connection import apply_schema, get_connection


def test_get_connection_passes_database_url(monkeypatch):
    captured = {}

    def fake_connect(url):
        captured["url"] = url
        return "connection-object"

    monkeypatch.setattr("psycopg.connect", fake_connect)

    result = get_connection("postgresql://user:pass@localhost/db")

    assert result == "connection-object"
    assert captured["url"] == "postgresql://user:pass@localhost/db"


def test_apply_schema_executes_file_contents_and_commits(tmp_path):
    schema_path = tmp_path / "schema.sql"
    schema_path.write_text("CREATE TABLE foo (id INT);")

    executed = []

    class FakeCursor:
        def execute(self, sql):
            executed.append(sql)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class FakeConnection:
        def __init__(self):
            self.committed = False

        def cursor(self):
            return FakeCursor()

        def commit(self):
            self.committed = True

    conn = FakeConnection()

    apply_schema(conn, schema_path)

    assert executed == ["CREATE TABLE foo (id INT);"]
    assert conn.committed is True
