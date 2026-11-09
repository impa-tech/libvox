"""Milestone 1: the Discovery Index schema.

Needs a live PostgreSQL:

    docker compose up -d postgres
    pytest

Tests skip rather than fail when no database is reachable, so a checkout
without Docker still runs the rest of the suite.
"""

from __future__ import annotations

import os

import psycopg
import pytest

# Settings is a slots dataclass, so its defaults are not readable as class
# attributes — take them from an instance.
from conftest import as_test_database

from libvox_sync.config import Settings
from libvox_sync.db import apply_migrations, tenant_scope

# The test database, never the development one: everything below truncates, and
# rebuilding a real library's index costs half an hour against its own server.
_DEFAULTS = Settings()
ADMIN_DSN = as_test_database(os.environ.get("LIBVOX_DB_ADMIN_DSN", _DEFAULTS.admin_dsn))
APP_DSN = as_test_database(os.environ.get("LIBVOX_DB_DSN", _DEFAULTS.dsn))

TENANT_A = "lib-furg"
TENANT_B = "lib-ibict"


def _reachable(dsn: str) -> bool:
    try:
        with psycopg.connect(dsn, connect_timeout=3):
            return True
    except psycopg.Error:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(ADMIN_DSN),
    reason="no PostgreSQL at LIBVOX_DB_ADMIN_DSN; run `docker compose up -d postgres`",
)


@pytest.fixture(scope="session")
def migrated() -> None:
    with psycopg.connect(ADMIN_DSN) as conn:
        apply_migrations(conn)


@pytest.fixture
def app_conn(migrated) -> psycopg.Connection:
    """An unprivileged connection — the one row-level security applies to."""
    with psycopg.connect(APP_DSN) as conn:
        yield conn


@pytest.fixture(autouse=True)
def clean(migrated) -> None:
    """Wipe tenant data between tests, as admin (which bypasses RLS)."""
    with psycopg.connect(ADMIN_DSN) as conn, conn.cursor() as cur:
        cur.execute(
                "TRUNCATE discovery_biblio, discovery_tenant, discovery_vocabulary"
            )
        conn.commit()


def _insert(conn, tenant, biblio_id, title, author=None, generation=1, publisher=None):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO discovery_biblio "
            "(tenant_id, generation, biblio_id, title, author, publisher) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (tenant, generation, biblio_id, title, author, publisher),
        )


def _activate(conn, tenant, generation=1, status="ready"):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO discovery_tenant (tenant_id, active_generation, index_status) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (tenant_id) DO UPDATE "
            "SET active_generation = EXCLUDED.active_generation, "
            "    index_status = EXCLUDED.index_status",
            (tenant, generation, status),
        )


# --- migrations -------------------------------------------------------------


def test_migrations_are_idempotent(migrated) -> None:
    with psycopg.connect(ADMIN_DSN) as conn:
        assert apply_migrations(conn) == []


def test_editing_an_applied_migration_is_refused(migrated, tmp_path) -> None:
    """Guards against a schema change that silently never runs anywhere."""
    from libvox_sync.db import MigrationError

    (tmp_path / "001_discovery_index.sql").write_text("SELECT 1;")
    with (
        psycopg.connect(ADMIN_DSN) as conn,
        pytest.raises(MigrationError, match="changed after being applied"),
    ):
        apply_migrations(conn, tmp_path)


# --- full-text search -------------------------------------------------------


def test_portuguese_stemming(app_conn) -> None:
    """The reason for choosing a Portuguese text search configuration."""
    with tenant_scope(app_conn, TENANT_A) as conn:
        _insert(conn, TENANT_A, 1, "O romance brasileiro", "Machado de Assis")
        _activate(conn, TENANT_A)

        with conn.cursor() as cur:
            # Plural query, singular in the text.
            cur.execute(
                "SELECT title FROM discovery_active "
                "WHERE search @@ plainto_tsquery('portuguese', %s)",
                ("romances",),
            )
            assert cur.fetchall() == [("O romance brasileiro",)]


def test_title_outranks_author(app_conn) -> None:
    """Weights are why relevance is not flat."""
    with tenant_scope(app_conn, TENANT_A) as conn:
        _insert(conn, TENANT_A, 1, "Estudo sobre Machado", author="Silva")
        _insert(conn, TENANT_A, 2, "Contos", author="Machado de Assis")
        _activate(conn, TENANT_A)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT biblio_id FROM discovery_active "
                "WHERE search @@ plainto_tsquery('portuguese', %s) "
                "ORDER BY ts_rank(search, plainto_tsquery('portuguese', %s)) DESC",
                ("Machado", "Machado"),
            )
            assert [row[0] for row in cur.fetchall()] == [1, 2]


# --- generations ------------------------------------------------------------


def test_only_the_active_generation_is_visible(app_conn) -> None:
    with tenant_scope(app_conn, TENANT_A) as conn:
        _insert(conn, TENANT_A, 1, "Edição antiga", generation=1)
        _insert(conn, TENANT_A, 1, "Edição nova", generation=2)
        _activate(conn, TENANT_A, generation=1)

        with conn.cursor() as cur:
            cur.execute("SELECT title FROM discovery_active")
            assert cur.fetchall() == [("Edição antiga",)]

            # The swap is a single-row update, so search never sees a mix.
            cur.execute(
                "UPDATE discovery_tenant SET active_generation = 2 WHERE tenant_id = %s",
                (TENANT_A,),
            )
            cur.execute("SELECT title FROM discovery_active")
            assert cur.fetchall() == [("Edição nova",)]


def test_ready_without_a_generation_is_rejected(app_conn) -> None:
    """The database refuses the state that produces a confidently wrong answer.

    'ready' with nothing to serve makes an empty search look like a genuine
    "no matches" — exactly what index_status exists to prevent.
    """
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        tenant_scope(app_conn,
        TENANT_A) as conn,
        conn.cursor() as cur,
    ):
            cur.execute(
                "INSERT INTO discovery_tenant "
                "(tenant_id, active_generation, index_status) VALUES (%s, NULL, 'ready')",
                (TENANT_A,),
            )


def test_unknown_status_is_rejected(app_conn) -> None:
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        tenant_scope(app_conn,
        TENANT_A) as conn,
        conn.cursor() as cur,
    ):
            cur.execute(
                "INSERT INTO discovery_tenant (tenant_id, index_status) "
                "VALUES (%s, 'indexing')",
                (TENANT_A,),
            )


# --- tenant isolation (rule 4) ----------------------------------------------


def test_a_tenant_cannot_see_another_tenants_records(app_conn) -> None:
    with tenant_scope(app_conn, TENANT_A) as conn:
        _insert(conn, TENANT_A, 1, "Acervo da FURG")
        _activate(conn, TENANT_A)
    with tenant_scope(app_conn, TENANT_B) as conn:
        _insert(conn, TENANT_B, 1, "Acervo do IBICT")
        _activate(conn, TENANT_B)

    with tenant_scope(app_conn, TENANT_A) as conn, conn.cursor() as cur:
        cur.execute("SELECT title FROM discovery_active")
        assert cur.fetchall() == [("Acervo da FURG",)]
        # Even asking for the other tenant explicitly returns nothing.
        cur.execute(
            "SELECT title FROM discovery_biblio WHERE tenant_id = %s", (TENANT_B,)
        )
        assert cur.fetchall() == []


def test_without_a_tenant_scope_nothing_is_visible(app_conn) -> None:
    """Fail closed: a forgotten scope reads empty, never cross-tenant."""
    with tenant_scope(app_conn, TENANT_A) as conn:
        _insert(conn, TENANT_A, 1, "Acervo da FURG")
        _activate(conn, TENANT_A)

    with app_conn.cursor() as cur:  # no tenant_scope
        cur.execute("SELECT count(*) FROM discovery_biblio")
        assert cur.fetchone()[0] == 0


def test_cannot_write_outside_the_current_scope(app_conn) -> None:
    """WITH CHECK stops a scoped session from planting rows for another tenant."""
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        tenant_scope(app_conn,
        TENANT_A) as conn,
    ):
            _insert(conn, TENANT_B, 99, "Registro contrabandeado")


def test_application_role_cannot_bypass_rls(app_conn) -> None:
    """The premise the isolation tests rest on.

    A superuser ignores row-level security entirely. If the application ever
    connects as one, every test above passes while enforcing nothing.
    """
    with app_conn.cursor() as cur:
        cur.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        is_superuser, can_bypass = cur.fetchone()
    assert not is_superuser, "application role is a superuser; RLS is not in force"
    assert not can_bypass, "application role has BYPASSRLS; RLS is not in force"


# --- contract with libvox-mcp -----------------------------------------------


def test_columns_match_the_mcp_record_model(app_conn) -> None:
    """Catch schema drift between what Sync writes and what MCP returns.

    Skips when libvox-mcp is not installed, so neither module hard-depends on
    the other; it runs in a dev environment that has both.
    """
    models = pytest.importorskip(
        "libvox_mcp.models", reason="libvox-mcp not installed in this environment"
    )

    with app_conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'discovery_active'"
        )
        columns = {row[0] for row in cur.fetchall()}

    expected = set(models.CatalogRecord.model_fields)
    missing = expected - columns
    assert not missing, f"Discovery Index is missing CatalogRecord fields: {missing}"
