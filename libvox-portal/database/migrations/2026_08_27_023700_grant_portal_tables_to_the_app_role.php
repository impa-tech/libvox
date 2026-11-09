<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;

/*
 * Grants for the role the application actually connects as.
 *
 * Migrations run as the owner, so every table created so far is reachable by
 * `libvox` and by nobody else. Without this the panels would authenticate
 * against a users table they cannot read, and the error would point at Laravel
 * rather than at a missing GRANT.
 *
 * None of these tables carries row-level security, and each for the same
 * reason: they are consulted *before* a tenant is known. The authentication
 * query is what determines which library the request belongs to, so a policy
 * comparing against libvox.tenant_id would be evaluated against a setting
 * nothing has had the chance to set. What protects them is that they hold no
 * tenant data -- no credentials, no catalog, no configuration.
 *
 * Sequences need their own grant: `$table->id()` produces a bigserial, and
 * INSERT on the table is not enough to advance its sequence. The failure is a
 * permission error on the first insert that reads like a framework bug.
 */
return new class extends Migration
{
    public function up(): void
    {
        DB::unprepared(<<<'SQL'
            GRANT SELECT, INSERT, UPDATE, DELETE ON
                users,
                password_reset_tokens,
                roles,
                permissions,
                model_has_roles,
                model_has_permissions,
                role_has_permissions
            TO libvox_app
        SQL);

        DB::unprepared('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO libvox_app');
    }

    public function down(): void
    {
        DB::unprepared(<<<'SQL'
            REVOKE ALL ON
                users, password_reset_tokens, roles, permissions,
                model_has_roles, model_has_permissions, role_has_permissions
            FROM libvox_app
        SQL);
    }
};
