"""Storing and retrieving per-tenant ILS credentials.

These operate on the **Tenant DB**, whose schema is owned by /libvox-portal --
a different database from the Discovery Index, reached through
`db.connect_tenant`. Every function here takes the connection, so which database
it means is the caller's choice and not an assumption baked in here; that is why
moving the table cost this module no SQL change at all.

In production the portal is the writer and /libvox-core the reader. `store` and
`list_tenants` remain for standalone operation, and both are candidates for
retirement now that a panel does the same job.

The encryption key is passed to PostgreSQL as a **bind parameter**, never via
`SET LOCAL`. A session setting persists for the life of the transaction and is
readable through `current_setting`; a bind parameter is used for one statement
and is gone. Neither is invisible to a server with verbose statement logging,
but the parameter leaves the smaller footprint.

The key itself comes from the environment and is never written to this database
— a key stored next to the ciphertext it protects is decoration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import psycopg

logger = logging.getLogger(__name__)

# AES-256, no compression. These values are short, and compressing a secret
# leaks information about its content through the resulting length.
_PGP_OPTIONS = "compress-algo=0, cipher-algo=aes256"


class CredentialError(RuntimeError):
    """A credential could not be stored or read."""


@dataclass(frozen=True, slots=True)
class ILSCredentials:
    """Everything needed to reach one library's ILS."""

    tenant_id: str
    ils_type: str
    base_url: str
    client_id: str
    client_secret: str

    def __repr__(self) -> str:  # pragma: no cover - defensive
        """Never render the secret.

        Credentials end up in tracebacks, debuggers and log lines by accident;
        the repr is the one place that can be fixed once for all of them.
        """
        return (
            f"ILSCredentials(tenant_id={self.tenant_id!r}, "
            f"ils_type={self.ils_type!r}, base_url={self.base_url!r}, "
            f"client_id={self.client_id!r}, client_secret=<redacted>)"
        )


def store(
    conn: psycopg.Connection,
    credentials: ILSCredentials,
    *,
    crypto_key: str,
) -> None:
    """Insert or replace a tenant's ILS credentials.

    Must be called inside a `tenant_scope` for the same tenant — row-level
    security rejects writing a row for anyone else.

    A development shortcut: /libvox-portal is the production writer, and it
    writes the identical statement so the two cannot drift.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tenant_ils
                   (tenant_id, ils_type, base_url, client_id, client_secret_enc)
            VALUES (%s, %s, %s, %s, pgp_sym_encrypt(%s, %s, %s))
            ON CONFLICT (tenant_id) DO UPDATE
               SET ils_type          = EXCLUDED.ils_type,
                   base_url          = EXCLUDED.base_url,
                   client_id         = EXCLUDED.client_id,
                   client_secret_enc = EXCLUDED.client_secret_enc,
                   updated_at        = now()
            """,
            (
                credentials.tenant_id,
                credentials.ils_type,
                credentials.base_url,
                credentials.client_id,
                credentials.client_secret,
                crypto_key,
                _PGP_OPTIONS,
            ),
        )


def fetch(
    conn: psycopg.Connection,
    tenant_id: str,
    *,
    crypto_key: str,
) -> ILSCredentials | None:
    """Read and decrypt one tenant's credentials, or None if unconfigured.

    Must be called inside a `tenant_scope` for the same tenant.
    """
    with conn.cursor() as cur:
        try:
            cur.execute(
                """
                SELECT ils_type,
                       base_url,
                       client_id,
                       pgp_sym_decrypt(client_secret_enc, %s)
                  FROM tenant_ils
                 WHERE tenant_id = %s
                """,
                (crypto_key, tenant_id),
            )
        except psycopg.errors.ExternalRoutineInvocationException as exc:
            # pgcrypto reports both a wrong key and damaged ciphertext this
            # way, and it cannot tell them apart — say so rather than implying
            # the data is corrupt.
            raise CredentialError(
                f"cannot decrypt credentials for {tenant_id!r}: wrong "
                "LIBVOX_CRYPTO_KEY, or the stored value is damaged"
            ) from exc

        row = cur.fetchone()

    if row is None:
        return None

    ils_type, base_url, client_id, secret = row
    return ILSCredentials(
        tenant_id=tenant_id,
        ils_type=ils_type,
        base_url=base_url,
        client_id=client_id,
        client_secret=bytes(secret).decode() if isinstance(secret, memoryview) else secret,
    )


def list_tenants(conn: psycopg.Connection) -> list[tuple[str, str, str]]:
    """Every configured tenant, without touching a secret.

    Runs outside a tenant scope on purpose: this is the operator's inventory
    view, and it is deliberately incapable of returning ciphertext, let alone
    plaintext. Requires a role that can bypass row-level security, which in the
    Tenant DB means `tenant_admin_dsn`.

    Protected by the SELECT list and by nothing else, which is weaker than it
    looks: the next edit that adds a column defeats it. /libvox-portal answers
    the same question without reading this table at all — it keeps a timestamp
    on the registry, written in the same transaction as the credential.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tenant_id, ils_type, base_url FROM tenant_ils ORDER BY tenant_id"
        )
        return list(cur.fetchall())
