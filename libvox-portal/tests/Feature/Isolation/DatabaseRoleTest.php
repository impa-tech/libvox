<?php

use Illuminate\Support\Facades\DB;

/*
 * The test every other isolation test depends on.
 *
 * Tenant isolation here is PostgreSQL row-level security, and a superuser --
 * or any role holding BYPASSRLS -- ignores it unconditionally. Connected as
 * one, every policy in the schema is still present, still readable, and
 * enforcing nothing: the panels work, and the tests that assert one library
 * cannot see another's rows pass without proving anything at all.
 *
 * So this runs first, and it is the reason the rest is not vacuous.
 */

test('the application connection is neither a superuser nor bypassrls', function () {
    $role = DB::connection('pgsql')->selectOne(
        'SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user'
    );

    expect($role)->not->toBeNull()
        ->and($role->rolsuper)->toBeFalse('the application connects as a superuser; RLS would be inert')
        ->and($role->rolbypassrls)->toBeFalse('the application role can bypass RLS; isolation would be inert');
});

test('the application and the migration connections are different roles', function () {
    $app = DB::connection('pgsql')->selectOne('SELECT current_user');
    $admin = DB::connection('pgsql_admin')->selectOne('SELECT current_user');

    // Migrations create extensions, policies and SECURITY DEFINER functions;
    // serving a request must not be able to. One role doing both would mean
    // the application holds privileges it only needs at deploy time.
    expect($app->current_user)->not->toBe($admin->current_user);
});

test('the migration connection owns the schema', function () {
    $owner = DB::connection('pgsql_admin')->selectOne(
        "SELECT tableowner FROM pg_tables WHERE tablename = 'users'"
    );

    expect($owner->tableowner)->toBe('libvox');
});
