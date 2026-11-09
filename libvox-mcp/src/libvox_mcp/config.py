"""Runtime configuration, read from the environment.

The MCP layer is a protocol adapter: it holds no tenant data and no ILS
credentials. The only thing it needs to know is how to reach `/libvox-core`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# 8091, not 8080: that one is Koha Testing Docker's OPAC, and a developer
# working on this project has one running. /libvox-core picked 8091 for the
# same reason /libvox-portal picked 8090 and the databases 5433 and 5434.
DEFAULT_CORE_URL = "http://127.0.0.1:8091"
DEFAULT_ISSUER_URL = "http://127.0.0.1:8091"
DEFAULT_RESOURCE_URL = "http://127.0.0.1:8000/mcp"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the MCP server needs to start."""

    core_url: str = DEFAULT_CORE_URL
    """Base URL of /libvox-core, which implements the tools."""

    core_timeout: float = 60.0
    """How long to wait on /libvox-core.

    Sixty because one `check_availability` is four sequential requests to a
    library's own server, and the Koha driver allows thirty seconds for each.
    Ten was shorter than the work it waits on, which is the same mistake the
    Core made in the other direction -- see its `mcp_timeout_s`. Each timeout
    must exceed the one it waits on: driver 30s < this 60s < Core's 90s."""
    """Per-request timeout, in seconds, for calls to Core."""

    service_token: str | None = None
    """Shared secret identifying this MCP instance to Core.

    This authenticates the *server*, not the caller. It never identifies a
    tenant — that comes from the caller's own credential on every request.
    """

    issuer_url: str = DEFAULT_ISSUER_URL
    """OAuth issuer that mints session credentials (Core's auth endpoint)."""

    resource_server_url: str = DEFAULT_RESOURCE_URL
    """Public URL of this MCP endpoint, as advertised to clients."""

    required_scopes: tuple[str, ...] = ("catalog:read",)
    """Scopes a credential must carry to reach any tool here."""

    backend: str = "core"
    """Which implementation answers the tools.

    `core` is the architecture: Core resolves the tenant and routes to the
    Discovery Index or a driver. `koha` is a temporary experiment that talks
    to one Koha directly, with no tenant and no authorization — see
    direct_koha.py. Anything other than these two is refused at startup rather
    than silently falling back, because falling back to `core` would look like
    it worked.
    """

    koha_url: str | None = None
    koha_client_id: str | None = None
    koha_client_secret: str | None = None
    """Connection for `backend='koha'` only. Unused, and expected to be unset,
    on the production path."""

    @classmethod
    def from_env(cls) -> Settings:
        scopes = _env("LIBVOX_REQUIRED_SCOPES", "catalog:read")
        backend = _env("LIBVOX_MCP_BACKEND", "core").lower()
        if backend not in ("core", "koha"):
            raise ValueError(
                f"LIBVOX_MCP_BACKEND must be 'core' or 'koha', got {backend!r}"
            )
        return cls(
            core_url=_env("LIBVOX_CORE_URL", DEFAULT_CORE_URL).rstrip("/"),
            core_timeout=_env_float("LIBVOX_CORE_TIMEOUT", 60.0),
            service_token=os.environ.get("LIBVOX_MCP_SERVICE_TOKEN") or None,
            issuer_url=_env("LIBVOX_ISSUER_URL", DEFAULT_ISSUER_URL),
            resource_server_url=_env("LIBVOX_RESOURCE_URL", DEFAULT_RESOURCE_URL),
            required_scopes=tuple(s for s in scopes.replace(",", " ").split() if s),
            backend=backend,
            koha_url=os.environ.get("KOHA_URL") or None,
            koha_client_id=os.environ.get("KOHA_CLIENT_ID") or None,
            koha_client_secret=os.environ.get("KOHA_CLIENT_SECRET") or None,
        )

    def require_koha(self) -> dict[str, str]:
        """Connection details for the direct-Koha experiment, or refuse.

        Fails at startup rather than on the first tool call: an MCP server that
        starts and then errors on every call looks like a broken integration
        to the host, and the actual cause never reaches anyone.
        """
        missing = [
            name
            for name, value in (
                ("KOHA_URL", self.koha_url),
                ("KOHA_CLIENT_ID", self.koha_client_id),
                ("KOHA_CLIENT_SECRET", self.koha_client_secret),
            )
            if not value
        ]
        if missing:
            raise SystemExit(
                "LIBVOX_MCP_BACKEND=koha needs: "
                + ", ".join(missing)
                + "\nSet them in the environment — never on the command line."
            )
        return {
            "base_url": str(self.koha_url),
            "client_id": str(self.koha_client_id),
            "client_secret": str(self.koha_client_secret),
        }
