<?php

use App\Models\Tenant;
use Illuminate\Support\Facades\DB;

/*
 * Asking for a catalogue import.
 *
 * The guarantee under test is the shape of the function rather than any check
 * the panel performs: request_catalog_sync() takes no argument, so there is
 * nowhere to write another library's name.
 */

beforeEach(function () {
    DB::connection('pgsql_admin')->statement('TRUNCATE tenants, users, model_has_roles, roles CASCADE');
    DB::connection('pgsql_admin')->statement('DELETE FROM tenant_events');
    seed_roles();
});

function configured(string $tenantId, ?string $ilsAt = 'now()'): Tenant
{
    $tenant = Tenant::create(['tenant_id' => $tenantId, 'display_name' => $tenantId]);

    if ($ilsAt !== null) {
        DB::connection('pgsql_admin')->update(
            'UPDATE tenants SET ils_configured_at = now() WHERE tenant_id = ?', [$tenantId]
        );
    }

    return $tenant->fresh();
}

function askAs(?string $tenantId): bool
{
    DB::select('SELECT set_config(?, ?, false)', ['libvox.tenant_id', $tenantId ?? '']);

    return (bool) DB::selectOne('SELECT request_catalog_sync() AS ok')->ok;
}

function pending(): array
{
    return DB::connection('pgsql_admin')
        ->table('tenant_events')
        ->where('event', 'SyncRequested')
        ->whereNull('consumed_at')
        ->pluck('tenant_id')
        ->all();
}

test('a librarian can only ask for her own library, because there is no argument', function () {
    configured('lib-furg');
    configured('lib-ibict');

    expect(askAs('lib-furg'))->toBeTrue()
        ->and(pending())->toBe(['lib-furg']);
});

test('asking without a tenant bound does nothing at all', function () {
    configured('lib-furg');

    expect(askAs(null))->toBeFalse()
        ->and(pending())->toBe([]);
});

test('a second ask while one is pending changes nothing', function () {
    // Enforced by a unique index rather than a read-then-insert, so two clicks
    // a millisecond apart cannot both get through.
    configured('lib-furg');

    expect(askAs('lib-furg'))->toBeTrue()
        ->and(askAs('lib-furg'))->toBeFalse()
        ->and(pending())->toBe(['lib-furg']);
});

test('asking again is allowed once the first was answered', function () {
    configured('lib-furg');
    askAs('lib-furg');

    DB::connection('pgsql_admin')->statement('UPDATE tenant_events SET consumed_at = now()');

    expect(askAs('lib-furg'))->toBeTrue();
});

test('a library with no ILS configured cannot ask', function () {
    // Refused in the function rather than only in the panel, so the rule holds
    // for any caller.
    configured('lib-furg', ilsAt: null);

    expect(askAs('lib-furg'))->toBeFalse()
        ->and(pending())->toBe([]);
});

test('a library still being provisioned can ask, so its index is ready when it opens', function () {
    // `status` defaults to 'provisioning', and that is exactly when a library
    // wants its catalogue read: the index should be built before MARC.IA is
    // switched on, not after.
    configured('lib-furg');

    expect(Tenant::find('lib-furg')->status->value)->toBe('provisioning')
        ->and(askAs('lib-furg'))->toBeTrue();
});

test('a suspended library cannot ask', function () {
    configured('lib-furg');
    DB::connection('pgsql_admin')->update("UPDATE tenants SET status = 'suspended'");

    expect(askAs('lib-furg'))->toBeFalse();
});

test('the request is an event, on the outbox that already existed', function () {
    // Rather than a column, or a queue table of its own. And not TenantUpdated:
    // that fires on every configuration change, the widget's colour included.
    configured('lib-furg');

    DB::connection('pgsql_admin')->statement('DELETE FROM tenant_events');
    askAs('lib-furg');

    $events = DB::connection('pgsql_admin')->table('tenant_events')->pluck('event')->all();

    expect($events)->toBe(['SyncRequested']);
});
