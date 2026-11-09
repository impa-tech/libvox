<?php

use App\Database\TenantScope;
use App\Filament\Library\Pages\WidgetSettings;
use App\Models\Tenant;
use App\Models\TenantWidget;
use App\Models\User;
use Illuminate\Database\QueryException;
use Illuminate\Support\Facades\DB;
use Symfony\Component\HttpFoundation\Response;

/*
 * The guarantee this whole module rests on: one library's rows are unreachable
 * from another library's request.
 *
 * Not a panel check and not an Eloquent scope -- PostgreSQL row-level security,
 * bound per transaction. These tests are only meaningful because
 * DatabaseRoleTest proves the connection cannot bypass it.
 */

beforeEach(function () {
    DB::connection('pgsql_admin')->statement('TRUNCATE tenants, users, model_has_roles, roles CASCADE');
    seed_roles();

    foreach (['lib-furg', 'lib-ibict'] as $id) {
        Tenant::create(['tenant_id' => $id, 'display_name' => $id]);
    }

    foreach (['lib-furg', 'lib-ibict'] as $id) {
        scope()->run($id, fn () => TenantWidget::create([
            'tenant_id' => $id,
            'assistant_name' => 'MARC.IA de '.$id,
        ]));
    }
});

function scope(): TenantScope
{
    return app(TenantScope::class);
}

test('without a tenant scope nothing is visible', function () {
    // Fail closed. Forgetting to bind must yield no rows, never a stranger's.
    expect(TenantWidget::count())->toBe(0);
});

test('a tenant sees only its own widget settings', function () {
    $seen = scope()->run('lib-furg', fn () => TenantWidget::pluck('tenant_id')->all());

    expect($seen)->toBe(['lib-furg']);
});

test('a tenant cannot read another tenants widget settings by primary key', function () {
    // Not a 403 and not an empty list with a hint: the row is not there.
    $found = scope()->run('lib-furg', fn () => TenantWidget::find('lib-ibict'));

    expect($found)->toBeNull();
});

test('a tenant cannot write a row for another tenant', function () {
    expect(fn () => scope()->run('lib-furg', fn () => TenantWidget::create([
        'tenant_id' => 'lib-ibict',
        'assistant_name' => 'injected',
    ])))->toThrow(QueryException::class);
});

test('a tenant cannot update another tenants row', function () {
    scope()->run('lib-furg', fn () => DB::connection('pgsql')->update(
        "UPDATE tenant_widget SET assistant_name = 'hijacked' WHERE tenant_id = 'lib-ibict'"
    ));

    $untouched = scope()->run('lib-ibict', fn () => TenantWidget::find('lib-ibict'));

    expect($untouched->assistant_name)->toBe('MARC.IA de lib-ibict');
});

test('the binding does not outlive the scope', function () {
    scope()->run('lib-furg', fn () => TenantWidget::count());

    // set_config with a transaction-local flag dies with the transaction. If it
    // did not, the next request on a pooled connection would inherit it -- the
    // exact failure the session-scoped alternative has under Octane.
    expect(TenantWidget::count())->toBe(0)
        ->and(scope()->current())->toBeNull();
});

test('a scope cannot be re-bound to a different tenant inside an open one', function () {
    // Laravel's transaction() is re-entrant through savepoints, so a nested
    // bind would inherit the outer one and appear to work.
    expect(fn () => scope()->run('lib-furg', fn () => scope()->run('lib-ibict', fn () => null)))
        ->toThrow(LogicException::class);
});

test('re-entering the same scope is allowed', function () {
    $name = scope()->run(
        'lib-furg',
        fn () => scope()->run('lib-furg', fn () => TenantWidget::find('lib-furg')->assistant_name),
    );

    expect($name)->toBe('MARC.IA de lib-furg');
});

test('a superuser bypasses row level security entirely', function () {
    // Not a defect -- it is how PostgreSQL works, and it is the whole reason
    // the application must never connect as one. FORCE ROW LEVEL SECURITY
    // subjects a table's *owner* to its policies; superusers and BYPASSRLS
    // roles go through regardless of what the table says.
    //
    // Asserted rather than assumed, because it is the fact that makes
    // DatabaseRoleTest load-bearing instead of ceremonial.
    $rows = DB::connection('pgsql_admin')->select('SELECT tenant_id FROM tenant_widget');

    expect($rows)->toHaveCount(2)
        ->and(DB::connection('pgsql_admin')->selectOne(
            'SELECT rolsuper FROM pg_roles WHERE rolname = current_user'
        )->rolsuper)->toBeTrue();
});

test('the table is marked for forced row level security', function () {
    // The flag is what protects the deployment where the owner is not a
    // superuser. Checking the catalog rather than the behaviour, because the
    // behaviour cannot be observed from a superuser connection.
    $flags = DB::connection('pgsql_admin')->selectOne(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'tenant_widget'"
    );

    expect($flags->relrowsecurity)->toBeTrue()
        ->and($flags->relforcerowsecurity)->toBeTrue();
});

test('deleting a library takes its librarians with it', function () {
    $librarian = librarian('lib-furg');

    Tenant::find('lib-furg')->delete();

    // So a request can never end up bound to a library that no longer exists:
    // there is nobody left to authenticate as.
    expect(User::find($librarian->id))->toBeNull();
});

test('a librarian request is bound to their library', function () {
    $response = $this->actingAs(librarian('lib-furg'))->get('/app');

    expect($response->status())->toBe(Response::HTTP_OK)
        // The scope is released when the request ends, not left dangling.
        ->and(scope()->current())->toBeNull();
});

test('the widget settings page shows only this librarys row', function () {
    $response = $this->actingAs(librarian('lib-furg'))
        ->get('/app/widget-settings');

    $response->assertOk()
        ->assertSee('MARC.IA de lib-furg', escape: false)
        ->assertDontSee('MARC.IA de lib-ibict', escape: false);
});

test('an unscoped component write is refused by the database', function () {
    // Livewire component tests bypass the panel's middleware, so BindTenantScope
    // never runs and the request is unscoped. In production that cannot happen
    // -- Filament registers its Livewire routes with the panel's middleware, and
    // the HTTP test above covers that path.
    //
    // Worth keeping as a test anyway, because it is the fail-closed guarantee
    // observed directly: with no tenant bound the insert is refused by the
    // policy rather than quietly succeeding under someone else's id.
    // Asserted on the message rather than the class: the failure happens
    // during render, so Laravel wraps it in a ViewException. What matters is
    // which rule refused it.
    expect(fn () => Livewire\Livewire::actingAs(librarian('lib-furg'))
        ->test(WidgetSettings::class))
        ->toThrow(Exception::class, 'violates row-level security policy');
});

test('saving the widget settings never carries a tenant id', function () {
    $librarian = librarian('lib-furg');

    // Stands in for what BindTenantScope does around a real request.
    scope()->run('lib-furg', fn () => Livewire\Livewire::actingAs($librarian)
        ->test(WidgetSettings::class)
        ->fillForm(['assistant_name' => 'Bibi'])
        ->call('save')
        ->assertHasNoFormErrors());

    $furg = scope()->run('lib-furg', fn () => TenantWidget::find('lib-furg'));
    $ibict = scope()->run('lib-ibict', fn () => TenantWidget::find('lib-ibict'));

    expect($furg->assistant_name)->toBe('Bibi')
        ->and($ibict->assistant_name)->toBe('MARC.IA de lib-ibict');
});

test('a dead connection cannot make the scope recurse until php dies', function () {
    // apply() runs a query. On a connection that has died -- a second database
    // that went away mid-request -- Laravel reconnects to run it, reconnecting
    // fires ConnectionEstablished, AppServiceProvider listens for that and
    // calls apply(), and the whole thing recurses. Measured before the guard:
    // 1,102 stack frames and "Premature end of PHP process", taking the
    // dashboard with it.
    config(['database.connections.pgsql_dead' => [
        'driver' => 'pgsql',
        'url' => 'postgresql://nobody:nobody@127.0.0.1:1/nothing',
        'search_path' => 'public',
    ]]);

    $scope = new TenantScope(DB::connection('pgsql_dead'));

    // The failure surfaces as a connection error, not as exhaustion.
    expect(fn () => $scope->bind('lib-furg'))->toThrow(QueryException::class);
});
