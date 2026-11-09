"""CLI: `python -m libvox_driver_koha`.

This is the container's entry point. It is a thin wrapper over `handle()` — the
same function the serverless version will expose — so that what you exercise
here is what runs in production, not a parallel code path that happens to look
similar.

Credentials come from the environment, never from arguments: `/proc/<pid>/cmdline`
is readable by any local process on the host, and shell history keeps them
afterwards.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

from .driver import DEFAULT_SEARCH_LIMIT, MAX_SEARCH_LIMIT
from .handler import handle

logger = logging.getLogger("libvox_driver_koha")

REQUIRED_ENV = ("KOHA_URL", "KOHA_CLIENT_ID", "KOHA_CLIENT_SECRET")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def connection_from_env() -> dict[str, str]:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise SystemExit(
            "missing environment variables: "
            + ", ".join(missing)
            + "\nSet them in the environment or an --env-file; never on the "
            "command line, where /proc/<pid>/cmdline exposes them to any "
            "local process."
        )
    return {
        "base_url": os.environ["KOHA_URL"],
        "client_id": os.environ["KOHA_CLIENT_ID"],
        "client_secret": os.environ["KOHA_CLIENT_SECRET"],
    }


def _emit(response: dict[str, Any]) -> int:
    """Print the response and turn `ok` into an exit status.

    The whole envelope goes to stdout either way, so a failing call is still
    machine-readable — a shell pipeline gets the error code, not just a
    non-zero exit.
    """
    print(json.dumps(response, indent=2, ensure_ascii=False))
    return EXIT_OK if response.get("ok") else EXIT_ERROR


def cmd_get_biblio(args: argparse.Namespace) -> int:
    return _emit(
        handle(
            {
                "action": "get_biblio",
                "params": {"biblio_id": args.id},
                "connection": connection_from_env(),
            }
        )
    )


def cmd_search_title(args: argparse.Namespace) -> int:
    return _emit(
        handle(
            {
                "action": "search_by_title",
                "params": {"title": args.title, "limit": args.limit},
                "connection": connection_from_env(),
            }
        )
    )


def cmd_invoke(args: argparse.Namespace) -> int:
    """Run a raw invocation, exactly as the serverless version will receive it.

    The connection block is injected from the environment rather than read from
    the request, so a secret never has to be typed into a JSON document that
    might be saved, pasted or logged.
    """
    raw = sys.stdin.read() if args.request == "-" else args.request
    try:
        request = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"request is not valid JSON: {exc}") from None
    if not isinstance(request, dict):
        raise SystemExit("request must be a JSON object")

    request.setdefault("connection", connection_from_env())
    return _emit(handle(request))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="libvox-driver-koha",
        description=(
            "Koha integration driver. Connection details come from "
            "KOHA_URL, KOHA_CLIENT_ID and KOHA_CLIENT_SECRET."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    get_biblio = sub.add_parser(
        "get-biblio",
        help="Fetch one bibliographic record by id.",
        description=(
            "The live-path lookup, and the smoke test for a new integration: "
            "it exercises authentication, permissions, content negotiation and "
            "normalization in a single call."
        ),
    )
    get_biblio.add_argument("--id", type=int, required=True, help="Koha biblio_id.")

    search = sub.add_parser(
        "search-title",
        help="Find records whose name contains a term (diagnostic).",
        description=(
            "A probe against the library's own server, for proving an "
            "integration or reconciling the index against its source. This is "
            "NOT how a patron searches: those are answered from the "
            "Discovery Index precisely so they generate no ILS traffic."
        ),
    )
    search.add_argument("--title", required=True, help="Text to look for.")
    search.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_SEARCH_LIMIT,
        help=f"Maximum records to return (1-{MAX_SEARCH_LIMIT}).",
    )

    invoke = sub.add_parser(
        "invoke",
        help="Run a raw JSON invocation (the serverless contract).",
        description=(
            "Reads the same request object the serverless version will "
            "receive. The connection block is filled from the environment if "
            "the request omits it."
        ),
    )
    invoke.add_argument(
        "request",
        nargs="?",
        default="-",
        help="Request JSON, or '-' to read it from stdin (the default).",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("LIBVOX_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stderr,  # stdout stays pure JSON, so it can be piped
    )
    args = build_parser().parse_args(argv)

    handlers = {
        "get-biblio": cmd_get_biblio,
        "search-title": cmd_search_title,
        "invoke": cmd_invoke,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
