<?php

use App\Database\TenantScope;
use App\Filament\Library\Pages\CatalogConnection;
use App\Ils\IlsConnectionCheck;
use App\Models\Tenant;
use Filament\Facades\Filament;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Http;

/*
 * Asking /libvox-core whether a library's catalogue connection works.
 *
 * This application cannot answer it -- no read path to the secret, held by two
 * tests -- so what is worth asserting here is that it asks correctly, and that
 * it tells LibVox being down apart from the library's server being down.
 */

function checker(): IlsConnectionCheck
{
    return new IlsConnectionCheck('http://core.test', 'a-service-token');
}

test('it asks core about one library, with the service token', function () {
    Http::fake(['core.test/*' => Http::response(['ok' => true, 'records_seen' => 1])]);

    checker()->run('lib-furg');

    Http::assertSent(fn ($request) => $request->url() === 'http://core.test/internal/v1/ils/check'
        && $request['tenant_id'] === 'lib-furg'
        && $request->hasHeader('x-libvox-service-token', 'a-service-token'));
});

test('a working connection comes back whole', function () {
    Http::fake(['core.test/*' => Http::response([
        'ok' => true, 'problem' => null, 'detail' => null, 'records_seen' => 1,
    ])]);

    expect(checker()->run('lib-furg'))
        ->toBe(['ok' => true, 'problem' => null, 'detail' => null, 'records_seen' => 1]);
});

test('the reason a handshake failed is carried through, not flattened', function () {
    // The whole value of the button: unreachable, rejected and unpermitted have
    // three different fixes, and "it did not work" sends someone to check all
    // three.
    Http::fake(['core.test/*' => Http::response([
        'ok' => false,
        'problem' => 'permission_denied',
        'detail' => 'Koha returned HTTP 403 on /api/v1/biblios',
        'records_seen' => 0,
    ])]);

    $result = checker()->run('lib-furg');

    expect($result['problem'])->toBe('permission_denied')
        ->and($result['detail'])->toContain('403');
});

test('libvox being unreachable is not reported as the librarys server failing', function () {
    // Otherwise a librarian goes and checks a Koha that was fine all along.
    Http::fake(['core.test/*' => fn () => throw new ConnectionException('refused')]);

    expect(checker()->run('lib-furg')['problem'])->toBe('core_unreachable');
});

test('core refusing the call is not reported as the librarys server failing either', function () {
    Http::fake(['core.test/*' => Http::response(['detail' => 'bad service token'], 401)]);

    expect(checker()->run('lib-furg')['problem'])->toBe('core_unreachable');
});

test('the page offers the test, and shows what came back', function () {
    DB::connection('pgsql_admin')->statement('TRUNCATE tenants, users, model_has_roles, roles CASCADE');
    seed_roles();
    Tenant::create(['tenant_id' => 'lib-furg', 'display_name' => 'lib-furg']);

    Http::fake(['*' => Http::response([
        'ok' => false,
        'problem' => 'permission_denied',
        'detail' => 'Koha returned HTTP 403 on /api/v1/biblios',
        'records_seen' => 0,
    ])]);

    Filament::setCurrentPanel('library');

    app(TenantScope::class)->run('lib-furg', function () {
        $page = Livewire\Livewire::actingAs(librarian('lib-furg'))
            ->test(CatalogConnection::class)
            ->call('testConnection');

        $page->assertOk();

        // The message is chosen by the driver's own error code, so the three
        // failures do not collapse into one sentence on the way to the modal.
        expect($page->instance()->checkMessage())
            ->toBe(__('portal.ils.check.permission_denied'));

        // And the detail reaches her: it names her own server and the status
        // it returned, and she is the only person who can act on either.
        expect($page->instance()->check['detail'])->toContain('403');
    });
});
