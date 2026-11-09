-- 001 — the Discovery Index
--
-- A durable, read-optimized replica of each tenant's bibliographic metadata.
-- Not a cache: a miss here never falls back to the ILS, and losing it takes
-- catalog search offline. See ../../README.md § Why "Index" and not "Cache".
--
-- Two tables and one view:
--   discovery_biblio  — the records, plus a Portuguese full-text vector
--   discovery_tenant  — per-tenant index state (which generation is live)
--   discovery_active  — the read surface; hides generations from callers

-- No BEGIN/COMMIT here: the migration runner wraps each file in its own
-- transaction, and a COMMIT inside would close it out from under the runner.

-- ---------------------------------------------------------------------------
-- Records
-- ---------------------------------------------------------------------------

CREATE TABLE discovery_biblio (
    tenant_id      text        NOT NULL,
    generation     integer     NOT NULL,
    biblio_id      bigint      NOT NULL,

    -- Mirrors libvox_mcp.models.CatalogRecord. The two must not drift:
    -- tests/test_schema.py asserts they match.
    title          text        NOT NULL,
    author         text,
    isbn           text,
    publisher      text,
    copyright_date integer,
    item_type      text,

    -- Highest ILS mtime seen for this record. Drives incremental sync.
    ils_timestamp  timestamptz,
    synced_at      timestamptz NOT NULL DEFAULT now(),

    -- The two-argument form of to_tsvector is required here: the one-argument
    -- form depends on default_text_search_config and is therefore not
    -- IMMUTABLE, so PostgreSQL rejects it in a generated column. Naming the
    -- configuration explicitly also pins the language regardless of how the
    -- server is configured.
    --
    -- Weights make title outrank author, which outranks publisher, under
    -- ts_rank. Without them relevance is flat.
    search tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('portuguese', coalesce(title,     '')), 'A') ||
        setweight(to_tsvector('portuguese', coalesce(author,    '')), 'B') ||
        setweight(to_tsvector('portuguese', coalesce(publisher, '')), 'C')
    ) STORED,

    PRIMARY KEY (tenant_id, generation, biblio_id)
);

CREATE INDEX discovery_biblio_search_idx
    ON discovery_biblio USING gin (search);

-- Supports the incremental sync's "what changed since?" probe.
CREATE INDEX discovery_biblio_incremental_idx
    ON discovery_biblio (tenant_id, generation, ils_timestamp);

COMMENT ON TABLE discovery_biblio IS
    'Discovery Index records. Derived from the ILS; the ILS remains the source of truth.';
COMMENT ON COLUMN discovery_biblio.generation IS
    'Full imports write a new generation and swap atomically. Incremental syncs upsert into the active one.';

-- ---------------------------------------------------------------------------
-- Per-tenant index state
-- ---------------------------------------------------------------------------

CREATE TABLE discovery_tenant (
    tenant_id         text        PRIMARY KEY,

    -- NULL until the first full import completes. A tenant with no active
    -- generation has no searchable records — which is exactly 'building'.
    active_generation integer,

    -- Contract with libvox_mcp.models.IndexStatus. Do not add a value here
    -- without adding it there: the model uses it to decide whether an empty
    -- result set means "no matches" or "not indexed yet".
    index_status      text        NOT NULL DEFAULT 'building'
        CONSTRAINT discovery_tenant_status_check
        CHECK (index_status IN ('ready', 'building', 'stale')),

    last_sync_at      timestamptz,
    last_error        text,

    CONSTRAINT discovery_tenant_ready_needs_generation
        CHECK (index_status <> 'ready' OR active_generation IS NOT NULL)
);

COMMENT ON CONSTRAINT discovery_tenant_ready_needs_generation ON discovery_tenant IS
    'A tenant cannot be ready with nothing to serve: that combination would make an empty search look like a genuine "no matches".';

-- ---------------------------------------------------------------------------
-- Read surface
-- ---------------------------------------------------------------------------

-- Callers query this, never discovery_biblio directly, so generations stay an
-- implementation detail of the sync worker.
--
-- security_invoker makes row-level security below evaluate against the role
-- running the query rather than the view's owner. Without it the view would
-- quietly bypass tenant isolation. Requires PostgreSQL 15+.
CREATE VIEW discovery_active
    WITH (security_invoker = true) AS
SELECT b.tenant_id,
       b.biblio_id,
       b.title,
       b.author,
       b.isbn,
       b.publisher,
       b.copyright_date,
       b.item_type,
       b.ils_timestamp,
       b.search
  FROM discovery_biblio b
  JOIN discovery_tenant t
    ON t.tenant_id = b.tenant_id
   AND t.active_generation = b.generation;

COMMENT ON VIEW discovery_active IS
    'The live generation for each tenant. Read from here; write to discovery_biblio.';

-- ---------------------------------------------------------------------------
-- Tenant isolation
-- ---------------------------------------------------------------------------

-- Rule 4 of the isolation model: the database enforces tenant scoping, not the
-- author of each query. A forgotten WHERE clause returns nothing rather than
-- another library's records.
--
-- The policy reads libvox.tenant_id, which the application sets per
-- transaction with SET LOCAL. The `true` second argument to current_setting
-- makes a missing setting return NULL instead of raising — and NULL fails the
-- comparison, so an unscoped session sees no rows. Fail closed.
--
-- FORCE matters: without it the table owner bypasses its own policy, and since
-- the application usually connects as the owner, isolation would silently not
-- apply. Superusers still bypass, which is what lets migrations run.

ALTER TABLE discovery_biblio ENABLE ROW LEVEL SECURITY;
ALTER TABLE discovery_biblio FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON discovery_biblio
    USING (tenant_id = current_setting('libvox.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('libvox.tenant_id', true));

ALTER TABLE discovery_tenant ENABLE ROW LEVEL SECURITY;
ALTER TABLE discovery_tenant FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON discovery_tenant
    USING (tenant_id = current_setting('libvox.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('libvox.tenant_id', true));

-- ---------------------------------------------------------------------------
-- Application role
-- ---------------------------------------------------------------------------

-- The role that reads and writes tenant data is deliberately not the role that
-- runs this migration: a superuser bypasses row-level security, which would
-- make everything above decorative. See docker/initdb/01-roles.sql.
--
-- Guarded, so the migration still applies against a database where the role is
-- provisioned by other means (or not at all, in a throwaway test database).

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'libvox_app') THEN
        GRANT USAGE ON SCHEMA public TO libvox_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON discovery_biblio TO libvox_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON discovery_tenant TO libvox_app;
        GRANT SELECT ON discovery_active TO libvox_app;
    ELSE
        RAISE NOTICE 'role libvox_app not found; skipping grants';
    END IF;
END
$$;
