<?php

use App\Filament\Library\Resources\Catalog\Pages\ListCatalog;
use App\Support\CatalogCsv;
use Filament\Actions\Testing\TestAction;
use Filament\Facades\Filament;
use Illuminate\Support\Facades\DB;

/*
 * The catalogue as a file.
 *
 * The screen searches the way MARC.IA searches, one query at a time. Counting
 * how much of a collection has no author, or which item types exist at all, is
 * a spreadsheet's job -- and the export is the only way this panel lets anyone
 * ask a question it did not anticipate.
 *
 * What has to be true is the same thing that has to be true of every other
 * screen here: one librarian, one library's shelves.
 */

beforeEach(function () {
    if (! discovery_db_is_reachable()) {
        test()->markTestSkipped(
            'no Discovery Index; run `docker compose up -d postgres` in libvox-sync '
            .'and `python -m libvox_sync migrate`'
        );
    }

    DB::connection('pgsql_admin')->statement('TRUNCATE tenants, users, model_has_roles, roles CASCADE');
    seed_roles();

    discovery()->statement('TRUNCATE discovery_biblio, discovery_tenant CASCADE');

    // The panel id, not the path it is served under: /app is `library`. A
    // resource page resolves its own routes and those exist only inside the
    // panel that discovered it.
    Filament::setCurrentPanel('library');
});

/** The bytes the download would send, captured without a response. */
function exported(string $tenantId): string
{
    ob_start();
    CatalogCsv::write($tenantId);

    return ob_get_clean();
}

test('the file carries the collection, one record to a line', function () {
    index('lib-furg', [
        ['biblio_id' => 1, 'title' => 'Dom Casmurro', 'author' => 'Machado de Assis'],
        ['biblio_id' => 2, 'title' => 'Grande sertão: veredas', 'author' => 'Guimarães Rosa'],
    ]);

    $csv = exported('lib-furg');
    $lines = array_values(array_filter(explode("\n", $csv)));

    expect($lines)->toHaveCount(3)
        ->and($lines[0])->toContain(__('portal.catalog.title'))
        ->and($csv)->toContain('Dom Casmurro')
        ->and($csv)->toContain('Grande sertão: veredas');
});

test('a librarian exports her own shelves and no one else s', function () {
    // The rule the whole panel rests on, asserted here too because this is the
    // one screen that hands the data over as a file. There is no tenant in the
    // query: row-level security decides, and this is what proves it still does
    // when the rows are being written to a socket.
    index('lib-furg', [['biblio_id' => 1, 'title' => 'Dom Casmurro']]);
    index('lib-ibict', [['biblio_id' => 2, 'title' => 'Grande sertão']]);

    $csv = exported('lib-furg');

    expect($csv)->toContain('Dom Casmurro')
        ->and($csv)->not->toContain('Grande sertão');
});

test('the two fields the reader never sees are in the file', function () {
    // Why it reads discovery_biblio rather than discovery_active. The view
    // omits abstract and notes on purpose -- they are indexed at the lowest
    // weight and never returned to the model -- and they are exactly where the
    // answer to "why did this book turn up?" lives.
    index('lib-furg', [[
        'biblio_id' => 1,
        'title' => 'Metapod',
        'abstract' => 'Metapod resembles a chrysalis.',
        'notes' => 'Based on butterfly chrysalises.',
    ]]);

    expect(exported('lib-furg'))
        ->toContain('Metapod resembles a chrysalis.')
        ->toContain('Based on butterfly chrysalises.');
});

test('a half-written import does not export twice', function () {
    // The join to discovery_tenant is what the view was doing for us. Without
    // it a generation nobody is being served yet lands in the file beside the
    // one that is, and every record appears once per generation.
    index('lib-furg', [['biblio_id' => 1, 'title' => 'Dom Casmurro']]);

    discovery()->insert(
        'INSERT INTO discovery_biblio (tenant_id, generation, biblio_id, title) '
        .'VALUES (?, 2, 1, ?)',
        ['lib-furg', 'Dom Casmurro — importação pela metade'],
    );

    $csv = exported('lib-furg');

    expect(substr_count($csv, 'Dom Casmurro'))->toBe(1)
        ->and($csv)->not->toContain('importação pela metade');
});

test('the file opens in a spreadsheet with its accents intact', function () {
    // A byte-order mark and semicolons, both for one reader: a librarian
    // double-clicking this in Excel with a Brazilian locale. Without the first
    // the accents arrive as mojibake; without the second every row lands in a
    // single column.
    index('lib-furg', [['biblio_id' => 1, 'title' => 'Memórias póstumas']]);

    $csv = exported('lib-furg');

    expect($csv)->toStartWith("\xEF\xBB\xBF")
        ->and($csv)->toContain('Memórias póstumas')
        ->and(explode("\n", $csv)[0])->toContain(';');
});

test('a quotation mark in a title survives the round trip', function () {
    // The catalogue has these: `"Macmillan""s Political Science"` was a real
    // record. `escape: ''` is what keeps fputcsv writing RFC 4180 instead of
    // PHP's own backslash dialect, which no spreadsheet reads back.
    index('lib-furg', [['biblio_id' => 1, 'title' => 'Orwellian Vision in "Literature"']]);

    $handle = fopen('php://memory', 'r+');
    fwrite($handle, exported('lib-furg'));
    rewind($handle);

    $rows = [];
    while (($row = fgetcsv($handle, separator: ';', escape: '')) !== false) {
        $rows[] = $row;
    }
    fclose($handle);

    expect($rows[1][1])->toBe('Orwellian Vision in "Literature"');
});

test('the button is on the screen, and refuses when there is nothing to export', function () {
    index('lib-furg', [['biblio_id' => 1, 'title' => 'Dom Casmurro']]);
    asLibrarianOf('lib-furg');

    // One account, made once: `librarian()` inserts rather than finds.
    $her = librarian('lib-furg');

    Livewire\Livewire::actingAs($her)
        ->test(ListCatalog::class)
        ->assertActionEnabled(TestAction::make('exportCsv'));

    // A collection that was never imported has no active generation, and a
    // file of column headings explains nothing to whoever downloads it.
    discovery()->statement('UPDATE discovery_tenant SET last_sync_at = NULL');

    Livewire\Livewire::actingAs($her)
        ->test(ListCatalog::class)
        ->assertActionDisabled(TestAction::make('exportCsv'));
});

test('the name of the file says which library and which day', function () {
    expect(CatalogCsv::filename('lib-furg'))
        ->toBe('acervo-lib-furg-'.now()->format('Y-m-d').'.csv');
});
