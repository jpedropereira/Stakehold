from datetime import date

from data_store.regimes.loader import RegimeConfig
from shared.db.connection import transaction


def save_regimes(conn, rows: list[RegimeConfig]) -> int:
    with transaction(conn), conn.cursor() as cur:
        cur.execute("DELETE FROM regimes")

        if not rows:
            return 0

        with cur.copy(
            "COPY regimes (regime_id, dimension, scope, name, start_date, "
            "end_date, known_from, severity, source, notes) FROM STDIN"
        ) as copy:
            for row in rows:
                copy.write_row(
                    (
                        row.regime_id,
                        row.dimension,
                        row.scope,
                        row.name,
                        row.start_date,
                        row.end_date,
                        row.known_from,
                        row.severity,
                        row.source,
                        row.notes,
                    )
                )

        return len(rows)


def get_overlapping_regimes(conn, start: date, end: date) -> list[tuple]:
    """Every regime overlapping [start, end], both ends inclusive, oldest first. A NULL
    end_date is an ongoing regime, so it overlaps any range ending on or after its start.
    Columns in `RegimeConfig` order."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT regime_id, dimension, scope, name, start_date,
                   end_date, known_from, severity, source, notes
            FROM regimes
            WHERE start_date <= %s AND (end_date IS NULL OR end_date >= %s)
            ORDER BY start_date, regime_id
            """,
            (end, start),
        )
        return cur.fetchall()


def count_regimes(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM regimes")
        return cur.fetchone()[0]
