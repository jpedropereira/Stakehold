"""Runnable units: load settings, open a connection, run one flow, close it, return a result.

The seam every entry point goes through. `cli.py` is argument parsing plus a call into here,
and a future HTTP route will be the same -- anything that lived in only one of those two would
be, by construction, untested by the other. See docs/architecture.md's layering section.
"""

import logging
from pathlib import Path

from portfolio.run_result import build_run_result
from portfolio.settings import load_settings
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from shared.db.connection import apply_schema, get_connection, transaction
from shared.logging_config import configure_logging

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent / "db" / "schema.sql"

_TARGET_TABLES = ("accounts", "transactions", "position_snapshots", "return_snapshots")


def _existing_tables(conn, schema: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name = ANY(%s)",
            (schema, list(_TARGET_TABLES)),
        )
        return {row[0] for row in cur.fetchall()}


def _ensure_role_and_schema(admin_conn, role: str, password: str) -> None:
    """Create this service's own non-superuser role and its like-named schema, if missing.

    `admin_conn` is a Postgres superuser only because the official postgres image's bootstrap
    (`POSTGRES_USER`) always grants that -- this is the one place that privilege gets used, so
    nothing after it (the rest of this run, or the running app) ever holds it. Owning its own
    schema is sufficient isolation on its own: Postgres denies cross-schema access by default,
    so `role` gets no more reach than `schema`, and no GRANTs beyond AUTHORIZATION are needed.
    A role connecting under its own name gets this schema first on its search_path for free
    (Postgres's built-in `"$user"` entry), which is why nothing downstream has to
    schema-qualify a single table name.
    """
    with transaction(admin_conn):
        with admin_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            if cur.fetchone() is None:
                cur.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(password)
                    )
                )
            cur.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION {}").format(
                    sql.Identifier(role), sql.Identifier(role)
                )
            )


def run_db_init() -> dict[str, int | str]:
    """Apply the schema. Idempotent -- see docs/schema.md on why every statement is guarded.

    The before/after table count is what makes a rerun distinguishable from a first run in the
    output: a fresh database reports `ok` and the tables it created, an already-migrated one
    reports `nothing_to_do`, and neither looks like a failure.
    """
    configure_logging()
    settings = load_settings()
    conn_info = conninfo_to_dict(settings.database_url)
    role = conn_info["user"]

    admin_conn = get_connection(settings.admin_database_url)
    try:
        logger.info("db init: ensuring role and schema %r exist.", role)
        _ensure_role_and_schema(admin_conn, role, conn_info["password"])
    finally:
        admin_conn.close()

    conn = get_connection(settings.database_url)
    try:
        logger.info(
            "db init: applying %s (tables: %s).", SCHEMA_PATH.name, ", ".join(_TARGET_TABLES)
        )
        before = _existing_tables(conn, role)
        apply_schema(conn, SCHEMA_PATH)
    finally:
        conn.close()

    created = len(set(_TARGET_TABLES) - before)
    logger.info("db init: schema applied, %d table(s) created.", created)
    # units_total is the work there was to do -- the tables that were missing -- so a rerun
    # that created nothing reports nothing_to_do rather than a hollow ok.
    return build_run_result(units_total=created, rows_saved=created, failed=0)
