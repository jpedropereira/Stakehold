"""Workspace-wide pytest setup: keeps the integration suite on disposable test databases, and
refuses a run in which one test file was silently replaced by another (see the end of this
docstring).

Integration fixtures TRUNCATE tables (users, sessions, accounts, transactions, ...). Locally they
used to read the services' own `.env` files, i.e. the dev stack's real databases, and twice
(2026-10-03 and 2026-10-06) a run wiped real users and portfolio data. So when a run selects any
integration test, this file:

1. Fills in the test databases' URLs (`TEST_DATABASE_ENV`), unless the environment already sets
   them, as CI or a developer may, plus a freshly generated signing key for `auth`'s delegated
   tokens unless one is already set. It also turns off python-dotenv for the run
   (`PYTHON_DOTENV_DISABLED`): every service's `load_settings()` calls `load_dotenv()`, which
   would otherwise read that service's dev settings at test time, after the check below.
2. Refuses to start (exit code 4, before the first test) unless every `*DATABASE_URL` variable
   names a database ending in `_test` on a server started with
   `stakehold.purpose=integration-test`. Only `docker-compose.test.yml` starts servers with that
   setting, so neither a dev URL nor a `_test` database created on the dev server gets through.

A unit-only run (the default, `-m 'not integration'`) is untouched: it never connects anywhere.

It lives at the repo root, not in one service, so it covers every service's tests whichever
path pytest is given. Its own tests: services/shared/tests/unit/test_integration_db_guard.py.

Shadowed test files: under `--import-mode=importlib` a test file's module name comes from its
package path, so two services' `tests/unit/__init__.py` once made both `unit`, and portfolio's
`tests/unit/test_cli.py` silently ran auth's tests in its place. Every run now checks that each
collected test's module was loaded from that test's own file, and stops (exit code 4) if not.
That's why no service's `tests/` holds an `__init__.py`. Its own tests:
services/shared/tests/unit/test_shadowed_module_guard.py.
"""

import os
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]  # used by the guard's own tests

# Must match docker-compose.test.yml. Throwaway credentials for in-memory containers that hold
# nothing but test data, so committing them is fine (the same reasoning CI's always used).
_PASSWORD = "test-throwaway"  # pragma: allowlist secret
_ADMIN_PASSWORD = "test-throwaway-admin"  # pragma: allowlist secret


def _url(user, password, port, dbname):
    return f"postgresql://{user}:{password}@localhost:{port}/{dbname}"


TEST_DATABASE_ENV = {
    "DATABASE_URL": _url("data_store", _PASSWORD, 55432, "data_store_test"),
    "AUTH_DATABASE_URL": _url("auth", _PASSWORD, 55434, "auth_test"),
    # app-postgres's bootstrap role; portfolio's `db init` uses it to create the scoped
    # `portfolio` role below, with the password from PORTFOLIO_DATABASE_URL.
    "APP_POSTGRES_ADMIN_DATABASE_URL": _url(
        "app_postgres_admin", _ADMIN_PASSWORD, 55435, "app_test"
    ),
    "PORTFOLIO_DATABASE_URL": _url("portfolio", _PASSWORD, 55435, "app_test"),
    "AUTH_INTERNAL_SECRET": "test-throwaway-internal-secret",  # pragma: allowlist secret
    "PORTFOLIO_INTERNAL_SECRET": (
        "test-throwaway-portfolio-internal-secret"  # pragma: allowlist secret
    ),
}

TEST_DATABASE_SUFFIX = "_test"
SERVER_PURPOSE_SETTING = "stakehold.purpose"
SERVER_PURPOSE = "integration-test"

_START_HINT = (
    "Start the disposable test databases with:\n"
    "    docker compose -f docker-compose.test.yml up -d --wait\n"
    "Integration tests truncate tables. Never point them at the dev stack."
)


def _is_integration(item) -> bool:
    # The path counts as well as the marker, so a test added under tests/integration/ without
    # the marker is still guarded rather than silently treated as a unit test.
    return item.get_closest_marker("integration") is not None or (
        "/tests/integration/" in item.path.as_posix()
    )


def check_test_databases(environ, *, connect=None) -> list[str]:
    """Problems that make it unsafe to run integration tests, or [] if there are none.

    Names are checked first, for every `*DATABASE_URL` variable, and nothing connects if any
    fails: a wrong name may be a real database. Then each server (host and port) must be
    reachable through at least one of its URLs and report the test-server setting.
    """
    from psycopg.conninfo import conninfo_to_dict

    if connect is None:
        import psycopg

        connect = psycopg.connect

    urls = {name: url for name, url in environ.items() if name.endswith("DATABASE_URL")}
    problems = []
    servers: dict[tuple, list[tuple[str, str]]] = {}
    for name, url in sorted(urls.items()):
        try:
            info = conninfo_to_dict(url)
        except Exception as exc:  # malformed URL: refuse it rather than guess
            problems.append(f"{name} isn't a valid database URL ({exc}).")
            continue
        dbname = info.get("dbname") or ""
        if not dbname.endswith(TEST_DATABASE_SUFFIX):
            problems.append(
                f"{name} points at database {dbname or '(none)'!r}, not a "
                f"'*{TEST_DATABASE_SUFFIX}' test database."
            )
            continue
        servers.setdefault((info.get("host"), info.get("port")), []).append((name, url))
    if problems:
        return problems

    for (host, port), entries in servers.items():
        names = ", ".join(name for name, _ in entries)
        purpose, reached = None, False
        for _, url in entries:
            try:
                with connect(url, connect_timeout=5) as conn:
                    row = conn.execute(
                        "SELECT current_setting(%s, true)", (SERVER_PURPOSE_SETTING,)
                    ).fetchone()
            except Exception:
                continue
            reached, purpose = True, row[0]
            break
        if not reached:
            problems.append(f"{names}: no test database reachable at {host}:{port}.")
        elif purpose != SERVER_PURPOSE:
            problems.append(
                f"{names}: {host}:{port} is not a disposable test server "
                f"({SERVER_PURPOSE_SETTING} is {purpose!r}, expected {SERVER_PURPOSE!r})."
            )
    return problems


def _ephemeral_auth_signing_key() -> str:
    """A fresh Ed25519 PEM for auth's delegated tokens: auth refuses to start without one.
    Generated per run rather than committed, so no private key ever sits in the repo."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    return (
        Ed25519PrivateKey.generate()
        .private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        .decode("ascii")
    )


def prepare_integration_environment(environ) -> None:
    for name, value in TEST_DATABASE_ENV.items():
        environ.setdefault(name, value)
    # Only when neither source is set: auth rejects both at once.
    if not environ.get("AUTH_SIGNING_KEY") and not environ.get("AUTH_SIGNING_KEY_FILE"):
        environ["AUTH_SIGNING_KEY"] = _ephemeral_auth_signing_key()
    # Forced, not defaulted: nothing in an integration run may read a dotenv file.
    environ["PYTHON_DOTENV_DISABLED"] = "1"


def find_shadowed_test_files(items) -> list[str]:
    """One line per test file whose tests came from a different file's module: a same-named
    module imported earlier was reused in its place. Items with no Python module (doctests,
    other plugins' items) can't be shadowed this way and are skipped."""
    shadowed = {}
    for item in items:
        module = getattr(item, "module", None)
        module_file = getattr(module, "__file__", None)
        if module_file is None:
            continue
        item_file, loaded_from = item.path.resolve(), Path(module_file).resolve()
        if item_file != loaded_from:
            shadowed[item_file] = loaded_from
    return [
        f"{item_file} ran the tests of {loaded}" for item_file, loaded in sorted(shadowed.items())
    ]


def pytest_collection_finish(session):
    # After -m deselection, so session.items is exactly what will run; before any test or
    # fixture, so a refused run touches nothing.
    shadowed = find_shadowed_test_files(session.items)
    if shadowed:
        pytest.exit(
            "Refusing to run: test files were replaced by same-named modules:\n  - "
            + "\n  - ".join(shadowed)
            + "\nGive each its own module name: with --import-mode=importlib, don't add an "
            "__init__.py under a service's tests/.",
            returncode=pytest.ExitCode.USAGE_ERROR,
        )
    if not any(_is_integration(item) for item in session.items):
        return
    prepare_integration_environment(os.environ)
    problems = check_test_databases(os.environ)
    if problems:
        pytest.exit(
            "Refusing to run integration tests:\n  - "
            + "\n  - ".join(problems)
            + "\n"
            + _START_HINT,
            returncode=pytest.ExitCode.USAGE_ERROR,
        )
