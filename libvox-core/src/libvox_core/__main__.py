"""Run the service: `python -m libvox_core`.

Or apply the conversation schema: `python -m libvox_core migrate`.

Or forget old conversations: `python -m libvox_core prune`. That subcommand is
not an afterthought — a conversation log with no expiry is an accumulation, not
a record, and the privacy claim in the root README depends on there being one.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from pathlib import Path

from .config import ConfigurationError, Settings


def _migrate(settings: Settings, argv: list[str]) -> int:
    """Apply `migrations/` to the conversation log.

    This existed only as a pytest fixture until a second migration needed
    applying. `001_conversations.sql` reached production because someone ran it
    by hand; a schema whose only runner is the test suite is a schema that
    drifts, and the drift is silent until a query names a column nobody
    created.

    The runner is /libvox-sync's — the same code, pointed at this module's
    directory, exactly as `tests/conftest.py` has been calling it. Core already
    imports `tenant_scope` from that module, so the dependency is not new.

    The admin DSN, not the one the service writes through: creating a table
    needs an owner, and `libvox_app` is deliberately not one. Same connection
    `prune` uses, and for a related reason — both are operator jobs rather than
    things a request does.
    """
    import psycopg
    from libvox_sync.db import apply_migrations

    parser = argparse.ArgumentParser(
        prog="libvox-core migrate",
        description="Apply pending migrations to the conversation log.",
    )
    parser.parse_args(argv)

    here = Path(__file__).resolve().parents[2] / "migrations"
    if not here.is_dir():
        raise SystemExit(f"no migrations directory at {here}")

    with psycopg.connect(settings.conversation_admin_dsn) as conn:
        applied = apply_migrations(conn, here)

    if applied:
        print(f"applied migrations: {', '.join(str(v) for v in applied)}")
    else:
        print("schema already up to date")
    return 0


def _prune(settings: Settings, argv: list[str]) -> int:
    import psycopg

    from .agent.store import DEFAULT_RETENTION_DAYS, RetentionUnavailable, prune

    parser = argparse.ArgumentParser(
        prog="libvox-core prune",
        description="Forget conversations nobody has added to in a while.",
    )
    parser.add_argument(
        "--older-than",
        type=int,
        default=DEFAULT_RETENTION_DAYS,
        metavar="DAYS",
        help=f"Retention window in days (default: {DEFAULT_RETENTION_DAYS}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count what would go, and delete nothing.",
    )
    args = parser.parse_args(argv)

    if args.older_than < 0:
        raise SystemExit("--older-than cannot be negative.")

    # The admin DSN, not the one the service writes through: see prune's
    # docstring. Getting this wrong is not an error, it is a silent no-op,
    # which is why prune checks the role rather than trusting the caller.
    with psycopg.connect(settings.conversation_admin_dsn) as conn:
        try:
            count = prune(
                conn, older_than_days=args.older_than, dry_run=args.dry_run
            )
        except RetentionUnavailable as exc:
            raise SystemExit(str(exc)) from None

    verb = "would be forgotten" if args.dry_run else "forgotten"
    print(f"{count} conversation(s) older than {args.older_than} days {verb}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("LIBVOX_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-8s [pid %(process)d] %(name)s: %(message)s",
    )

    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        # The refusal to start without a service token outside development
        # arrives here. It is a message, not a traceback: what to do about it
        # is one command, and a stack trace buries it.
        raise SystemExit(str(exc)) from None

    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "migrate":
        return _migrate(settings, argv[1:])
    if argv and argv[0] == "prune":
        return _prune(settings, argv[1:])

    import asyncio

    import uvicorn

    from .app import create_app, create_public_app
    from .sessions import SessionStore

    # One store, two surfaces. A session opened on the public WebSocket is
    # introspected on the internal port -- that is the call /libvox-mcp makes
    # before every tool call -- so two stores would be two ideas of who is
    # connected, and the reader's would be the one nobody could verify.
    sessions = SessionStore(ttl_s=settings.session_ttl_s)

    log = logging.getLogger(__name__)
    log.info(
        "libvox-core internal on %s:%d, public on %s:%d (%s)",
        settings.host,
        settings.port,
        settings.public_host,
        settings.public_port,
        settings.environment,
    )

    servers = [
        uvicorn.Server(
            uvicorn.Config(
                create_app(settings, sessions),
                host=settings.host,
                port=settings.port,
            )
        ),
        uvicorn.Server(
            uvicorn.Config(
                create_public_app(settings, sessions),
                host=settings.public_host,
                port=settings.public_port,
            )
        ),
    ]

    async def serve() -> None:
        # Neither server installs its own handlers: the second would overwrite
        # the first, and on SIGTERM one would keep the gather pending forever
        # -- a container that never stops, which docker resolves by killing it
        # nine seconds later.
        loop = asyncio.get_running_loop()
        for server in servers:
            server.install_signal_handlers = False
        for signal_number in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signal_number, _stop, servers, log)
        await asyncio.gather(*(server.serve() for server in servers))

    asyncio.run(serve())
    return 0


def _stop(servers: list, log: logging.Logger) -> None:
    """Ask both servers to finish, once."""
    log.info("shutting down both surfaces")
    for server in servers:
        server.should_exit = True


if __name__ == "__main__":
    sys.exit(main())
