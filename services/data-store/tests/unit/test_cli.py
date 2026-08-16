import pytest
from data_store import cli
from data_store.settings import Settings


@pytest.fixture
def fake_settings(tmp_path):
    return Settings(database_url="postgresql://user:pass@localhost/db", config_path=tmp_path)


def test_db_init_applies_schema(monkeypatch, fake_settings):
    calls = {}

    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(
        cli,
        "apply_schema",
        lambda conn, path: calls.setdefault("apply_schema", (conn, path)),
    )
    closed = []

    def fake_get_connection(url):
        calls["conn_url"] = url
        return FakeConn(closed)

    monkeypatch.setattr(cli, "get_connection", fake_get_connection)

    exit_code = cli.main(["db", "init"])

    assert exit_code == 0
    assert calls["conn_url"] == "postgresql://user:pass@localhost/db"
    assert calls["apply_schema"][0].__class__.__name__ == "FakeConn"
    assert closed == [True]


class FakeConn:
    def __init__(self, closed):
        self._closed = closed

    def close(self):
        self._closed.append(True)


def test_missing_command_exits_nonzero():
    with pytest.raises(SystemExit) as exc_info:
        cli.main([])

    assert exc_info.value.code != 0


def test_missing_db_subcommand_exits_nonzero():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["db"])

    assert exc_info.value.code != 0
