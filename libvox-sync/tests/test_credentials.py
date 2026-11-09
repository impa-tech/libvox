"""Encrypted storage of per-tenant ILS credentials.

These run against the **Tenant DB**, which is /libvox-portal's -- a different
database from the Discovery Index, on a different port. The file stays here
because it is the Python half of a cross-language contract: the portal writes
this ciphertext and this module reads it, and the test belongs next to the
reader.

Needs that database to be up and migrated, which is done from the portal.
"""

from __future__ import annotations

import os

import psycopg
import pytest
from conftest import as_test_database

from libvox_sync.config import Settings
from libvox_sync.credentials import (
    CredentialError,
    ILSCredentials,
    fetch,
    list_tenants,
    store,
)
from libvox_sync.db import tenant_scope

# The test database, never the development one. The `clean` fixture truncates
# tenant_ils, and a stored client secret is ciphertext with no read path: wiping
# the development copy means asking every library for its API key again.
_DEFAULTS = Settings()
ADMIN_DSN = as_test_database(
    os.environ.get("LIBVOX_TENANT_DB_ADMIN_DSN", _DEFAULTS.tenant_admin_dsn)
)
APP_DSN = as_test_database(
    os.environ.get("LIBVOX_TENANT_DB_DSN", _DEFAULTS.tenant_dsn)
)

KEY = "test-key-not-for-production"
OTHER_KEY = "a-different-key"
TENANT_A = "lib-furg"
TENANT_B = "lib-ibict"

SECRET = "c5274202-da71-462d-bd7e-fadde4fb5696"


def _reachable(dsn: str) -> bool:
    try:
        with psycopg.connect(dsn, connect_timeout=3):
            return True
    except psycopg.Error:
        return False


def _has_tenant_ils(dsn: str) -> bool:
    """Whether the Tenant DB is up *and* migrated.

    Two conditions rather than one, because they fail differently and the
    second is the confusing one: a reachable database without the table
    produces UndefinedTable from deep inside a query, which reads as a bug in
    this module rather than as a module that has not been set up.
    """
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.tenant_ils') IS NOT NULL")
            return bool(cur.fetchone()[0])
    except psycopg.Error:
        return False


pytestmark = pytest.mark.skipif(
    not _has_tenant_ils(ADMIN_DSN),
    reason=(
        "no Tenant DB at LIBVOX_TENANT_DB_ADMIN_DSN. It belongs to "
        "/libvox-portal, which owns its schema:\n"
        "  cd ../libvox-portal && docker compose up -d postgres && composer migrate"
    ),
)


@pytest.fixture(autouse=True)
def clean() -> None:
    """Reset, and seed the registry tenant_ils now references.

    The table gained a foreign key when it moved: the Tenant DB has a tenants
    table, which the Discovery Index never did.
    """
    with psycopg.connect(ADMIN_DSN) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE tenant_ils")
        cur.executemany(
            "INSERT INTO tenants (tenant_id, display_name) VALUES (%s, %s) "
            "ON CONFLICT (tenant_id) DO NOTHING",
            [(TENANT_A, TENANT_A), (TENANT_B, TENANT_B)],
        )
        conn.commit()


@pytest.fixture
def app_conn() -> psycopg.Connection:
    with psycopg.connect(APP_DSN) as conn:
        yield conn


def creds(tenant: str = TENANT_A, secret: str = SECRET) -> ILSCredentials:
    return ILSCredentials(
        tenant_id=tenant,
        ils_type="koha",
        base_url="http://localhost:8081",
        client_id="eaa7f169-1275-4ec9-8230-d55a58b4e88f",
        client_secret=secret,
    )


# --- round trip -------------------------------------------------------------


def test_store_and_fetch(app_conn) -> None:
    with tenant_scope(app_conn, TENANT_A) as conn:
        store(conn, creds(), crypto_key=KEY)
    with tenant_scope(app_conn, TENANT_A) as conn:
        got = fetch(conn, TENANT_A, crypto_key=KEY)

    assert got is not None
    assert got.client_secret == SECRET
    assert got.base_url == "http://localhost:8081"
    assert got.ils_type == "koha"


def test_unconfigured_tenant_is_none_not_an_error(app_conn) -> None:
    with tenant_scope(app_conn, TENANT_A) as conn:
        assert fetch(conn, TENANT_A, crypto_key=KEY) is None


def test_storing_twice_replaces(app_conn) -> None:
    with tenant_scope(app_conn, TENANT_A) as conn:
        store(conn, creds(secret="old"), crypto_key=KEY)
        store(conn, creds(secret="new"), crypto_key=KEY)
        assert fetch(conn, TENANT_A, crypto_key=KEY).client_secret == "new"


# --- what the encryption actually buys --------------------------------------


def test_the_secret_is_not_stored_in_plaintext(app_conn) -> None:
    """The point of the exercise: a table dump must not reveal the secret."""
    with tenant_scope(app_conn, TENANT_A) as conn:
        store(conn, creds(), crypto_key=KEY)
        with conn.cursor() as cur:
            cur.execute("SELECT client_secret_enc FROM tenant_ils")
            (ciphertext,) = cur.fetchone()

    raw = bytes(ciphertext)
    assert SECRET.encode() not in raw
    assert raw[:1] == b"\xc3", "expected an OpenPGP message, not something else"


def test_the_wrong_key_cannot_read_it(app_conn) -> None:
    with tenant_scope(app_conn, TENANT_A) as conn:
        store(conn, creds(), crypto_key=KEY)

    with (
        tenant_scope(app_conn, TENANT_A) as conn,
        pytest.raises(CredentialError, match="wrong LIBVOX_CRYPTO_KEY"),
    ):
        fetch(conn, TENANT_A, crypto_key=OTHER_KEY)


def test_the_secret_never_appears_in_a_repr(app_conn) -> None:
    """Credentials reach tracebacks and log lines by accident; repr is the
    one place that can be fixed for all of them at once."""
    text = repr(creds())
    assert SECRET not in text
    assert "<redacted>" in text


# --- isolation --------------------------------------------------------------


def test_a_tenant_cannot_read_another_tenants_credentials(app_conn) -> None:
    with tenant_scope(app_conn, TENANT_A) as conn:
        store(conn, creds(TENANT_A), crypto_key=KEY)

    with tenant_scope(app_conn, TENANT_B) as conn:
        assert fetch(conn, TENANT_A, crypto_key=KEY) is None


def test_cannot_store_credentials_for_another_tenant(app_conn) -> None:
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        tenant_scope(app_conn, TENANT_A) as conn,
    ):
        store(conn, creds(TENANT_B), crypto_key=KEY)


# --- operator view ----------------------------------------------------------


def test_the_inventory_view_cannot_leak_a_secret(app_conn) -> None:
    """`tenant list` is deliberately incapable of returning ciphertext."""
    with tenant_scope(app_conn, TENANT_A) as conn:
        store(conn, creds(TENANT_A), crypto_key=KEY)
    with tenant_scope(app_conn, TENANT_B) as conn:
        store(conn, creds(TENANT_B), crypto_key=KEY)

    with psycopg.connect(ADMIN_DSN) as admin:
        rows = list_tenants(admin)

    assert [r[0] for r in rows] == [TENANT_A, TENANT_B]
    assert all(len(r) == 3 for r in rows), "inventory rows must not carry a secret"
    assert SECRET not in str(rows)


# --- key handling -----------------------------------------------------------


def test_missing_key_is_refused_with_guidance() -> None:
    settings = Settings(crypto_key=None)
    with pytest.raises(SystemExit, match="LIBVOX_CRYPTO_KEY is not set"):
        settings.require_crypto_key()


def test_a_configured_key_is_returned() -> None:
    assert Settings(crypto_key=KEY).require_crypto_key() == KEY
