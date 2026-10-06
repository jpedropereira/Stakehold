"""Persistence for the global ticker_mappings table. Doesn't commit: the caller owns the
transaction, as with every other repository here."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from portfolio.ticker_mapping.base import TickerMappings

_COLUMNS = (
    "id, identifier_kind, identifier, broker, canonical_ticker, updated_by, created_at, updated_at"
)

# One statement per kind, because ON CONFLICT has to name the partial index it targets: an ISIN
# is unique on (identifier), a SYMBOL on (broker, identifier). See db/schema.sql.
_UPSERT_SQL = {
    "ISIN": f"""
        INSERT INTO ticker_mappings
            (identifier_kind, identifier, broker, canonical_ticker, updated_by)
        VALUES ('ISIN', %(identifier)s, NULL, %(canonical_ticker)s, %(updated_by)s)
        ON CONFLICT (identifier) WHERE identifier_kind = 'ISIN'
        DO UPDATE SET canonical_ticker = EXCLUDED.canonical_ticker,
                      updated_by = EXCLUDED.updated_by,
                      updated_at = now()
        RETURNING {_COLUMNS}
    """,
    "SYMBOL": f"""
        INSERT INTO ticker_mappings
            (identifier_kind, identifier, broker, canonical_ticker, updated_by)
        VALUES ('SYMBOL', %(identifier)s, %(broker)s, %(canonical_ticker)s, %(updated_by)s)
        ON CONFLICT (broker, identifier) WHERE identifier_kind = 'SYMBOL'
        DO UPDATE SET canonical_ticker = EXCLUDED.canonical_ticker,
                      updated_by = EXCLUDED.updated_by,
                      updated_at = now()
        RETURNING {_COLUMNS}
    """,
}


@dataclass(frozen=True)
class TickerMappingRow:
    id: int
    identifier_kind: str
    identifier: str
    broker: str | None
    canonical_ticker: str
    updated_by: UUID
    created_at: datetime
    updated_at: datetime


def load_all(conn) -> TickerMappings:
    """Every mapping as the lookup a rebuild resolves through."""
    mappings = TickerMappings(isin={}, symbol={})
    with conn.cursor() as cur:
        cur.execute(
            "SELECT identifier_kind, identifier, broker, canonical_ticker FROM ticker_mappings"
        )
        for kind, identifier, broker, canonical_ticker in cur.fetchall():
            if kind == "ISIN":
                mappings.isin[identifier] = canonical_ticker
            else:
                mappings.symbol.setdefault(broker, {})[identifier] = canonical_ticker
    return mappings


def list_all(conn) -> list[TickerMappingRow]:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_COLUMNS} FROM ticker_mappings "
            "ORDER BY identifier_kind, broker NULLS FIRST, identifier"
        )
        return [TickerMappingRow(*row) for row in cur.fetchall()]


def upsert(
    conn,
    *,
    identifier_kind: str,
    identifier: str,
    broker: str | None,
    canonical_ticker: str,
    updated_by: UUID,
) -> TickerMappingRow:
    """Insert the mapping, or re-point the existing one for the same instrument in place."""
    with conn.cursor() as cur:
        cur.execute(
            _UPSERT_SQL[identifier_kind],
            {
                "identifier": identifier,
                "broker": broker,
                "canonical_ticker": canonical_ticker,
                "updated_by": updated_by,
            },
        )
        return TickerMappingRow(*cur.fetchone())


def delete(conn, mapping_id: int) -> TickerMappingRow | None:
    """The deleted row, or None if there was no such mapping."""
    with conn.cursor() as cur:
        cur.execute(
            f"DELETE FROM ticker_mappings WHERE id = %s RETURNING {_COLUMNS}", (mapping_id,)
        )
        row = cur.fetchone()
    return TickerMappingRow(*row) if row else None
