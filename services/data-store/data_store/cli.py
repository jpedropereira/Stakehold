import argparse
import sys
from pathlib import Path

from data_store.db.connection import apply_schema, get_connection
from data_store.settings import load_settings

_SCHEMA_PATH = Path(__file__).parent / "db" / "schema.sql"


def _db_init(_args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = get_connection(settings.database_url)
    try:
        apply_schema(conn, _SCHEMA_PATH)
    finally:
        conn.close()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="data-store")
    subparsers = parser.add_subparsers(dest="command", required=True)

    db_parser = subparsers.add_parser("db", help="Database maintenance commands")
    db_subparsers = db_parser.add_subparsers(dest="db_command", required=True)
    init_parser = db_subparsers.add_parser("init", help="Apply the database schema")
    init_parser.set_defaults(handler=_db_init)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
