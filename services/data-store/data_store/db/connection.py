from pathlib import Path

import psycopg


def get_connection(database_url: str) -> psycopg.Connection:
    """Open a new connection. Callers own commit/rollback and closing."""
    return psycopg.connect(database_url)


def apply_schema(conn, schema_path: Path) -> None:
    sql = schema_path.read_text()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
