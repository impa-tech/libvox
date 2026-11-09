<?php

use App\Models\User;
use Filament\Facades\Filament;
use Illuminate\Support\Facades\DB;

/*
 * Two panels, and who each one lets in.
 *
 * Worth stating what these tests are and are not. They cover authorization:
 * which screens exist for whom. They do not cover isolation -- keeping one
 * library's rows away from another library's request is row-level security's
 * job, in the database, and it would still hold if canAccessPanel returned
 * true for everybody. These two guarantees are independent on purpose, and
 * confusing them is how a panel check ends up being trusted for something it
 * cannot do.
 */

beforeEach(function () {
    DB::connection('pgsql_admin')->statement('TRUNCATE users, tenants, model_has_roles, roles CASCADE');
    seed_roles();
});

test('an operator can reach the operator panel', function () {
    expect(operator()->canAccessPanel(Filament::getPanel('admin')))->toBeTrue();
});

test('a librarian cannot reach the operator panel', function () {
    expect(librarian()->canAccessPanel(Filament::getPanel('admin')))->toBeFalse();
});

test('a librarian can reach the library panel', function () {
    expect(librarian()->canAccessPanel(Filament::getPanel('library')))->toBeTrue();
});

test('an operator cannot reach the library panel', function () {
    // Not a convenience refusal: the library panel binds the request to a
    // tenant, and an operator has none. Letting one in would mean an unscoped
    // session on tenant tables, which reads empty and looks like data loss.
    expect(operator()->canAccessPanel(Filament::getPanel('library')))->toBeFalse();
});

test('a librarian without a library reaches neither panel', function () {
    $stray = librarian(tenantId: null);

    expect($stray->canAccessPanel(Filament::getPanel('library')))->toBeFalse()
        ->and($stray->canAccessPanel(Filament::getPanel('admin')))->toBeFalse();
});

test('a user with no role reaches neither panel', function () {
    $nobody = User::create([
        'name' => 'Nobody',
        'email' => 'nobody@libvox.local',
        'password' => 'secret',
    ]);

    expect($nobody->canAccessPanel(Filament::getPanel('admin')))->toBeFalse()
        ->and($nobody->canAccessPanel(Filament::getPanel('library')))->toBeFalse();
});

test('the operator panel refuses a librarian over http', function () {
    $response = $this->actingAs(librarian())->get('/admin');

    expect($response->status())->toBe(403);
});

test('the library panel refuses an operator over http', function () {
    $response = $this->actingAs(operator())->get('/app');

    expect($response->status())->toBe(403);
});

test('the two panels are served from different paths', function () {
    expect(Filament::getPanel('admin')->getPath())->toBe('admin')
        ->and(Filament::getPanel('library')->getPath())->toBe('app');
});

test('the operator panel renders for an operator', function () {
    // Not just an authorization check: this is the one test that would notice
    // a panel which is configured correctly and then fails to render.
    $this->actingAs(operator())->get('/admin')->assertOk();
});

test('the library panel renders for a librarian', function () {
    $this->actingAs(librarian())->get('/app')->assertOk();
});
