from contextlib import contextmanager
from pathlib import Path

import psycopg


def get_connection(database_url: str) -> psycopg.Connection:
    """Open a new connection. Callers own commit/rollback and closing."""
    return psycopg.connect(database_url)


@contextmanager
def transaction(conn):
    """Commit on success, rollback on any exception.

    Rolling back on failure matters beyond correctness: a failed statement leaves Postgres's
    transaction aborted, so every later statement on this connection would fail too until
    something rolls it back. Callers that isolate one unit's failure and keep going (see
    pipeline.py's per-ticker/per-batch loops) depend on this to leave the connection usable for
    the next one.
    """
    try:
        yield
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def apply_schema(conn, schema_path: Path) -> None:
    sql = schema_path.read_text()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
