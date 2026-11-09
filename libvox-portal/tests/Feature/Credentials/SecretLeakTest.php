<?php

use App\Database\TenantScope;
use App\Enums\IlsType;
use App\Filament\Library\Pages\CatalogConnection;
use App\Ils\IlsConnection;
use App\Ils\IlsCredentialStore;
use App\Logging\RedactSecrets;
use App\Models\Tenant;
use Illuminate\Contracts\Debug\ExceptionHandler;
use Illuminate\Database\QueryException;
use Illuminate\Support\Facades\DB;
use Monolog\Level;
use Monolog\LogRecord;

/*
 * The three places a secret could escape, and what stops it at each.
 *
 * Storing a credential means passing the plaintext and LIBVOX_CRYPTO_KEY to
 * PostgreSQL as query bindings. That is the right call -- it is what makes the
 * ciphertext readable by /libvox-sync without a second implementation -- and
 * it puts both values on a path that Laravel is unusually good at writing down.
 */

beforeEach(function () {
    DB::connection('pgsql_admin')
        ->statement('TRUNCATE tenants, users, model_has_roles, roles CASCADE');
});

test('the client secret is never flashed back into the session', function () {
    // Laravel's default list is password, password_confirmation and
    // current_password. Without client_secret on it, one failed validation
    // puts a library's ILS secret into the session and returns it to the form
    // as old input. Read from the configured handler rather than from
    // bootstrap/app.php, so the test fails if the list is moved or narrowed.
    $handler = app(ExceptionHandler::class);

    $property = new ReflectionProperty($handler, 'dontFlash');

    expect($property->getValue($handler))->toContain('client_secret');
});

test('a query exception carrying the key is replaced before it can be reported', function () {
    $key = config('libvox.crypto_key');

    // The shape Laravel produces: bindings interpolated into the message.
    $original = new QueryException(
        'pgsql',
        'insert into tenant_ils values (?, pgp_sym_encrypt(?, ?, ?))',
        ['lib-furg', 'the-actual-secret', $key, 'compress-algo=0'],
        new PDOException('SQLSTATE[42501]: Insufficient privilege'),
    );

    expect($original->getMessage())->toContain($key);

    $handler = app(ExceptionHandler::class);
    $mapped = (new ReflectionMethod($handler, 'mapException'))->invoke($handler, $original);

    expect($mapped)->not->toBeInstanceOf(QueryException::class)
        ->and($mapped->getMessage())->not->toContain($key)
        ->and($mapped->getMessage())->not->toContain('the-actual-secret');
});

test('a query exception without the key keeps its message', function () {
    // Mapping every QueryException would cost the operator the database's own
    // explanation for every constraint violation in the application. The map
    // is narrow on purpose, and this is the half that proves it.
    $original = new QueryException(
        'pgsql',
        'insert into tenants (tenant_id) values (?)',
        ['lib-furg'],
        new PDOException('SQLSTATE[23505]: Unique violation'),
    );

    $handler = app(ExceptionHandler::class);
    $mapped = (new ReflectionMethod($handler, 'mapException'))->invoke($handler, $original);

    expect($mapped)->toBeInstanceOf(QueryException::class)
        ->and($mapped->getMessage())->toContain('Unique violation');
});

test('the crypto key never survives into a log record', function () {
    $key = config('libvox.crypto_key');

    $record = new LogRecord(
        datetime: new DateTimeImmutable,
        channel: 'testing',
        level: Level::Error,
        message: "failed with key {$key}",
        context: ['sql' => "pgp_sym_encrypt('s', '{$key}')", 'nested' => ['also' => $key]],
    );

    $redacted = (new RedactSecrets)($record);

    expect($redacted->message)->not->toContain($key)
        ->and($redacted->context['sql'])->not->toContain($key)
        ->and($redacted->context['nested']['also'])->not->toContain($key)
        // Redacted, not deleted: a log line that silently loses content is
        // worse to debug than one that says something was removed.
        ->and($redacted->message)->toContain('[LIBVOX_CRYPTO_KEY redacted]');
});

test('no query inspector is installed', function () {
    // Telescope and Debugbar both render query bindings, and the bindings here
    // are the secret and the key that encrypts it. Neither may be added to this
    // application -- not even under require-dev, where "it is only local" is
    // exactly how a developer's log file ends up holding a library's
    // credentials.
    $composer = json_decode(file_get_contents(base_path('composer.json')), true);

    $installed = array_keys(array_merge(
        $composer['require'] ?? [],
        $composer['require-dev'] ?? [],
    ));

    expect($installed)->not->toContain('laravel/telescope')
        ->and($installed)->not->toContain('barryvdh/laravel-debugbar');
});

test('the catalog connection form never returns a stored secret', function () {
    seed_roles();
    Tenant::firstOrCreate(['tenant_id' => 'lib-furg'], ['display_name' => 'FURG']);

    app(IlsCredentialStore::class)->put(
        new IlsConnection('lib-furg', IlsType::Koha, 'http://koha.local', 'a-client-id'),
        'the-actual-secret',
    );

    $librarian = librarian('lib-furg');

    app(TenantScope::class)->run('lib-furg', function () use ($librarian) {
        Livewire\Livewire::actingAs($librarian)
            ->test(CatalogConnection::class)
            // The client_id comes back, because it is not a secret.
            ->assertFormSet(['client_id' => 'a-client-id'])
            // The secret does not, because there is no method that could
            // produce it. The field renders empty and the helper text says a
            // secret is stored.
            ->assertFormSet(['client_secret' => null])
            ->assertDontSee('the-actual-secret');
    });
});

test('a blank secret field leaves the stored secret alone', function () {
    seed_roles();
    Tenant::firstOrCreate(['tenant_id' => 'lib-furg'], ['display_name' => 'FURG']);

    $store = app(IlsCredentialStore::class);
    $store->put(new IlsConnection('lib-furg', IlsType::Koha, 'http://koha.local', 'old-id'), 'keep-me');

    $librarian = librarian('lib-furg');

    app(TenantScope::class)->run('lib-furg', function () use ($librarian) {
        Livewire\Livewire::actingAs($librarian)
            ->test(CatalogConnection::class)
            ->fillForm(['client_id' => 'new-id'])
            ->call('save')
            ->assertHasNoFormErrors();
    });

    $secret = DB::connection('pgsql_admin')->selectOne(
        'SELECT pgp_sym_decrypt(client_secret_enc, ?) AS secret, client_id FROM tenant_ils WHERE tenant_id = ?',
        [config('libvox.crypto_key'), 'lib-furg'],
    );

    expect($secret->client_id)->toBe('new-id')
        ->and($secret->secret)->toBe('keep-me');
});
