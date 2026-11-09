<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;

/*
 * Per-tenant ILS connection details.
 *
 * This is libvox-sync/migrations/002_tenant_ils_credentials.sql, moved. Its
 * header said so itself: "This table properly belongs to the Tenant DB, owned
 * by /libvox-portal-api. It lives here because that module does not exist yet.
 * It moves when the Tenant DB does." It does now.
 *
 * The columns are reproduced without a single change, because /libvox-sync and
 * /libvox-core read them through the same pgcrypto calls. The comments come
 * with it: they are the reasoning, and reasoning that stays behind when the
 * table moves is reasoning that gets re-derived wrong.
 *
 * WHAT THIS DEFENDS, AND WHAT IT DOES NOT
 *
 * Defends: a database dump, a backup, a read replica, an operator with SELECT
 * on the table, a screenshot of a query result. Those are the realistic ways a
 * credential store leaks, and in all of them the secret is ciphertext.
 *
 * Does not defend: a compromise of the application process itself, which by
 * definition holds the key. Closing that needs a KMS or Vault, where the key
 * never enters the application's memory space -- a later step, not this one.
 *
 * What it gains by moving: a foreign key to tenants, which was impossible
 * while there was no table saying which libraries exist.
 */
return new class extends Migration
{
    public function up(): void
    {
        DB::unprepared(<<<'SQL'
            CREATE TABLE tenant_ils (
                tenant_id  text        PRIMARY KEY
                    REFERENCES tenants(tenant_id) ON DELETE CASCADE,

                -- Constrained to drivers that exist. Adding 'folio' here without
                -- a FOLIO driver would let an operator configure a tenant that
                -- can never sync.
                ils_type   text        NOT NULL
                    CONSTRAINT tenant_ils_type_check CHECK (ils_type IN ('koha')),

                base_url   text        NOT NULL,

                -- Not encrypted, deliberately: in OAuth2 client-credentials the
                -- client_id is an identifier, not a secret -- the equivalent of
                -- a username. Treating it as sensitive would imply a protection
                -- it does not need and does not get.
                client_id  text        NOT NULL,

                -- pgp_sym_encrypt output. AES-256, no compression: these values
                -- are short, and compressing a secret leaks information about
                -- its content through its length.
                client_secret_enc bytea NOT NULL,

                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        SQL);

        DB::unprepared(<<<'SQL'
            COMMENT ON TABLE tenant_ils IS
                'Per-tenant ILS connection details. client_secret_enc is pgcrypto
                 ciphertext; the key is supplied by the application per statement
                 and is never stored here.'
        SQL);

        DB::unprepared(<<<'SQL'
            COMMENT ON COLUMN tenant_ils.client_id IS
                'Not a secret: the OAuth2 client_id is an identifier, like a username.'
        SQL);

        // Same tenant isolation as the Discovery Index. A credential row is
        // tenant data like any other, and the reasoning in migration 001 there
        // applies unchanged.
        DB::unprepared('ALTER TABLE tenant_ils ENABLE ROW LEVEL SECURITY');
        DB::unprepared('ALTER TABLE tenant_ils FORCE ROW LEVEL SECURITY');

        DB::unprepared(<<<'SQL'
            CREATE POLICY tenant_isolation ON tenant_ils
                USING      (tenant_id = current_setting('libvox.tenant_id', true))
                WITH CHECK (tenant_id = current_setting('libvox.tenant_id', true))
        SQL);

        DB::unprepared('GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_ils TO libvox_app');
    }

    public function down(): void
    {
        DB::unprepared('DROP TABLE IF EXISTS tenant_ils');
    }
};
