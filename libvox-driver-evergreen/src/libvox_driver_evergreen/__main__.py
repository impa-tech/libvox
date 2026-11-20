"""CLI: `python -m libvox_driver_evergreen`.

A thin wrapper over `handle()`, so what runs here is what runs in production.
Credentials come from the environment, never from arguments:
`/proc/<pid>/cmdline` is readable by any local process.
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

REQUIRED_ENV = ("EVERGREEN_URL", "EVERGREEN_USERNAME", "EVERGREEN_PASSWORD")


def connection_from_env() -> dict[str, str]:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise SystemExit(
            "missing environment variables: " + ", ".join(missing)
            + "\nSet them in the environment, never on the command line."
        )
    return {
        "base_url": os.environ["EVERGREEN_URL"],
        "client_id": os.environ["EVERGREEN_USERNAME"],
        "client_secret": os.environ["EVERGREEN_PASSWORD"],
    }


def _emit(response: dict[str, Any]) -> int:
    print(json.dumps(response, indent=2, ensure_ascii=False))
    return 0 if response.get("ok") else 1


def _run(action: str, params: dict[str, Any]) -> int:
    return _emit(handle({"action": action, "params": params, "connection": connection_from_env()}))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="libvox-driver-evergreen",
        description=(
            "Evergreen integration driver. Connection details come from "
            "EVERGREEN_URL, EVERGREEN_USERNAME and EVERGREEN_PASSWORD."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    get_biblio = sub.add_parser("get-biblio", help="Fetch one record by id.")
    get_biblio.add_argument("--id", type=int, required=True)

    availability = sub.add_parser("availability", help="Live availability of one record.")
    availability.add_argument("--id", type=int, required=True)

    search = sub.add_parser("search-title", help="Find records by title (diagnostic).")
    search.add_argument("--title", required=True)
    search.add_argument(
        "--limit", type=int, default=DEFAULT_SEARCH_LIMIT,
        help=f"Maximum records to return (1-{MAX_SEARCH_LIMIT}).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("LIBVOX_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    args = build_parser().parse_args(argv)
    if args.command == "get-biblio":
        return _run("get_biblio", {"biblio_id": args.id})
    if args.command == "availability":
        return _run("check_availability", {"biblio_id": args.id})
    return _run("search_by_title", {"title": args.title, "limit": args.limit})


if __name__ == "__main__":
    sys.exit(main())
