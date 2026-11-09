<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;

/*
 * The tenants registry -- the first table in LibVox that says which libraries
 * exist.
 *
 * Until now there was none. `tenant_id` is a bare `text` column in
 * discovery_biblio, discovery_tenant and tenant_ils; /libvox-core types it
 * `str` and /libvox-mcp coerces it to one. Nothing anywhere validates the
 * format, so a typo in `libvox-sync tenant set --tenant furg` creates a ghost
 * library in silence and the index fills up under an id nobody will look for.
 *
 * Written as raw SQL rather than through the schema builder, for the reason
 * /libvox-sync gives for having no ORM: a format CHECK, a trigger and, in the
 * migrations that follow, row-level security policies and SECURITY DEFINER
 * functions are all things you would hand-write anyway. Half-expressing them
 * through a builder is worse than writing the SQL.
 */
return new class extends Migration
{
    public function up(): void
    {
        // Encrypts stored ILS client secrets, from the migration after next.
        // Creating an extension needs the owner, which is why migrations run
        // on pgsql_admin.
        DB::unprepared('CREATE EXTENSION IF NOT EXISTS pgcrypto');

        DB::unprepared(<<<'SQL'
            CREATE TABLE tenants (
                -- The primary key IS the value row-level security compares
                -- against, here and in the Discovery Index. Not a surrogate id
                -- with a slug beside it: a second identifier would mean two
                -- things could disagree about which library a row belongs to,
                -- and the one that reaches set_config decides.
                --
                -- This CHECK is the only place in LibVox that validates the
                -- format. It also refuses whitespace and quotes, which would
                -- otherwise travel into set_config('libvox.tenant_id', ...).
                tenant_id     text        PRIMARY KEY
                    CONSTRAINT tenants_id_format
                    CHECK (tenant_id ~ '^lib-[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$'),

                -- The changeable human label. tenant_id is not it.
                display_name  text        NOT NULL,

                status        text        NOT NULL DEFAULT 'provisioning'
                    CONSTRAINT tenants_status_check
                    CHECK (status IN ('provisioning', 'active', 'suspended')),

                -- BCP 47, with a hyphen: this value crosses the wire to a
                -- browser and to /libvox-core. Laravel's own lang directories
                -- use pt_BR with an underscore; converting is the application's
                -- job, at the boundary, and not this column's.
                locale        text        NOT NULL DEFAULT 'pt-BR'
                    CONSTRAINT tenants_locale_check CHECK (locale IN ('pt-BR', 'en')),

                opac_url      text,
                contact_email text,

                created_at    timestamptz NOT NULL DEFAULT now(),
                updated_at    timestamptz NOT NULL DEFAULT now()
            )
        SQL);

        DB::unprepared(<<<'SQL'
            COMMENT ON COLUMN tenants.tenant_id IS
                'The tenant_id every other LibVox component carries. This is the
                 first place that enforces its format; nothing downstream does.'
        SQL);

        // A tenant_id is embedded in Discovery Index rows in another database,
        // with no foreign key that could catch a rename. So renaming is refused
        // structurally rather than by disabling a field on a form.
        DB::unprepared(<<<'SQL'
            -- OR REPLACE: `migrate:fresh` wipes tables but leaves functions
            -- behind, so a plain CREATE fails on the second run against the
            -- same database -- which is every test run after the first.
            CREATE OR REPLACE FUNCTION tenants_refuse_id_change() RETURNS trigger
                LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN
                    RAISE EXCEPTION
                        'tenant_id % cannot be renamed to %: rows in the Discovery '
                        'Index reference it and no foreign key would catch it',
                        OLD.tenant_id, NEW.tenant_id;
                END IF;
                RETURN NEW;
            END
            $$
        SQL);

        DB::unprepared(<<<'SQL'
            CREATE TRIGGER tenants_id_is_immutable
                BEFORE UPDATE ON tenants
                FOR EACH ROW EXECUTE FUNCTION tenants_refuse_id_change()
        SQL);

        /*
         * No row-level security on this table, deliberately.
         *
         * It is the registry that *defines* which tenants exist. A policy
         * `tenant_id = current_setting('libvox.tenant_id')` is circular: the
         * operator legitimately needs the whole list, and every listing query
         * would have to be routed around it.
         *
         * The tempting fix -- adding `current_setting(...) IS NULL OR ...` --
         * must never appear in this codebase. It inverts the invariant every
         * other table here is built on: an unscoped session would see
         * everything instead of nothing. Fail-open, by name, so nobody
         * reinvents it.
         *
         * Tables that hold a secret get RLS. This one is protected by which
         * panel can reach it.
         */
        DB::unprepared('GRANT USAGE ON SCHEMA public TO libvox_app');
        DB::unprepared('GRANT SELECT, INSERT, UPDATE, DELETE ON tenants TO libvox_app');
    }

    public function down(): void
    {
        DB::unprepared('DROP TABLE IF EXISTS tenants');
        DB::unprepared('DROP FUNCTION IF EXISTS tenants_refuse_id_change()');
    }
};
