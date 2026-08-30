import logging
from datetime import UTC, datetime
from pathlib import Path

from auth.sessions import repository as sessions_repository
from auth.settings import load_settings
from shared.db.connection import apply_schema, get_connection
from shared.logging_config import configure_logging

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent / "db" / "schema.sql"

STATUS_OK = "ok"
STATUS_NOTHING_TO_DO = "nothing_to_do"

_TARGET_TABLES = ("users", "sessions")


def _existing_tables(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ANY(%s)",
            (list(_TARGET_TABLES),),
        )
        return {row[0] for row in cur.fetchall()}


def run_db_init() -> dict[str, int | str]:
    """Apply the schema. Idempotent -- see docs/schema.md on why every statement is guarded."""
    configure_logging()
    settings = load_settings()
    conn = get_connection(settings.database_url)
    try:
        logger.info(
            "db init: applying %s (tables: %s).", SCHEMA_PATH.name, ", ".join(_TARGET_TABLES)
        )
        before = _existing_tables(conn)
        apply_schema(conn, SCHEMA_PATH)
    finally:
        conn.close()

    created = len(set(_TARGET_TABLES) - before)
    status = STATUS_OK if created else STATUS_NOTHING_TO_DO
    logger.info("db init: schema applied, %d table(s) created.", created)
    return {"status": status, "rows_saved": created}


def run_purge_expired_sessions() -> dict[str, int | str]:
    """Delete sessions past their sliding or absolute expiry.

    The `(expires_at)` index in schema.sql exists specifically for this sweep -- without it the
    sessions table only ever grows, since logout is the only other row-removal path.
    """
    configure_logging()
    settings = load_settings()
    conn = get_connection(settings.database_url)
    try:
        now = datetime.now(UTC)
        logger.info("sessions purge-expired: deleting sessions expired as of %s.", now.isoformat())
        deleted = sessions_repository.delete_expired(conn, now)
    finally:
        conn.close()

    status = STATUS_OK if deleted else STATUS_NOTHING_TO_DO
    logger.info("sessions purge-expired: done, %d session(s) deleted.", deleted)
    return {"status": status, "rows_saved": deleted}
