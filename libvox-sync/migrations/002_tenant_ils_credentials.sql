-- 002 — per-tenant ILS connection details
--
-- Which ILS a library runs, where it is, and how to authenticate against it.
-- The client secret is encrypted with pgcrypto; the key lives in the
-- application's environment and never in this database.
--
-- WHAT THIS DEFENDS, AND WHAT IT DOES NOT
--
-- Defends: a database dump, a backup, a read replica, an operator with SELECT
-- on the table, a screenshot of a query result. Those are the realistic ways a
-- credential store leaks, and in all of them the secret is ciphertext.
--
-- Does not defend: a compromise of the application process itself, which by
-- definition holds the key. Closing that needs a KMS or Vault, where the key
-- never enters the application's memory space — a later step, not this one.
--
-- SCOPE NOTE
--
-- This table properly belongs to the Tenant DB, owned by /libvox-portal-api.
-- It lives here because that module does not exist yet and this is the only
-- component with a database. It moves when the Tenant DB does; nothing else
-- about the design changes, because the sync worker reads it through one
-- function either way.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE tenant_ils (
    tenant_id  text        PRIMARY KEY,

    -- Constrained to drivers that exist. Adding 'folio' here without a FOLIO
    -- driver would let an operator configure a tenant that can never sync.
    ils_type   text        NOT NULL
        CONSTRAINT tenant_ils_type_check CHECK (ils_type IN ('koha')),

    base_url   text        NOT NULL,

    -- Not encrypted, deliberately: in OAuth2 client-credentials the client_id
    -- is an identifier, not a secret — the equivalent of a username. Treating
    -- it as sensitive would imply a protection it does not need and does not
    -- get.
    client_id  text        NOT NULL,

    -- pgp_sym_encrypt output. AES-256, no compression: these values are short,
    -- and compressing a secret leaks information about its content through its
    -- length.
    client_secret_enc bytea NOT NULL,

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE tenant_ils IS
    'Per-tenant ILS connection details. client_secret_enc is pgcrypto ciphertext; the key is supplied by the application per statement and is never stored here.';
COMMENT ON COLUMN tenant_ils.client_id IS
    'Not a secret: the OAuth2 client_id is an identifier, like a username.';

-- Same tenant isolation as the Discovery Index. A credential row is tenant
-- data like any other, and the reasoning in migration 001 applies unchanged.
ALTER TABLE tenant_ils ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_ils FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON tenant_ils
    USING (tenant_id = current_setting('libvox.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('libvox.tenant_id', true));

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'libvox_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_ils TO libvox_app;
    ELSE
        RAISE NOTICE 'role libvox_app not found; skipping grants';
    END IF;
END
$$;
