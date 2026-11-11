<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;

/*
 * tenant_ils.opac_url: where readers open this library's catalogue, so a book
 * MARC.IA names can link to its record page.
 *
 * Not the column 2026_09_07_120000 removed from `tenants`, and the difference
 * is the reason this one is allowed back. That one claimed to be "the OPAC
 * address" in the sense of where the widget may run -- a question
 * channel_bindings.allowed_origins already answered and enforced -- and the two
 * diverged. This one answers nothing about access. Core reads it for one
 * purpose: to build a link, through the driver for `ils_type`, to a record a
 * tool has just returned.
 *
 * Next to the connection rather than on the registry, because the shape of the
 * link belongs to the ILS: Koha's is /cgi-bin/koha/opac-detail.pl, and the
 * next driver's will be something else. And separate from base_url, because
 * Koha serves its API and its OPAC on different hosts.
 *
 * Nullable: a library that leaves it empty gets answers without links, which
 * is what every library had before this. The CHECK is not tidiness -- the value
 * ends up in an href in a reader's browser, and `javascript:` is a URL too.
 */
return new class extends Migration
{
    public function up(): void
    {
        DB::unprepared(<<<'SQL'
            ALTER TABLE tenant_ils
                ADD COLUMN opac_url text
                    CONSTRAINT tenant_ils_opac_url_check CHECK (opac_url ~* '^https?://')
        SQL);

        DB::unprepared(<<<'SQL'
            COMMENT ON COLUMN tenant_ils.opac_url IS
                'Where readers open the catalogue. Used only to link a record MARC.IA names; decides nothing about access (that is channel_bindings.allowed_origins).'
        SQL);
    }

    public function down(): void
    {
        DB::unprepared('ALTER TABLE tenant_ils DROP COLUMN opac_url');
    }
};
