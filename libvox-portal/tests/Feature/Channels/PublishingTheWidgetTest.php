<?php

use App\Database\TenantScope;
use App\Enums\ChannelType;
use App\Filament\Library\Pages\WidgetSettings;
use App\Models\ChannelBinding;
use App\Tenancy\TenantProvisioner;
use Illuminate\Support\Facades\DB;
use Livewire\Features\SupportTesting\Testable;

/*
 * Where the widget is published, and who publishes it.
 *
 * The address of the page a widget lives on used to be typed by a LibVox
 * operator on the library registration form -- a guess by the one person in the
 * arrangement who does not run the OPAC, and one nothing could correct
 * afterwards, because editing a library never went back through the code that
 * turned it into an allowed origin. Two fields that both claimed to be "the
 * OPAC address", diverging in silence from the second day.
 *
 * It is the librarian's now, on the page where she copies the snippet: the
 * address goes in, the line comes out, the key turns.
 *
 * The Channels screen still edits the same column of the same row -- these are
 * two doors into one fact, which is exactly what the old arrangement was not.
 */

beforeEach(function () {
    DB::connection('pgsql_admin')->statement('TRUNCATE tenants, users, model_has_roles, roles CASCADE');
    seed_roles();
    config(['libvox.widget_url' => 'https://libvox.exemplo']);

    app(TenantProvisioner::class)->provision([
        'tenant_id' => 'lib-nova',
        'display_name' => 'Nova',
    ]);
});

/** The web binding as the database holds it, read inside a scope. */
function webBinding(string $tenantId = 'lib-nova'): ChannelBinding
{
    return app(TenantScope::class)->run(
        $tenantId,
        fn () => ChannelBinding::query()->where('channel', ChannelType::Web)->first(),
    );
}

/**
 * The Assistant page, driven the way a librarian drives it.
 *
 * Wrapped in a scope because Livewire's test helper runs no HTTP middleware, so
 * without it BindTenantScope never fires and row-level security -- correctly --
 * shows the page nothing at all.
 */
function assistant(string $tenantId = 'lib-nova'): Testable
{
    return Livewire\Livewire::actingAs(librarian($tenantId))->test(WidgetSettings::class);
}

test('a librarian registers the OPAC page and turns the widget on', function () {
    expect(webBinding()->enabled)->toBeFalse()
        ->and(webBinding()->allowed_origins)->toBe([]);

    app(TenantScope::class)->run('lib-nova', fn () => assistant()
        ->fillForm([
            'allowed_origins' => ['https://biblioteca.nova.br'],
            'channel_enabled' => true,
        ])
        ->call('save')
        ->assertHasNoFormErrors());

    expect(webBinding()->allowed_origins)->toBe(['https://biblioteca.nova.br'])
        ->and(webBinding()->enabled)->toBeTrue();
});

test('the page is stored normalized, however she typed it', function () {
    // The same normalization the Channels screen applies, because it is the
    // same component: the allowlist is compared as an exact string forever, so
    // four spellings of one page must not be four rows.
    app(TenantScope::class)->run('lib-nova', fn () => assistant()
        ->fillForm([
            'allowed_origins' => [
                'HTTPS://Biblioteca.NOVA.br:443/opac/index.pl',
                'https://biblioteca.nova.br',
                'https://*.nova.br',
            ],
            'channel_enabled' => true,
        ])
        ->call('save')
        ->assertHasNoFormErrors());

    // One row: the two spellings collapse, and the wildcard is dropped rather
    // than approximated.
    expect(webBinding()->allowed_origins)->toBe(['https://biblioteca.nova.br']);
});

test('turning it on with no page listed is a form error, not a database exception', function () {
    /*
     * channel_bindings_web_needs_an_origin is the guarantee, and it holds
     * whatever the screen does. But a constraint reaching the librarian as a
     * Postgres exception is a 500 where a field error belongs, so the form
     * mirrors it and arrives first.
     */
    app(TenantScope::class)->run('lib-nova', fn () => assistant()
        ->fillForm(['allowed_origins' => [], 'channel_enabled' => true])
        ->call('save')
        ->assertHasFormErrors(['channel_enabled']));

    expect(webBinding()->enabled)->toBeFalse();
});

test('a page that normalizes to nothing counts as no page at all', function () {
    // The list is not empty on screen, and it is empty by the time it reaches
    // the column. The check has to run on what will be stored.
    app(TenantScope::class)->run('lib-nova', fn () => assistant()
        ->fillForm(['allowed_origins' => ['nao-e-uma-url'], 'channel_enabled' => true])
        ->call('save')
        ->assertHasFormErrors(['channel_enabled']));

    expect(webBinding()->enabled)->toBeFalse()
        ->and(webBinding()->allowed_origins)->toBe([]);
});

test('editing the assistant leaves the allowlist alone', function () {
    app(TenantScope::class)->run('lib-nova', fn () => ChannelBinding::query()
        ->where('channel', ChannelType::Web)
        ->update(['allowed_origins' => ['https://biblioteca.nova.br'], 'enabled' => true]));

    app(TenantScope::class)->run('lib-nova', fn () => assistant()
        ->fillForm(['assistant_name' => 'Bibi'])
        ->call('save')
        ->assertHasNoFormErrors());

    // Two tables behind one form, and the half she did not touch is untouched.
    expect(webBinding()->allowed_origins)->toBe(['https://biblioteca.nova.br'])
        ->and(webBinding()->enabled)->toBeTrue();
});
