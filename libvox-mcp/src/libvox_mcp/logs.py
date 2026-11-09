"""Logging setup for the MCP server.

One rule dominates this module: **nothing may ever be written to stdout.**
Under the stdio transport — the default, and what every MCP host uses when it
launches this server as a subprocess — stdout *is* the JSON-RPC channel. A
single stray log line there corrupts the stream, and the host reports it as a
protocol error with no hint of where it came from.

So handlers here write to stderr, to a file, or to both. Never stdout.

**Where the file goes is the server's decision, not the host's.** Under stdio
the environment belongs to whoever launched the process — opencode's
`environment` block, an MCP host's config, a compose file. Configuring the log
destination through that channel would make it a property of the agent, and
several different agents will drive this same server. So the path defaults to
`$XDG_STATE_HOME/libvox/mcp.log` and every agent lands in the same place with
no configuration at all. `LIBVOX_LOG_FILE` still overrides it, as an operator's
exception rather than the mechanism.

Rotation is deliberately *not* done in-process: an external collector tails
this file and owns retention. `WatchedFileHandler` is what makes that safe —
it reopens the file when it notices the inode changed underneath it, which is
exactly what happens when a rotator moves the old one aside. The cost is that
with no collector configured the file grows unbounded; at a few lines per tool
call that is months, not days.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import WatchedFileHandler
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-8s [pid %(process)d] %(name)s: %(message)s"
"""The pid is not decoration. One agent runs at a time, but successive runs by
different agents append to the same file, and without it their lines read as
one continuous session."""


def default_log_path() -> Path:
    """Where logs go when nobody said otherwise.

    XDG puts variable state that should persist between runs — logs among the
    named examples — under `$XDG_STATE_HOME`, defaulting to `~/.local/state`.
    """
    state = os.environ.get("XDG_STATE_HOME", "").strip()
    root = Path(state).expanduser() if state else Path.home() / ".local" / "state"
    return root / "libvox" / "mcp.log"


def resolve_log_path() -> Path:
    override = os.environ.get("LIBVOX_LOG_FILE", "").strip()
    return Path(override).expanduser() if override else default_log_path()


def _file_handler(path: Path) -> WatchedFileHandler:
    path.parent.mkdir(parents=True, exist_ok=True)
    # delay=False on purpose: open the file now, so a bad path fails at
    # startup where someone is watching, rather than on the first warning
    # hours later.
    return WatchedFileHandler(path, encoding="utf-8")


def configure_logging() -> None:
    """Install handlers on the root logger.

    `LIBVOX_LOG_LEVEL` sets the level (default INFO). `LIBVOX_LOG_FILE`
    overrides the default file path.
    """
    level = os.environ.get("LIBVOX_LOG_LEVEL", "").strip().upper() or "INFO"

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]

    path = resolve_log_path()
    writing_to_file = False
    try:
        handlers.append(_file_handler(path))
        writing_to_file = True
    except OSError as exc:
        # Not fatal — logging is not the mission, and refusing to start
        # because a log directory is read-only would be worse. But it is not
        # silent either: believing you have a log file when you do not is how
        # an incident gets investigated with no evidence.
        print(
            f"libvox-mcp: cannot write to {path} ({exc}); logging to stderr only",
            file=sys.stderr,
        )

    logging.basicConfig(level=level, format=FORMAT, handlers=handlers, force=True)

    if writing_to_file:
        # Announce the destination on every start. The path is deliberately not
        # the host's to choose, which means nobody has it written down in their
        # agent config either — so the server says where it is, on stderr where
        # the host shows it and in the file itself.
        logging.getLogger(__name__).info("logging to %s", path)
