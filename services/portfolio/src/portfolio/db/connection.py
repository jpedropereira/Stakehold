"""The two ways portfolio's code reaches its own tables under row-level security.

db/schema.sql's policies show a `portfolio` connection only the rows of the user named by the
session setting `app.user_id`. With no user set, the connection sees no rows and can insert
none. So every job that acts for one user opens its connection with `user_connection`. The few
reads that must enumerate across users use `cross_user_connection`: a separate role that
bypasses RLS but can only SELECT. See docs/architecture.md's "User isolation".
"""

from uuid import UUID

import psycopg
from portfolio.settings import Settings, SettingsError
from shared.db.connection import get_connection, transaction


def user_connection(settings: Settings, user_id: UUID) -> psycopg.Connection:
    """A connection that sees and writes only `user_id`'s rows. Callers own closing it.

    The setting is session-wide (`is_local` false), not per transaction, so it covers reads that
    run outside `transaction()` too. That is safe only because nothing pools portfolio's
    connections: each one serves a single job for a single user and is closed afterwards. A pool
    would have to reset it on every checkout.
    """
    conn = get_connection(settings.database_url)
    try:
        with transaction(conn), conn.cursor() as cur:
            cur.execute("SELECT set_config('app.user_id', %s, false)", (str(user_id),))
    except Exception:
        conn.close()
        raise
    return conn


def cross_user_connection(settings: Settings) -> psycopg.Connection:
    """A read-only connection that sees every user's rows. Callers own closing it.

    Only for reads that enumerate across users so the work can be handed to per-user jobs.
    Raises SettingsError if PORTFOLIO_CROSS_USER_DATABASE_URL isn't set."""
    if not settings.cross_user_database_url:
        raise SettingsError(
            "PORTFOLIO_CROSS_USER_DATABASE_URL is not set -- this command reads across users "
            "and needs the read-only cross-user role. Run `portfolio db init` after setting it."
        )
    return get_connection(settings.cross_user_database_url)
