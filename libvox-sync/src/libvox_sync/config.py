"""Runtime configuration, read from the environment.

Standalone mode only. When `/libvox-sync` becomes an event worker, the tenant
and ILS credentials arrive from Core instead of from here — see the README's
note on the boundary this must not cross.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_DSN = "postgresql://libvox_app:libvox_app@127.0.0.1:5435/libvox_index"
DEFAULT_ADMIN_DSN = "postgresql://libvox:libvox@127.0.0.1:5435/libvox_index"

# The Tenant DB is a different database, on a different port, owned by
# /libvox-portal. Nothing here joins across the two, so the split costs one
# extra connection and no query rewriting -- see the README.
DEFAULT_TENANT_DSN = "postgresql://libvox_app:libvox_app@127.0.0.1:5435/libvox_tenant"
DEFAULT_TENANT_ADMIN_DSN = "postgresql://libvox:libvox@127.0.0.1:5435/libvox_tenant"


@dataclass(frozen=True, slots=True)
class Settings:
    dsn: str = DEFAULT_DSN
    """Connection for tenant data — an unprivileged role, so row-level
    security actually applies. A superuser here silently disables it."""

    admin_dsn: str = DEFAULT_ADMIN_DSN
    """Connection for Discovery Index migrations, which need to create objects.
    Used by `migrate` and nothing else. It does not reach the Tenant DB: that
    schema belongs to /libvox-portal and is migrated from there."""

    tenant_dsn: str = DEFAULT_TENANT_DSN
    """The Tenant DB -- a *different database*, owned by /libvox-portal, where
    per-tenant ILS credentials live.

    PostgreSQL cannot join across databases, and nothing here needs to:
    credentials are resolved in one query before the index is touched. The
    unprivileged role again, so row-level security applies there too.
    """

    tenant_admin_dsn: str = DEFAULT_TENANT_ADMIN_DSN
    """The Tenant DB as its owner. **Nothing in the application uses this.**

    It was how `tenant list` read across libraries, back when the only
    cross-tenant table was `tenant_ils` and its policy had to be bypassed. The
    registry replaced that: `tenants` carries no policy, so the unprivileged
    role answers the question and this DSN left the operating path.

    It remains because the test suite needs an owner connection to truncate,
    and because migrating the Tenant DB is /libvox-portal's job and it needs
    one too. A command that reaches for it again is almost certainly asking a
    question the registry can answer.
    """

    statement_timeout_ms: int = 30_000
    """Guardrail against a runaway query holding a connection open."""

    crypto_key: str | None = None
    """Key for encrypting stored ILS secrets, from LIBVOX_CRYPTO_KEY.

    Never persisted alongside the ciphertext it protects. Absent unless a
    command actually needs it, so a `migrate` on a machine without the key
    still works.
    """

    def require_crypto_key(self) -> str:
        if not self.crypto_key:
            raise SystemExit(
                "LIBVOX_CRYPTO_KEY is not set.\n"
                "It encrypts stored ILS secrets and must not live in the "
                "database or in the repository. For local development:\n"
                "  export LIBVOX_CRYPTO_KEY=\"$(openssl rand -base64 32)\"\n"
                "Keep it — credentials stored under one key cannot be read "
                "back with another."
            )
        return self.crypto_key

    @classmethod
    def from_env(cls) -> Settings:
        raw_timeout = os.environ.get("LIBVOX_DB_STATEMENT_TIMEOUT_MS", "30000")
        try:
            timeout = int(raw_timeout)
        except ValueError as exc:
            raise ValueError(
                f"LIBVOX_DB_STATEMENT_TIMEOUT_MS must be an integer, got {raw_timeout!r}"
            ) from exc

        dsn = os.environ.get("LIBVOX_DB_DSN", DEFAULT_DSN)
        return cls(
            dsn=dsn,
            admin_dsn=os.environ.get("LIBVOX_DB_ADMIN_DSN", DEFAULT_ADMIN_DSN),
            tenant_dsn=os.environ.get("LIBVOX_TENANT_DB_DSN", DEFAULT_TENANT_DSN),
            tenant_admin_dsn=os.environ.get(
                "LIBVOX_TENANT_DB_ADMIN_DSN", DEFAULT_TENANT_ADMIN_DSN
            ),
            statement_timeout_ms=timeout,
            crypto_key=os.environ.get("LIBVOX_CRYPTO_KEY") or None,
        )
