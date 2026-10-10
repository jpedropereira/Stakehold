"""Reads and writes the tracked_tickers table. Knows nothing about where a row came from beyond
what the row itself records -- the YAML sync's decisions live in ticker_onboarding.py."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from data_store.config_loader import TickerConfig
from shared.db.connection import transaction

ORIGIN_CONFIG = "config"
ORIGIN_REQUEST = "request"

STATUS_PENDING = "pending"
STATUS_BACKFILLING = "backfilling"
STATUS_READY = "ready"
STATUS_FAILED = "failed"

# VARCHAR(20), the same width as raw_market_data.ticker. Checked before a write so one
# over-long entry is skipped with a warning rather than failing the whole statement.
TICKER_MAX_LENGTH = 20


@dataclass(frozen=True)
class TrackedTicker:
    ticker: str
    source: str
    display_name: str
    asset_type: str
    currency: str | None
    exchange: str | None
    origin: str
    status: str
    enabled: bool
    attempts: int
    next_attempt_at: datetime | None
    last_error: str | None
    requested_by: UUID | None
    requested_at: datetime
    ready_at: datetime | None
    updated_at: datetime


# Column order matches TrackedTicker's field order.
_SELECT_ROWS = """
    SELECT ticker, source, display_name, asset_type, currency, exchange, origin, status,
           enabled, attempts, next_attempt_at, last_error, requested_by, requested_at,
           ready_at, updated_at
    FROM tracked_tickers
"""


def get_tracked_tickers(conn) -> list[TrackedTicker]:
    with conn.cursor() as cur:
        cur.execute(_SELECT_ROWS + " ORDER BY source, ticker")
        return [TrackedTicker(*row) for row in cur.fetchall()]


def get_tracked_ticker(conn, ticker: str, source: str) -> TrackedTicker | None:
    with conn.cursor() as cur:
        cur.execute(_SELECT_ROWS + " WHERE ticker = %s AND source = %s", (ticker, source))
        row = cur.fetchone()
    return TrackedTicker(*row) if row else None


def list_tracked_tickers(
    conn,
    *,
    source: str,
    requested_by: UUID | None = None,
    statuses: list[str] | None = None,
    tickers: list[str] | None = None,
) -> list[TrackedTicker]:
    """Every row for `source`, narrowed by whichever filters are given."""
    conditions = ["source = %s"]
    params: list = [source]
    if requested_by is not None:
        conditions.append("requested_by = %s")
        params.append(requested_by)
    if statuses:
        conditions.append("status = ANY(%s)")
        params.append(list(statuses))
    if tickers:
        conditions.append("ticker = ANY(%s)")
        params.append(list(tickers))
    with conn.cursor() as cur:
        cur.execute(
            _SELECT_ROWS + " WHERE " + " AND ".join(conditions) + " ORDER BY ticker", params
        )
        return [TrackedTicker(*row) for row in cur.fetchall()]


def count_requests_since(conn, requested_by: UUID, since: datetime) -> int:
    """Rows `requested_by` created at or after `since` -- what the per-user daily limit counts."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM tracked_tickers WHERE requested_by = %s AND requested_at >= %s",
            (requested_by, since),
        )
        return cur.fetchone()[0]


def insert_requested_ticker(
    conn,
    *,
    ticker: str,
    source: str,
    display_name: str,
    asset_type: str,
    currency: str | None,
    exchange: str | None,
    requested_by: UUID,
) -> bool:
    """Add a user's request as a `pending` row. False if the ticker was already there -- a
    concurrent request or sync got in first -- which is a no-op, not an error."""
    with transaction(conn), conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tracked_tickers
                (ticker, source, display_name, asset_type, currency, exchange, origin, status,
                 requested_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, source) DO NOTHING
            """,
            (
                ticker,
                source,
                display_name,
                asset_type,
                currency,
                exchange,
                ORIGIN_REQUEST,
                STATUS_PENDING,
                requested_by,
            ),
        )
        return cur.rowcount == 1


def set_enabled(conn, ticker: str, source: str, enabled: bool) -> None:
    with transaction(conn), conn.cursor() as cur:
        cur.execute(
            "UPDATE tracked_tickers SET enabled = %s, updated_at = now() "
            "WHERE ticker = %s AND source = %s",
            (enabled, ticker, source),
        )


def reset_for_retry(conn, ticker: str, source: str) -> bool:
    """Put a `failed` row back in the onboarding queue with a fresh set of attempts. False if it
    wasn't `failed` (any more)."""
    with transaction(conn), conn.cursor() as cur:
        cur.execute(
            """
            UPDATE tracked_tickers
            SET status = %s, attempts = 0, next_attempt_at = NULL, last_error = NULL,
                updated_at = now()
            WHERE ticker = %s AND source = %s AND status = %s
            """,
            (STATUS_PENDING, ticker, source, STATUS_FAILED),
        )
        return cur.rowcount == 1


def get_due_tickers(conn, *, now: datetime, limit: int) -> list[TrackedTicker]:
    """Enabled `pending` rows whose retry time (if any) has come, oldest request first."""
    with conn.cursor() as cur:
        cur.execute(
            _SELECT_ROWS + """
            WHERE enabled AND status = %s AND (next_attempt_at IS NULL OR next_attempt_at <= %s)
            ORDER BY requested_at, ticker
            LIMIT %s
            """,
            (STATUS_PENDING, now, limit),
        )
        rows = [TrackedTicker(*row) for row in cur.fetchall()]
    # Ends the read's implicit transaction, so the claims that follow each commit on their own.
    conn.commit()
    return rows


def claim_for_backfill(conn, ticker: str, source: str) -> bool:
    """Move one row from `pending` to `backfilling`. False if it wasn't `pending` any more --
    another run got there first -- which the caller treats as "not mine"."""
    with transaction(conn), conn.cursor() as cur:
        cur.execute(
            """
            UPDATE tracked_tickers SET status = %s, updated_at = now()
            WHERE ticker = %s AND source = %s AND status = %s
            """,
            (STATUS_BACKFILLING, ticker, source, STATUS_PENDING),
        )
        return cur.rowcount == 1


def reset_stuck_backfilling(conn, *, stale_before: datetime) -> list[str]:
    """Return `backfilling` rows untouched since `stale_before` to `pending`, and name them. A
    row only stays `backfilling` that long if the run working on it died."""
    with transaction(conn), conn.cursor() as cur:
        cur.execute(
            """
            UPDATE tracked_tickers SET status = %s, updated_at = now()
            WHERE status = %s AND updated_at < %s
            RETURNING ticker
            """,
            (STATUS_PENDING, STATUS_BACKFILLING, stale_before),
        )
        return sorted(row[0] for row in cur.fetchall())


def set_listing_details(
    conn, ticker: str, source: str, *, currency: str | None, exchange: str | None
) -> None:
    with transaction(conn), conn.cursor() as cur:
        cur.execute(
            """
            UPDATE tracked_tickers SET currency = %s, exchange = %s, updated_at = now()
            WHERE ticker = %s AND source = %s
            """,
            (currency, exchange, ticker, source),
        )


def mark_ready(conn, ticker: str, source: str) -> None:
    with transaction(conn), conn.cursor() as cur:
        cur.execute(
            """
            UPDATE tracked_tickers
            SET status = %s, attempts = 0, next_attempt_at = NULL, last_error = NULL,
                ready_at = now(), updated_at = now()
            WHERE ticker = %s AND source = %s
            """,
            (STATUS_READY, ticker, source),
        )


def record_failed_attempt(
    conn,
    ticker: str,
    source: str,
    *,
    attempts: int,
    status: str,
    next_attempt_at: datetime | None,
    error: str,
) -> None:
    """Store a failed onboarding attempt. Whether to retry, and when, is the caller's decision."""
    with transaction(conn), conn.cursor() as cur:
        cur.execute(
            """
            UPDATE tracked_tickers
            SET status = %s, attempts = %s, next_attempt_at = %s, last_error = %s,
                updated_at = now()
            WHERE ticker = %s AND source = %s
            """,
            (status, attempts, next_attempt_at, error, ticker, source),
        )


def save_config_tickers(
    conn, *, inserts: list[TickerConfig], updates: list[TickerConfig]
) -> tuple[int, int]:
    """Insert new config rows and rewrite the YAML-owned fields of existing ones, in one
    transaction. Returns (inserted, updated).

    An insert that finds the row already there is a no-op (`ON CONFLICT DO NOTHING`), not an
    error: something else (a user's request, a concurrent sync) can add the same ticker between
    the caller's read and this write, and the next sync turns it into an update anyway.

    An update sets `origin = 'config'` and leaves status, attempts and requested_by alone: the
    YAML owns what the ticker is, not how far its onboarding has got or who asked for it first.
    """
    inserted = updated = 0
    with transaction(conn), conn.cursor() as cur:
        for entry in inserts:
            cur.execute(
                """
                INSERT INTO tracked_tickers
                    (ticker, source, display_name, asset_type, origin, status, enabled)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, source) DO NOTHING
                """,
                (
                    entry.ticker,
                    entry.price_source,
                    entry.display_name,
                    entry.asset_type,
                    ORIGIN_CONFIG,
                    STATUS_PENDING,
                    entry.enabled,
                ),
            )
            inserted += cur.rowcount
        for entry in updates:
            cur.execute(
                """
                UPDATE tracked_tickers
                SET display_name = %s, asset_type = %s, enabled = %s, origin = %s,
                    updated_at = now()
                WHERE ticker = %s AND source = %s
                """,
                (
                    entry.display_name,
                    entry.asset_type,
                    entry.enabled,
                    ORIGIN_CONFIG,
                    entry.ticker,
                    entry.price_source,
                ),
            )
            updated += cur.rowcount
    return inserted, updated
