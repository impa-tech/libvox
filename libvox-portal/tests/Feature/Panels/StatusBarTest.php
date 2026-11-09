<?php

use App\Enums\TenantStatus;
use App\Tenancy\TenantProvisioner;
use Illuminate\Support\Facades\DB;

/*
 * The bar under every librarian screen: her email, and the identifier of the
 * library the session is bound to.
 *
 * The identifier is the half worth testing. The tenant is deliberately absent
 * from the URL -- a tenant a browser can edit is a tenant an attacker can edit
 * -- so this is the only place on screen that says which library a page is
 * about, and it is the string every other LibVox component uses for her.
 */

beforeEach(function () {
    DB::connection('pgsql_admin')->statement('TRUNCATE tenants, users, model_has_roles, roles CASCADE');
    seed_roles();

    app(TenantProvisioner::class)->provision([
        'tenant_id' => 'lib-furg',
        'display_name' => 'Biblioteca da FURG',
        'status' => TenantStatus::Active->value,
    ]);
});

test('a librarian sees her email and her library on every screen', function (string $path) {
    $librarian = librarian('lib-furg');

    $this->actingAs($librarian)
        ->get($path)
        ->assertOk()
        ->assertSee($librarian->email)
        ->assertSee('lib-furg');
})->with(['/app', '/app/widget-settings', '/app/profile']);

test('the bar is absent before anyone signs in', function () {
    // FOOTER renders on the simple layout too, which is the login page. A view
    // that assumed a user would fatal there rather than render nothing.
    $this->get('/app/login')
        ->assertOk()
        ->assertDontSee('Signed in as')
        ->assertDontSee('Conectado como');
});

test('it names the library in the language she reads', function () {
    // The hyphenated tag, which is what the column stores and what the check
    // constraint allows -- `pt_BR` is Laravel's directory name and stops at
    // App\Support\Locale. Writing the wrong one here is the mistake that class
    // exists to catch, and it caught this test.
    $librarian = librarian('lib-furg');
    $librarian->update(['locale' => 'pt-BR']);

    $this->actingAs($librarian)->get('/app')->assertSee('Conectado como');

    $librarian->update(['locale' => 'en']);

    $this->actingAs($librarian)->get('/app')->assertSee('Signed in as');
});
