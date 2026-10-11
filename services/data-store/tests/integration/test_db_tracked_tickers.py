"""tracked_tickers against a real Postgres: the DDL, its defaults, and the config-sync writes.

The sync's decisions are unit-tested in tests/unit/test_ticker_onboarding.py. What only a real
database can show is here: the column defaults a config row gets, that rerunning the schema is
safe, and that an insert racing an existing row is a no-op rather than an error.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest
from data_store import cli, ticker_onboarding
from data_store.config_loader import TickerConfig
from data_store.tracked_tickers import repository
from shared.db.connection import get_connection

pytestmark = pytest.mark.integration


def config_ticker(ticker, *, display_name=None, asset_type="etf", enabled=True):
    return TickerConfig(
        ticker=ticker,
        display_name=display_name or ticker,
        asset_type=asset_type,
        price_source="yfinance",
        enabled=enabled,
    )


@pytest.fixture
def conn():
    # No fallback URL: the repo-root conftest.py sets DATABASE_URL and refuses any value that
    # isn't a disposable test database.
    assert cli.main(["db", "init"]) == 0
    connection = get_connection(os.environ["DATABASE_URL"])
    with connection.cursor() as cur:
        cur.execute("DELETE FROM tracked_tickers")
    connection.commit()
    yield connection
    with connection.cursor() as cur:
        cur.execute("DELETE FROM tracked_tickers")
    connection.commit()
    connection.close()


def test_db_init_creates_tracked_tickers_and_reruns_cleanly(conn):
    assert cli.main(["db", "init"]) == 0

    with conn.cursor() as cur:
        cur.execute("""
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'tracked_tickers' ORDER BY indexname
            """)
        indexes = [row[0] for row in cur.fetchall()]

    assert indexes == [
        "idx_tracked_tickers_in_flight",
        "idx_tracked_tickers_requested_by",
        "tracked_tickers_pkey",
    ]


def test_sync_inserts_config_rows_with_pending_defaults(conn):
    result = ticker_onboarding.sync_config_tickers(
        conn, [config_ticker("IWDA.AS", display_name="iShares World")]
    )

    assert result["rows_saved"] == 1
    [row] = repository.get_tracked_tickers(conn)
    assert row.ticker == "IWDA.AS"
    assert row.source == "yfinance"
    assert row.display_name == "iShares World"
    assert row.origin == "config"
    assert row.status == "pending"
    assert row.enabled is True
    assert row.attempts == 0
    assert row.requested_by is None
    assert row.requested_at is not None
    assert row.ready_at is None


def test_a_second_sync_of_the_same_yaml_writes_nothing(conn):
    entries = [config_ticker("IWDA.AS"), config_ticker("VWCE.DE")]
    ticker_onboarding.sync_config_tickers(conn, entries)

    result = ticker_onboarding.sync_config_tickers(conn, entries)

    assert result["rows_saved"] == 0
    assert len(repository.get_tracked_tickers(conn)) == 2


def test_sync_updates_the_yaml_owned_fields_and_keeps_the_status(conn):
    ticker_onboarding.sync_config_tickers(conn, [config_ticker("IWDA.AS")])
    with conn.cursor() as cur:
        cur.execute("UPDATE tracked_tickers SET status = 'ready' WHERE ticker = 'IWDA.AS'")
    conn.commit()

    ticker_onboarding.sync_config_tickers(
        conn, [config_ticker("IWDA.AS", display_name="Renamed", enabled=False)]
    )

    [row] = repository.get_tracked_tickers(conn)
    assert (row.display_name, row.enabled, row.status) == ("Renamed", False, "ready")


def test_sync_takes_over_a_user_requested_row(conn):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO tracked_tickers
                (ticker, display_name, asset_type, origin, status, requested_by)
            VALUES ('ASML.AS', 'ASML Holding N.V.', 'equity', 'request', 'ready',
                    '00000000-0000-0000-0000-000000000001')
            """)
    conn.commit()

    ticker_onboarding.sync_config_tickers(
        conn, [config_ticker("ASML.AS", display_name="ASML", asset_type="equity")]
    )

    [row] = repository.get_tracked_tickers(conn)
    assert row.origin == "config"
    assert row.display_name == "ASML"
    # Who asked for it first is history worth keeping, not something the YAML owns.
    assert str(row.requested_by) == "00000000-0000-0000-0000-000000000001"


def test_an_insert_racing_an_existing_row_is_a_no_op(conn):
    ticker_onboarding.sync_config_tickers(conn, [config_ticker("IWDA.AS")])

    inserted, updated = repository.save_config_tickers(
        conn, inserts=[config_ticker("IWDA.AS", display_name="Racer")], updates=[]
    )

    assert (inserted, updated) == (0, 0)
    [row] = repository.get_tracked_tickers(conn)
    assert row.display_name == "IWDA.AS"


def test_the_daily_ingest_sees_a_synced_ticker_only_once_it_is_ready(conn):
    ticker_onboarding.sync_config_tickers(conn, [config_ticker("IWDA.AS")])

    assert ticker_onboarding.tracked_ticker_configs(conn, ready_only=True) == []
    assert [c.ticker for c in ticker_onboarding.tracked_ticker_configs(conn, ready_only=False)] == [
        "IWDA.AS"
    ]

    with conn.cursor() as cur:
        cur.execute("UPDATE tracked_tickers SET status = 'ready' WHERE ticker = 'IWDA.AS'")
    conn.commit()

    assert [c.ticker for c in ticker_onboarding.tracked_ticker_configs(conn, ready_only=True)] == [
        "IWDA.AS"
    ]


# --- onboarding state transitions (TK-5) ---------------------------------------------------------


def insert(
    conn, ticker, *, status="pending", next_attempt_at=None, enabled=True, requested_at=None
):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tracked_tickers (ticker, display_name, asset_type, origin, status,
                                         next_attempt_at, enabled, requested_at)
            VALUES (%s, %s, 'equity', 'request', %s, %s, %s, COALESCE(%s, now()))
            """,
            (ticker, ticker, status, next_attempt_at, enabled, requested_at),
        )
    conn.commit()


def by_ticker(conn):
    return {row.ticker: row for row in repository.get_tracked_tickers(conn)}


def test_due_tickers_are_enabled_pending_rows_whose_retry_time_has_come(conn):
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    insert(conn, "OLDEST", requested_at=now - timedelta(days=2))
    insert(conn, "RETRY.DUE", next_attempt_at=now - timedelta(minutes=1))
    insert(conn, "RETRY.LATER", next_attempt_at=now + timedelta(minutes=1))
    insert(conn, "OFF", enabled=False)
    insert(conn, "BUSY", status="backfilling")
    insert(conn, "DONE", status="ready")

    due = repository.get_due_tickers(conn, now=now, limit=10)

    assert [r.ticker for r in due] == ["OLDEST", "RETRY.DUE"]
    assert [r.ticker for r in repository.get_due_tickers(conn, now=now, limit=1)] == ["OLDEST"]


def test_claiming_moves_pending_to_backfilling_exactly_once(conn):
    insert(conn, "SPY")

    assert repository.claim_for_backfill(conn, "SPY", "yfinance") is True
    assert repository.claim_for_backfill(conn, "SPY", "yfinance") is False
    assert by_ticker(conn)["SPY"].status == "backfilling"


def test_mark_ready_clears_retry_state(conn):
    insert(conn, "SPY", status="backfilling", next_attempt_at=datetime.now(timezone.utc))
    with conn.cursor() as cur:
        cur.execute("UPDATE tracked_tickers SET attempts = 2, last_error = 'x'")
    conn.commit()

    repository.mark_ready(conn, "SPY", "yfinance")

    row = by_ticker(conn)["SPY"]
    assert (row.status, row.attempts, row.next_attempt_at, row.last_error) == (
        "ready",
        0,
        None,
        None,
    )
    assert row.ready_at is not None


def test_record_failed_attempt_stores_what_the_flow_decided(conn):
    insert(conn, "GHOST", status="backfilling")
    retry_at = datetime(2026, 10, 10, 12, 10, tzinfo=timezone.utc)

    repository.record_failed_attempt(
        conn,
        "GHOST",
        "yfinance",
        attempts=1,
        status="pending",
        next_attempt_at=retry_at,
        error="no price history",
    )

    row = by_ticker(conn)["GHOST"]
    assert (row.status, row.attempts, row.next_attempt_at, row.last_error) == (
        "pending",
        1,
        retry_at,
        "no price history",
    )


def test_set_listing_details(conn):
    insert(conn, "VOD.L")

    repository.set_listing_details(conn, "VOD.L", "yfinance", currency="GBp", exchange="LSE")

    row = by_ticker(conn)["VOD.L"]
    assert (row.currency, row.exchange) == ("GBp", "LSE")


def test_only_stale_backfilling_rows_are_reset(conn):
    insert(conn, "STUCK", status="backfilling")
    insert(conn, "BUSY", status="backfilling")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE tracked_tickers SET updated_at = now() - interval '2 hours' "
            "WHERE ticker = 'STUCK'"
        )
    conn.commit()

    reset = repository.reset_stuck_backfilling(
        conn, stale_before=datetime.now(timezone.utc) - timedelta(hours=1)
    )

    assert reset == ["STUCK"]
    rows = by_ticker(conn)
    assert (rows["STUCK"].status, rows["BUSY"].status) == ("pending", "backfilling")


# --- requests (TK-6) ---------------------------------------------------------------------------

ALICE = "00000000-0000-0000-0000-00000000000a"


def request(conn, ticker="ASML.AS"):
    return repository.insert_requested_ticker(
        conn,
        ticker=ticker,
        source="yfinance",
        display_name="ASML Holding N.V.",
        asset_type="equity",
        currency="EUR",
        exchange="Amsterdam",
        requested_by=ALICE,
    )


def test_a_requested_ticker_is_inserted_once(conn):
    assert request(conn) is True
    assert request(conn) is False

    row = repository.get_tracked_ticker(conn, "ASML.AS", "yfinance")
    assert (row.origin, row.status, str(row.requested_by), row.currency) == (
        "request",
        "pending",
        ALICE,
        "EUR",
    )
    assert repository.get_tracked_ticker(conn, "NOPE", "yfinance") is None


def test_requests_are_counted_per_user_within_the_window(conn):
    request(conn, "A")
    request(conn, "B")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE tracked_tickers SET requested_at = now() - interval '2 days' WHERE ticker = 'A'"
        )
    conn.commit()
    since = datetime.now(timezone.utc) - timedelta(days=1)

    assert repository.count_requests_since(conn, ALICE, since) == 1
    assert repository.count_requests_since(conn, "00000000-0000-0000-0000-00000000000b", since) == 0


def test_list_filters_combine(conn):
    request(conn, "A")
    request(conn, "B")
    insert(conn, "C", status="ready")
    with conn.cursor() as cur:
        cur.execute("UPDATE tracked_tickers SET status = 'ready' WHERE ticker = 'B'")
    conn.commit()

    def tickers(**filters):
        rows = repository.list_tracked_tickers(conn, source="yfinance", **filters)
        return [r.ticker for r in rows]

    assert tickers() == ["A", "B", "C"]
    assert tickers(requested_by=ALICE) == ["A", "B"]
    assert tickers(statuses=["ready"]) == ["B", "C"]
    assert tickers(requested_by=ALICE, statuses=["ready"]) == ["B"]
    assert tickers(tickers=["A", "C", "Z"]) == ["A", "C"]
    assert repository.list_tracked_tickers(conn, source="other") == []


def test_set_enabled_and_reset_for_retry(conn):
    insert(conn, "GHOST", status="failed")
    with conn.cursor() as cur:
        cur.execute("UPDATE tracked_tickers SET attempts = 3, last_error = 'gone'")
    conn.commit()

    repository.set_enabled(conn, "GHOST", "yfinance", False)
    assert repository.reset_for_retry(conn, "GHOST", "yfinance") is True
    assert repository.reset_for_retry(conn, "GHOST", "yfinance") is False

    row = repository.get_tracked_ticker(conn, "GHOST", "yfinance")
    assert (row.enabled, row.status, row.attempts, row.last_error) == (False, "pending", 0, None)
