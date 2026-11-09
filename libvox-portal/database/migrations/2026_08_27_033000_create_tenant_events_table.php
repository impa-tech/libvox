<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;

/*
 * Saying that a library's configuration changed.
 *
 * The README describes a TenantUpdated event that /libvox-sync consumes. There
 * is no broker deployed -- the compose file has only PostgreSQL -- and the one
 * intended consumer is a one-shot CLI, which is to say it is always down.
 *
 * That rules out plain LISTEN/NOTIFY on its own: it is fire-and-forget, so
 * every event would be dropped, and "the portal has been silently discarding
 * every configuration change since March" is an expensive thing to discover.
 * It also rules out waiting for a broker, because a publisher retrofitted later
 * tends to publish from wherever the retrofit happens rather than from one
 * place.
 *
 * So: an append-only table written in the same transaction as the change --
 * which is what makes it unable to claim a change that rolled back -- with
 * pg_notify layered on top as a latency optimization. NOTIFY is delivered on
 * commit, which is exactly what makes the pair safe. The table is the
 * durability; the notification is only a way to skip a poll.
 *
 * When a broker arrives this becomes its transactional outbox rather than
 * being thrown away, and nothing in the application changes.
 */
return new class extends Migration
{
    public function up(): void
    {
        DB::unprepared(<<<'SQL'
            CREATE TABLE tenant_events (
                id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

                -- Deliberately NOT a foreign key. A log entry that vanishes
                -- with the thing it describes is not a log, and the deletion of
                -- a library is one of the changes most worth recording: it is
                -- what tells a consumer to drop that tenant's index.
                tenant_id  text NOT NULL,

                event      text NOT NULL
                    CONSTRAINT tenant_events_event_check
                    CHECK (event IN ('TenantUpdated')),

                -- There is deliberately no payload column. The event says which
                -- library changed and nothing else; the consumer comes back and
                -- reads the tables under its own tenant scope. A payload would
                -- be a second, unversioned copy of the schema, and the one place
                -- an encrypted column could escape in cleartext.
                occurred_at timestamptz NOT NULL DEFAULT now(),
                consumed_at timestamptz
            )
        SQL);

        DB::unprepared('CREATE INDEX tenant_events_unconsumed_idx ON tenant_events (id) WHERE consumed_at IS NULL');
        DB::unprepared('CREATE INDEX tenant_events_tenant_idx ON tenant_events (tenant_id, id)');

        DB::unprepared(<<<'SQL'
            CREATE OR REPLACE FUNCTION libvox_notify_tenant_event() RETURNS trigger
                LANGUAGE plpgsql AS $$
            BEGIN
                -- Delivered on commit, so a listener never learns of a change
                -- that then rolled back. The payload carries an id and nothing
                -- worth reading: a consumer that misses this finds the row on
                -- its next poll, and one that receives it skips the poll.
                -- NOTIFY is an optimization here, never the delivery guarantee.
                PERFORM pg_notify('libvox_tenant_events', json_build_object(
                    'id', NEW.id, 'tenant_id', NEW.tenant_id, 'event', NEW.event
                )::text);
                RETURN NULL;
            END
            $$
        SQL);

        DB::unprepared(<<<'SQL'
            CREATE TRIGGER tenant_events_notify
                AFTER INSERT ON tenant_events
                FOR EACH ROW EXECUTE FUNCTION libvox_notify_tenant_event()
        SQL);

        /*
         * Publishing is a trigger, not an Eloquent observer.
         *
         * "Somebody forgot to publish" is the failure mode, and it is silent.
         * A trigger also fires for the writes an observer never sees: a bulk
         * update, a psql session, a seeder, and the cascade that removes a
         * library's rows when the library goes.
         */
        DB::unprepared(<<<'SQL'
            CREATE OR REPLACE FUNCTION libvox_record_tenant_change() RETURNS trigger
                LANGUAGE plpgsql AS $$
            BEGIN
                INSERT INTO tenant_events (tenant_id, event)
                VALUES (COALESCE(NEW.tenant_id, OLD.tenant_id), 'TenantUpdated');
                RETURN NULL;
            END
            $$
        SQL);

        foreach (['tenants', 'tenant_widget', 'channel_bindings', 'tenant_ils'] as $table) {
            DB::unprepared(<<<SQL
                CREATE TRIGGER {$table}_changed
                    AFTER INSERT OR UPDATE OR DELETE ON {$table}
                    FOR EACH ROW EXECUTE FUNCTION libvox_record_tenant_change()
            SQL);
        }

        /*
         * No row-level security here.
         *
         * A consumer polls this before it knows which library an event concerns
         * -- that is what the event tells it -- so a policy keyed on
         * libvox.tenant_id would be evaluated against a setting nothing has had
         * the chance to set. There is also nothing to protect: an id, a library
         * id, an event name and two timestamps. No credentials, no catalog, no
         * configuration.
         *
         * The trigger functions are SECURITY INVOKER, so the INSERT happens as
         * whoever made the change -- which is why libvox_app needs INSERT here
         * even though nothing in the application inserts directly.
         */
        DB::unprepared('GRANT SELECT, INSERT, UPDATE ON tenant_events TO libvox_app');

        DB::unprepared(<<<'SQL'
            COMMENT ON TABLE tenant_events IS
                'Transactional outbox for TenantUpdated. Written in the same
                 transaction as the change it describes, so it cannot claim one
                 that rolled back. Nothing consumes it yet: /libvox-sync is a
                 one-shot CLI today. LISTEN libvox_tenant_events for the
                 notification, and read this table for the guarantee.'
        SQL);
    }

    public function down(): void
    {
        foreach (['tenants', 'tenant_widget', 'channel_bindings', 'tenant_ils'] as $table) {
            DB::unprepared("DROP TRIGGER IF EXISTS {$table}_changed ON {$table}");
        }

        DB::unprepared('DROP TABLE IF EXISTS tenant_events');
        DB::unprepared('DROP FUNCTION IF EXISTS libvox_record_tenant_change()');
        DB::unprepared('DROP FUNCTION IF EXISTS libvox_notify_tenant_event()');
    }
};
