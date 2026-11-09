<?php

use App\Database\TenantScope;
use App\Enums\ChannelType;
use App\Enums\IlsType;
use App\Enums\TenantStatus;
use App\Ils\IlsConnection;
use App\Ils\IlsCredentialStore;
use App\Models\ChannelBinding;
use App\Tenancy\TenantProvisioner;
use Filament\Facades\Filament;
use Illuminate\Support\Facades\DB;

/*
 * Every screen in both panels, rendered against a library that has everything
 * filled in.
 *
 * This exists because three bugs in a row reached a user through a screen no
 * test had ever rendered with real data, and the third slipped past a render
 * test that used the empty case: a column formatting an array of origins was
 * never exercised, because the library in the fixture had no origins.
 *
 * So the fixture here is deliberately *full* -- credentials stored, origins
 * listed, every channel bound, statuses set -- and the assertion is only that
 * the page comes back 200. A screen that throws is a bug regardless of what it
 * was supposed to show, and this is the cheapest possible net under that.
 */

beforeEach(function () {
    DB::connection('pgsql_admin')->statement('TRUNCATE tenants, users, model_has_roles, roles CASCADE');
    seed_roles();

    app(TenantProvisioner::class)->provision([
        'tenant_id' => 'lib-furg',
        'display_name' => 'Biblioteca da FURG',
        'status' => TenantStatus::Active->value,
        'contact_email' => 'biblioteca@furg.br',
    ]);

    app(IlsCredentialStore::class)->put(
        new IlsConnection('lib-furg', IlsType::Koha, 'http://127.0.0.1:8081', 'um-client-id'),
        'um-segredo',
    );

    app(TenantScope::class)->run('lib-furg', function () {
        // Enabled here rather than by provisioning: a web channel is born off,
        // and this fixture is deliberately the full case.
        ChannelBinding::query()->where('channel', ChannelType::Web)->update([
            'allowed_origins' => ['https://biblioteca.furg.br', 'https://catalogo.furg.br'],
            'enabled' => true,
        ]);

        // A channel with a secret, so the shape that differs from the widget's
        // is rendered too.
        DB::connection('pgsql')->insert(
            "INSERT INTO channel_bindings (tenant_id, channel, binding_key, secret_enc, enabled)
             VALUES ('lib-furg', 'telegram', '123456789', pgp_sym_encrypt('bot-token', ?), true)",
            [config('libvox.crypto_key')],
        );
    });

    // A second library, so any list has more than one row and any accidental
    // cross-tenant read has something to find.
    app(TenantProvisioner::class)->provision(['tenant_id' => 'lib-ibict', 'display_name' => 'IBICT']);
});

test('every operator screen renders', function (string $path) {
    $this->actingAs(operator())->get($path)->assertOk();
})->with([
    '/admin',
    '/admin/tenants',
    '/admin/tenants/create',
    '/admin/tenants/lib-furg/edit',
    '/admin/users',
    '/admin/users/create',
    // The conversation screens were absent from this sweep entirely, through a
    // rewrite and a modal. The transcript needs its own test for the record id,
    // below; this covers the listing.
    '/admin/conversations',
]);

test('every library screen renders', function (string $path) {
    $this->actingAs(librarian('lib-furg'))->get($path)->assertOk();
})->with([
    '/app',
    '/app/widget-settings',
    '/app/catalog-connection',
    '/app/channel-bindings',
    '/app/profile',
    // Not in navigation -- it is reached from the button on the catalogue
    // screen -- which is why it was missing here, through a rewrite of every
    // class on it.
    '/app/catalog-import',
]);

test('editing each channel binding renders', function () {
    $ids = app(TenantScope::class)->run('lib-furg', fn () => ChannelBinding::pluck('id')->all());

    expect($ids)->toHaveCount(2);

    $librarian = librarian('lib-furg');

    foreach ($ids as $id) {
        $this->actingAs($librarian)
            ->get("/app/channel-bindings/{$id}/edit")
            ->assertOk();
    }
});

test('editing an account renders', function () {
    $librarian = librarian('lib-furg');

    $this->actingAs(operator())->get("/admin/users/{$librarian->id}/edit")->assertOk();
});

test('the screens a librarian must not reach are refused', function (string $path) {
    // The same sweep from the other direction: a page that renders for the
    // wrong person is worse than one that throws.
    $this->actingAs(librarian('lib-furg'))->get($path)->assertForbidden();
})->with(['/admin', '/admin/tenants', '/admin/users']);

test('the library panel is refused to an operator', function () {
    Filament::setCurrentPanel('library');

    $this->actingAs(operator())->get('/app')->assertForbidden();
});
