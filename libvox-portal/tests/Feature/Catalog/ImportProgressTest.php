<?php

use App\Database\TenantScope;
use App\Filament\Library\Pages\CatalogImport;
use App\Models\Tenant;
use Filament\Facades\Filament;
use Illuminate\Support\Facades\DB;

/*
 * The screen that shows an import happening.
 *
 * Five states, four of which have already confused somebody: queued, nobody
 * answering, importing, finished, failed. What is asserted here is that they
 * are told apart, because collapsing them into "in progress" is the bug this
 * page exists to fix.
 */

beforeEach(function () {
    if (! discovery_db_is_reachable()) {
        test()->markTestSkipped('no Discovery Index; run /libvox-sync migrations');
    }

    DB::connection('pgsql_admin')->statement('TRUNCATE tenants, users, model_has_roles, roles CASCADE');
    DB::connection('pgsql_admin')->statement('DELETE FROM tenant_events');
    seed_roles();
    discovery()->statement('TRUNCATE discovery_biblio, discovery_tenant CASCADE');

    Tenant::create(['tenant_id' => 'lib-furg', 'display_name' => 'lib-furg']);
    Filament::setCurrentPanel('library');

    // Made here, once per test, because `librarian()` uses a fixed email
    // address: a second call within one test violates a unique constraint and
    // aborts the transaction the tenant scope is holding, which surfaces as a
    // failure on the release at the end and names neither the insert nor the
    // email.
    $GLOBALS['librarian'] = librarian('lib-furg');
});

/** The index's own state, as /libvox-sync would have left it. */
function indexState(?int $active, string $status, ?string $error = null, ?string $started = null): void
{
    discovery()->insert(
        'INSERT INTO discovery_tenant (tenant_id, active_generation, index_status, last_error, import_started_at) '
        .'VALUES (?, ?, ?, ?, ?)',
        ['lib-furg', $active, $status, $error, $started],
    );
}

/** Rows in one generation, which is what the count reads. */
function rowsIn(int $generation, int $count): void
{
    for ($i = 1; $i <= $count; $i++) {
        discovery()->insert(
            'INSERT INTO discovery_biblio (tenant_id, generation, biblio_id, title) VALUES (?, ?, ?, ?)',
            ['lib-furg', $generation, $generation * 100_000 + $i, "Livro {$i}"],
        );
    }
}

function queueRequest(int $secondsAgo = 0): void
{
    DB::connection('pgsql_admin')->insert(
        "INSERT INTO tenant_events (tenant_id, event, occurred_at) VALUES (?, 'SyncRequested', ?)",
        ['lib-furg', now()->subSeconds($secondsAgo)],
    );
}

function theProgressPage(): CatalogImport
{
    return app(TenantScope::class)->run('lib-furg', fn () => Livewire\Livewire::actingAs($GLOBALS['librarian'])
        ->test(CatalogImport::class)
        ->instance());
}

test('a request just made says it is queued, not that nobody is answering', function () {
    queueRequest();

    expect(theProgressPage()->progress()['state'])->toBe('queued');
});

test('an import in flight beats a request still queued behind it', function () {
    // The bug this replaced, seen in the panel while an import was running:
    // "Ninguém parece estar atendendo" over a live import. Both are true at
    // once whenever the button is pressed twice -- the first press is claimed
    // and starts writing, the second sits pending until the first finishes.
    // The queue was being read first, so the pending request won.
    //
    // Measured from the log that produced this: 50 and 91 seconds behind an
    // import of the same library.
    indexState(active: 2, status: 'building');
    rowsIn(2, 40);
    rowsIn(3, 18);
    queueRequest(secondsAgo: 91);

    $progress = theProgressPage()->progress();

    expect($progress['state'])->toBe('importing')
        ->and($progress['imported'])->toBe(18);
});

test('an import that has not written its first record yet is not reported as finished', function () {
    // The gap this screen fell into, and the reason migration 007 exists.
    //
    // The worker claims the request the instant it is made -- so nothing is
    // pending any more -- and writes its first batch a page later, so no row of
    // the new generation exists either. Everything the page could see said the
    // previous import, finished: it showed 1.284 registros with a green tick,
    // stopped polling, and the librarian who had just pressed the button
    // refreshed until it moved.
    indexState(active: 4, status: 'ready', started: (string) now());
    rowsIn(4, 462);

    $page = theProgressPage();

    expect($page->progress()['state'])->toBe('importing')
        ->and($page->progress()['imported'])->toBe(0)
        ->and($page->progress()['previous'])->toBe(462)
        ->and($page->watching())->toBeTrue();
});

test('an import running after an earlier failure is reported as running, not as the failure', function () {
    // last_error is whatever the *previous* attempt left behind; the pipeline
    // clears it only on publishing. A retry that is under way must not be
    // reported as the failure it is trying to fix.
    indexState(active: null, status: 'building', error: 'cannot reach http://koha.exemplo: timed out', started: (string) now());

    expect(theProgressPage()->progress()['state'])->toBe('importing');
});

test('a request nothing has taken in ten seconds says so, without blaming the service', function () {
    // The state this page was built after. The worker wakes on NOTIFY within
    // milliseconds and, failing that, on its own 5-second backstop poll -- so
    // past ten seconds there is no worker, and saying "queued" forever is what
    // happened before.
    queueRequest(secondsAgo: 15);

    // 'unclaimed', not 'unanswered': from here a service that is down cannot be
    // told apart from one busy with another library, because row-level
    // security means this page sees one library's index and no other's.
    expect(theProgressPage()->progress()['state'])->toBe('unclaimed');
});

test('an import in flight reports how many records have landed', function () {
    indexState(active: 2, status: 'building');
    rowsIn(2, 40);   // the previous import, still serving
    rowsIn(3, 12);   // the one being written

    $progress = theProgressPage()->progress();

    expect($progress['state'])->toBe('importing')
        ->and($progress['imported'])->toBe(12)
        ->and($progress['previous'])->toBe(40);
});

test('the percentage is measured against the previous import', function () {
    indexState(active: 2, status: 'building');
    rowsIn(2, 40);
    rowsIn(3, 10);

    expect(theProgressPage()->percentage())->toBe(25);
});

test('there is no percentage when there is no previous import to compare against', function () {
    // A library's first import has nothing behind it, and Koha returns no
    // total -- no X-Total-Count, no Link header. Inventing a denominator would
    // be inventing the answer.
    indexState(active: null, status: 'building');
    rowsIn(1, 30);

    $page = theProgressPage();

    expect($page->progress()['state'])->toBe('importing')
        ->and($page->progress()['imported'])->toBe(30)
        ->and($page->percentage())->toBeNull();
});

test('a catalogue that grew passes one hundred percent, and is not hidden', function () {
    // A bar frozen at 99% while records keep arriving is a bar that lies to
    // look tidy.
    indexState(active: 2, status: 'building');
    rowsIn(2, 10);
    rowsIn(3, 15);

    expect(theProgressPage()->percentage())->toBe(150);
});

test('a failed import shows what the server said, not a bar', function () {
    // `building` with an error recorded is an import that stopped: a first
    // import has no previous generation to fall back to, so the pipeline
    // leaves the status alone.
    indexState(active: null, status: 'building', error: 'cannot reach http://koha.exemplo: timed out');

    $progress = theProgressPage()->progress();

    expect($progress['state'])->toBe('failed')
        ->and($progress['error'])->toContain('timed out');
});

test('a finished import reports the catalogue it produced', function () {
    indexState(active: 4, status: 'ready');
    rowsIn(4, 462);

    $progress = theProgressPage()->progress();

    expect($progress['state'])->toBe('done')
        ->and($progress['previous'])->toBe(462);
});

test('the page stops polling once there is nothing left to watch', function () {
    // A tab left open overnight should not redraw the same number 3,600 times
    // an hour.
    indexState(active: 4, status: 'ready');
    rowsIn(4, 5);

    expect(theProgressPage()->watching())->toBeFalse();

    discovery()->statement('TRUNCATE discovery_biblio, discovery_tenant CASCADE');
    indexState(active: 4, status: 'building');
    rowsIn(4, 5);
    rowsIn(5, 2);

    expect(theProgressPage()->watching())->toBeTrue();
});

test('one library cannot watch another library s import', function () {
    Tenant::create(['tenant_id' => 'lib-ibict', 'display_name' => 'lib-ibict']);

    // Another library, mid-import, with records of its own.
    discovery()->insert(
        "INSERT INTO discovery_tenant (tenant_id, active_generation, index_status) VALUES (?, 1, 'building')",
        ['lib-ibict'],
    );
    discovery()->insert(
        'INSERT INTO discovery_biblio (tenant_id, generation, biblio_id, title) VALUES (?, 2, 1, ?)',
        ['lib-ibict', 'Não é meu'],
    );

    indexState(active: 1, status: 'ready');
    rowsIn(1, 3);

    $progress = theProgressPage()->progress();

    expect($progress['state'])->toBe('done')
        ->and($progress['previous'])->toBe(3)
        ->and($progress['imported'])->toBe(0);
});
