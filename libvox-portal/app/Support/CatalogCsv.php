<?php

namespace App\Support;

use App\Models\CatalogRecord;
use Illuminate\Support\Facades\DB;
use Symfony\Component\HttpFoundation\StreamedResponse;

/**
 * The whole catalogue as one CSV, written straight to the socket.
 *
 * **Never assembled in memory, and that is the whole design.** The screen this
 * hangs off already holds 8.774 records in one test collection and a municipal
 * library holds ten to a hundred times that; a `->get()` here is a request that
 * dies at the memory limit on exactly the libraries that most need the export.
 * So rows are read a page at a time by key — `biblio_id > last` rather than
 * OFFSET, which stays O(1) instead of degrading over half a million rows — and
 * each page is written and flushed before the next is asked for.
 *
 * **Read from `discovery_biblio`, not from `discovery_active`.** The view
 * deliberately omits `abstract` and `notes`: they are indexed at the lowest
 * weight and never returned to the model, so keeping them out of it keeps the
 * view honest as the MCP contract. A librarian exporting her catalogue to look
 * at it is not the model, and those two fields are exactly where the answer to
 * "why did this book turn up?" lives. The join to `discovery_tenant` on the
 * active generation is what the view was doing for us, and is not optional: a
 * half-written import would otherwise export twice.
 *
 * Row-level security does the scoping, here as everywhere else in this
 * application. There is no tenant in the WHERE clause, and a forgotten binding
 * exports an empty file rather than someone else's shelves.
 */
final class CatalogCsv
{
    /** Rows per round trip. Big enough to amortize, small enough to bound. */
    private const CHUNK = 1_000;

    /**
     * The columns, in the order they are written, each with the label the
     * screen already uses for it. No new translations: a librarian reading
     * this file in a spreadsheet should meet the same words she met in the
     * table she exported it from.
     */
    private const COLUMNS = [
        'biblio_id' => 'portal.catalog.biblio_id',
        'title' => 'portal.catalog.title',
        'author' => 'portal.catalog.author',
        'series' => 'portal.catalog.series',
        'publisher' => 'portal.catalog.publisher',
        'copyright_date' => 'portal.catalog.year',
        'isbn' => 'portal.catalog.isbn',
        'item_type' => 'portal.catalog.item_type',
        'abstract' => 'portal.catalog.abstract',
        'notes' => 'portal.catalog.notes',
        'ils_timestamp' => 'portal.catalog.changed',
    ];

    public static function filename(string $tenantId): string
    {
        return sprintf('acervo-%s-%s.csv', $tenantId, now()->format('Y-m-d'));
    }

    public static function download(string $tenantId): StreamedResponse
    {
        return response()->streamDownload(
            fn () => self::write($tenantId),
            self::filename($tenantId),
            ['Content-Type' => 'text/csv; charset=UTF-8'],
        );
    }

    /**
     * Write the file, one page at a time.
     *
     * Public so a test can capture it without going through a response, which
     * is the only way to assert on bytes that are streamed rather than
     * returned.
     */
    public static function write(string $tenantId): void
    {
        // Re-bound here rather than trusted from the request. This callback
        // runs while the response is being sent, after the action that made
        // it has returned, and the binding is a side effect on a connection
        // rather than something that travels with the call. Re-binding costs
        // one `set_config` and removes the question entirely -- the same move
        // `ViewConversation::booted()` makes for the same reason.
        CatalogRecord::scopeToTenant($tenantId);

        $out = fopen('php://output', 'w');

        // A byte-order mark, and semicolons rather than commas. Both are for
        // one reader: a librarian double-clicking this file in Excel with a
        // Brazilian locale, where without the first the accents arrive as
        // mojibake and without the second every row lands in a single column.
        // It costs a `sep=';'` to whoever reads it with pandas instead, which
        // is the trade this panel's audience decides.
        fwrite($out, "\xEF\xBB\xBF");

        self::row($out, array_map(__(...), array_values(self::COLUMNS)));

        $after = 0;

        while ($rows = self::page($after)) {
            foreach ($rows as $record) {
                self::row($out, array_map(
                    fn (string $column) => $record->{$column},
                    array_keys(self::COLUMNS),
                ));

                $after = $record->biblio_id;
            }

            // Out of PHP's buffer and onto the wire, so the browser starts
            // receiving a large catalogue instead of waiting for all of it.
            flush();
        }

        fclose($out);
    }

    /**
     * One line.
     *
     * `escape: ''` on purpose: PHP's historical default lets a backslash escape
     * the following character, which is not CSV, is not what any spreadsheet
     * reads back, and mangles a call number like `PZ7.S41267 Mat 1995\` on the
     * way out. Empty means RFC 4180 — quotes doubled inside quotes, nothing
     * else special.
     */
    private static function row($handle, array $fields): void
    {
        fputcsv($handle, $fields, separator: ';', escape: '');
    }

    /** @return array<int, object> */
    private static function page(int $after): array
    {
        return DB::connection('pgsql_discovery')->select(
            'SELECT b.biblio_id, b.title, b.author, b.series, b.publisher,
                    b.copyright_date, b.isbn, b.item_type, b.abstract, b.notes,
                    b.ils_timestamp
               FROM discovery_biblio b
               JOIN discovery_tenant t
                 ON t.tenant_id = b.tenant_id
                AND t.active_generation = b.generation
              WHERE b.biblio_id > ?
              ORDER BY b.biblio_id
              LIMIT '.self::CHUNK,
            [$after],
        );
    }
}
