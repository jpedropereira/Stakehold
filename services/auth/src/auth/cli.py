import argparse
import sys

from auth import jobs

_SUCCESS_STATUSES = {"ok", "nothing_to_do"}


def _db_init(_args: argparse.Namespace) -> int:
    result = jobs.run_db_init()
    print(f"{result['rows_saved']} table(s) created")
    return 0 if result["status"] in _SUCCESS_STATUSES else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="auth")
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
