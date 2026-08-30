import argparse
import sys

from portfolio import jobs
from portfolio.run_result import STATUS_PARTIAL


def _exit_code(result: dict[str, int | str]) -> int:
    """Non-zero when a run only partly succeeded, so a scheduler can tell.

    "nothing_to_do" is deliberately a success: a quiet day is not a broken one, and the run has
    already said so on stdout.
    """
    return 1 if result["status"] == STATUS_PARTIAL else 0


def _report(result: dict[str, int | str], noun: str) -> int:
    print(f"{result['rows_saved']} {noun}")
    return _exit_code(result)


def _db_init(_args: argparse.Namespace) -> int:
    return _report(jobs.run_db_init(), "table(s) created")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="portfolio")
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
