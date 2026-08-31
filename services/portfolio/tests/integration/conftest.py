import os
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv
from portfolio import cli

# Explicit path, not a bare load_dotenv(): the default search only walks upward from the
# current working directory, so it wouldn't find services/portfolio/.env when pytest is run
# from the repo root.
load_dotenv(Path(__file__).parents[2] / ".env")


def connect():
    return psycopg.connect(os.environ["PORTFOLIO_DATABASE_URL"])


@pytest.fixture(autouse=True)
def clean_tables():
    cli.main(["db", "init"])
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE return_snapshots, position_snapshots, transactions, accounts CASCADE"
            )
        conn.commit()
    yield


@pytest.fixture
def pg_connect():
    """The `connect` factory, injected so test modules can reach it without their own
    import-mode workaround for pulling a plain helper out of conftest.py."""
    return connect
