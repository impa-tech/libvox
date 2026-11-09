<?php

use App\Enums\UserRole;
use App\Filament\Admin\Resources\Users\Pages\CreateUser;
use App\Filament\Admin\Resources\Users\Pages\EditUser;
use App\Models\User;
use App\Tenancy\TenantProvisioner;
use Filament\Facades\Filament;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Hash;
use Livewire\Livewire;

/*
 * Creating an account through the form.
 *
 * These exist because the form was shipped without them and the first person to
 * open it got `where roles.id = 'librarian'` from Postgres: the role select was
 * bound to Shield's many-to-many, which saves by id, with options keyed by name.
 * Every test here would have caught it, and none of them existed.
 */

beforeEach(function () {
    DB::connection('pgsql_admin')->statement('TRUNCATE tenants, users, model_has_roles, roles CASCADE');
    seed_roles();
    Filament::setCurrentPanel('admin');

    app(TenantProvisioner::class)->provision(['tenant_id' => 'lib-furg', 'display_name' => 'FURG']);
});

test('the operator creates a librarian bound to one library', function () {
    Livewire::actingAs(operator())
        ->test(CreateUser::class)
        ->fillForm([
            'name' => 'Bibliotecária',
            'email' => 'bib@furg.br',
            'role' => UserRole::Librarian->value,
            'tenant_id' => 'lib-furg',
            'password' => 'uma-senha-boa',
        ])
        ->call('create')
        ->assertHasNoFormErrors();

    $created = User::where('email', 'bib@furg.br')->firstOrFail();

    expect($created->isLibrarian())->toBeTrue()
        ->and($created->tenant_id)->toBe('lib-furg')
        ->and($created->canAccessPanel(Filament::getPanel('library')))->toBeTrue()
        ->and($created->canAccessPanel(Filament::getPanel('admin')))->toBeFalse();
});

test('the operator creates another operator, who belongs to no library', function () {
    Livewire::actingAs(operator())
        ->test(CreateUser::class)
        ->fillForm([
            'name' => 'Outro operador',
            'email' => 'outro@libvox.local',
            'role' => UserRole::Operator->value,
            'password' => 'uma-senha-boa',
        ])
        ->call('create')
        ->assertHasNoFormErrors();

    $created = User::where('email', 'outro@libvox.local')->firstOrFail();

    expect($created->isOperator())->toBeTrue()
        ->and($created->tenant_id)->toBeNull();
});

test('an account ends up with exactly one role', function () {
    // syncRoles rather than assignRole: two roles would make canAccessPanel
    // answer for both, and the domain has no room for an account that is an
    // operator and a librarian at once.
    Livewire::actingAs(operator())
        ->test(CreateUser::class)
        ->fillForm([
            'name' => 'x', 'email' => 'x@libvox.local',
            'role' => UserRole::Librarian->value, 'tenant_id' => 'lib-furg',
            'password' => 'uma-senha-boa',
        ])
        ->call('create');

    expect(User::where('email', 'x@libvox.local')->firstOrFail()->roles)->toHaveCount(1);
});

test('changing an account from librarian to operator clears its library', function () {
    $librarian = librarian('lib-furg');

    Livewire::actingAs(operator())
        ->test(EditUser::class, ['record' => $librarian->id])
        ->fillForm(['role' => UserRole::Operator->value])
        ->call('save')
        ->assertHasNoFormErrors();

    $changed = $librarian->fresh();

    // Left behind, the old tenant_id would be invisible in the form and still
    // in the column -- and a role change that half-applies is worse than one
    // that is refused.
    expect($changed->isOperator())->toBeTrue()
        ->and($changed->tenant_id)->toBeNull()
        ->and($changed->roles)->toHaveCount(1);
});

test('the form shows the role an account already has', function () {
    Livewire::actingAs(operator())
        ->test(EditUser::class, ['record' => librarian('lib-furg')->id])
        ->assertFormSet(['role' => UserRole::Librarian->value, 'tenant_id' => 'lib-furg']);
});

test('a librarian without a library is refused by the form', function () {
    Livewire::actingAs(operator())
        ->test(CreateUser::class)
        ->fillForm([
            'name' => 'x', 'email' => 'x@libvox.local',
            'role' => UserRole::Librarian->value,
            'password' => 'uma-senha-boa',
        ])
        ->call('create')
        ->assertHasFormErrors(['tenant_id']);
});

test('the password is hashed and never stored as typed', function () {
    Livewire::actingAs(operator())
        ->test(CreateUser::class)
        ->fillForm([
            'name' => 'x', 'email' => 'x@libvox.local',
            'role' => UserRole::Operator->value,
            'password' => 'uma-senha-boa',
        ])
        ->call('create');

    $stored = DB::connection('pgsql_admin')->selectOne(
        "SELECT password FROM users WHERE email = 'x@libvox.local'"
    );

    expect($stored->password)->not->toBe('uma-senha-boa')
        ->and(Hash::check('uma-senha-boa', $stored->password))->toBeTrue();
});

test('a blank password on edit keeps the current one', function () {
    $librarian = librarian('lib-furg');
    $before = $librarian->password;

    Livewire::actingAs(operator())
        ->test(EditUser::class, ['record' => $librarian->id])
        ->fillForm(['name' => 'Nome novo'])
        ->call('save')
        ->assertHasNoFormErrors();

    expect($librarian->fresh()->password)->toBe($before)
        ->and($librarian->fresh()->name)->toBe('Nome novo');
});
