from data_store.regimes.loader import RegimeConfig


def save_regimes(conn, rows: list[RegimeConfig]) -> int:
    with conn.cursor() as cur:
        try:
            cur.execute("DELETE FROM regimes")

            if not rows:
                conn.commit()
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

            conn.commit()
            return len(rows)

        except Exception:
            conn.rollback()
            raise
