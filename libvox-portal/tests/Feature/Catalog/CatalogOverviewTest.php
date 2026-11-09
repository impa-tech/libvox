<?php

use App\Database\TenantScope;
use App\Filament\Library\Widgets\CatalogOverview;
use App\Models\Tenant;
use Filament\Facades\Filament;
use Illuminate\Support\Facades\DB;

/*
 * The card on the library's landing page.
 *
 * Two numbers, and one property that outranks both: it must not take the
 * dashboard down. The Discovery Index is a second database that /libvox-sync
 * owns and this application only reads, and a librarian whose first screen is
 * a stack trace has lost the panel, not a card.
 */

beforeEach(function () {
    if (! discovery_db_is_reachable()) {
        test()->markTestSkipped('no Discovery Index; run /libvox-sync migrations');
    }

    DB::connection('pgsql_admin')->statement('TRUNCATE tenants, users, model_has_roles, roles CASCADE');
    seed_roles();
    discovery()->statement('TRUNCATE discovery_biblio, discovery_tenant CASCADE');

    Tenant::create(['tenant_id' => 'lib-furg', 'display_name' => 'lib-furg']);
    Filament::setCurrentPanel('library');
    $GLOBALS['librarian'] = librarian('lib-furg');
});

/** The stats the widget would render, as plain text. */
function overviewText(): string
{
    return app(TenantScope::class)->run('lib-furg', function () {
        $rendered = Livewire\Livewire::actingAs($GLOBALS['librarian'])
            ->test(CatalogOverview::class)
            ->html();

        return trim(preg_replace('/\s+/', ' ', strip_tags($rendered)));
    });
}

function indexed(?int $generation, string $status, int $records, ?string $error = null): void
{
    discovery()->insert(
        'INSERT INTO discovery_tenant (tenant_id, active_generation, index_status, last_sync_at, last_error) '
        .'VALUES (?, ?, ?, now(), ?)',
        ['lib-furg', $generation, $status, $error],
    );

    for ($i = 1; $i <= $records; $i++) {
        discovery()->insert(
            'INSERT INTO discovery_biblio (tenant_id, generation, biblio_id, title) VALUES (?, ?, ?, ?)',
            ['lib-furg', $generation ?? 1, $i, "Livro {$i}"],
        );
    }
}

test('it shows how many records the assistant can find', function () {
    indexed(generation: 3, status: 'ready', records: 42);

    expect(overviewText())->toContain('42');
});

test('it shows when the catalogue was last read from the ILS', function () {
    indexed(generation: 3, status: 'ready', records: 5);

    expect(overviewText())
        ->toContain(__('portal.overview.last_import'))
        ->toContain(__('portal.overview.current'));
});

test('a stale index says the last attempt failed, beside the date', function () {
    // "ontem" over a stale index means the last attempt was yesterday and it
    // failed, which reads nothing like "ontem" over a current one. The date
    // alone would be true and misleading.
    indexed(generation: 3, status: 'stale', records: 42, error: 'cannot reach koha: timed out');

    expect(overviewText())->toContain(__('portal.overview.last_attempt_failed'));
});

test('a library that has never imported is told that, not shown a zero', function () {
    // An empty catalogue is an answer; this is the absence of one.
    indexed(generation: null, status: 'building', records: 0);

    expect(overviewText())->toContain(__('portal.overview.never_imported'));
});

test('a library with no row at all is treated as never imported', function () {
    expect(overviewText())->toContain(__('portal.overview.never_imported'));
});

test('the dashboard survives a Discovery Index it cannot reach', function () {
    // The property that outranks the numbers. This is the landing page: a
    // second database being down must cost this card and not the panel.
    config(['database.connections.pgsql_discovery.url' => 'postgresql://nobody:nobody@127.0.0.1:1/nothing']);
    DB::purge('pgsql_discovery');

    $text = overviewText();

    expect($text)->toContain(__('portal.overview.unreachable'))
        ->and($text)->not->toContain('SQLSTATE');
});

test('one library never sees another library s count', function () {
    indexed(generation: 3, status: 'ready', records: 7);

    Tenant::create(['tenant_id' => 'lib-ibict', 'display_name' => 'lib-ibict']);
    discovery()->insert(
        "INSERT INTO discovery_tenant (tenant_id, active_generation, index_status) VALUES (?, 9, 'ready')",
        ['lib-ibict'],
    );
    for ($i = 1; $i <= 500; $i++) {
        discovery()->insert(
            'INSERT INTO discovery_biblio (tenant_id, generation, biblio_id, title) VALUES (?, 9, ?, ?)',
            ['lib-ibict', $i, "Não é meu {$i}"],
        );
    }

    expect(overviewText())->toContain('7')->not->toContain('500');
});
