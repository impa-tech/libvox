<?php

use App\Channels\WidgetKey;
use App\Database\TenantScope;
use App\Enums\ChannelType;
use App\Filament\Library\Resources\ChannelBindings\Pages\EditChannelBinding;
use App\Models\ChannelBinding;
use App\Models\Tenant;
use App\Tenancy\TenantProvisioner;
use Illuminate\Database\QueryException;
use Illuminate\Support\Facades\DB;

/*
 * The states this table refuses.
 *
 * Each of these is a configuration that would look plausible in a form and be
 * wrong in a way nobody would notice until a reader was affected. They are
 * constraints rather than validation rules because a validation rule protects
 * one form, and a constraint protects the table.
 */

beforeEach(function () {
    DB::connection('pgsql_admin')->statement('TRUNCATE tenants CASCADE');

    foreach (['lib-furg', 'lib-ibict'] as $id) {
        Tenant::create(['tenant_id' => $id, 'display_name' => $id]);
    }
});

function bind(string $tenantId, array $attributes): ChannelBinding
{
    return app(TenantScope::class)->run($tenantId, fn () => ChannelBinding::create([
        'tenant_id' => $tenantId,
        'binding_key' => WidgetKey::mint(),
        ...$attributes,
    ]));
}

test('a web binding cannot be enabled without an allowed origin', function () {
    // A widget with an empty allowlist is embeddable from any page on the
    // internet. It may exist -- a library that has not told us its OPAC URL yet
    // -- but it may not be switched on.
    expect(fn () => bind('lib-furg', [
        'channel' => ChannelType::Web,
        'enabled' => true,
        'allowed_origins' => [],
    ]))->toThrow(QueryException::class);
});

test('a web binding may exist disabled with no origin', function () {
    $binding = bind('lib-furg', [
        'channel' => ChannelType::Web,
        'enabled' => false,
        'allowed_origins' => [],
    ]);

    expect($binding->enabled)->toBeFalse();
});

test('an origin that is not a bare scheme and host is refused', function (string $origin) {
    expect(fn () => bind('lib-furg', [
        'channel' => ChannelType::Web,
        'allowed_origins' => [$origin],
    ]))->toThrow(QueryException::class);
})->with([
    'with a path' => 'https://biblioteca.furg.br/opac',
    'with a trailing slash' => 'https://biblioteca.furg.br/',
    'uppercase' => 'https://Biblioteca.FURG.br',
    'a wildcard' => 'https://*.furg.br',
    'no scheme' => 'biblioteca.furg.br',
]);

test('a telegram binding without a token is refused', function () {
    // Enabling a channel that can receive a message and cannot answer is worse
    // than not offering it: the reader writes and nothing happens.
    expect(fn () => bind('lib-furg', [
        'channel' => ChannelType::Telegram,
        'binding_key' => '123456789',
    ]))->toThrow(QueryException::class);
});

test('a web binding cannot carry a secret', function () {
    // The widget key is public by design. A column that could hold a secret
    // here would invite treating the key as one, and the Origin check as
    // optional.
    expect(fn () => app(TenantScope::class)->run('lib-furg', fn () => DB::connection('pgsql')->insert(
        "INSERT INTO channel_bindings (tenant_id, channel, binding_key, secret_enc)
         VALUES ('lib-furg', 'web', 'wk_x', pgp_sym_encrypt('s', 'k'))"
    )))->toThrow(QueryException::class);
});

test('origins belong to the web channel alone', function () {
    expect(fn () => bind('lib-furg', [
        'channel' => ChannelType::Email,
        'binding_key' => 'biblioteca@furg.br',
        'allowed_origins' => ['https://biblioteca.furg.br'],
    ]))->toThrow(QueryException::class);
});

test('two libraries cannot claim the same widget key', function () {
    $key = WidgetKey::mint();

    bind('lib-furg', ['channel' => ChannelType::Web, 'binding_key' => $key]);

    // Not a validation rule: two libraries answering on one key is a tenant
    // crossing, and the row-level security policy would hide the conflict from
    // any check written in PHP.
    expect(fn () => bind('lib-ibict', ['channel' => ChannelType::Web, 'binding_key' => $key]))
        ->toThrow(QueryException::class);
});

test('a library gets one binding per channel', function () {
    bind('lib-furg', ['channel' => ChannelType::Web]);

    expect(fn () => bind('lib-furg', ['channel' => ChannelType::Web]))
        ->toThrow(QueryException::class);
});

test('a library cannot see another librarys bindings', function () {
    bind('lib-furg', ['channel' => ChannelType::Web]);
    bind('lib-ibict', ['channel' => ChannelType::Web]);

    $seen = app(TenantScope::class)->run('lib-furg', fn () => ChannelBinding::pluck('tenant_id')->all());

    expect($seen)->toBe(['lib-furg']);
});

test('a new library starts with the web widget, off, and nothing else', function () {
    app(TenantProvisioner::class)->provision([
        'tenant_id' => 'lib-nova',
        'display_name' => 'Nova',
    ]);

    $bindings = app(TenantScope::class)->run('lib-nova', fn () => ChannelBinding::all());

    // The binding exists so the key is stable from day one, and it is off
    // because nobody here knows where the widget will live. The operator used
    // to type an OPAC address on the registration form and this row came up
    // enabled from it -- a guess by the one person who does not run the OPAC,
    // and one that nothing corrected afterwards, because editing a library
    // never came back through this code. The librarian registers the page on
    // her own Assistant screen now.
    expect($bindings)->toHaveCount(1)
        ->and($bindings->first()->channel)->toBe(ChannelType::Web)
        ->and($bindings->first()->enabled)->toBeFalse()
        ->and($bindings->first()->allowed_origins)->toBe([])
        ->and($bindings->first()->binding_key)->toStartWith('wk_');
});

test('deleting a library takes its channel bindings with it', function () {
    app(TenantProvisioner::class)->provision(['tenant_id' => 'lib-nova', 'display_name' => 'Nova']);

    Tenant::find('lib-nova')->delete();

    $rows = DB::connection('pgsql_admin')->select('SELECT id FROM channel_bindings');

    expect($rows)->toBeEmpty();
});

test('the library sees its channels and can widen the allowlist', function () {
    app(TenantProvisioner::class)->provision([
        'tenant_id' => 'lib-nova',
        'display_name' => 'Nova',
    ]);

    seed_roles();
    $librarian = librarian('lib-nova');

    // A Livewire component test has no HTTP request, so Filament resolves the
    // default panel -- which is the operator's. The resource lives in the
    // library panel, and outside a request nothing else says so.
    Filament\Facades\Filament::setCurrentPanel('library');

    app(TenantScope::class)->run('lib-nova', function () use ($librarian) {
        $binding = ChannelBinding::first();

        Livewire\Livewire::actingAs($librarian)
            ->test(EditChannelBinding::class, ['record' => $binding->id])
            // Typed however the librarian pleases; stored normalized, so the
            // allowlist holds one row per origin rather than four spellings.
            ->fillForm(['allowed_origins' => [
                'https://biblioteca.nova.br',
                'HTTPS://Biblioteca.NOVA.br:443/opac/',
                'https://catalogo.nova.br',
            ]])
            ->call('save')
            ->assertHasNoFormErrors();
    });

    $origins = app(TenantScope::class)->run('lib-nova', fn () => ChannelBinding::first()->allowed_origins);

    expect($origins)->toBe(['https://biblioteca.nova.br', 'https://catalogo.nova.br']);
});

test('the channels screen renders with an origin listed', function () {
    /*
     * With origins, not without.
     *
     * The version of this test that provisioned a library and stopped there
     * passed while the screen was broken: allowed_origins was an empty array,
     * so the column's per-element formatting never ran. Filament calls
     * formatStateUsing once per element when the state is an array, and one
     * origin was enough to throw. Provisioning leaves it empty, so the origin
     * has to be registered here on purpose.
     */
    app(TenantProvisioner::class)->provision([
        'tenant_id' => 'lib-nova',
        'display_name' => 'Nova',
    ]);
    seed_roles();

    app(TenantScope::class)->run('lib-nova', fn () => ChannelBinding::first()->update([
        'allowed_origins' => ['https://biblioteca.nova.br'],
    ]));

    $this->actingAs(librarian('lib-nova'))
        ->get('/app/channel-bindings')
        ->assertOk()
        ->assertSee('https://biblioteca.nova.br');
});

test('the channels screen renders for a library with no origins yet', function () {
    // The empty case still has to work -- it is what a library looks like
    // before it tells us where its OPAC is.
    app(TenantProvisioner::class)->provision(['tenant_id' => 'lib-nova', 'display_name' => 'Nova']);
    seed_roles();

    $this->actingAs(librarian('lib-nova'))->get('/app/channel-bindings')->assertOk();
});

test('several origins are each listed', function () {
    app(TenantProvisioner::class)->provision([
        'tenant_id' => 'lib-nova',
        'display_name' => 'Nova',
    ]);
    seed_roles();

    app(TenantScope::class)->run('lib-nova', fn () => ChannelBinding::first()->update([
        'allowed_origins' => ['https://biblioteca.nova.br', 'https://catalogo.nova.br'],
    ]));

    // Shown separately rather than joined: the allowlist is compared exactly,
    // so four spellings of one origin are four entries and a single line would
    // hide that.
    $this->actingAs(librarian('lib-nova'))
        ->get('/app/channel-bindings')
        ->assertOk()
        ->assertSee('https://biblioteca.nova.br')
        ->assertSee('https://catalogo.nova.br');
});
