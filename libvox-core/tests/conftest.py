"""Shared wiring.

Three databases, every one of them the `_test` copy. `/libvox-sync` learned the
expensive way what a suite truncating a development database costs: the Tenant
DB loses credentials that are ciphertext with no read path, and the Discovery
Index loses an import that takes half an hour against a real library's server.
The conversation log is the third, and the one where a mistake would delete
what people actually said.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest

from libvox_core.config import DEFAULT_CONVERSATION_DSN, Settings

TENANT_A = "lib-furg"
TENANT_B = "lib-ibict"

SERVICE_TOKEN = "test-service-token"
CRYPTO_KEY = "test-key-not-for-production"


def as_test_database(dsn: str, *, suffix: str = "_test") -> str:
    parsed = urlparse(dsn)
    name = parsed.path.lstrip("/")
    if name.endswith(suffix):
        return dsn
    return urlunparse(parsed._replace(path=f"/{name}{suffix}"))


def _sync_defaults():
    from libvox_sync.config import Settings as SyncSettings

    return SyncSettings()


_D = _sync_defaults()

DISCOVERY_DSN = as_test_database(os.environ.get("LIBVOX_DB_DSN", _D.dsn))
DISCOVERY_ADMIN = as_test_database(
    os.environ.get("LIBVOX_DB_ADMIN_DSN", _D.admin_dsn)
)
TENANT_DSN = as_test_database(os.environ.get("LIBVOX_TENANT_DB_DSN", _D.tenant_dsn))

# The conversation log, which Core owns. Its own database on the Tenant DB's
# instance -- one writer per database, not one database per server.
CONVERSATION_DSN = as_test_database(
    os.environ.get("LIBVOX_CONVERSATION_DB_DSN", DEFAULT_CONVERSATION_DSN)
)
CONVERSATION_ADMIN = CONVERSATION_DSN.replace("libvox_app:libvox_app", "libvox:libvox")
TENANT_ADMIN = as_test_database(
    os.environ.get("LIBVOX_TENANT_DB_ADMIN_DSN", _D.tenant_admin_dsn)
)


def _reachable(dsn: str) -> bool:
    try:
        with psycopg.connect(dsn, connect_timeout=3):
            return True
    except psycopg.Error:
        return False


# Probed once, at import, and held: three connection attempts per test would be
# slower than the tests.
#
# CONVERSATION_ADMIN joined the other two because `migrated` and `clean` both
# write to it. Without it, a machine holding the discovery and tenant databases
# but not the conversation log reported itself ready and then errored inside the
# fixture -- which is the case this constant exists to make honest.
DATABASES_UP = (
    _reachable(DISCOVERY_ADMIN)
    and _reachable(TENANT_ADMIN)
    and _reachable(CONVERSATION_ADMIN)
)

databases_up = pytest.mark.skipif(
    not DATABASES_UP,
    reason=(
        "needs the test databases:\n"
        "  cd ../libvox-db     && ./db.sh\n"
        "  cd ../libvox-portal && ./vendor/bin/pest   # migrates libvox_tenant_test"
    ),
)


@pytest.fixture(scope="session")
def migrated() -> None:
    """The two schemas Core is responsible for.

    The Discovery Index is /libvox-sync's schema but Core's tests need it; the
    conversation log is Core's own. The Tenant DB is /libvox-portal's to
    migrate and is not touched here.

    Does nothing when there is no server to migrate. Guarded here as well as in
    `clean`, and that is not belt and braces: `clean` takes this as a parameter,
    so pytest resolves it *first* and a guard on `clean` alone would never be
    reached. The connection error would still be raised, cached at session
    scope, and re-raised as a setup error for every test in the package.
    """
    if not DATABASES_UP:
        return

    from pathlib import Path

    from libvox_sync.db import apply_migrations

    with psycopg.connect(DISCOVERY_ADMIN) as conn:
        apply_migrations(conn)
    with psycopg.connect(CONVERSATION_ADMIN) as conn:
        apply_migrations(conn, Path(__file__).resolve().parents[1] / "migrations")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        discovery_dsn=DISCOVERY_DSN,
        tenant_dsn=TENANT_DSN,
        crypto_key=CRYPTO_KEY,
        service_token=SERVICE_TOKEN,
        environment="development",
        conversation_dsn=CONVERSATION_DSN,
    )


@pytest.fixture
def client(settings: Settings, migrated: None):
    from fastapi.testclient import TestClient

    from libvox_core.app import create_app

    with TestClient(create_app(settings)) as test_client:
        test_client.headers["x-libvox-service-token"] = SERVICE_TOKEN
        yield test_client


@pytest.fixture(autouse=True)
def clean(migrated: None):
    """Empty both, as the owner. TRUNCATE ... CASCADE on the registry empties
    every table that references it, users included -- learned by wiping one.

    **This is a correctness fixture, not a convenience.** `register_library`
    inserts into `channel_bindings` with no conflict clause, and
    `test_endpoints.py` asserts that one library cannot read another's records.
    Both depend on starting empty. It is autouse because a test that forgets to
    clean does not fail -- it leaks into whichever test runs next.

    Which is exactly why the guard below is on a server being *absent* rather
    than on anything a test can choose. With no databases there is nothing to
    truncate and nothing to leak; the tests that genuinely need one carry
    `databases_up` and skip, and the ~113 that need none can finally run. With
    the databases up, `DATABASES_UP` is true and this behaves as it always has.
    """
    if not DATABASES_UP:
        return

    with psycopg.connect(DISCOVERY_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE discovery_biblio, discovery_tenant, discovery_vocabulary"
            )
        conn.commit()
    with psycopg.connect(TENANT_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE tenants CASCADE")
        conn.commit()
    with psycopg.connect(CONVERSATION_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE conversations CASCADE")
        conn.commit()


def register_library(
    tenant_id: str = TENANT_A,
    *,
    status: str = "active",
    origins: list[str] | None = None,
    binding_key: str | None = None,
    enabled: bool = True,
) -> str:
    """Put a library in the registry with a web channel binding. Returns the key."""
    import json

    key = binding_key or f"wk_{tenant_id.replace('-', '_')}_key"
    allowed = origins if origins is not None else ["http://localhost:8081"]

    with psycopg.connect(TENANT_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tenants (tenant_id, display_name, status) "
                "VALUES (%s, %s, %s) ON CONFLICT (tenant_id) DO UPDATE "
                "SET status = EXCLUDED.status",
                (tenant_id, tenant_id, status),
            )
            cur.execute(
                "INSERT INTO tenant_widget (tenant_id, assistant_name) "
                "VALUES (%s, 'MARC.IA') ON CONFLICT (tenant_id) DO NOTHING",
                (tenant_id,),
            )
            cur.execute(
                "INSERT INTO channel_bindings "
                "  (tenant_id, channel, binding_key, enabled, allowed_origins) "
                "VALUES (%s, 'web', %s, %s, %s::jsonb)",
                (tenant_id, key, enabled, json.dumps(allowed)),
            )
        conn.commit()
    return key


def store_credentials(tenant_id: str = TENANT_A, *, base_url: str = "http://koha.test") -> None:
    """Give a library an ILS, so availability has something to fail against."""
    with psycopg.connect(TENANT_ADMIN) as conn:
        with conn.cursor() as cur:
            cur.execute("SET ROLE libvox")
            cur.execute(
                "INSERT INTO tenant_ils "
                "  (tenant_id, ils_type, base_url, client_id, client_secret_enc) "
                "VALUES (%s, 'koha', %s, 'id', "
                "        pgp_sym_encrypt('secret', %s, 'compress-algo=0, cipher-algo=aes256')) "
                "ON CONFLICT (tenant_id) DO UPDATE SET base_url = EXCLUDED.base_url",
                (tenant_id, base_url, CRYPTO_KEY),
            )
        conn.commit()


def index_records(tenant_id: str, rows: list[tuple], *, status: str = "ready") -> None:
    """Put records in the Discovery Index without running an import."""
    from libvox_sync.db import tenant_scope

    with psycopg.connect(DISCOVERY_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 15000")
        conn.commit()
        with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
            cur.execute(
                "INSERT INTO discovery_tenant "
                "  (tenant_id, active_generation, index_status) VALUES (%s, 1, %s) "
                "ON CONFLICT (tenant_id) DO UPDATE SET active_generation = 1, "
                "  index_status = EXCLUDED.index_status",
                (tenant_id, status),
            )
            cur.executemany(
                "INSERT INTO discovery_biblio "
                "  (tenant_id, generation, biblio_id, title, author, "
                "   copyright_date) "
                "VALUES (%s, 1, %s, %s, %s, %s)",
                # A row may be (id, title, author) or carry a year as a fourth.
                [(tenant_id, *(tuple(row) + (None,) * (4 - len(row)))) for row in rows],
            )

        # The word list a typo is repaired against, through the real builder --
        # a hand-written one would be a list of words the *test* catalogued.
        from libvox_sync.pipeline import _build_vocabulary

        _build_vocabulary(conn, tenant_id, 1)
