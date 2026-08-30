import logging
from pathlib import Path

from auth.db.connection import apply_schema, get_connection
from auth.logging_config import configure_logging
from auth.settings import load_settings

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent / "db" / "schema.sql"


def run_db_init() -> dict[str, int | str]:
    """Apply the schema. Idempotent -- see docs/schema.md on why every statement is guarded."""
    configure_logging()
    settings = load_settings()
    conn = get_connection(settings.database_url)
    try:
        apply_schema(conn, SCHEMA_PATH)
    finally:
        conn.close()
    logger.info("db init: schema applied.")
    return {"status": "ok", "rows_saved": 0}
