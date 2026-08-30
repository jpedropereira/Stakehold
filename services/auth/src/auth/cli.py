import argparse
import sys

from auth import jobs

_SUCCESS_STATUSES = {"ok", "nothing_to_do"}


def _db_init(_args: argparse.Namespace) -> int:
    result = jobs.run_db_init()
    print(f"{result['rows_saved']} table(s) created")
    return 0 if result["status"] in _SUCCESS_STATUSES else 1


def _sessions_purge_expired(_args: argparse.Namespace) -> int:
    result = jobs.run_purge_expired_sessions()
    print(f"{result['rows_saved']} session(s) deleted")
    return 0 if result["status"] in _SUCCESS_STATUSES else 1


def _serve(_args: argparse.Namespace) -> int:
    # Imported lazily so `auth db init`/`auth sessions purge-expired` don't pay for importing
    # FastAPI/uvicorn on every invocation -- only `auth serve` needs them.
    import uvicorn
    from auth.api.app import create_app
    from auth.settings import load_settings

    settings = load_settings()
    uvicorn.run(create_app(settings), host=settings.api_host, port=settings.api_port)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="auth")
    subparsers = parser.add_subparsers(dest="command", required=True)

    db_parser = subparsers.add_parser("db", help="Database maintenance commands")
    db_subparsers = db_parser.add_subparsers(dest="db_command", required=True)
    init_parser = db_subparsers.add_parser("init", help="Apply the database schema")
    init_parser.set_defaults(handler=_db_init)

    sessions_parser = subparsers.add_parser("sessions", help="Session maintenance commands")
    sessions_subparsers = sessions_parser.add_subparsers(dest="sessions_command", required=True)
    purge_parser = sessions_subparsers.add_parser(
        "purge-expired", help="Delete expired/absolutely-expired sessions"
    )
    purge_parser.set_defaults(handler=_sessions_purge_expired)

    serve_parser = subparsers.add_parser("serve", help="Run the HTTP API")
    serve_parser.set_defaults(handler=_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
