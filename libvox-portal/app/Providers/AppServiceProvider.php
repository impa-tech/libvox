<?php

namespace App\Providers;

use App\Database\ConversationScope;
use App\Database\DiscoveryScope;
use App\Database\TenantScope;
use App\Ils\IlsConnectionCheck;
use App\Ils\IlsCredentialStore;
use App\Ils\PgcryptoCipher;
use App\Ils\SecretCipher;
use Illuminate\Database\Events\ConnectionEstablished;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Event;
use Illuminate\Support\ServiceProvider;
use RuntimeException;

class AppServiceProvider extends ServiceProvider
{
    public function register(): void
    {
        // One per request: the scope remembers which library the request is
        // bound to, and two instances would disagree about that.
        $this->app->singleton(TenantScope::class, fn ($app) => new TenantScope(
            $app['db']->connection('pgsql')
        ));

        // The Discovery Index carries the same policy, on its own connection,
        // and therefore needs its own binding. Resolved lazily: a deployment
        // that never opens the catalogue screen never connects to it.
        $this->app->singleton(DiscoveryScope::class, fn ($app) => new DiscoveryScope(
            $app['db']->connection('pgsql_discovery')
        ));

        // Same shape again, for the conversation log. Two one-line subclasses
        // do not yet pay for an abstraction over them; a third would.
        $this->app->singleton(ConversationScope::class, fn ($app) => new ConversationScope(
            $app['db']->connection('pgsql_conversations')
        ));

        $this->app->singleton(SecretCipher::class, function (): SecretCipher {
            $key = config('libvox.crypto_key');

            throw_if(blank($key), new RuntimeException(
                "LIBVOX_CRYPTO_KEY is not set.\n"
                .'It encrypts stored ILS secrets and must not live in the database '
                ."or in the repository. For local development:\n"
                ."  export LIBVOX_CRYPTO_KEY=\"\$(openssl rand -base64 32)\"\n"
                .'Keep it -- credentials stored under one key cannot be read back '
                .'with another, and /libvox-sync must be given the same value to '
                .'read what this portal writes.'
            ));

            return new PgcryptoCipher($key);
        });

        $this->app->singleton(IlsConnectionCheck::class, fn (): IlsConnectionCheck => new IlsConnectionCheck(
            config('libvox.core_url'),
            config('libvox.core_service_token'),
        ));

        $this->app->singleton(IlsCredentialStore::class, fn ($app) => new IlsCredentialStore(
            $app['db']->connection('pgsql'),
            $app->make(SecretCipher::class),
            $app->make(TenantScope::class),
        ));
    }

    public function boot(): void
    {
        $this->assertTheApplicationCannotBypassRowLevelSecurity();
        $this->rebindTheTenantScopeOnReconnect();
    }

    /**
     * A session setting dies with the connection that holds it.
     *
     * If Laravel reconnects mid-request -- after a lost connection -- the tenant
     * binding would be gone and the rest of the request would read empty rather
     * than fail, which is the quiet kind of wrong.
     */
    private function rebindTheTenantScopeOnReconnect(): void
    {
        Event::listen(ConnectionEstablished::class, function (ConnectionEstablished $event): void {
            if ($event->connectionName === 'pgsql') {
                app(TenantScope::class)->apply();
            }

            // `resolved` first: this event fires while the container is
            // building DiscoveryScope itself -- its closure asks for this very
            // connection -- and resolving it from inside its own constructor
            // would re-enter a singleton that does not exist yet.
            if ($event->connectionName === 'pgsql_discovery'
                && app()->resolved(DiscoveryScope::class)) {
                app(DiscoveryScope::class)->apply();
            }

            if ($event->connectionName === 'pgsql_conversations'
                && app()->resolved(ConversationScope::class)) {
                app(ConversationScope::class)->apply();
            }
        });
    }

    /**
     * Refuse to serve a request on a privileged database connection.
     *
     * Tenant isolation in this module is enforced by PostgreSQL row-level
     * security, and a superuser -- or any role holding BYPASSRLS -- ignores it
     * unconditionally. Connect as one and every policy in the schema is still
     * there, still readable, and enforcing nothing at all.
     *
     * That failure is silent: the panels work, the tests that matter would
     * pass against them, and one library's credentials would be visible from
     * another library's session. A convenient DSN during setup is exactly how
     * it happens, so the application checks rather than trusts.
     *
     * Console commands are exempt because `migrate` legitimately runs as the
     * owner. What is guarded is serving a request.
     */
    private function assertTheApplicationCannotBypassRowLevelSecurity(): void
    {
        if ($this->app->runningInConsole() || ! config('libvox.assert_unprivileged')) {
            return;
        }

        $role = DB::connection('pgsql')->selectOne(
            'SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user'
        );

        throw_if($role === null || $role->rolsuper || $role->rolbypassrls, new RuntimeException(
            'LIBVOX_TENANT_DB_DSN connects as a role that bypasses row-level '
            .'security. Tenant isolation would be inert. Use libvox_app.'
        ));

        // Not checked here: the Discovery Index. The same failure applies to it
        // -- a privileged role there shows every library's catalogue in every
        // librarian's panel -- but this method runs on boot, and connecting to
        // a second database on every request to a screen that may never be
        // opened is a cost paid by everyone for one page. CatalogRecord asserts
        // it instead, on the connection, the first time it is used.
    }
}
