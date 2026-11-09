<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;

/*
 * One users table, two kinds of person.
 *
 * An operator belongs to LibVox and has no library; a librarian belongs to
 * exactly one. The column is nullable because of the first, and a librarian
 * without it cannot reach the library panel at all -- see User::canAccessPanel.
 *
 * The pairing role-to-tenant cannot be a CHECK: roles live in spatie's
 * model_has_roles, and a CHECK constraint may not reference another table. So
 * it is enforced where it can be, and the guarantee that actually matters --
 * that one library's rows are unreachable from another library's request --
 * does not depend on it. That one is row-level security.
 */
return new class extends Migration
{
    public function up(): void
    {
        DB::unprepared(<<<'SQL'
            ALTER TABLE users
                ADD COLUMN tenant_id text
                    REFERENCES tenants(tenant_id) ON DELETE CASCADE,

                -- BCP 47, matching tenants.locale. Laravel's own lang
                -- directories spell it pt_BR; converting is the application's
                -- job at the boundary, not this column's.
                ADD COLUMN locale text NOT NULL DEFAULT 'pt-BR'
                    CONSTRAINT users_locale_check CHECK (locale IN ('pt-BR', 'en'))
        SQL);

        DB::unprepared('CREATE INDEX users_tenant_idx ON users (tenant_id)');

        DB::unprepared(<<<'SQL'
            COMMENT ON COLUMN users.tenant_id IS
                'Null for an operator, required for a librarian. A librarian
                 without it cannot reach the library panel.'
        SQL);
    }

    public function down(): void
    {
        DB::unprepared('DROP INDEX IF EXISTS users_tenant_idx');
        DB::unprepared('ALTER TABLE users DROP COLUMN IF EXISTS tenant_id, DROP COLUMN IF EXISTS locale');
    }
};
