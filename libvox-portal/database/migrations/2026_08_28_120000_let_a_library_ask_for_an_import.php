<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;

/*
 * A library asking for its catalogue to be imported.
 *
 * WHY THIS IS AN EVENT AND NOT A COLUMN
 *
 * The outbox already exists, with durability, claiming and a notification, and
 * it has been recording faithfully since it was built with nothing consuming
 * it. A `sync_requested_at` column on `tenants` would be a second mechanism
 * for the same shape of thing -- and, because `tenants` carries the
 * TenantUpdated trigger, writing it would emit an event anyway.
 *
 * WHY IT IS NOT TenantUpdated EITHER
 *
 * That fires whenever any configuration changes, the widget's colour included.
 * A consumer that re-imported a catalogue on every one of them would send
 * hundreds of requests to a library's own server because somebody picked a new
 * shade of blue. An import is asked for deliberately, so it gets its own name.
 *
 * WHY THE FUNCTION TAKES NO ARGUMENT
 *
 * It reads `libvox.tenant_id`, which BindTenantScope has already bound for the
 * request. So there is no way to ask for another library's catalogue, because
 * there is nowhere to write another library's name -- the same property every
 * other boundary in this system buys, and the same shape as
 * resolve_channel_binding.
 *
 * `tenants` has no row-level security, so an UPDATE from the panel would be
 * guarded by a WHERE clause and nothing else. This is guarded by the absence of
 * a parameter.
 */
return new class extends Migration
{
    public function up(): void
    {
        DB::unprepared(<<<'SQL'
            ALTER TABLE tenant_events
                DROP CONSTRAINT tenant_events_event_check,
                ADD CONSTRAINT tenant_events_event_check
                    CHECK (event IN ('TenantUpdated', 'SyncRequested'))
        SQL);

        /*
         * One pending request per library, as a constraint rather than a check
         * the caller performs.
         *
         * A read-then-insert would let two clicks a millisecond apart both find
         * nothing pending and both insert. This cannot: the second INSERT is
         * refused by the index, and the function turns that refusal into
         * `false`. "At most one" is true because the database cannot represent
         * two, which is the version that survives concurrency.
         */
        DB::unprepared(<<<'SQL'
            CREATE UNIQUE INDEX tenant_events_one_pending_sync_idx
                ON tenant_events (tenant_id)
             WHERE event = 'SyncRequested' AND consumed_at IS NULL
        SQL);

        DB::unprepared(<<<'SQL'
            CREATE OR REPLACE FUNCTION request_catalog_sync() RETURNS boolean
                LANGUAGE plpgsql
                SECURITY DEFINER
                SET search_path = public, pg_temp
                AS $$
            DECLARE
                v_tenant text := nullif(current_setting('libvox.tenant_id', true), '');
            BEGIN
                -- Fail-closed, like every other unbound read in this schema: no
                -- scope, no library, nothing asked for.
                IF v_tenant IS NULL THEN
                    RETURN false;
                END IF;

                -- A library with no catalogue connection has nothing to
                -- import, and a suspended one should not be reaching anyone's
                -- server. Refused here rather than in the panel, so the rule
                -- holds for any caller.
                --
                -- `<> 'suspended'` and not `= 'active'`, which is where this
                -- started: a library still in `provisioning` is exactly the one
                -- that wants its index built, so that MARC.IA works the moment
                -- the operator activates it. Requiring `active` would leave the
                -- button dead through the whole of setup.
                INSERT INTO tenant_events (tenant_id, event)
                SELECT v_tenant, 'SyncRequested'
                  FROM tenants
                 WHERE tenant_id = v_tenant
                   AND status <> 'suspended'
                   AND ils_configured_at IS NOT NULL;

                RETURN FOUND;
            EXCEPTION
                WHEN unique_violation THEN
                    -- Already asked for and not yet consumed. Not an error: the
                    -- second click means the same thing as the first.
                    RETURN false;
            END
            $$
        SQL);

        DB::unprepared('REVOKE ALL ON FUNCTION request_catalog_sync() FROM PUBLIC');
        DB::unprepared('GRANT EXECUTE ON FUNCTION request_catalog_sync() TO libvox_app');

        DB::unprepared(<<<'SQL'
            COMMENT ON FUNCTION request_catalog_sync() IS
                'Ask for this library''s catalogue to be imported. Takes no
                 argument on purpose: the library is whichever one the request
                 is scoped to. Returns false when one is already pending, when
                 no ILS is configured, or when nothing is bound.'
        SQL);
    }

    public function down(): void
    {
        DB::unprepared('DROP FUNCTION IF EXISTS request_catalog_sync()');
        DB::unprepared('DROP INDEX IF EXISTS tenant_events_one_pending_sync_idx');
        DB::unprepared("DELETE FROM tenant_events WHERE event = 'SyncRequested'");
        DB::unprepared(<<<'SQL'
            ALTER TABLE tenant_events
                DROP CONSTRAINT tenant_events_event_check,
                ADD CONSTRAINT tenant_events_event_check
                    CHECK (event IN ('TenantUpdated'))
        SQL);
    }
};
