"""Resolving a channel binding to a library, and reading that library's config.

Read-only on the Tenant DB. `/libvox-portal` is that database's one writer, and
a second author would make the registry something two components disagree
about.

Nothing here invents a lookup. `resolve_channel_binding` is a SECURITY DEFINER
function the portal already created, owned by a role that cannot log in, and it
answers the one genuinely cross-tenant question in the system: *which library
owns this key, and may this page embed it?* That question is asked before any
tenant is known, so it cannot run inside a tenant scope.
"""

from __future__ import annotations

import logging

import psycopg

logger = logging.getLogger(__name__)


def resolve_channel(
    conn: psycopg.Connection,
    channel: str,
    binding_key: str,
    origin: str | None = None,
) -> str | None:
    """Which library owns this binding, or None.

    **Every refusal is the same answer.** An unknown key, a channel switched
    off, a suspended library and a page not on the allowlist all come back as
    None, because telling them apart would let a caller learn which of those a
    given key is. That collapsing happens inside the function, in the database,
    not here — so it cannot be undone by an edit to this file.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT resolve_channel_binding(%s, %s, %s)",
            (channel, binding_key, origin),
        )
        row = cur.fetchone()
    return row[0] if row and row[0] else None


def widget_config(conn: psycopg.Connection, tenant_id: str) -> dict[str, object]:
    """The library's widget settings: how MARC.IA introduces herself.

    Inside a tenant scope, because `tenant_widget` carries the same policy as
    everything else. Returns the defaults when a library has no row rather than
    failing — a widget with no configuration is a widget with default
    configuration, not a broken session.

    **`persona_tone` is an enum in the database and stays one here.** There is
    no free-text instruction field to read, and its absence is the enforcement:
    a library changes how MARC.IA sounds, never what she is allowed to claim.
    """
    from libvox_sync.db import tenant_scope

    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        cur.execute(
            "SELECT assistant_name, welcome_message, default_locale, "
            "       persona_tone, theme_color "
            "  FROM tenant_widget WHERE tenant_id = %s",
            (tenant_id,),
        )
        row = cur.fetchone()

    if row is None:
        logger.info("%s has no widget row; serving defaults", tenant_id)
        return {
            "assistant_name": "MARC.IA",
            "welcome_message": None,
            "locale": "pt-BR",
            "persona_tone": "neutral",
            "theme_color": "#4f46e5",
        }

    name, welcome, locale, tone, colour = row
    return {
        "assistant_name": name,
        "welcome_message": welcome,
        "locale": locale,
        "persona_tone": tone,
        "theme_color": colour,
    }
