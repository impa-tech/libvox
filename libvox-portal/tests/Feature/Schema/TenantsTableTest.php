<?php

use App\Enums\TenantStatus;
use App\Models\Tenant;
use Illuminate\Database\QueryException;
use Illuminate\Support\Facades\DB;

beforeEach(fn () => DB::connection('pgsql_admin')->statement('TRUNCATE tenants CASCADE'));

test('a tenant id that is not a slug is refused', function (string $id) {
    expect(fn () => Tenant::create(['tenant_id' => $id, 'display_name' => 'x']))
        ->toThrow(QueryException::class);
})->with([
    'no lib prefix' => 'furg',
    'uppercase' => 'lib-FURG',
    'a space' => 'lib-fu rg',
    'a quote' => "lib-fu'rg",
    'trailing hyphen' => 'lib-furg-',
    'empty' => '',
]);

test('the tenant ids already in use elsewhere are accepted', function (string $id) {
    // These are the values libvox-sync's own tests and README use. The format
    // CHECK arrived after them, so it has to admit them or the registry cannot
    // describe the libraries that already exist.
    expect(Tenant::create(['tenant_id' => $id, 'display_name' => $id])->tenant_id)->toBe($id);
})->with(['lib-furg', 'lib-ibict']);

test('a tenant id cannot be changed after creation', function () {
    Tenant::create(['tenant_id' => 'lib-furg', 'display_name' => 'FURG']);

    // Not a disabled form field: rows in the Discovery Index live in another
    // database and reference this string with no foreign key to catch a rename.
    expect(fn () => DB::connection('pgsql')
        ->statement("UPDATE tenants SET tenant_id = 'lib-other' WHERE tenant_id = 'lib-furg'"))
        ->toThrow(QueryException::class);
});

test('the display name can be changed', function () {
    $tenant = Tenant::create(['tenant_id' => 'lib-furg', 'display_name' => 'FURG']);

    $tenant->update(['display_name' => 'Biblioteca Central da FURG']);

    expect($tenant->fresh()->display_name)->toBe('Biblioteca Central da FURG');
});

test('a tenant starts out provisioning rather than active', function () {
    // A library that has registered but has no ILS credentials yet cannot
    // answer anything. Defaulting to active would let a channel binding
    // resolve to a tenant with an empty index.
    $tenant = Tenant::create(['tenant_id' => 'lib-furg', 'display_name' => 'FURG']);

    expect($tenant->fresh()->status)->toBe(TenantStatus::Provisioning);
});

test('an unknown status is refused', function () {
    expect(fn () => DB::connection('pgsql')->statement(
        "INSERT INTO tenants (tenant_id, display_name, status) VALUES ('lib-furg', 'FURG', 'retired')"
    ))->toThrow(QueryException::class);
});

test('an unknown locale is refused', function () {
    expect(fn () => DB::connection('pgsql')->statement(
        "INSERT INTO tenants (tenant_id, display_name, locale) VALUES ('lib-furg', 'FURG', 'fr-FR')"
    ))->toThrow(QueryException::class);
});
