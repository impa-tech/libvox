<?php

use App\Enums\IlsType;
use App\Ils\CredentialStorageFailed;
use App\Ils\IlsConnection;
use App\Ils\IlsCredentialStore;
use App\Models\Tenant;
use Illuminate\Support\Facades\DB;

/*
 * Storing an ILS secret, and the absence of any way to read one back.
 */

beforeEach(function () {
    DB::connection('pgsql_admin')->statement('TRUNCATE tenants CASCADE');

    foreach (['lib-furg', 'lib-ibict'] as $id) {
        Tenant::create(['tenant_id' => $id, 'display_name' => $id]);
    }
});

function store(): IlsCredentialStore
{
    return app(IlsCredentialStore::class);
}

function connection(string $tenantId = 'lib-furg'): IlsConnection
{
    return new IlsConnection(
        tenantId: $tenantId,
        ilsType: IlsType::Koha,
        baseUrl: 'http://127.0.0.1:8081',
        clientId: 'a-client-id',
    );
}

test('the stored client secret is never plaintext in the database', function () {
    store()->put(connection(), 'the-actual-secret');

    // Read as the superuser, which bypasses RLS -- the point is what is in the
    // bytes, not who can reach them.
    $row = DB::connection('pgsql_admin')->selectOne(
        "SELECT client_secret_enc, encode(client_secret_enc, 'escape') AS readable
           FROM tenant_ils WHERE tenant_id = 'lib-furg'"
    );

    expect($row->readable)->not->toContain('the-actual-secret');
});

test('the client id is stored in plaintext, deliberately', function () {
    // In OAuth2 client-credentials the client_id is an identifier, not a
    // secret. Encrypting it would imply a protection it does not need and
    // would make the operator's inventory useless.
    store()->put(connection(), 'the-actual-secret');

    $row = DB::connection('pgsql_admin')->selectOne(
        "SELECT client_id FROM tenant_ils WHERE tenant_id = 'lib-furg'"
    );

    expect($row->client_id)->toBe('a-client-id');
});

test('what libvox-sync reads back is what the portal wrote', function () {
    // The cross-language contract, asserted in the language that writes it.
    // Nearly tautological under pgcrypto -- and that is the point: it is the
    // assertion that fails the day someone "improves" the cipher options on one
    // side only.
    store()->put(connection(), 'the-actual-secret');

    $row = DB::connection('pgsql_admin')->selectOne(
        'SELECT pgp_sym_decrypt(client_secret_enc, ?) AS secret
           FROM tenant_ils WHERE tenant_id = ?',
        [config('libvox.crypto_key'), 'lib-furg'],
    );

    expect($row->secret)->toBe('the-actual-secret');
});

test('a secret written under one key cannot be read with another', function () {
    store()->put(connection(), 'the-actual-secret');

    expect(fn () => DB::connection('pgsql_admin')->selectOne(
        'SELECT pgp_sym_decrypt(client_secret_enc, ?) AS secret
           FROM tenant_ils WHERE tenant_id = ?',
        ['a-different-key', 'lib-furg'],
    ))->toThrow(Exception::class);
});

test('the store has no way to read a secret back', function () {
    // A structural test, not a behavioural one: the guarantee is that the
    // method does not exist, so asserting on its absence is the only way to
    // notice the day someone adds it.
    $methods = get_class_methods(IlsCredentialStore::class);

    expect($methods)->toBe(['__construct', 'put', 'updateConnection', 'describe', 'forget']);
});

test('nothing in the application decrypts a credential', function () {
    // /libvox-core is the only component that may. Enforced here by looking,
    // because an interface with no read path is easy to route around with one
    // raw query.
    $offenders = [];

    $files = new RecursiveIteratorIterator(new RecursiveDirectoryIterator(app_path()));
    foreach ($files as $file) {
        if (! $file->isFile() || $file->getExtension() !== 'php') {
            continue;
        }

        // Tokenized rather than grepped: comments explaining why there is no
        // read path would otherwise register as one.
        foreach (token_get_all(file_get_contents($file->getPathname())) as $token) {
            if (! is_array($token) || in_array($token[0], [T_COMMENT, T_DOC_COMMENT], true)) {
                continue;
            }

            if (str_contains($token[1], 'pgp_sym_decrypt')) {
                $offenders[] = $file->getPathname();
            }
        }
    }

    expect($offenders)->toBeEmpty();
});

test('describe returns the connection without the secret', function () {
    store()->put(connection(), 'the-actual-secret');

    $summary = store()->describe('lib-furg');

    expect($summary->connection->clientId)->toBe('a-client-id')
        ->and($summary->connection->baseUrl)->toBe('http://127.0.0.1:8081')
        ->and($summary->secretUpdatedAt)->not->toBeNull()
        // The secret is not a property of the DTO, so it cannot be reached.
        ->and(property_exists($summary->connection, 'clientSecret'))->toBeFalse();
});

test('describe finds nothing for an unconfigured library', function () {
    expect(store()->describe('lib-ibict'))->toBeNull();
});

test('a library cannot store credentials for another library', function () {
    // The store binds the scope to the connection's own tenant, so the policy
    // sees a matching row. What this asserts is that the row lands under the
    // right library and nowhere else.
    store()->put(connection('lib-furg'), 'furg-secret');
    store()->put(connection('lib-ibict'), 'ibict-secret');

    $rows = DB::connection('pgsql_admin')->select('SELECT tenant_id FROM tenant_ils ORDER BY tenant_id');

    expect(array_column($rows, 'tenant_id'))->toBe(['lib-furg', 'lib-ibict']);
});

test('storing for a library that does not exist is refused', function () {
    // The foreign key that became possible only once a tenants registry
    // existed. Before it, a typo created a ghost library in silence.
    expect(fn () => store()->put(connection('lib-ghost'), 'secret'))
        ->toThrow(CredentialStorageFailed::class);
});

test('a storage failure carries neither the secret nor the key', function () {
    try {
        store()->put(connection('lib-ghost'), 'the-actual-secret');
        $thrown = null;
    } catch (CredentialStorageFailed $e) {
        $thrown = $e;
    }

    $rendered = (string) $thrown;

    expect($rendered)->not->toContain('the-actual-secret')
        ->and($rendered)->not->toContain(config('libvox.crypto_key'))
        // Dropped rather than chained: a chained QueryException would carry
        // both in its own message.
        ->and($thrown->getPrevious())->toBeNull();
});

test('the crypto key is never written to the database', function () {
    store()->put(connection(), 'the-actual-secret');

    $key = config('libvox.crypto_key');
    $dump = DB::connection('pgsql_admin')->select(
        "SELECT encode(client_secret_enc, 'escape') AS body, client_id, base_url FROM tenant_ils"
    );

    foreach ($dump as $row) {
        expect((array) $row)->each->not->toContain($key);
    }
});

test('updating the connection without a secret does not blank the stored one', function () {
    // The bug this method exists to prevent: the secret field is write-only, so
    // it renders blank on every edit, and a librarian changing only the URL
    // submits no secret at all.
    store()->put(connection(), 'keep-me');

    store()->updateConnection(new IlsConnection(
        tenantId: 'lib-furg',
        ilsType: IlsType::Koha,
        baseUrl: 'http://moved.example',
        clientId: 'a-client-id',
    ));

    $row = DB::connection('pgsql_admin')->selectOne(
        'SELECT base_url, pgp_sym_decrypt(client_secret_enc, ?) AS secret
           FROM tenant_ils WHERE tenant_id = ?',
        [config('libvox.crypto_key'), 'lib-furg'],
    );

    expect($row->base_url)->toBe('http://moved.example')
        ->and($row->secret)->toBe('keep-me');
});

test('updating a connection that was never configured is refused', function () {
    // There is no row to update and no secret to invent, so this cannot fall
    // back to an insert.
    expect(fn () => store()->updateConnection(connection()))
        ->toThrow(CredentialStorageFailed::class);
});
