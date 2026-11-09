"""Entry point: `python -m libvox_mcp`.

Defaults to stdio so the server can be launched as a subprocess by any MCP
host. Set LIBVOX_MCP_TRANSPORT=streamable-http to listen on a port instead —
which is what a deployment does, and the only mode where authorization exists
at all (it rides HTTP headers).
"""

from __future__ import annotations

import os

from .config import Settings
from .logs import configure_logging
from .server import create_server


def main() -> None:
    configure_logging()

    transport = os.environ.get("LIBVOX_MCP_TRANSPORT", "stdio").strip()
    mcp = create_server(Settings.from_env())

    if transport == "stdio":
        mcp.run()
        return

    mcp.run(
        transport=transport,
        host=os.environ.get("LIBVOX_MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("LIBVOX_MCP_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
