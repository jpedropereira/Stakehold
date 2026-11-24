"""The workspace-wide guard in the repo-root conftest.py that keeps integration tests off any
database that isn't a disposable test one.

It lives here because `pytest services` is the canonical run and `shared` is the workspace's
cross-service package; the guard itself has to be the root conftest, so that it covers every
service's tests whichever path pytest is pointed at.

Integration fixtures TRUNCATE tables. Twice, locally run integration tests picked up the dev
stack's real database URLs and wiped real users and portfolio data, so this guard fails closed:
a run that selects any integration test aborts before the first test unless every database URL
names a `_test` database on a server marked as a test instance.
"""

import importlib.util
from pathlib import Path

import pytest

_ROOT_CONFTEST = Path(__file__).resolve().parents[4] / "conftest.py"

_DEV_LIKE = "postgresql://auth:secret@localhost:5434/auth"  # pragma: allowlist secret
_TEST_LIKE = "postgresql://auth:secret@localhost:55434/auth_test"  # pragma: allowlist secret


def _load_guard():
    spec = importlib.util.spec_from_file_location("root_conftest", _ROOT_CONFTEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeConnection:
    def __init__(self, purpose):
        self.purpose = purpose

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        return self

    def fetchone(self):
        return (self.purpose,)


def _connect_reporting(purpose_by_port, refused_ports=()):
    """A psycopg.connect stand-in: each server (by port) reports its stakehold.purpose
    setting, or refuses the connection."""
    calls = []

    def connect(url, **kwargs):
        calls.append(url)
        port = int(url.split("@")[1].split(":")[1].split("/")[0])
        if port in refused_ports:
            raise OSError("connection refused")
        return _FakeConnection(purpose_by_port.get(port))

    connect.calls = calls
    return connect


# --- check_test_databases ---------------------------------------------------


def test_accepts_test_databases_on_marked_servers():
    guard = _load_guard()
    connect = _connect_reporting({55434: "integration-test"})

    problems = guard.check_test_databases({"AUTH_DATABASE_URL": _TEST_LIKE}, connect=connect)

    assert problems == []


@pytest.mark.parametrize(
    "url",
    [
        _DEV_LIKE,
        "postgresql://auth:secret@localhost:55434/auth_testing",  # pragma: allowlist secret
        "postgresql://auth:secret@localhost:55434/test_auth",  # pragma: allowlist secret
        # No dbname: libpq falls back to the user name, which isn't a test database.
        "postgresql://auth:secret@localhost:55434",  # pragma: allowlist secret
    ],
)
def test_refuses_a_database_not_named_test(url):
    guard = _load_guard()
    connect = _connect_reporting({55434: "integration-test"})

    problems = guard.check_test_databases({"AUTH_DATABASE_URL": url}, connect=connect)

    assert len(problems) == 1
    assert "AUTH_DATABASE_URL" in problems[0]
    # A wrong name is refused on the name alone: nothing connects to a possibly-real server.
    assert connect.calls == []


def test_checks_every_database_url_variable_not_just_known_ones():
    guard = _load_guard()
    connect = _connect_reporting({55434: "integration-test"})
    env = {
        "AUTH_DATABASE_URL": _TEST_LIKE,
        "SOME_FUTURE_SERVICE_DATABASE_URL": _DEV_LIKE,
        "UNRELATED": _DEV_LIKE,
    }

    problems = guard.check_test_databases(env, connect=connect)

    assert len(problems) == 1
    assert "SOME_FUTURE_SERVICE_DATABASE_URL" in problems[0]


def test_refuses_a_test_named_database_on_an_unmarked_server():
    """The name alone isn't proof: a `_test` database could be created on the dev server. The
    disposable test containers are started with a server setting the dev stack never has."""
    guard = _load_guard()
    connect = _connect_reporting({55434: None})

    problems = guard.check_test_databases({"AUTH_DATABASE_URL": _TEST_LIKE}, connect=connect)

    assert len(problems) == 1
    assert "not a disposable test server" in problems[0]


def test_refuses_an_unreachable_server():
    guard = _load_guard()
    connect = _connect_reporting({}, refused_ports={55434})

    problems = guard.check_test_databases({"AUTH_DATABASE_URL": _TEST_LIKE}, connect=connect)

    assert len(problems) == 1
    assert "no test database reachable at localhost:55434" in problems[0]


def test_one_reachable_url_vouches_for_its_server():
    """portfolio's own role doesn't exist until `db init` creates it through the admin URL on
    the same server, so a refused login there is fine once another URL proved the server is a
    test instance."""
    guard = _load_guard()
    admin = "postgresql://admin:x@localhost:55435/app_test"  # pragma: allowlist secret
    scoped = "postgresql://portfolio:x@localhost:55435/app_test"  # pragma: allowlist secret
    calls = []

    def connect(url, **kwargs):
        calls.append(url)
        if url == scoped:
            raise OSError('role "portfolio" does not exist')
        return _FakeConnection("integration-test")

    problems = guard.check_test_databases(
        {"PORTFOLIO_DATABASE_URL": scoped, "APP_POSTGRES_ADMIN_DATABASE_URL": admin},
        connect=connect,
    )

    assert problems == []


def test_default_test_databases_are_all_named_test():
    guard = _load_guard()
    urls = {k: v for k, v in guard.TEST_DATABASE_ENV.items() if k.endswith("DATABASE_URL")}

    assert urls
    assert (
        guard.check_test_databases(
            urls,
            connect=_connect_reporting(
                {55432: "integration-test", 55434: "integration-test", 55435: "integration-test"}
            ),
        )
        == []
    )


# --- prepare_integration_environment ----------------------------------------


def test_preparing_fills_in_the_test_databases():
    guard = _load_guard()
    env = {}

    guard.prepare_integration_environment(env)

    for name, value in guard.TEST_DATABASE_ENV.items():
        assert env[name] == value


def test_preparing_keeps_an_explicit_setting_for_the_guard_to_vet():
    guard = _load_guard()
    env = {"AUTH_DATABASE_URL": _DEV_LIKE}

    guard.prepare_integration_environment(env)

    assert env["AUTH_DATABASE_URL"] == _DEV_LIKE


def test_preparing_supplies_an_ephemeral_auth_signing_key():
    """auth refuses to start without a signing key, and with dotenv disabled nothing else would
    give it one. Generated per run, so no private key is ever committed."""
    guard = _load_guard()
    env = {}

    guard.prepare_integration_environment(env)

    # The PEM header only, no key material.
    pem_header = "-----BEGIN PRIVATE KEY-----"  # pragma: allowlist secret
    assert env["AUTH_SIGNING_KEY"].startswith(pem_header)


def test_preparing_keeps_an_explicit_auth_signing_key_file():
    # auth rejects both sources at once, so a key file from the environment must not gain an
    # inline key alongside it.
    guard = _load_guard()
    env = {"AUTH_SIGNING_KEY_FILE": "/run/secrets/key.pem"}

    guard.prepare_integration_environment(env)

    assert "AUTH_SIGNING_KEY" not in env


def test_preparing_keeps_an_explicit_inline_auth_signing_key():
    guard = _load_guard()
    env = {"AUTH_SIGNING_KEY": "explicit"}

    guard.prepare_integration_environment(env)

    assert env["AUTH_SIGNING_KEY"] == "explicit"


@pytest.mark.parametrize("existing", [None, "0", "false"])
def test_preparing_disables_dotenv_loading(existing):
    """Every service's load_settings() calls load_dotenv(), which finds that service's dotenv
    file, i.e. the dev stack's settings, at test time, after the guard has run. Disabling it
    for the whole run means a test can only ever see what the guard checked."""
    guard = _load_guard()
    env = {} if existing is None else {"PYTHON_DOTENV_DISABLED": existing}

    guard.prepare_integration_environment(env)

    assert env["PYTHON_DOTENV_DISABLED"] == "1"


# --- wired into pytest --------------------------------------------------------


@pytest.fixture
def inner(pytester, monkeypatch):
    """A throwaway pytest project using the real root conftest, run in a subprocess so its
    environment changes can't leak into this session."""
    pytester.makeconftest(_ROOT_CONFTEST.read_text())
    pytester.makeini("[pytest]\nmarkers =\n    integration: needs the test databases\n")
    for name in list(__import__("os").environ):
        if name.endswith("DATABASE_URL"):
            monkeypatch.delenv(name)
    return pytester


_WRITES_SENTINEL = """
import pathlib
{mark}
def test_touches_the_database():
    pathlib.Path("ran.txt").write_text("ran")
"""


def test_an_integration_run_against_a_dev_database_stops_before_any_test(inner, monkeypatch):
    monkeypatch.setenv("AUTH_DATABASE_URL", _DEV_LIKE)
    inner.makepyfile(
        test_flow=_WRITES_SENTINEL.format(mark="import pytest\n@pytest.mark.integration")
    )

    result = inner.runpytest_subprocess("-m", "integration")

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert not (inner.path / "ran.txt").exists()
    result.stdout.fnmatch_lines(["*AUTH_DATABASE_URL*'auth'*", "*docker-compose.test.yml*"])


def test_a_test_under_tests_integration_counts_even_without_the_marker(inner, monkeypatch):
    monkeypatch.setenv("AUTH_DATABASE_URL", _DEV_LIKE)
    inner.mkdir("tests")
    inner.mkdir("tests/integration")
    (inner.path / "tests" / "integration" / "test_flow.py").write_text(
        _WRITES_SENTINEL.format(mark="")
    )

    result = inner.runpytest_subprocess("tests")

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert not (inner.path / "ran.txt").exists()


def test_a_unit_only_run_ignores_database_settings(inner, monkeypatch):
    """Unit tests never touch a database, so a dev URL in the shell mustn't block them."""
    monkeypatch.setenv("AUTH_DATABASE_URL", _DEV_LIKE)
    inner.makepyfile(test_unit=_WRITES_SENTINEL.format(mark=""))

    result = inner.runpytest_subprocess()

    assert result.ret == pytest.ExitCode.OK
    assert (inner.path / "ran.txt").exists()
