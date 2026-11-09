<?php

use App\Database\TenantScope;
use App\Filament\Library\Pages\WidgetSettings;
use App\Models\ChannelBinding;
use App\Models\Tenant;
use App\Models\TenantWidget;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;

/*
 * The install snippet the Assistant page generates.
 *
 * Generated rather than transcribed, and that is the point of testing it: a
 * librarian copying a key by hand can copy the wrong one, and a widget bound
 * to another library is a failure that looks like a working install.
 */

beforeEach(function () {
    DB::connection('pgsql_admin')->statement('TRUNCATE tenants, users, model_has_roles, roles CASCADE');
    seed_roles();
    config(['libvox.widget_url' => 'https://libvox.exemplo']);
});

/**
 * A library with a web channel, and the widget row the page would otherwise
 * create. Created inside a scope here because `Livewire::test` does not run
 * the middleware that binds one, and the insert would be refused by the same
 * policy these tests rely on everywhere else.
 */
function bindWeb(
    string $tenantId,
    array $origins = ['https://opac.exemplo'],
    bool $enabled = true,
): ChannelBinding {
    Tenant::firstOrCreate(['tenant_id' => $tenantId], ['display_name' => $tenantId]);
    widgetRow($tenantId);

    // A key shaped like a real one -- random, and naming no library. A
    // fixture spelling it "wk_lib-furg" would have made the assertion below
    // pass or fail for a reason the production key does not have.
    $key = 'wk_'.substr(md5($tenantId), 0, 16);

    DB::connection('pgsql_admin')->table('channel_bindings')->insert([
        'tenant_id' => $tenantId,
        'channel' => 'web',
        'binding_key' => $key,
        'enabled' => $enabled,
        'allowed_origins' => json_encode($origins),
    ]);

    return new ChannelBinding(['tenant_id' => $tenantId, 'binding_key' => $key]);
}

function widgetRow(string $tenantId): void
{
    scoped($tenantId, fn () => TenantWidget::firstOrCreate(
        ['tenant_id' => $tenantId],
        ['assistant_name' => 'MARC.IA'],
    ));
}

/**
 * Stands in for what BindTenantScope does around a real request: Livewire's
 * test helper does not run HTTP middleware, so without this every query here
 * runs unscoped and row-level security -- correctly -- shows nothing.
 */
function scoped(string $tenantId, Closure $work): mixed
{
    return app(TenantScope::class)->run($tenantId, $work);
}

/**
 * The widget URL the page would print for a librarian standing at this address.
 *
 * The request is swapped after the page is mounted rather than before: the
 * component needs a real one to mount against, and `widgetUrl()` reads the
 * current request at the moment it is called. `URL::forceRootUrl` does not
 * work here -- it steers URL *generation*, and this reads the request itself.
 */
function reachedAt(string $url): string
{
    $page = scoped('lib-furg', fn () => assistantPage('lib-furg'));
    app()->instance('request', Request::create($url));

    return $page->widgetUrl();
}

/** The Assistant page, rendered the way a request would render it. */
function assistantPage(string $tenantId): WidgetSettings
{
    return scoped($tenantId, fn () => Livewire\Livewire::actingAs(librarian($tenantId))
        ->test(WidgetSettings::class)
        ->instance());
}

test('the embed carries this library key and never another', function () {
    bindWeb('lib-furg');
    bindWeb('lib-outra');

    $embed = scoped('lib-furg', fn () => assistantPage('lib-furg')->embed());

    expect($embed)->toContain('wk_'.substr(md5('lib-furg'), 0, 16))
        ->and($embed)->not->toContain('wk_'.substr(md5('lib-outra'), 0, 16))
        ->and($embed)->toContain('https://libvox.exemplo/widget.js');
});

test('the snippet names the port only when the panel is reached on one', function () {
    // Direct, Core is a port away from the panel on the same host. Behind a
    // proxy it is not: `deploy/host.env.example` tells the operator to bind
    // 8090 and 8092 to loopback there, so a panel on 443 emitting
    // `https://host:8092/widget.js` names a port its own deployment guide
    // just closed -- a line that copies cleanly and loads nothing.
    config(['libvox.widget_url' => null]);
    bindWeb('lib-furg');

    expect(reachedAt('https://libvox.exemplo/app/widget-settings'))
        ->toBe('https://libvox.exemplo');
});

test('reached on a port, the snippet keeps naming Core s own', function () {
    // The laptop, where the panel is on 8090 and Core is on 8092. Dropping the
    // port here would point the snippet at the portal, which serves no
    // widget.js at all.
    config(['libvox.widget_url' => null]);
    bindWeb('lib-furg');

    expect(reachedAt('http://localhost:8090/app/widget-settings'))
        ->toBe('http://localhost:8092');
});

test('an explicit widget url beats both guesses', function () {
    // The two-domain deployment in deploy/proxy/Caddyfile, where the widget
    // has a hostname of its own and no guess from this request can find it.
    config(['libvox.widget_url' => 'https://widget.exemplo/']);
    bindWeb('lib-furg');

    expect(reachedAt('https://libvox.exemplo/app/widget-settings'))
        ->toBe('https://widget.exemplo');
});

test('the embed carries her name, because the button cannot wait for it', function () {
    // The launcher is on screen before any socket opens, so it is the one part
    // of the widget that cannot be told who she is by the session. Everything
    // else in the library's voice arrives over the wire and overrides this.
    bindWeb('lib-furg');
    scoped('lib-furg', fn () => TenantWidget::query()->update(['assistant_name' => 'Marcinha']));

    $embed = scoped('lib-furg', fn () => assistantPage('lib-furg')->embed());

    expect($embed)->toContain('data-name="Marcinha"');
});

test('the embed carries the accent, for the button that cannot wait for it', function () {
    // The launcher is the panel's sibling and was never reached by the accent
    // set on the panel, so it stayed the stylesheet's fallback purple for the
    // whole life of the page -- a purple button opening a green panel. The
    // widget fixes the inheritance; this is the half that makes it right on
    // the very first paint, before any socket exists.
    bindWeb('lib-furg');
    scoped('lib-furg', fn () => TenantWidget::query()->update(['theme_color' => '#0f766e']));

    $embed = scoped('lib-furg', fn () => assistantPage('lib-furg')->embed());

    expect($embed)->toContain('data-color="#0f766e"');
});

test('a library that never renamed her still gets a name in the snippet', function () {
    // Not an empty attribute: the widget falls back to the same default, and a
    // blank one would put "Converse com a " on a button.
    bindWeb('lib-furg');
    scoped('lib-furg', fn () => TenantWidget::query()->update(['assistant_name' => '']));

    $embed = scoped('lib-furg', fn () => assistantPage('lib-furg')->embed());

    // The colour comes from the column's own default rather than from a
    // cleared field: `theme_color` is NOT NULL with a CHECK for `#rrggbb`, so
    // an empty one is a state the database will not hold. Both defaults match
    // what widget.js writes into its `var(--accent, ...)` fallbacks -- a
    // snippet and a widget that disagree there give a button one colour and
    // the panel it opens another.
    expect($embed)->toContain('data-name="MARC.IA"')
        ->and($embed)->toContain('data-color="#4f46e5"');
});

test('the embed names no library, only its key', function () {
    // A tenant id in an OPAC page is a tenant id anyone can read. The key
    // identifies the binding; nothing downstream takes a tenant as an argument.
    bindWeb('lib-furg');

    $embed = scoped('lib-furg', fn () => assistantPage('lib-furg')->embed());

    expect($embed)->not->toContain('lib-furg')
        ->and($embed)->not->toContain('tenant');
});

test('a library with no web channel is given no snippet at all', function () {
    // Rather than a tag with an empty key, which would install cleanly and
    // fail silently in a reader's browser.
    Tenant::firstOrCreate(['tenant_id' => 'lib-furg'], ['display_name' => 'lib-furg']);
    widgetRow('lib-furg');

    scoped('lib-furg', function () {
        $page = assistantPage('lib-furg');

        expect($page->embed())->toBeNull()
            ->and($page->webChannel())->toBeNull();
    });
});

test('the assistant page renders for a library with no channel yet', function () {
    Tenant::firstOrCreate(['tenant_id' => 'lib-furg'], ['display_name' => 'lib-furg']);
    widgetRow('lib-furg');

    scoped('lib-furg', fn () => Livewire\Livewire::actingAs(librarian('lib-furg'))
        ->test(WidgetSettings::class)
        ->assertOk());
});

test('a switched-off channel still shows its snippet, and says it is off', function () {
    // A web channel with no allowed origin can exist only switched off: the
    // database refuses the enabled-and-origin-less combination outright
    // (channel_bindings_web_needs_an_origin). So that is the state a library
    // lands in, and the snippet is still the right one to show.
    bindWeb('lib-desligada', origins: [], enabled: false);

    scoped('lib-desligada', fn () => Livewire\Livewire::actingAs(librarian('lib-desligada'))
        ->test(WidgetSettings::class)
        ->assertOk()
        ->assertSee('wk_'.substr(md5('lib-desligada'), 0, 16)));
});

test('with no widget URL configured, the snippet points at the host the librarian is on', function () {
    /*
     * The default used to be `http://127.0.0.1:8092`, which is right on a
     * laptop and wrong on every server -- and wrong in the way that costs the
     * most, because the snippet copies cleanly, goes into an OPAC, and loads
     * nothing with no error on the page to say why. A librarian reaching this
     * panel at some address is evidence about an address that resolves.
     */
    config(['libvox.widget_url' => null]);
    bindWeb('lib-furg');

    $embed = scoped('lib-furg', fn () => assistantPage('lib-furg')->embed());

    expect($embed)->toContain('://localhost:8092/widget.js')
        ->and($embed)->not->toContain('127.0.0.1');
});

test('an explicit widget URL wins over the guess', function () {
    // Separate hosts, or a proxy terminating TLS in front of one and not the
    // other: the guess is wrong and the deployment says so.
    config(['libvox.widget_url' => 'https://marcia.biblioteca.example/']);
    bindWeb('lib-furg');

    $embed = scoped('lib-furg', fn () => assistantPage('lib-furg')->embed());

    // Trailing slash trimmed, so the snippet never carries `//widget.js`.
    expect($embed)->toContain('https://marcia.biblioteca.example/widget.js');
});
