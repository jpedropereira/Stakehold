import os

import psycopg
import pytest
from portfolio import cli

# PORTFOLIO_DATABASE_URL comes from the repo-root conftest.py (the disposable test databases in
# docker-compose.test.yml), which refuses to start an integration run against anything else.
# Never load this service's own dotenv file here: it points at the dev stack, and the fixture
# below truncates every portfolio table.


def connect():
    return psycopg.connect(os.environ["PORTFOLIO_DATABASE_URL"])


@pytest.fixture(autouse=True)
def clean_tables():
    """Applies to every test under tests/integration/, not just this file -- deliberately
    directory-wide, so any future integration test gets a freshly-initialized, empty schema by
    default without having to opt in. A test that needs different setup (a different role, or
    state that must survive across test functions) should override this fixture locally rather
    than relying on it being scoped narrower than it is."""
    cli.main(["db", "init"])
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE return_snapshots, position_snapshots, transactions, accounts, "
                "ticker_mappings, saved_lab_assets, saved_labs CASCADE"
            )
        conn.commit()
    yield


@pytest.fixture
def pg_connect():
    """The `connect` factory, injected so test modules can reach it without their own
    import-mode workaround for pulling a plain helper out of conftest.py."""
    return connect


# Who a seeded mapping says set it -- the column is NOT NULL, and nothing here checks it.
SEED_ADMIN = "00000000-0000-7000-8000-000000000001"


def seed_ticker_mapping(kind, identifier, canonical_ticker, *, broker=None):
    """A row straight into ticker_mappings, for tests about something that *reads* mappings.
    Tests about writing them go through the real CLI/API path instead."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ticker_mappings "
            "(identifier_kind, identifier, broker, canonical_ticker, updated_by) "
            "VALUES (%s, %s, %s, %s, %s)",
            (kind, identifier, broker, canonical_ticker, SEED_ADMIN),
        )


@pytest.fixture
def seed_mapping():
    """seed_ticker_mapping, injected the same way pg_connect is."""
    return seed_ticker_mapping
