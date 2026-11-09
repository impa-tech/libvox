"""Reading the library registry out of the Tenant DB.

That database is /libvox-portal's. This file lives here because it is the
consumer side of the contract, and the point being asserted is one this module
cares about and the portal does not: that an unprivileged role can answer
"which libraries should I sync?" without a connection that bypasses row-level
security.
"""

from __future__ import annotations

import os

import psycopg
import pytest
from conftest import as_test_database

from libvox_sync.config import Settings
from libvox_sync.registry import describe, syncable_tenants

# The test database, never the development one: the fixture below truncates,
# and the development registry holds credentials that cannot be recovered.
_DEFAULTS = Settings()
ADMIN_DSN = as_test_database(
    os.environ.get("LIBVOX_TENANT_DB_ADMIN_DSN", _DEFAULTS.tenant_admin_dsn)
)
APP_DSN = as_test_database(
    os.environ.get("LIBVOX_TENANT_DB_DSN", _DEFAULTS.tenant_dsn)
)

TENANT_A = "lib-furg"
TENANT_B = "lib-ibict"
TENANT_C = "lib-suspensa"


def _has_registry(dsn: str) -> bool:
    """Whether the Tenant DB is up *and* migrated far enough to have a registry."""
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.tenants') IS NOT NULL")
            return bool(cur.fetchone()[0])
    except psycopg.Error:
        return False


pytestmark = pytest.mark.skipif(
    not _has_registry(ADMIN_DSN),
    reason=(
        "no Tenant DB at LIBVOX_TENANT_DB_ADMIN_DSN. It belongs to "
        "/libvox-portal, which owns its schema:\n"
        "  cd ../libvox-portal && docker compose up -d postgres && composer migrate"
    ),
)


@pytest.fixture(autouse=True)
def clean():
    """Seed a registry, as the owner. Truncating cascades into tenant_ils."""
    with psycopg.connect(ADMIN_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE tenants CASCADE")
            cur.executemany(
                "INSERT INTO tenants (tenant_id, display_name, status, ils_configured_at) "
                "VALUES (%s, %s, %s, %s)",
                [
                    (TENANT_A, "FURG", "active", "2026-08-01"),
                    (TENANT_B, "IBICT", "provisioning", "2026-08-02"),
                    (TENANT_C, "Suspensa", "suspended", "2026-08-03"),
                    ("lib-sem-ils", "Sem catálogo", "active", None),
                ],
            )
        conn.commit()
    yield
    with psycopg.connect(ADMIN_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE tenants CASCADE")
        conn.commit()


@pytest.fixture
def app_conn():
    with psycopg.connect(APP_DSN) as conn:
        yield conn


def test_the_registry_lists_libraries_that_configured_an_ils(app_conn) -> None:
    listed = {t.tenant_id for t in syncable_tenants(app_conn)}
    assert TENANT_A in listed
    assert "lib-sem-ils" not in listed


def test_a_provisioning_library_is_synced(app_conn) -> None:
    """It has to be indexed before it can answer anyone.

    Waiting for an operator to mark it `active` would mean the first thing a
    reader meets is an empty catalog — which is the state `building` exists to
    describe, not one to arrange on purpose.
    """
    assert TENANT_B in {t.tenant_id for t in syncable_tenants(app_conn)}


def test_a_suspended_library_is_skipped(app_conn) -> None:
    """That one is a decision, not a stage."""
    assert TENANT_C not in {t.tenant_id for t in syncable_tenants(app_conn)}


def test_the_registry_does_not_need_a_privileged_connection(app_conn) -> None:
    """The point of reading `tenants` instead of `tenant_ils`.

    Listing across libraries used to require a role that bypasses row-level
    security, because the credentials table carries a policy. `tenants` has
    none — it is the table that defines which tenants exist, and a policy there
    would be circular — so the unprivileged role answers the question and the
    superuser leaves the normal operating path.
    """
    with app_conn.cursor() as cur:
        cur.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        superuser, bypasses = cur.fetchone()
    assert not superuser and not bypasses
    assert syncable_tenants(app_conn)


def test_libraries_come_back_in_a_stable_order(app_conn) -> None:
    """`full --all` reports per library; an unordered list makes runs unreadable."""
    listed = [t.tenant_id for t in syncable_tenants(app_conn)]
    assert listed == sorted(listed)


def test_describe_needs_a_scope_and_finds_nothing_without_credentials(app_conn) -> None:
    """An unconfigured library is None, not an error."""
    assert describe(app_conn, TENANT_A) is None
