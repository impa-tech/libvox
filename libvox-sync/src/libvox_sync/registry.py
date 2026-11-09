"""Which libraries there are, read from the Tenant DB.

The registry is `/libvox-portal`'s, and this module only reads it. What it buys
is worth stating: **enumerating libraries no longer needs a privileged
connection.** `tenant_ils` carries row-level security, so listing across
libraries meant a role that bypasses it — `tenant_admin_dsn`, a superuser. The
`tenants` table deliberately has no policy, because it is the table that
*defines* which tenants exist and a policy there would be circular. So the
unprivileged role can read it, and the superuser leaves the normal operating
path entirely.

`ils_configured_at` is what says a library has credentials. It is maintained by
the portal in the same transaction as the credential itself, which is why it
cannot disagree with `tenant_ils` — and why answering the question does not
require reading a table that is deliberately unreadable across tenants.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import psycopg

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TenantRef:
    """One library, as far as the sync worker needs to know it."""

    tenant_id: str
    display_name: str
    status: str


def syncable_tenants(conn: psycopg.Connection) -> list[TenantRef]:
    """Libraries that have configured an ILS and are not switched off.

    No tenant scope, and none is possible: this question spans libraries and is
    asked before any one of them is chosen.

    **`provisioning` is included, deliberately.** A library has to be indexed
    before it can answer anyone, so waiting for an operator to mark it `active`
    would mean the first thing a reader meets is an empty catalog. That is
    exactly the state `index_status = 'building'` exists to describe. Only
    `suspended` is skipped — that one is a decision, not a stage.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tenant_id, display_name, status "
            "  FROM tenants "
            " WHERE ils_configured_at IS NOT NULL "
            "   AND status <> 'suspended' "
            " ORDER BY tenant_id"
        )
        return [TenantRef(*row) for row in cur.fetchall()]


def describe(conn: psycopg.Connection, tenant_id: str) -> tuple[str, str] | None:
    """One library's ILS type and base URL. Never its secret.

    Inside a scope, because `tenant_ils` is tenant data like any other. The
    SELECT list is short for a reason, but the SELECT list is not the
    protection — the policy is.
    """
    from .db import tenant_scope

    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        cur.execute(
            "SELECT ils_type, base_url FROM tenant_ils WHERE tenant_id = %s",
            (tenant_id,),
        )
        return cur.fetchone()
