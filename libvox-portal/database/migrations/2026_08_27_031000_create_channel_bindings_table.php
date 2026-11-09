<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;

/*
 * One row per way a reader can reach a library.
 *
 * The widget key in an OPAC page, a Telegram bot, a WhatsApp business number
 * and an inbound e-mail address look like four different things and are one:
 * a per-tenant channel binding. Keeping them in one table is what makes
 * resolving a tenant a single lookup whatever the channel, and what makes
 * "a new channel is a new row" literal rather than a figure of speech.
 *
 * Every channel except the web widget needs a credential only the library can
 * create -- a bot token from BotFather, a verified business number, an MX
 * record. There is no central switch that turns WhatsApp on for everybody,
 * which is why enabling a channel is data in this table rather than a
 * deployment step.
 */
return new class extends Migration
{
    public function up(): void
    {
        /*
         * A CHECK constraint may not contain a subquery or a set-returning
         * call, so jsonb_array_elements_text cannot appear inline. Hence a
         * function -- IMMUTABLE, because a CHECK may only call one that is.
         *
         * bool_and over an empty array is NULL, and `IS NOT FALSE` is what
         * lets the empty case pass: a binding that is not enabled yet has no
         * origins, and that is a valid state.
         */
        DB::unprepared(<<<'SQL'
            CREATE OR REPLACE FUNCTION libvox_origins_are_well_formed(origins jsonb)
                RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
                SELECT bool_and(value ~ '^https?://[a-z0-9.-]+(:[0-9]{1,5})?$') IS NOT FALSE
                  FROM jsonb_array_elements_text(origins) AS value
            $$
        SQL);

        DB::unprepared(<<<'SQL'
            CREATE TABLE channel_bindings (
                id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

                tenant_id    text NOT NULL
                    REFERENCES tenants(tenant_id) ON DELETE CASCADE,

                channel      text NOT NULL
                    CONSTRAINT channel_bindings_channel_check
                    CHECK (channel IN ('web', 'telegram', 'whatsapp', 'email')),

                -- What an inbound request presents, and the whole of the lookup
                -- key.
                --
                -- INVARIANT: binding_key is never a secret. Telegram's is the
                -- numeric bot id -- the part before the colon, which is public
                -- -- and not the token; WhatsApp's is the phone_number_id and
                -- not the app secret. Anything secret lives in secret_enc.
                -- That is what lets a tenant be resolved from a binding without
                -- the resolver ever touching ciphertext.
                binding_key  text NOT NULL,

                -- pgcrypto ciphertext, same key and same rules as
                -- tenant_ils.client_secret_enc. NULL where the channel has no
                -- secret: the widget key is public by design -- it identifies,
                -- it does not authenticate.
                secret_enc   bytea,

                enabled      boolean NOT NULL DEFAULT false,

                -- Web only. Stored normalized -- lowercase scheme and host,
                -- explicit port only when it is not the default, no path, no
                -- trailing slash -- because an Origin header is compared as a
                -- string, and "almost equal" is "not equal".
                allowed_origins jsonb NOT NULL DEFAULT '[]'::jsonb,

                -- Non-secret, channel-specific extras: the readable WhatsApp
                -- number, the bot's username. Never a credential.
                metadata     jsonb NOT NULL DEFAULT '{}'::jsonb,

                created_at   timestamptz NOT NULL DEFAULT now(),
                updated_at   timestamptz NOT NULL DEFAULT now(),

                -- Two libraries answering on one bot or one address is not a
                -- configuration, it is a tenant crossing. So it is a constraint
                -- and not a validation rule.
                CONSTRAINT channel_bindings_key_is_unique UNIQUE (channel, binding_key),

                -- One binding per channel per library, for now. Drop this the
                -- day a library wants two Telegram bots; nothing else assumes it.
                CONSTRAINT channel_bindings_one_per_channel UNIQUE (tenant_id, channel),

                CONSTRAINT channel_bindings_secret_shape CHECK (
                    (channel IN ('web', 'email')         AND secret_enc IS NULL) OR
                    (channel IN ('telegram', 'whatsapp') AND secret_enc IS NOT NULL)
                ),

                CONSTRAINT channel_bindings_origins_are_web_only CHECK (
                    channel = 'web' OR allowed_origins = '[]'::jsonb
                ),

                -- The fail-closed rule that matters: a widget with no registered
                -- origin is embeddable from any page on the internet. It may
                -- exist; it may not be switched on.
                CONSTRAINT channel_bindings_web_needs_an_origin CHECK (
                    channel <> 'web' OR NOT enabled OR jsonb_array_length(allowed_origins) > 0
                ),

                CONSTRAINT channel_bindings_origins_are_well_formed CHECK (
                    libvox_origins_are_well_formed(allowed_origins)
                )
            )
        SQL);

        DB::unprepared('CREATE INDEX channel_bindings_tenant_idx ON channel_bindings (tenant_id)');
        DB::unprepared('CREATE INDEX channel_bindings_origins_idx ON channel_bindings USING gin (allowed_origins)');

        DB::unprepared(<<<'SQL'
            COMMENT ON COLUMN channel_bindings.binding_key IS
                'Never a secret. The public half of a channel: a widget key, a
                 numeric bot id, a business number, an inbound address.'
        SQL);

        DB::unprepared('ALTER TABLE channel_bindings ENABLE ROW LEVEL SECURITY');
        DB::unprepared('ALTER TABLE channel_bindings FORCE ROW LEVEL SECURITY');

        DB::unprepared(<<<'SQL'
            CREATE POLICY tenant_isolation ON channel_bindings
                USING      (tenant_id = current_setting('libvox.tenant_id', true))
                WITH CHECK (tenant_id = current_setting('libvox.tenant_id', true))
        SQL);

        DB::unprepared('GRANT SELECT, INSERT, UPDATE, DELETE ON channel_bindings TO libvox_app');
        DB::unprepared('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO libvox_app');
    }

    public function down(): void
    {
        DB::unprepared('DROP TABLE IF EXISTS channel_bindings');
        DB::unprepared('DROP FUNCTION IF EXISTS libvox_origins_are_well_formed(jsonb)');
    }
};
