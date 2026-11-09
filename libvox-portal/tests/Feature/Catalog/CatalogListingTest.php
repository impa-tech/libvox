<?php

use App\Database\TenantScope;
use App\Filament\Library\Resources\Catalog\CatalogResource;
use App\Filament\Library\Resources\Catalog\Pages\ListCatalog;
use App\Models\CatalogRecord;
use App\Models\Tenant;
use Filament\Actions\Testing\TestAction;
use Filament\Facades\Filament;
use Illuminate\Database\Connection;
use Illuminate\Support\Facades\DB;

/*
 * The catalogue screen reads a database this application does not own.
 *
 * Which makes two things worth asserting that are assumed everywhere else: that
 * the connection is unprivileged, so the policy on discovery_biblio actually
 * applies, and that a librarian sees her library's shelves and no other's.
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
});

/** Put records in one library's live generation, as the owner. */
function index(string $tenantId, array $rows): void
{
    Tenant::firstOrCreate(['tenant_id' => $tenantId], ['display_name' => $tenantId]);

    discovery()->insert(
        'INSERT INTO discovery_tenant (tenant_id, active_generation, index_status, last_sync_at) '
        ."VALUES (?, 1, 'ready', now())",
        [$tenantId],
    );

    foreach ($rows as $row) {
        discovery()->insert(
            'INSERT INTO discovery_biblio '
            .'  (tenant_id, generation, biblio_id, title, author, series, abstract, notes) '
            .'VALUES (?, 1, ?, ?, ?, ?, ?, ?)',
            [
                $tenantId, $row['biblio_id'], $row['title'],
                $row['author'] ?? null, $row['series'] ?? null,
                $row['abstract'] ?? null, $row['notes'] ?? null,
            ],
        );
    }
}

function asLibrarianOf(string $tenantId): void
{
    CatalogRecord::scopeToTenant($tenantId);
}

test('a librarian sees her own shelves and no one else s', function () {
    index('lib-furg', [['biblio_id' => 1, 'title' => 'Dom Casmurro', 'author' => 'Machado']]);
    index('lib-ibict', [['biblio_id' => 2, 'title' => 'Grande sertão']]);

    asLibrarianOf('lib-furg');

    $titles = CatalogRecord::query()->pluck('title')->all();

    expect($titles)->toBe(['Dom Casmurro']);
});

test('without a binding the catalogue reads empty, never another library s', function () {
    // The fail-closed half of the same rule. Forgetting to scope is a screen
    // with nothing on it, which reads as a bug and is therefore noticed --
    // unlike the alternative.
    index('lib-furg', [['biblio_id' => 1, 'title' => 'Dom Casmurro']]);

    DB::connection('pgsql_discovery')->select("select set_config('libvox.tenant_id', '', false)");

    expect(CatalogRecord::query()->count())->toBe(0);
});

test('the connection cannot bypass row level security', function () {
    // Asserted rather than assumed: a privileged role here shows every
    // library's catalogue to every librarian, and the screen looks correct
    // while doing it.
    $role = DB::connection('pgsql_discovery')->selectOne(
        'SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user'
    );

    expect($role->rolsuper)->toBeFalse()
        ->and($role->rolbypassrls)->toBeFalse();
});

test('search finds what the assistant finds, accents and series included', function () {
    // The reason the screen exists: a librarian asking "why did she not find
    // my book?" should get the same answer here as a reader gets there.
    index('lib-furg', [
        ['biblio_id' => 16, 'title' => 'Metapod', 'series' => 'Pokémon - Generation I'],
        ['biblio_id' => 3, 'title' => 'Grande sertão: veredas'],
    ]);

    asLibrarianOf('lib-furg');

    $found = fn (string $term) => CatalogRecord::query()
        ->whereRaw("search @@ plainto_tsquery('libvox_search', ?)", [$term])
        ->pluck('title')
        ->all();

    expect($found('pokemon'))->toBe(['Metapod'])      // series, and unaccented
        ->and($found('sertao'))->toBe(['Grande sertão: veredas']);
});

test('the index is a read model, and refuses to be edited', function () {
    index('lib-furg', [['biblio_id' => 1, 'title' => 'Dom Casmurro']]);
    asLibrarianOf('lib-furg');

    $record = CatalogRecord::query()->first();
    $record->title = 'Outro título';

    expect(fn () => $record->save())->toThrow(LogicException::class)
        ->and(fn () => $record->delete())->toThrow(LogicException::class);
});

test('the screen says whether the index is current', function () {
    // An empty result against a building index and against a ready one are
    // opposite answers, and the table alone cannot tell them apart.
    index('lib-furg', [['biblio_id' => 1, 'title' => 'Dom Casmurro']]);

    // A resource page resolves its own routes, and those exist only inside the
    // panel that discovered it.
    Filament::setCurrentPanel('library');

    $page = Livewire\Livewire::actingAs(librarian('lib-furg'))
        ->test(ListCatalog::class);

    $page->assertOk()->assertSee('Dom Casmurro');

    expect($page->instance()->getSubheading())->toContain(__('portal.catalog.status.ready'));
});

test('the resource offers nothing that would write to the index', function () {
    expect(CatalogResource::canCreate())->toBeFalse();
});

test('the refresh button is offered, and refused when there is nothing to import', function () {
    // A button that starts hundreds of requests against a library's own server
    // should not be pressable when the answer is already known.
    Tenant::firstOrCreate(['tenant_id' => 'lib-furg'], ['display_name' => 'lib-furg']);

    Filament::setCurrentPanel('library');

    // Bound here rather than through a helper in another test file: Pest makes
    // those global once that file is loaded, so borrowing one works in a full
    // run and fails when this directory is run on its own.
    $asLibrarian = fn (Closure $work) => app(TenantScope::class)->run('lib-furg', $work);

    // Created once. `librarian()` uses a fixed email address, so calling it
    // twice in one test violates the unique constraint and aborts the
    // transaction the scope is holding -- which surfaces, unhelpfully, as a
    // failure on the release at the end.
    $user = librarian('lib-furg');

    $asLibrarian(fn () => Livewire\Livewire::actingAs($user)
        ->test(ListCatalog::class)
        ->assertActionExists('requestSync')
        ->assertActionDisabled('requestSync'));

    // With a catalogue connection, it becomes pressable.
    DB::connection('pgsql_admin')->update(
        'UPDATE tenants SET ils_configured_at = now() WHERE tenant_id = ?', ['lib-furg']
    );

    $asLibrarian(fn () => Livewire\Livewire::actingAs($user)
        ->test(ListCatalog::class)
        ->assertActionEnabled('requestSync'));
});

test('a first import that failed does not lock the button that would retry it', function () {
    // The deadlock this replaced. A first import has no previous generation to
    // fall back to, so the pipeline leaves index_status at 'building' -- which
    // is the honest thing to tell MARC.IA about an index that was never
    // published, and was being read here as "an import is running". The only
    // button that could fix it disabled itself, permanently.
    Tenant::firstOrCreate(['tenant_id' => 'lib-furg'], ['display_name' => 'lib-furg']);
    DB::connection('pgsql_admin')->update(
        'UPDATE tenants SET ils_configured_at = now() WHERE tenant_id = ?', ['lib-furg']
    );

    discovery()->insert(
        'INSERT INTO discovery_tenant (tenant_id, active_generation, index_status, last_error) '
        ."VALUES (?, NULL, 'building', ?)",
        ['lib-furg', 'cannot reach http://koha.exemplo: timed out'],
    );

    Filament::setCurrentPanel('library');

    app(TenantScope::class)->run('lib-furg', fn () => Livewire\Livewire::actingAs(librarian('lib-furg'))
        ->test(ListCatalog::class)
        ->assertActionEnabled('requestSync'));
});

test('an import actually under way does disable it', function () {
    // The distinction is last_error: the pipeline writes one on every failure,
    // so `building` without one is genuinely in progress.
    Tenant::firstOrCreate(['tenant_id' => 'lib-furg'], ['display_name' => 'lib-furg']);
    DB::connection('pgsql_admin')->update(
        'UPDATE tenants SET ils_configured_at = now() WHERE tenant_id = ?', ['lib-furg']
    );

    discovery()->insert(
        'INSERT INTO discovery_tenant (tenant_id, active_generation, index_status) '
        ."VALUES (?, NULL, 'building')",
        ['lib-furg'],
    );

    Filament::setCurrentPanel('library');

    app(TenantScope::class)->run('lib-furg', fn () => Livewire\Livewire::actingAs(librarian('lib-furg'))
        ->test(ListCatalog::class)
        ->assertActionDisabled('requestSync'));
});

test('the confirmation says what went wrong last time, and what the server said', function () {
    // She is about to send hundreds of requests to a server that failed two
    // minutes ago, and the dialog said only that imports are expensive. Not a
    // cooling-off window: blocking would punish exactly the person who just
    // fixed the credential.
    Tenant::firstOrCreate(['tenant_id' => 'lib-furg'], ['display_name' => 'lib-furg']);
    DB::connection('pgsql_admin')->update(
        'UPDATE tenants SET ils_configured_at = now() WHERE tenant_id = ?', ['lib-furg']
    );
    discovery()->insert(
        'INSERT INTO discovery_tenant (tenant_id, active_generation, index_status, last_sync_at, last_error) '
        ."VALUES (?, 2, 'stale', now(), ?)",
        ['lib-furg', 'Koha returned HTTP 403 on /api/v1/biblios'],
    );

    Filament::setCurrentPanel('library');
    $user = librarian('lib-furg');

    // Asserted on the dialog's own text, not on the page: the subheading
    // already prints last_error, so assertSee('403') would have passed without
    // the dialog saying anything at all.
    $text = app(TenantScope::class)->run('lib-furg', fn () => Livewire\Livewire::actingAs($user)
        ->test(ListCatalog::class)
        ->instance()
        ->confirmationText());

    expect($text)->toContain('403')
        ->and($text)->not->toBe(__('portal.catalog.request_confirm'));
});

test('a library that never failed is asked the plain question', function () {
    Tenant::firstOrCreate(['tenant_id' => 'lib-furg'], ['display_name' => 'lib-furg']);
    DB::connection('pgsql_admin')->update(
        'UPDATE tenants SET ils_configured_at = now() WHERE tenant_id = ?', ['lib-furg']
    );
    discovery()->insert(
        'INSERT INTO discovery_tenant (tenant_id, active_generation, index_status, last_sync_at) '
        .'VALUES (?, 2, \'ready\', now())',
        ['lib-furg'],
    );

    Filament::setCurrentPanel('library');
    $user = librarian('lib-furg');

    $text = app(TenantScope::class)->run('lib-furg', fn () => Livewire\Livewire::actingAs($user)
        ->test(ListCatalog::class)
        ->instance()
        ->confirmationText());

    expect($text)->toBe(__('portal.catalog.request_confirm'));
});

test('the record view carries the two fields the search uses and a reader never sees', function () {
    // The point of the modal. `abstract` and `notes` are indexed at weight D
    // and deliberately absent from `discovery_active`, which migration 004
    // shaped as the MCP contract -- so a record that matched on a word
    // appearing only in its abstract is inexplicable from the listing alone.
    // A librarian is not the model, and this is the screen where she finds out.
    index('lib-furg', [[
        'biblio_id' => 1,
        'title' => 'Dom Casmurro',
        'abstract' => 'Bentinho suspeita de Capitu.',
        'notes' => 'Exemplar doado, capa dura.',
    ]]);

    asLibrarianOf('lib-furg');

    $text = CatalogRecord::query()->findOrFail(1)->indexedText();

    expect($text['abstract'])->toBe('Bentinho suspeita de Capitu.')
        ->and($text['notes'])->toBe('Exemplar doado, capa dura.');
});

test('a record with neither says so rather than failing', function () {
    // Most records have neither: 39 of 436 carried an abstract on the catalogue
    // this was written against. Absent is the ordinary case, not the error one.
    index('lib-furg', [['biblio_id' => 1, 'title' => 'Dom Casmurro']]);

    asLibrarianOf('lib-furg');

    expect(CatalogRecord::query()->findOrFail(1)->indexedText())
        ->toBe(['abstract' => null, 'notes' => null]);
});

test('the hidden text is read from the live generation, never a half-written one', function () {
    // Without the generation join this would answer with text from an import
    // nobody is being served yet -- the same trap `discovery_active` exists to
    // close for every other column.
    index('lib-furg', [[
        'biblio_id' => 1, 'title' => 'Dom Casmurro', 'abstract' => 'A que está no ar.',
    ]]);

    // A second generation being written now, with different text for the same
    // record. `index()` promoted generation 1; this one is not active.
    discovery()->insert(
        'INSERT INTO discovery_biblio '
        .'  (tenant_id, generation, biblio_id, title, abstract) VALUES (?, 2, 1, ?, ?)',
        ['lib-furg', 'Dom Casmurro', 'A meio caminho.'],
    );

    asLibrarianOf('lib-furg');

    expect(CatalogRecord::query()->findOrFail(1)->indexedText()['abstract'])
        ->toBe('A que está no ar.');
});

test('one library cannot read another library s hidden text', function () {
    index('lib-furg', [['biblio_id' => 1, 'title' => 'Meu', 'abstract' => 'meu resumo']]);
    index('lib-ibict', [['biblio_id' => 2, 'title' => 'Dela', 'abstract' => 'resumo dela']]);

    asLibrarianOf('lib-furg');

    // Reached through the table rather than the view, so the policy on
    // discovery_biblio is what has to hold -- and it is a different policy from
    // the one the listing exercises.
    $mine = CatalogRecord::query()->findOrFail(1);
    $mine->biblio_id = 2;

    expect($mine->indexedText())->toBe(['abstract' => null, 'notes' => null]);
});

test('the record view opens without throwing', function () {
    // What is observable here, and what is not.
    //
    // The schema is an array built when the table is configured, so a component
    // named wrong throws on the *listing* -- which the render sweep in
    // Panels/EveryScreenRendersTest already opens. Mounting is the half that
    // sweep cannot reach, and this is it.
    //
    // The modal's *contents* are deliberately not asserted: Filament v5 renders
    // modals client-side, and `fi-modal` appears nowhere in a Livewire
    // component test's HTML -- checked directly. An assertSee here would either
    // fail forever or, worse, pass on text the listing happens to show anyway.
    // What the modal puts on screen is asserted through the model instead, in
    // the three tests above.
    index('lib-furg', [[
        'biblio_id' => 7,
        'title' => 'Viridiana',
        'abstract' => 'Uma noviça visita o tio antes dos votos.',
    ]]);

    Filament::setCurrentPanel('library');

    app(TenantScope::class)->run('lib-furg', function () {
        Livewire\Livewire::actingAs(librarian('lib-furg'))
            ->test(ListCatalog::class)
            ->mountAction(TestAction::make('view')->table(7))
            ->assertActionMounted(TestAction::make('view')->table(7))
            ->assertHasNoActionErrors();
    });
});
