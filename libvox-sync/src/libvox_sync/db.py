"""Database access and migrations for the Discovery Index.

The migration runner here owns the Discovery Index alone. The Tenant DB has its
own schema, its own migrations and its own owner -- /libvox-portal.

Migrations are numbered `.sql` files applied in filename order and recorded in
`schema_migrations`. No ORM and no migration framework on purpose: this schema
is SQL-first — a generated tsvector column, weighted full-text, and row-level
security are all things you would end up hand-writing anyway.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import psycopg

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
MIGRATION_PATTERN = re.compile(r"^(\d+)_.+\.sql$")

SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     integer     PRIMARY KEY,
    name        text        NOT NULL,
    checksum    text        NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    """A migration is missing, out of order, or was edited after being applied."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()[:16]


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Load migrations from disk, ordered by version."""
    if not directory.is_dir():
        raise MigrationError(f"No migrations directory at {directory}")

    found: list[Migration] = []
    for path in sorted(directory.iterdir()):
        match = MIGRATION_PATTERN.match(path.name)
        if not match:
            continue
        found.append(
            Migration(
                version=int(match.group(1)),
                name=path.name,
                sql=path.read_text(encoding="utf-8"),
            )
        )

    versions = [m.version for m in found]
    if len(set(versions)) != len(versions):
        raise MigrationError(f"Duplicate migration versions in {directory}")
    return found


@contextmanager
def connect(dsn: str, *, statement_timeout_ms: int = 30_000) -> Iterator[psycopg.Connection]:
    """Open a connection with sane guardrails.

    The commit after `SET` is not a formality. psycopg opens a transaction on
    the first statement, so without it the connection is handed over already
    inside one — and then `tenant_scope` nests as a savepoint instead of
    opening a transaction of its own. Two consequences, both bad: nothing is
    durable until the connection closes, so a long import that crashes has
    written nothing; and the tenant binding outlives the scope it was supposed
    to die with, because the enclosing transaction is still open.

    A plain `SET` is session-level and survives the commit, which is why the
    guardrail is still in force afterwards. `SET LOCAL` would not be.
    """
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {int(statement_timeout_ms)}")
        conn.commit()
        yield conn


@contextmanager
def connect_discovery(settings) -> Iterator[psycopg.Connection]:
    """The Discovery Index: records, generations, index state."""
    with connect(settings.dsn, statement_timeout_ms=settings.statement_timeout_ms) as conn:
        yield conn


@contextmanager
def connect_tenant(settings) -> Iterator[psycopg.Connection]:
    """The Tenant DB, owned by /libvox-portal: credentials and configuration.

    A different database, so no statement can span both. That is not a
    limitation here -- nothing in this worker ever joins a credential to a
    record. Two thin wrappers rather than a `settings.dsn` attribute at each
    call site, because which database a query means is worth saying out loud.
    """
    with connect(settings.tenant_dsn, statement_timeout_ms=settings.statement_timeout_ms) as conn:
        yield conn


@contextmanager
def tenant_scope(conn: psycopg.Connection, tenant_id: str) -> Iterator[psycopg.Connection]:
    """Bind a transaction to one tenant, so row-level security can enforce it.

    Uses SET LOCAL, so the binding dies with the transaction and cannot leak
    into the next one on a pooled connection. Every read and write of tenant
    data must go through here — outside a scope, the policies match nothing and
    the tables look empty.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('libvox.tenant_id', %s, true)", (tenant_id,))
        yield conn


def apply_migrations(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[int]:
    """Apply any migrations not yet recorded. Returns the versions applied.

    Each migration runs in its own transaction, so a failure halfway through a
    sequence leaves the earlier ones applied and recorded rather than rolling
    back work that already succeeded.
    """
    with conn.cursor() as cur:
        cur.execute(SCHEMA_MIGRATIONS_DDL)
        conn.commit()
        cur.execute("SELECT version, name, checksum FROM schema_migrations ORDER BY version")
        recorded = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

    applied: list[int] = []
    for migration in discover_migrations(directory):
        if migration.version in recorded:
            _, previous_checksum = recorded[migration.version]
            if previous_checksum != migration.checksum:
                raise MigrationError(
                    f"Migration {migration.name} changed after being applied "
                    f"(recorded {previous_checksum}, now {migration.checksum}). "
                    "Add a new migration instead of editing an applied one."
                )
            continue

        logger.info("applying migration %s", migration.name)
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(migration.sql)
            cur.execute(
                "INSERT INTO schema_migrations (version, name, checksum) "
                "VALUES (%s, %s, %s)",
                (migration.version, migration.name, migration.checksum),
            )
        applied.append(migration.version)

    return applied
