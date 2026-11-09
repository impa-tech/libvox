<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;

/*
 * How one library wants MARC.IA to appear and to sound.
 *
 * The first table in this module with row-level security, and the pattern the
 * ones after it repeat: ENABLE, FORCE, and a policy comparing tenant_id to a
 * setting that is bound per transaction. Identical to
 * libvox-sync/migrations/001_discovery_index.sql, on purpose -- three
 * components read tenant data as the same unprivileged role, and one pattern
 * for one guarantee is what keeps that readable.
 *
 * FORCE subjects the table's owner to its own policies, which it otherwise
 * bypasses. It does NOT reach a superuser: PostgreSQL lets superusers and
 * BYPASSRLS roles through unconditionally, whatever the table says. In this
 * compose file the owner IS the superuser from POSTGRES_USER, so FORCE changes
 * nothing here -- it is written for the deployment where provisioning creates a
 * non-superuser owner, which is the one that matters.
 *
 * What actually keeps the application inside its policies is that it connects
 * as libvox_app, which is neither. AppServiceProvider refuses to serve if that
 * ever stops being true.
 */
return new class extends Migration
{
    public function up(): void
    {
        DB::unprepared(<<<'SQL'
            CREATE TABLE tenant_widget (
                tenant_id       text PRIMARY KEY
                    REFERENCES tenants(tenant_id) ON DELETE CASCADE,

                -- What SessionReadyEvent carries to the widget. These get
                -- columns rather than a settings blob because /libvox-core
                -- parses them, which makes them a contract.
                assistant_name  text NOT NULL DEFAULT 'MARC.IA',
                welcome_message text,

                default_locale  text NOT NULL DEFAULT 'pt-BR'
                    CONSTRAINT tenant_widget_locale_check
                    CHECK (default_locale IN ('pt-BR', 'en')),

                persona_tone    text NOT NULL DEFAULT 'neutral'
                    CONSTRAINT tenant_widget_tone_check
                    CHECK (persona_tone IN ('formal', 'neutral', 'friendly')),

                theme_color     text NOT NULL DEFAULT '#4f46e5'
                    CONSTRAINT tenant_widget_theme_check
                    CHECK (theme_color ~ '^#[0-9a-fA-F]{6}$'),

                created_at      timestamptz NOT NULL DEFAULT now(),
                updated_at      timestamptz NOT NULL DEFAULT now()
            )
        SQL);

        /*
         * There is deliberately no free-text instruction column here, and its
         * absence is the enforcement.
         *
         * The system prompt has two layers. The persona layer -- name, tone,
         * greeting, language -- belongs to the library. The policy layer --
         * answer only from tool results, never invent a record, admit when
         * availability is unknown -- does not, and must not be reachable from
         * a form. A textarea whose contents reach the model's instruction slot
         * would let an administrator write something reasonable-sounding
         * ("if the catalog has nothing, answer from your own knowledge") and
         * silently disable the guarantee the whole system rests on.
         *
         * persona_tone is an enum for the same reason. See the root README,
         * "The Agent (MARC.IA's loop)".
         */
        DB::unprepared(<<<'SQL'
            COMMENT ON TABLE tenant_widget IS
                'The persona layer of the system prompt, and the widget''s
                 appearance. No column reaches the policy layer, by design.'
        SQL);

        DB::unprepared('ALTER TABLE tenant_widget ENABLE ROW LEVEL SECURITY');
        DB::unprepared('ALTER TABLE tenant_widget FORCE ROW LEVEL SECURITY');

        // The `true` second argument to current_setting means a missing setting
        // returns NULL rather than raising. NULL = anything is NULL, which is
        // not true, so an unscoped session matches no rows at all. Fail closed:
        // forgetting to bind yields nothing, never another library's rows.
        DB::unprepared(<<<'SQL'
            CREATE POLICY tenant_isolation ON tenant_widget
                USING      (tenant_id = current_setting('libvox.tenant_id', true))
                WITH CHECK (tenant_id = current_setting('libvox.tenant_id', true))
        SQL);

        DB::unprepared('GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_widget TO libvox_app');
    }

    public function down(): void
    {
        DB::unprepared('DROP TABLE IF EXISTS tenant_widget');
    }
};
