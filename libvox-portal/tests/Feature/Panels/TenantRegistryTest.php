<?php

use App\Database\TenantScope;
use App\Enums\IlsType;
use App\Enums\TenantStatus;
use App\Filament\Admin\Resources\Tenants\Pages\CreateTenant;
use App\Filament\Admin\Resources\Tenants\Pages\ListTenants;
use App\Ils\IlsConnection;
use App\Ils\IlsCredentialStore;
use App\Models\Tenant;
use App\Models\TenantWidget;
use App\Tenancy\TenantProvisioner;
use Illuminate\Support\Facades\DB;
use Livewire\Livewire;

/*
 * The operator's registry.
 *
 * Everything here reads `tenants`, which has no row-level security because it
 * is the table that says which tenants exist. Nothing here reads a library's
 * credentials or configuration across libraries -- those are unreadable across
 * tenants on purpose. The one question the operator asks about all of them at
 * once, "is this library configured yet", is answered by a column on the
 * registry rather than by a privileged read of the table it describes.
 */

beforeEach(function () {
    DB::connection('pgsql_admin')->statement('TRUNCATE tenants, users, model_has_roles, roles CASCADE');
    seed_roles();
});

test('provisioning a library creates its widget settings too', function () {
    $tenant = app(TenantProvisioner::class)->provision([
        'tenant_id' => 'lib-furg',
        'display_name' => 'FURG',
    ]);

    $widget = app(TenantScope::class)
        ->run('lib-furg', fn () => TenantWidget::find('lib-furg'));

    expect($tenant->tenant_id)->toBe('lib-furg')
        // A library that exists but has no widget row would render a form with
        // nothing behind it, which reads as a bug rather than as missing setup.
        ->and($widget)->not->toBeNull()
        ->and($widget->assistant_name)->toBe('MARC.IA');
});

test('a provisioned library starts out provisioning, not active', function () {
    $tenant = app(TenantProvisioner::class)->provision([
        'tenant_id' => 'lib-furg',
        'display_name' => 'FURG',
    ]);

    expect($tenant->fresh()->status)->toBe(TenantStatus::Provisioning);
});

test('provisioning is one transaction', function () {
    // A tenant_id the CHECK refuses, so tenants fails and tenant_widget must
    // never have been written.
    expect(fn () => app(TenantProvisioner::class)->provision([
        'tenant_id' => 'not-a-library',
        'display_name' => 'x',
    ]))->toThrow(Exception::class);

    $orphans = DB::connection('pgsql_admin')->select('SELECT tenant_id FROM tenant_widget');

    expect($orphans)->toBeEmpty();
});

test('the operator sees every library', function () {
    foreach (['lib-furg', 'lib-ibict'] as $id) {
        app(TenantProvisioner::class)->provision(['tenant_id' => $id, 'display_name' => $id]);
    }

    Livewire::actingAs(operator())
        ->test(ListTenants::class)
        ->assertCanSeeTableRecords(Tenant::all());
});

test('the operator registers a library through the form', function () {
    Livewire::actingAs(operator())
        ->test(CreateTenant::class)
        ->fillForm([
            'tenant_id' => 'lib-furg',
            'display_name' => 'Biblioteca da FURG',
            'status' => TenantStatus::Provisioning->value,
            'locale' => 'pt-BR',
        ])
        ->call('create')
        ->assertHasNoFormErrors();

    // Through the provisioner, so a library created from the form comes into
    // existence exactly like one created by a seeder or a console command.
    $widget = app(TenantScope::class)
        ->run('lib-furg', fn () => TenantWidget::find('lib-furg'));

    expect(Tenant::find('lib-furg')->display_name)->toBe('Biblioteca da FURG')
        ->and($widget)->not->toBeNull();
});

test('the form refuses a tenant id that is not a slug', function () {
    Livewire::actingAs(operator())
        ->test(CreateTenant::class)
        ->fillForm(['tenant_id' => 'furg', 'display_name' => 'x', 'status' => 'provisioning', 'locale' => 'pt-BR'])
        ->call('create')
        // Refused by the form before the database has to refuse it, so the
        // operator gets a field error instead of a stack trace.
        ->assertHasFormErrors(['tenant_id']);
});

test('storing credentials marks the library as configured', function () {
    app(TenantProvisioner::class)->provision(['tenant_id' => 'lib-furg', 'display_name' => 'FURG']);

    expect(Tenant::find('lib-furg')->ils_configured_at)->toBeNull();

    app(IlsCredentialStore::class)->put(
        new IlsConnection('lib-furg', IlsType::Koha, 'http://koha.local', 'a-client-id'),
        'a-secret',
    );

    expect(Tenant::find('lib-furg')->ils_configured_at)->not->toBeNull();
});

test('forgetting credentials unmarks the library', function () {
    app(TenantProvisioner::class)->provision(['tenant_id' => 'lib-furg', 'display_name' => 'FURG']);
    app(IlsCredentialStore::class)->put(
        new IlsConnection('lib-furg', IlsType::Koha, 'http://koha.local', 'a-client-id'),
        'a-secret',
    );

    app(IlsCredentialStore::class)->forget('lib-furg');

    expect(Tenant::find('lib-furg')->ils_configured_at)->toBeNull();
});

test('the configured flag cannot disagree with the credentials it describes', function () {
    // Written in the same transaction as the credential, so a failed write
    // leaves neither behind.
    app(TenantProvisioner::class)->provision(['tenant_id' => 'lib-furg', 'display_name' => 'FURG']);

    expect(fn () => app(IlsCredentialStore::class)->put(
        new IlsConnection('lib-ghost', IlsType::Koha, 'http://koha.local', 'id'),
        'a-secret',
    ))->toThrow(Exception::class);

    $rows = DB::connection('pgsql_admin')->select('SELECT tenant_id FROM tenant_ils');

    expect($rows)->toBeEmpty()
        ->and(Tenant::find('lib-furg')->ils_configured_at)->toBeNull();
});

test('the operator panel screens render', function () {
    app(TenantProvisioner::class)->provision(['tenant_id' => 'lib-furg', 'display_name' => 'FURG']);

    $operator = operator();

    $this->actingAs($operator)->get('/admin/tenants')->assertOk();
    $this->actingAs($operator)->get('/admin/tenants/create')->assertOk();
    $this->actingAs($operator)->get('/admin/tenants/lib-furg/edit')->assertOk();
    $this->actingAs($operator)->get('/admin/users')->assertOk();
});

test('a librarian cannot reach the registry', function () {
    app(TenantProvisioner::class)->provision(['tenant_id' => 'lib-furg', 'display_name' => 'FURG']);

    // The library panel has no account management and no registry at all, so
    // there is no path by which a librarian grants access to a second library.
    $this->actingAs(librarian('lib-furg'))->get('/admin/tenants')->assertForbidden();
    $this->actingAs(librarian2('lib-furg'))->get('/admin/users')->assertForbidden();
});
