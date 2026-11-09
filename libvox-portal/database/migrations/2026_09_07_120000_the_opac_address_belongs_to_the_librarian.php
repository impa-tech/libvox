<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;

/*
 * tenants.opac_url goes, because the librarian registers the OPAC page now.
 *
 * The column had exactly one reader in the whole application -- the provisioner,
 * at the instant a library was created, where it seeded the first allowed
 * origin. After that nothing looked at it again, and nothing kept it true:
 * creating a library went through the provisioner, editing one did not, so a
 * corrected address changed the operator's screen and no allowlist anywhere.
 *
 * Two fields that both claimed to be "the OPAC address" and diverged in silence
 * from the second day. The one that decides anything is
 * channel_bindings.allowed_origins, which the librarian owns and which the
 * database enforces; this one was a guess by someone who does not run the OPAC.
 */
return new class extends Migration
{
    public function up(): void
    {
        DB::unprepared('ALTER TABLE tenants DROP COLUMN opac_url');
    }

    public function down(): void
    {
        // Nullable and unconstrained, exactly as it was. Nothing repopulates it:
        // the addresses it used to hold live in allowed_origins now, and copying
        // them back would recreate the divergence this removed.
        DB::unprepared('ALTER TABLE tenants ADD COLUMN opac_url text');
    }
};
