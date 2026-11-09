<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;

/*
 * Whether a library has configured its catalog connection, on the one table
 * the operator can read across libraries.
 *
 * This replaces what the plan called for -- a SECURITY DEFINER function
 * returning an inventory of tenant_ils -- and it is worth writing down why,
 * because the function looked like the more principled answer.
 *
 * SECURITY DEFINER runs as the function's owner, and FORCE ROW LEVEL SECURITY
 * subjects an owner to its own policies. So the function works only where its
 * owner is a superuser or carries a permissive policy. In this compose file
 * the owner is the superuser from POSTGRES_USER, so it would have worked here
 * and returned nothing in a production cluster where provisioning creates an
 * unprivileged owner -- a difference that shows up as an empty screen, not as
 * an error, and only after deployment.
 *
 * The narrower question turned out to be the only one the operator actually
 * asks across libraries: "is this one configured yet?". That is a boolean and
 * a timestamp, and it belongs on tenants, which has no row-level security
 * because it is the registry that defines the tenants.
 *
 * Anything more than that -- which Koha, under which client_id -- is a question
 * about one library, and the operator asks it by opening that library, where
 * the request is bound to a tenant scope like any other read of tenant data.
 * No privileged function, no third role, no cross-tenant read at all.
 *
 * It is denormalized, and denormalized data drifts. This one cannot: it is
 * written inside the same transaction as the credential it describes.
 */
return new class extends Migration
{
    public function up(): void
    {
        DB::unprepared('ALTER TABLE tenants ADD COLUMN ils_configured_at timestamptz');

        DB::unprepared(<<<'SQL'
            COMMENT ON COLUMN tenants.ils_configured_at IS
                'When this library last stored ILS credentials. Maintained by
                 IlsCredentialStore inside the same transaction as the write, so
                 it cannot disagree with tenant_ils. It exists so the operator
                 can see which libraries are configured without reading a table
                 that is deliberately unreadable across tenants.'
        SQL);
    }

    public function down(): void
    {
        DB::unprepared('ALTER TABLE tenants DROP COLUMN IF EXISTS ils_configured_at');
    }
};
