<?php

use App\Enums\UserRole;
use App\Models\Tenant;
use App\Models\User;
use Database\Seeders\RoleSeeder;
use Illuminate\Database\Connection;
use Illuminate\Support\Facades\Artisan;
use Illuminate\Support\Facades\DB;
use Tests\TestCase;

/*
 * Tests run against a real PostgreSQL, and skip when it is not there rather
 * than failing -- the same convention as /libvox-sync's test_schema.py, so a
 * contributor without Docker running gets guidance instead of a wall of
 * connection errors.
 */

uses(TestCase::class)
    ->beforeEach(function () {
        if (! tenant_db_is_reachable()) {
            test()->markTestSkipped(
                'no Tenant DB; run `docker compose up -d postgres` in libvox-portal'
            );
        }

        migrate_tenant_db_once();
    })
    ->in('Feature');

function tenant_db_is_reachable(): bool
{
    static $reachable = null;

    if ($reachable === null) {
        try {
            DB::connection('pgsql_admin')->getPdo();
            $reachable = true;
        } catch (Throwable) {
            $reachable = false;
        }
    }

    return $reachable;
}

/**
 * Migrate once per suite, as the owner.
 *
 * Not RefreshDatabase: it wraps every test in a transaction, and this suite
 * needs real commits -- the isolation tests switch database role, and a
 * transaction-local tenant binding inside an outer transaction lands in a
 * savepoint rather than where the assertions look for it.
 */
function migrate_tenant_db_once(): void
{
    static $done = false;

    if (! $done) {
        Artisan::call('migrate:fresh', ['--database' => 'pgsql_admin', '--force' => true]);
        $done = true;
    }
}

/*
 * Shared fixtures. Roles are data, so every test that authenticates needs them
 * seeded -- migrate:fresh wipes them along with everything else.
 */
function seed_roles(): void
{
    (new RoleSeeder)->run();
}

function operator(): User
{
    $user = User::create([
        'name' => 'Operator',
        'email' => 'operator@libvox.local',
        'password' => 'secret',
    ]);
    $user->assignRole(UserRole::Operator->value);

    return $user->fresh();
}

function librarian(?string $tenantId = 'lib-furg'): User
{
    if ($tenantId !== null) {
        Tenant::firstOrCreate(['tenant_id' => $tenantId], ['display_name' => $tenantId]);
    }

    $user = User::create([
        'name' => 'Librarian',
        'email' => 'librarian@biblioteca.local',
        'password' => 'secret',
        'tenant_id' => $tenantId,
    ]);
    $user->assignRole(UserRole::Librarian->value);

    return $user->fresh();
}

/** A second librarian, for tests that need two accounts at once. */
function librarian2(?string $tenantId = 'lib-furg'): User
{
    if ($tenantId !== null) {
        Tenant::firstOrCreate(['tenant_id' => $tenantId], ['display_name' => $tenantId]);
    }

    $user = User::create([
        'name' => 'Another librarian',
        'email' => 'librarian2@biblioteca.local',
        'password' => 'secret',
        'tenant_id' => $tenantId,
    ]);
    $user->assignRole(UserRole::Librarian->value);

    return $user->fresh();
}

/**
 * The Discovery Index as its owner, for setting a fixture up.
 *
 * Registered through config rather than DB::connectUsing, which does not run
 * the URL parser and answers `Undefined array key "database"` for a connection
 * described by a DSN.
 */
function discovery(): Connection
{
    config(['database.connections.pgsql_discovery_admin' => [
        'driver' => 'pgsql',
        'url' => env('LIBVOX_DISCOVERY_DB_ADMIN_DSN'),
        'charset' => 'utf8',
        'prefix' => '',
        'prefix_indexes' => true,
        'search_path' => 'public',
        'sslmode' => 'prefer',
    ]]);

    return DB::connection('pgsql_discovery_admin');
}

function discovery_db_is_reachable(): bool
{
    try {
        discovery()->select('SELECT 1');

        return true;
    } catch (Throwable) {
        return false;
    }
}

/*
 * The conversation log, for any test that reads it.
 *
 * These lived inside ConversationReadingTest until a second file needed them:
 * Pest scopes a function to its file, so the transcript tests could not see
 * them. They are fixtures for a database this application does not own, and
 * they belong where every test can reach them.
 */

function conversations(): Connection
{
    config(['database.connections.pgsql_conversations_admin' => [
        'driver' => 'pgsql',
        'url' => env('LIBVOX_CONVERSATION_DB_ADMIN_DSN'),
        'charset' => 'utf8',
        'prefix' => '',
        'prefix_indexes' => true,
        'search_path' => 'public',
        'sslmode' => 'prefer',
    ]]);

    return DB::connection('pgsql_conversations_admin');
}

function conversation_db_is_reachable(): bool
{
    try {
        conversations()->select('SELECT 1');

        return true;
    } catch (Throwable) {
        return false;
    }
}

/** One stored conversation, as /libvox-core would have written it. */
function storeConversation(string $tenantId, array $turns, ?string $id = null): string
{
    Tenant::firstOrCreate(['tenant_id' => $tenantId], ['display_name' => $tenantId]);

    $id ??= (string) Str::uuid();

    conversations()->insert(
        'INSERT INTO conversations (conversation_id, tenant_id, channel) VALUES (?, ?, ?)',
        [$id, $tenantId, 'web'],
    );

    foreach ($turns as $turn) {
        conversations()->insert(
            'INSERT INTO messages (conversation_id, role, content, tool_name) VALUES (?, ?, ?, ?)',
            [$id, $turn['role'], $turn['content'] ?? null, $turn['tool_name'] ?? null],
        );
    }

    return $id;
}

/** A reader's thumb on the last answer of a conversation, as the widget left it. */
function rateLastAnswer(string $conversationId, bool $useful): void
{
    conversations()->insert(
        'INSERT INTO message_ratings (message_id, useful) '
        .'SELECT message_id, ? FROM messages '
        ." WHERE conversation_id = ? AND role = 'assistant' AND tool_name IS NULL "
        .' ORDER BY message_id DESC LIMIT 1',
        [$useful, $conversationId],
    );
}

const FOUND_NOTHING = '{"index_status": "ready", "records": [], "truncated": false}';
const TOOL_BROKE = '{"error": "the catalog could not answer"}';
