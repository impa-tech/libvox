<?php

use App\Channels\ChannelResolver;
use App\Channels\WidgetKey;
use App\Database\TenantScope;
use App\Enums\ChannelType;
use App\Enums\TenantStatus;
use App\Models\ChannelBinding;
use App\Models\Tenant;
use App\Tenancy\TenantProvisioner;
use Illuminate\Support\Facades\DB;

/*
 * The contract /libvox-core will call.
 *
 * Every refusal returns the same thing -- nothing. An unknown key, a channel
 * that is switched off, a suspended library and a page that is not on the
 * allowlist are not distinguishable from outside, because telling them apart
 * would let a caller learn which of those a given key is.
 */

beforeEach(function () {
    DB::connection('pgsql_admin')->statement('TRUNCATE tenants CASCADE');

    app(TenantProvisioner::class)->provision([
        'tenant_id' => 'lib-furg',
        'display_name' => 'FURG',
        'status' => TenantStatus::Active->value,
    ]);

    // Provisioning brings the web channel up off and with an empty allowlist,
    // because only the librarian knows where the widget will live. Said here
    // rather than implied by a field on the registration form: what these tests
    // are about is which origin resolves, so the origin belongs in the fixture.
    app(TenantScope::class)->run('lib-furg', fn () => ChannelBinding::query()
        ->where('channel', ChannelType::Web)
        ->update(['allowed_origins' => ['https://biblioteca.furg.br'], 'enabled' => true]));
});

function resolver(): ChannelResolver
{
    return app(ChannelResolver::class);
}

function widgetKeyFor(string $tenantId): string
{
    return app(TenantScope::class)->run($tenantId, fn () => ChannelBinding::first()->binding_key);
}

test('a widget key and an allowed origin resolve to the library', function () {
    expect(resolver()->resolve(ChannelType::Web, widgetKeyFor('lib-furg'), 'https://biblioteca.furg.br'))
        ->toBe('lib-furg');
});

test('resolution works without a tenant scope, because that is the point', function () {
    // The question is asked before any tenant is known -- it is the question
    // whose answer determines the tenant. Every other read in this module
    // returns nothing from here.
    expect(app(TenantScope::class)->current())->toBeNull()
        ->and(resolver()->resolve(ChannelType::Web, widgetKeyFor('lib-furg'), 'https://biblioteca.furg.br'))
        ->toBe('lib-furg');
});

test('an unknown widget key resolves to nothing', function () {
    expect(resolver()->resolve(ChannelType::Web, WidgetKey::mint(), 'https://biblioteca.furg.br'))
        ->toBeNull();
});

test('a page that is not on the allowlist resolves to nothing', function () {
    // The key is public -- it sits in the OPAC's page source. This is what
    // keeps that from mattering.
    expect(resolver()->resolve(ChannelType::Web, widgetKeyFor('lib-furg'), 'https://attacker.example'))
        ->toBeNull();
});

test('a web binding with no origin at all resolves to nothing', function () {
    expect(resolver()->resolve(ChannelType::Web, widgetKeyFor('lib-furg'), null))->toBeNull();
});

test('a switched off channel resolves to nothing', function () {
    $key = widgetKeyFor('lib-furg');

    app(TenantScope::class)->run('lib-furg', fn () => ChannelBinding::first()->update(['enabled' => false]));

    expect(resolver()->resolve(ChannelType::Web, $key, 'https://biblioteca.furg.br'))->toBeNull();
});

test('a suspended library resolves to nothing', function () {
    $key = widgetKeyFor('lib-furg');

    Tenant::find('lib-furg')->update(['status' => TenantStatus::Suspended]);

    // Switching a library off is one edit on the registry, and it reaches every
    // channel at once without touching any of them.
    expect(resolver()->resolve(ChannelType::Web, $key, 'https://biblioteca.furg.br'))->toBeNull();
});

test('a library still provisioning resolves to nothing', function () {
    $key = widgetKeyFor('lib-furg');

    Tenant::find('lib-furg')->update(['status' => TenantStatus::Provisioning]);

    expect(resolver()->resolve(ChannelType::Web, $key, 'https://biblioteca.furg.br'))->toBeNull();
});

test('a key from one channel does not resolve on another', function () {
    expect(resolver()->resolve(ChannelType::Telegram, widgetKeyFor('lib-furg'), null))->toBeNull();
});

test('the origin comparison is exact', function (string $origin) {
    expect(resolver()->resolve(ChannelType::Web, widgetKeyFor('lib-furg'), $origin))->toBeNull();
})->with([
    'a trailing slash' => 'https://biblioteca.furg.br/',
    'a path' => 'https://biblioteca.furg.br/opac',
    'the wrong scheme' => 'http://biblioteca.furg.br',
    'a subdomain' => 'https://www.biblioteca.furg.br',
    'a suffix of the host' => 'https://evilbiblioteca.furg.br',
]);

test('the resolver cannot be widened into a credential read', function () {
    // A text return type is the width of the hole. There is no overload that
    // returns a row, and secret_enc is in neither the SELECT list nor the
    // signature.
    $signature = DB::connection('pgsql_admin')->selectOne(
        "SELECT pg_get_function_result(oid) AS result, prosecdef
           FROM pg_proc WHERE proname = 'resolve_channel_binding'"
    );

    expect($signature->result)->toBe('text')
        ->and($signature->prosecdef)->toBeTrue();
});

test('the function runs as a role that cannot log in', function () {
    // SECURITY DEFINER runs as the owner. Making that owner unable to connect
    // is what keeps its permissive policy reachable only through this function.
    $owner = DB::connection('pgsql_admin')->selectOne(
        "SELECT r.rolname, r.rolcanlogin, r.rolsuper, r.rolbypassrls
           FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner
          WHERE p.proname = 'resolve_channel_binding'"
    );

    expect($owner->rolname)->toBe('libvox_resolver')
        ->and($owner->rolcanlogin)->toBeFalse()
        ->and($owner->rolsuper)->toBeFalse()
        ->and($owner->rolbypassrls)->toBeFalse();
});

test('the resolver role can read nothing else', function () {
    // Its permissive policy is scoped to one table and to SELECT. tenant_ils is
    // the one that matters: a credential table must stay unreachable even from
    // the role that exists to cross tenants.
    $granted = DB::connection('pgsql_admin')->select(
        "SELECT table_name, privilege_type
           FROM information_schema.role_table_grants
          WHERE grantee = 'libvox_resolver'
          ORDER BY table_name, privilege_type"
    );

    $summary = collect($granted)->map(fn ($g) => "{$g->table_name}:{$g->privilege_type}")->all();

    expect($summary)->toBe(['channel_bindings:SELECT', 'tenants:SELECT']);
});

test('the resolver survives a non-superuser owner, which is the production shape', function () {
    /*
     * The trap this design exists to avoid, asserted as one fact rather than
     * three separate ones.
     *
     * FORCE ROW LEVEL SECURITY subjects a table's owner to its own policies,
     * and SECURITY DEFINER runs as the function's owner. An owner that is a
     * superuser sails through regardless -- so a resolver owned by one works
     * in development and returns nothing in production, where provisioning
     * creates unprivileged roles. Empty screen, no error, after deployment.
     *
     * Here the owner is unprivileged, FORCE is on, and resolution still works.
     * What makes it work is the explicit permissive policy, which is auditable
     * in the schema instead of implied by a role's privileges.
     */
    $flags = DB::connection('pgsql_admin')->selectOne(
        "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'channel_bindings'"
    );

    $owner = DB::connection('pgsql_admin')->selectOne(
        "SELECT r.rolsuper, r.rolbypassrls
           FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner
          WHERE p.proname = 'resolve_channel_binding'"
    );

    $policy = DB::connection('pgsql_admin')->selectOne(
        "SELECT polname, polcmd FROM pg_policy
          WHERE polrelid = 'channel_bindings'::regclass AND polname = 'channel_resolution'"
    );

    expect($flags->relforcerowsecurity)->toBeTrue()
        ->and($owner->rolsuper)->toBeFalse()
        ->and($owner->rolbypassrls)->toBeFalse()
        // 'r' is SELECT: the policy widens reads and nothing else.
        ->and($policy->polcmd)->toBe('r')
        ->and(resolver()->resolve(ChannelType::Web, widgetKeyFor('lib-furg'), 'https://biblioteca.furg.br'))
        ->toBe('lib-furg');
});
