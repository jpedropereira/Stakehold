from datetime import date

import pytest
from data_store.read_flows import regimes_overlapping
from data_store.regimes.loader import RegimeConfig
from data_store.regimes.repository import (
    count_regimes,
    get_overlapping_regimes,
    save_regimes,
)
from shared.db.connection import get_connection

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def postgres_conn():
    """Session-scoped connection to the test database."""
    import os

    # No fallback URL: the repo-root conftest.py sets DATABASE_URL and refuses any value that
    # isn't a disposable test database. A default here would bypass that check.
    conn = get_connection(os.environ["DATABASE_URL"])
    return conn


@pytest.fixture
def reset_regimes_table(postgres_conn):
    """Reset the regimes table before and after a test."""
    with postgres_conn.cursor() as cur:
        cur.execute("DELETE FROM regimes;")
    postgres_conn.commit()
    yield
    with postgres_conn.cursor() as cur:
        cur.execute("DELETE FROM regimes;")
    postgres_conn.commit()


def test_save_regimes_full_sync_replaces_data(postgres_conn, reset_regimes_table):
    """Full sync: inserting new rows removes old ones."""
    rows_1 = [
        RegimeConfig(
            regime_id="gfc",
            dimension="Financial",
            scope="Global",
            name="Global Financial Crisis",
            start_date=date(2007, 9, 1),
            end_date=date(2009, 3, 1),
            known_from=None,
            severity="extreme",
            source="Fed",
            notes=None,
        ),
        RegimeConfig(
            regime_id="covid",
            dimension="Health",
            scope="Global",
            name="COVID-19",
            start_date=date(2020, 3, 1),
            end_date=None,
            known_from=None,
            severity="severe",
            source="WHO",
            notes=None,
        ),
    ]

    save_regimes(postgres_conn, rows_1)

    with postgres_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM regimes;")
        assert cur.fetchone()[0] == 2

    # Now sync with just one regime (covid is gone)
    rows_2 = [
        RegimeConfig(
            regime_id="gfc",
            dimension="Financial",
            scope="Global",
            name="Global Financial Crisis",
            start_date=date(2007, 9, 1),
            end_date=date(2009, 3, 1),
            known_from=None,
            severity="extreme",
            source="Fed",
            notes=None,
        ),
    ]

    save_regimes(postgres_conn, rows_2)

    with postgres_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM regimes;")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT regime_id FROM regimes;")
        assert cur.fetchone()[0] == "gfc"


def test_save_regimes_is_idempotent(postgres_conn, reset_regimes_table):
    """Rerunning with the same data produces the same table."""
    rows = [
        RegimeConfig(
            regime_id="gfc",
            dimension="Financial",
            scope="Global",
            name="Global Financial Crisis",
            start_date=date(2007, 9, 1),
            end_date=date(2009, 3, 1),
            known_from=date(2010, 9, 20),
            severity="extreme",
            source="Fed",
            notes="Lehman collapse",
        ),
    ]

    save_regimes(postgres_conn, rows)

    with postgres_conn.cursor() as cur:
        cur.execute("SELECT regime_id, notes FROM regimes WHERE regime_id = 'gfc';")
        row1 = cur.fetchone()

    save_regimes(postgres_conn, rows)

    with postgres_conn.cursor() as cur:
        cur.execute("SELECT regime_id, notes FROM regimes WHERE regime_id = 'gfc';")
        row2 = cur.fetchone()

    assert row1 == row2


def test_save_regimes_nullable_fields_roundtrip(postgres_conn, reset_regimes_table):
    """NULL fields in the CSV are stored as NULL in the database."""
    rows = [
        RegimeConfig(
            regime_id="ongoing_war",
            dimension="Geopolitical",
            scope="Europe",
            name="Ongoing War",
            start_date=date(2022, 2, 24),
            end_date=None,
            known_from=None,
            severity=None,
            source="Bloomberg",
            notes=None,
        ),
    ]

    save_regimes(postgres_conn, rows)

    with postgres_conn.cursor() as cur:
        cur.execute(
            "SELECT end_date, known_from, severity, notes FROM regimes"
            " WHERE regime_id = 'ongoing_war';"
        )
        end_date, known_from, severity, notes = cur.fetchone()

    assert end_date is None
    assert known_from is None
    assert severity is None
    assert notes is None


def _regime(regime_id, start, end):
    return RegimeConfig(
        regime_id=regime_id,
        dimension="drawdown",
        scope="SPX",
        name=regime_id,
        start_date=start,
        end_date=end,
        known_from=None,
        severity=None,
        source="manual_approx",
        notes=None,
    )


def test_get_overlapping_regimes_is_inclusive_at_both_ends(postgres_conn, reset_regimes_table):
    """Range 2020-01-01..2020-12-31: a regime touching either end counts, one a day short
    doesn't, and an ongoing one (NULL end_date) that started before the range end counts."""
    save_regimes(
        postgres_conn,
        [
            _regime("ends_on_start", date(2019, 6, 1), date(2020, 1, 1)),
            _regime("ends_day_before_start", date(2019, 6, 1), date(2019, 12, 31)),
            _regime("starts_on_end", date(2020, 12, 31), date(2021, 3, 1)),
            _regime("starts_after_end", date(2021, 1, 1), date(2021, 3, 1)),
            _regime("ongoing", date(2018, 1, 1), None),
            _regime("ongoing_after_end", date(2021, 1, 1), None),
            _regime("inside", date(2020, 3, 1), date(2020, 4, 1)),
        ],
    )

    rows = get_overlapping_regimes(postgres_conn, date(2020, 1, 1), date(2020, 12, 31))

    # Ordered by start_date, then regime_id.
    assert [r[0] for r in rows] == ["ongoing", "ends_on_start", "inside", "starts_on_end"]
    assert count_regimes(postgres_conn) == 7


def test_regimes_overlapping_serves_every_column_and_the_total(postgres_conn, reset_regimes_table):
    save_regimes(
        postgres_conn,
        [
            RegimeConfig(
                regime_id="spx_bear_2020",
                dimension="drawdown",
                scope="SPX",
                name="COVID crash",
                start_date=date(2020, 2, 19),
                end_date=date(2020, 3, 23),
                known_from=date(2020, 3, 23),
                severity="severe",
                source="manual_approx",
                notes="about -34%",
            ),
            _regime("spx_bear_2007", date(2007, 10, 9), date(2009, 3, 9)),
        ],
    )

    result = regimes_overlapping(postgres_conn, date(2020, 1, 1), date(2020, 12, 31))

    assert result == {
        "total_regimes": 2,
        "regimes": [
            {
                "regime_id": "spx_bear_2020",
                "dimension": "drawdown",
                "scope": "SPX",
                "name": "COVID crash",
                "start_date": date(2020, 2, 19),
                "end_date": date(2020, 3, 23),
                "known_from": date(2020, 3, 23),
                "severity": "severe",
                "source": "manual_approx",
                "notes": "about -34%",
            }
        ],
    }


def test_regimes_overlapping_on_an_empty_table_reports_a_zero_total(
    postgres_conn, reset_regimes_table
):
    assert regimes_overlapping(postgres_conn, date(2020, 1, 1), date(2020, 12, 31)) == {
        "total_regimes": 0,
        "regimes": [],
    }
