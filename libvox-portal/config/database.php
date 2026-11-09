<?php

use Illuminate\Support\Str;

return [

    /*
    |--------------------------------------------------------------------------
    | Default Database Connection Name
    |--------------------------------------------------------------------------
    |
    | Here you may specify which of the database connections below you wish
    | to use as your default connection for database operations. This is
    | the connection which will be utilized unless another connection
    | is explicitly specified when you execute a query / statement.
    |
    */

    'default' => env('DB_CONNECTION', 'pgsql'),

    /*
    |--------------------------------------------------------------------------
    | Database Connections
    |--------------------------------------------------------------------------
    |
    | Below are all of the database connections defined for your application.
    | An example configuration is provided for each database system which
    | is supported by Laravel. You're free to add / remove connections.
    |
    */

    'connections' => [

        /*
         * The Tenant DB, as the application. An unprivileged role, so the
         * row-level security policies actually apply -- a superuser bypasses
         * them unconditionally and would leave every policy in the schema
         * enforcing nothing. AppServiceProvider refuses to boot if this
         * connection turns out to be privileged.
         *
         * The DSN is read whole, in the same postgresql:// form /libvox-sync
         * and /libvox-core use, so one library has one connection string
         * rather than five environment variables per role.
         */
        'pgsql' => [
            'driver' => 'pgsql',
            'url' => env('LIBVOX_TENANT_DB_DSN'),
            'charset' => 'utf8',
            'prefix' => '',
            'prefix_indexes' => true,
            'search_path' => 'public',
            'sslmode' => 'prefer',
        ],

        /*
         * The owner, for `php artisan migrate --database=pgsql_admin` and
         * nothing else. Creating an extension, a policy or a SECURITY DEFINER
         * function needs privileges the application must never hold while it
         * is serving a request.
         */
        'pgsql_admin' => [
            'driver' => 'pgsql',
            'url' => env('LIBVOX_TENANT_DB_ADMIN_DSN'),
            'charset' => 'utf8',
            'prefix' => '',
            'prefix_indexes' => true,
            'search_path' => 'public',
            'sslmode' => 'prefer',
        ],

        /*
         * The Discovery Index, which /libvox-sync owns and this application
         * only reads.
         *
         * A second database, not a second schema: it is a derived read model
         * rebuilt from an ILS, and the one-writer rule is about writing. Core
         * already reads the Tenant DB the same way round, and nothing here
         * joins across the two.
         *
         * Read-only is not a setting PostgreSQL enforces from this side -- it
         * is enforced by the grants libvox_app holds there, which are SELECT on
         * the view and nothing else. What this application must not do is
         * assume otherwise; see CatalogRecord.
         */
        /*
         * The conversation log, which /libvox-core owns and this application
         * only reads.
         *
         * On the same PostgreSQL instance as the Tenant DB -- docker/initdb/03
         * creates it -- and a different database, which is what the one-writer
         * rule is about. Inside the compose network it is `postgres:5432`, with
         * no host gateway in the way, unlike the Discovery Index.
         *
         * `libvox_app`, so row-level security applies. The operator panel binds
         * one library at a time rather than holding a credential that reads
         * every library's conversations; see ConversationScope.
         */
        'pgsql_conversations' => [
            'driver' => 'pgsql',
            'url' => env('LIBVOX_CONVERSATION_DB_DSN'),
            'charset' => 'utf8',
            'prefix' => '',
            'prefix_indexes' => true,
            'search_path' => 'public',
            'sslmode' => 'prefer',
        ],

        'pgsql_discovery' => [
            'driver' => 'pgsql',
            'url' => env('LIBVOX_DISCOVERY_DB_DSN'),
            'charset' => 'utf8',
            'prefix' => '',
            'prefix_indexes' => true,
            'search_path' => 'public',
            'sslmode' => 'prefer',
        ],

    ],

    'migrations' => [
        'table' => 'migrations',
        'update_date_on_publish' => true,
    ],

    /*
    |--------------------------------------------------------------------------
    | Redis Databases
    |--------------------------------------------------------------------------
    |
    | Redis is an open source, fast, and advanced key-value store that also
    | provides a richer body of commands than a typical key-value system
    | such as Memcached. You may define your connection settings here.
    |
    */

    'redis' => [

        'client' => env('REDIS_CLIENT', 'phpredis'),

        'options' => [
            'cluster' => env('REDIS_CLUSTER', 'redis'),
            'prefix' => env('REDIS_PREFIX', Str::slug((string) env('APP_NAME', 'laravel')).'-database-'),
            'persistent' => env('REDIS_PERSISTENT', false),
        ],

        'default' => [
            'url' => env('REDIS_URL'),
            'host' => env('REDIS_HOST', '127.0.0.1'),
            'username' => env('REDIS_USERNAME'),
            'password' => env('REDIS_PASSWORD'),
            'port' => env('REDIS_PORT', '6379'),
            'database' => env('REDIS_DB', '0'),
            'max_retries' => env('REDIS_MAX_RETRIES', 3),
            'backoff_algorithm' => env('REDIS_BACKOFF_ALGORITHM', 'decorrelated_jitter'),
            'backoff_base' => env('REDIS_BACKOFF_BASE', 100),
            'backoff_cap' => env('REDIS_BACKOFF_CAP', 1000),
        ],

        'cache' => [
            'url' => env('REDIS_URL'),
            'host' => env('REDIS_HOST', '127.0.0.1'),
            'username' => env('REDIS_USERNAME'),
            'password' => env('REDIS_PASSWORD'),
            'port' => env('REDIS_PORT', '6379'),
            'database' => env('REDIS_CACHE_DB', '1'),
            'max_retries' => env('REDIS_MAX_RETRIES', 3),
            'backoff_algorithm' => env('REDIS_BACKOFF_ALGORITHM', 'decorrelated_jitter'),
            'backoff_base' => env('REDIS_BACKOFF_BASE', 100),
            'backoff_cap' => env('REDIS_BACKOFF_CAP', 1000),
        ],

    ],

];
