<?php

use App\Enums\TenantStatus;
use App\Tenancy\TenantProvisioner;
use Illuminate\Support\Facades\DB;

/*
 * The offer of source at the foot of both panels.
 *
 * LibVox is AGPL-3.0, and §13 asks a modified version offered to people over a
 * network to give them a way to obtain the corresponding source. A librarian
 * working in this panel is one of those people, so this is a licence term with
 * a rendering, and the kind that regresses silently: nothing breaks when the
 * link stops appearing.
 *
 * The URL is configuration rather than a constant because it has to be. An
 * installation that patched a file owes its users *its* source, and a hardcoded
 * link would send them to code nobody is running.
 */

beforeEach(function () {
    DB::connection('pgsql_admin')->statement('TRUNCATE tenants, users, model_has_roles, roles CASCADE');
    seed_roles();

    app(TenantProvisioner::class)->provision([
        'tenant_id' => 'lib-furg',
        'display_name' => 'Biblioteca da FURG',
        'status' => TenantStatus::Active->value,
    ]);

    config(['libvox.source_url' => 'https://forge.exemplo/libvox']);
});

test('every librarian screen offers the source', function (string $path) {
    $this->actingAs(librarian('lib-furg'))
        ->get($path)
        ->assertOk()
        ->assertSee('https://forge.exemplo/libvox');
})->with(['/app', '/app/widget-settings', '/app/profile']);

test('an operator is offered it too', function () {
    $this->actingAs(operator())
        ->get('/admin')
        ->assertOk()
        ->assertSee('https://forge.exemplo/libvox');
});

test('so is someone who has not signed in', function () {
    // FOOTER renders on the simple layout as well, which is the login page --
    // and that is the right answer rather than an accident: §13 is about people
    // the software is offered to, and nobody has to authenticate to be one.
    $this->get('/app/login')
        ->assertOk()
        ->assertSee('https://forge.exemplo/libvox');
});

test('emptying it removes the offer entirely', function () {
    // The deployment holding a commercial licence, which is not under §13 and
    // says so deliberately. Asserted because the obvious implementation --
    // falling back with `??` or `or` -- would hand this case back the link it
    // had just deleted.
    config(['libvox.source_url' => '']);

    $this->actingAs(librarian('lib-furg'))
        ->get('/app')
        ->assertOk()
        ->assertDontSee('AGPL');
});
