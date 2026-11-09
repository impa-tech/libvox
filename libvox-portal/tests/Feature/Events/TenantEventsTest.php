<?php

use App\Database\TenantScope;
use App\Enums\IlsType;
use App\Enums\TenantStatus;
use App\Ils\IlsConnection;
use App\Ils\IlsCredentialStore;
use App\Models\Tenant;
use App\Models\TenantWidget;
use App\Tenancy\TenantProvisioner;
use Illuminate\Support\Facades\DB;

/*
 * The outbox.
 *
 * What it has to get right is not "did an event appear" but "did an event
 * appear exactly when the change it describes became real". A publisher that
 * announces a change which then rolled back is worse than one that announces
 * nothing.
 */

beforeEach(function () {
    DB::connection('pgsql_admin')->statement('TRUNCATE tenants CASCADE');
    DB::connection('pgsql_admin')->statement('TRUNCATE tenant_events');
});

function events(?string $tenantId = null): array
{
    $sql = 'SELECT tenant_id, event FROM tenant_events'
        .($tenantId !== null ? ' WHERE tenant_id = ?' : '')
        .' ORDER BY id';

    return DB::connection('pgsql_admin')->select($sql, $tenantId !== null ? [$tenantId] : []);
}

test('registering a library is announced', function () {
    app(TenantProvisioner::class)->provision(['tenant_id' => 'lib-furg', 'display_name' => 'FURG']);

    // One provisioning writes three rows -- the registry, the widget settings
    // and the web binding -- and each is a change worth re-reading for.
    expect(events('lib-furg'))->not->toBeEmpty()
        ->and(collect(events('lib-furg'))->pluck('event')->unique()->all())->toBe(['TenantUpdated']);
});

test('a rolled back change publishes nothing', function () {
    // The property the whole outbox exists for. Written in the same
    // transaction as the change, so it cannot claim one that did not happen.
    try {
        DB::connection('pgsql')->transaction(function () {
            Tenant::create(['tenant_id' => 'lib-furg', 'display_name' => 'FURG']);

            throw new RuntimeException('something went wrong after the write');
        });
    } catch (RuntimeException) {
        // expected
    }

    expect(events('lib-furg'))->toBeEmpty()
        ->and(Tenant::find('lib-furg'))->toBeNull();
});

test('editing a librarys widget settings is announced', function () {
    app(TenantProvisioner::class)->provision(['tenant_id' => 'lib-furg', 'display_name' => 'FURG']);
    DB::connection('pgsql_admin')->statement('TRUNCATE tenant_events');

    app(TenantScope::class)->run('lib-furg', fn () => TenantWidget::find('lib-furg')
        ->update(['assistant_name' => 'Bibi']));

    expect(events('lib-furg'))->toHaveCount(1);
});

test('storing a credential is announced without announcing the credential', function () {
    app(TenantProvisioner::class)->provision(['tenant_id' => 'lib-furg', 'display_name' => 'FURG']);
    DB::connection('pgsql_admin')->statement('TRUNCATE tenant_events');

    app(IlsCredentialStore::class)->put(
        new IlsConnection('lib-furg', IlsType::Koha, 'http://koha.local', 'a-client-id'),
        'the-actual-secret',
    );

    $rows = DB::connection('pgsql_admin')->select('SELECT * FROM tenant_events');

    // There is no payload column, so there is nowhere for a secret to be. The
    // consumer comes back and reads the tables under its own scope.
    foreach ($rows as $row) {
        expect(json_encode($row))->not->toContain('the-actual-secret')
            ->and(json_encode($row))->not->toContain('a-client-id');
    }

    expect($rows)->not->toBeEmpty();
});

test('an event carries only which library changed', function () {
    $columns = DB::connection('pgsql_admin')->select(
        "SELECT column_name FROM information_schema.columns
          WHERE table_name = 'tenant_events' ORDER BY ordinal_position"
    );

    expect(array_column($columns, 'column_name'))
        ->toBe(['id', 'tenant_id', 'event', 'occurred_at', 'consumed_at']);
});

test('deleting a library is announced, and the record outlives it', function () {
    app(TenantProvisioner::class)->provision(['tenant_id' => 'lib-furg', 'display_name' => 'FURG']);
    DB::connection('pgsql_admin')->statement('TRUNCATE tenant_events');

    Tenant::find('lib-furg')->delete();

    // No foreign key on tenant_events.tenant_id, on purpose: a log entry that
    // vanishes with the thing it describes is not a log, and this is the event
    // that tells a consumer to drop the tenant's index.
    expect(events('lib-furg'))->not->toBeEmpty()
        ->and(Tenant::find('lib-furg'))->toBeNull();
});

test('a change made outside the application is announced too', function () {
    app(TenantProvisioner::class)->provision(['tenant_id' => 'lib-furg', 'display_name' => 'FURG']);
    DB::connection('pgsql_admin')->statement('TRUNCATE tenant_events');

    // A trigger rather than an Eloquent observer, because "somebody forgot to
    // publish" is the failure mode and it is silent. This is the psql session,
    // the seeder and the bulk update an observer would never see.
    DB::connection('pgsql_admin')->statement(
        "UPDATE tenants SET status = 'active' WHERE tenant_id = 'lib-furg'"
    );

    expect(events('lib-furg'))->toHaveCount(1);
});

test('a bulk update announces every library it touched', function () {
    foreach (['lib-furg', 'lib-ibict'] as $id) {
        app(TenantProvisioner::class)->provision(['tenant_id' => $id, 'display_name' => $id]);
    }
    DB::connection('pgsql_admin')->statement('TRUNCATE tenant_events');

    Tenant::query()->update(['status' => TenantStatus::Active->value]);

    expect(events('lib-furg'))->toHaveCount(1)
        ->and(events('lib-ibict'))->toHaveCount(1);
});

test('a listener is notified on the channel the readme names', function () {
    $listener = DB::connection('pgsql_admin')->getPdo();
    $listener->exec('LISTEN libvox_tenant_events');

    app(TenantProvisioner::class)->provision(['tenant_id' => 'lib-furg', 'display_name' => 'FURG']);

    // pg_notify is delivered on commit, which is what makes it safe to pair
    // with a row written in the same transaction: a listener never learns of a
    // change that then rolled back.
    $notification = $listener->pgsqlGetNotify(PDO::FETCH_ASSOC, 2000);

    expect($notification)->not->toBeFalse()
        ->and($notification['message'])->toBe('libvox_tenant_events')
        ->and(json_decode($notification['payload'], true))->toHaveKeys(['id', 'tenant_id', 'event']);

    $listener->exec('UNLISTEN libvox_tenant_events');
});

test('nothing consumes the outbox yet, and the rows say so', function () {
    app(TenantProvisioner::class)->provision(['tenant_id' => 'lib-furg', 'display_name' => 'FURG']);

    $unconsumed = DB::connection('pgsql_admin')->select(
        'SELECT id FROM tenant_events WHERE consumed_at IS NULL'
    );

    // Not a queue with no worker -- a record that something happened, which is
    // useful the first time anyone asks when a library last changed its
    // configuration.
    expect($unconsumed)->not->toBeEmpty();
});
