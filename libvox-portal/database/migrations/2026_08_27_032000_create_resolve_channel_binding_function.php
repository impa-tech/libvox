<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;

/*
 * The contract /libvox-core calls, written before /libvox-core exists.
 *
 * "Which library owns this widget key, and is this page allowed to embed it?"
 * is asked before any tenant is known -- it is the question whose answer
 * determines the tenant. So it cannot run inside a tenant scope, and it is the
 * one genuinely cross-tenant read in this module.
 *
 * HOW THE HOLE IS KEPT NARROW
 *
 * SECURITY DEFINER runs as the function's owner, and FORCE ROW LEVEL SECURITY
 * subjects an owner to its own policies -- so an owner without a permissive
 * policy sees nothing, and one with BYPASSRLS sees everything everywhere. The
 * first fails in production and works in development, which is the worse of
 * the two.
 *
 * So the owner is libvox_resolver: a role that CANNOT LOG IN, holding SELECT
 * on two tables and one permissive SELECT policy. Nobody can connect as it.
 * The only way to exercise its privileges is to call this function, and the
 * width of the hole is therefore the return type -- one text column, which is
 * a tenant_id and can never be a secret.
 *
 * WHAT IT CANNOT RETURN
 *
 * channel_bindings.secret_enc is not in the SELECT list and not in the return
 * type, so no caller can widen this into a credential read. That is possible
 * only because binding_key is public by construction: Telegram's is the numeric
 * bot id and not the token, WhatsApp's is the phone_number_id and not the app
 * secret. Resolution never touches ciphertext.
 *
 * ON ORIGINS
 *
 * Comparison is exact, and that is correct rather than lax: a browser sends an
 * Origin header already lowercased, without a path and without a default port,
 * which is exactly the stored form. Origin::normalize exists to clean up what a
 * librarian types into a form, not to repair what a browser sends.
 */
return new class extends Migration
{
    public function up(): void
    {
        DB::unprepared('GRANT SELECT ON channel_bindings, tenants TO libvox_resolver');

        // The permissive half. Scoped TO one role and FOR SELECT only, so it
        // is auditable in the schema rather than implied by a superuser.
        DB::unprepared(<<<'SQL'
            CREATE POLICY channel_resolution ON channel_bindings
                FOR SELECT TO libvox_resolver
                USING (true)
        SQL);

        DB::unprepared(<<<'SQL'
            CREATE OR REPLACE FUNCTION resolve_channel_binding(
                p_channel text,
                p_key     text,
                p_origin  text DEFAULT NULL
            ) RETURNS text
            LANGUAGE sql
            STABLE
            SECURITY DEFINER
            SET search_path = public, pg_temp
            AS $$
                SELECT b.tenant_id
                  FROM channel_bindings b
                  JOIN tenants t ON t.tenant_id = b.tenant_id
                 WHERE b.channel     = p_channel
                   AND b.binding_key = p_key
                   AND b.enabled
                   AND t.status = 'active'
                   -- The Origin check lives here rather than in the caller, so
                   -- every channel adapter inherits it instead of each one
                   -- reimplementing it slightly differently.
                   AND (b.channel <> 'web'
                        OR (p_origin IS NOT NULL
                            AND b.allowed_origins @> to_jsonb(p_origin::text)))
            $$
        SQL);

        // Requires the migration role to be a superuser or a member of
        // libvox_resolver. Both are true of a role that can create the
        // function in the first place.
        DB::unprepared('ALTER FUNCTION resolve_channel_binding(text, text, text) OWNER TO libvox_resolver');

        DB::unprepared('REVOKE ALL ON FUNCTION resolve_channel_binding(text, text, text) FROM PUBLIC');
        DB::unprepared('GRANT EXECUTE ON FUNCTION resolve_channel_binding(text, text, text) TO libvox_app');

        DB::unprepared(<<<'SQL'
            COMMENT ON FUNCTION resolve_channel_binding(text, text, text) IS
                'The contract with every channel adapter: a channel, its public
                 binding key and (for the web widget) an Origin resolve to one
                 tenant_id, or to nothing. An unknown key, a channel that is
                 switched off, a suspended library and a page that is not on the
                 allowlist all return the same answer, which is nothing at all.
                 Origins are compared exactly; callers pass the Origin header as
                 the browser sent it.'
        SQL);
    }

    public function down(): void
    {
        DB::unprepared('DROP FUNCTION IF EXISTS resolve_channel_binding(text, text, text)');
        DB::unprepared('DROP POLICY IF EXISTS channel_resolution ON channel_bindings');
        DB::unprepared('REVOKE ALL ON channel_bindings, tenants FROM libvox_resolver');
    }
};
