<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;

/*
 * tenant_ils.ils_type may now be 'evergreen'.
 *
 * The CHECK was written to admit only ILS types a driver exists for -- "adding
 * 'folio' here without a FOLIO driver would let an operator configure a tenant
 * that can never sync" -- and /libvox-driver-evergreen now exists, so Evergreen
 * is admitted and FOLIO still is not. Kept equal to App\Enums\IlsType and to
 * libvox_sync.drivers.ILS_TYPES.
 *
 * No column changes. Evergreen has no API key: a library signs in with a staff
 * username and a password, and those go in client_id and client_secret_enc,
 * with the same encryption and the same write-only rule as a Koha secret. The
 * store does not need to know which ILS it holds; only the driver does.
 */
return new class extends Migration
{
    public function up(): void
    {
        DB::unprepared(<<<'SQL'
            ALTER TABLE tenant_ils
                DROP CONSTRAINT tenant_ils_type_check,
                ADD CONSTRAINT tenant_ils_type_check
                    CHECK (ils_type IN ('koha', 'evergreen'))
        SQL);

        DB::unprepared(<<<'SQL'
            COMMENT ON COLUMN tenant_ils.client_id IS
                'Not a secret. Koha: the OAuth2 client_id, an identifier like a username. Evergreen: the staff username itself.'
        SQL);
    }

    /*
     * Fails while an Evergreen library is configured, and should: going back
     * would leave a row the old constraint forbids.
     */
    public function down(): void
    {
        DB::unprepared(<<<'SQL'
            ALTER TABLE tenant_ils
                DROP CONSTRAINT tenant_ils_type_check,
                ADD CONSTRAINT tenant_ils_type_check CHECK (ils_type IN ('koha'))
        SQL);

        DB::unprepared(<<<'SQL'
            COMMENT ON COLUMN tenant_ils.client_id IS
                'Not a secret: the OAuth2 client_id is an identifier, like a username.'
        SQL);
    }
};
