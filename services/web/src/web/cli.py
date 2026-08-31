import argparse
import sys


def _serve(_args: argparse.Namespace) -> int:
    # Imported lazily so importing web.cli doesn't pay for FastAPI/uvicorn unless `web serve`
    # actually runs -- mirrors auth.cli's same reasoning.
    import uvicorn

    from web.api.app import create_app
    from web.settings import load_settings

    settings = load_settings()
    uvicorn.run(create_app(settings), host=settings.api_host, port=settings.api_port)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="web")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Run the HTTP server")
    serve_parser.set_defaults(handler=_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
