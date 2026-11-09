"""Print one library's ILS credentials as shell exports, for `eval`.

Called by `libvox-agent.sh`. A separate file rather than a heredoc inside that
script, because a Python heredoc nested in a shell heredoc terminates the wrong
one -- which it did, silently, on the first attempt.

The underscore is the convention: this is not something to run by hand. It
writes a client secret to stdout, and it exists so that secret can travel from
the database into one shell's environment without passing through a file, a
command line, or shell history.

Reads through /libvox-sync because that module already owns the decryption
path. /libvox-mcp deliberately has neither a database connection nor the
crypto key: "decrypted ILS credentials" is on its list of things it must never
grow, since in the finished architecture /libvox-core injects them into driver
calls and the MCP layer never sees one.
"""

from __future__ import annotations

import logging
import shlex
import sys

logging.basicConfig(level="ERROR")

from libvox_sync.config import Settings  # noqa: E402
from libvox_sync.credentials import CredentialError, fetch  # noqa: E402
from libvox_sync.db import connect_tenant, tenant_scope  # noqa: E402


def die(message: str) -> None:
    """Fail on stderr, leaving stdout empty so nothing gets evaluated."""
    print(message, file=sys.stderr)
    raise SystemExit(1)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        die(f"usage: {argv[0]} <tenant-id>")

    tenant = argv[1]
    settings = Settings.from_env()

    try:
        with connect_tenant(settings) as conn, tenant_scope(conn, tenant) as scoped:
            credentials = fetch(
                scoped, tenant, crypto_key=settings.require_crypto_key()
            )
    except CredentialError as exc:
        die(str(exc))

    if credentials is None:
        die(
            f"no ILS configured for {tenant!r}. Register the library and store "
            "its catalog credentials at http://localhost:8090/admin/tenants"
        )

    # shlex.quote rather than plain interpolation: a secret containing a quote
    # or a $ would otherwise break the eval, or run something.
    print(f"export KOHA_URL={shlex.quote(credentials.base_url)}")
    print(f"export KOHA_CLIENT_ID={shlex.quote(credentials.client_id)}")
    print(f"export KOHA_CLIENT_SECRET={shlex.quote(credentials.client_secret)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
