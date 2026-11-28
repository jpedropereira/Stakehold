"""The read-only cross-user role that `portfolio db init` sets up beside the service's own role.

Row-level security (db/schema.sql) shows the service role only one user's rows at a time. The
few reads that must enumerate across users connect as this second role instead: it bypasses
RLS, and it can only SELECT. See docs/architecture.md's "User isolation".
"""

from psycopg import sql
from shared.db.connection import transaction

# What the cross-user role may read: exactly the tables its enumeration queries touch
# (accounts_repository.list_user_ids, positions_repository.list_accounts_holding,
# transactions_repository.list_accounts_using_instrument and list_distinct_instruments_all),
# plus ticker_mappings for the admin unmapped list. SELECT only -- it can never write.
_CROSS_USER_READABLE_TABLES = ("accounts", "transactions", "position_snapshots", "ticker_mappings")


def ensure_cross_user_role(admin_conn, role: str, password: str, schema: str) -> None:
    """Create the read-only role that bypasses row-level security, if missing.

    BYPASSRLS can only be granted by a superuser, which is why this runs on the admin connection
    beside jobs._ensure_role_and_schema. The role gets `schema` as its search_path, because
    unlike the service role it has no like-named schema for Postgres's "$user" entry to find."""
    with transaction(admin_conn):
        with admin_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            if cur.fetchone() is None:
                # Same reason as in jobs._ensure_role_and_schema: keep the password out of the log.
                cur.execute("SET LOCAL log_statement = 'none'")
                cur.execute(
                    sql.SQL("CREATE ROLE {} LOGIN BYPASSRLS PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(password)
                    )
                )
            cur.execute(
                sql.SQL("ALTER ROLE {} SET search_path = {}").format(
                    sql.Identifier(role), sql.Identifier(schema)
                )
            )


def grant_cross_user_reads(conn, role: str, schema: str) -> None:
    """SELECT on the cross-user tables, granted by their owner (the service role). GRANT is
    idempotent, so a rerun changes nothing."""
    with transaction(conn):
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(schema), sql.Identifier(role)
                )
            )
            cur.execute(
                sql.SQL("GRANT SELECT ON {} TO {}").format(
                    sql.SQL(", ").join(sql.Identifier(t) for t in _CROSS_USER_READABLE_TABLES),
                    sql.Identifier(role),
                )
            )
