<?php

use App\Filament\Library\Pages\EditProfile;
use App\Models\User;
use Filament\Facades\Filament;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Hash;
use Livewire\Livewire;

/*
 * The librarian changing her own password.
 *
 * The point of the screen is that the operator stops being in the middle: a
 * password only its owner has typed is a different thing from one that was set
 * for her and then read out over whatever channel was to hand. So what is
 * asserted here is both halves -- that she can, and that the page gives her
 * nothing else.
 */

beforeEach(function () {
    DB::connection('pgsql_admin')->statement('TRUNCATE tenants, users, model_has_roles, roles CASCADE');
    seed_roles();
    Filament::setCurrentPanel('library');
});

test('a librarian sets her own password', function () {
    $librarian = librarian('lib-furg');

    Livewire::actingAs($librarian)
        ->test(EditProfile::class)
        ->fillForm([
            'password' => 'uma-senha-nova',
            'passwordConfirmation' => 'uma-senha-nova',
            'currentPassword' => 'secret',
        ])
        ->call('save')
        ->assertHasNoFormErrors();

    expect(Hash::check('uma-senha-nova', $librarian->fresh()->password))->toBeTrue();
});

test('the old password has to be produced, not just the new one', function () {
    // Otherwise an unattended session is a password change: whoever walks up to
    // the machine owns the account from then on, and the real librarian finds
    // out by being unable to sign in.
    $librarian = librarian('lib-furg');

    Livewire::actingAs($librarian)
        ->test(EditProfile::class)
        ->fillForm([
            'password' => 'uma-senha-nova',
            'passwordConfirmation' => 'uma-senha-nova',
            'currentPassword' => 'nao-e-essa',
        ])
        ->call('save')
        ->assertHasFormErrors(['currentPassword']);

    expect(Hash::check('secret', $librarian->fresh()->password))->toBeTrue();
});

test('a new password typed twice differently is refused', function () {
    $librarian = librarian('lib-furg');

    Livewire::actingAs($librarian)
        ->test(EditProfile::class)
        ->fillForm([
            'password' => 'uma-senha-nova',
            'passwordConfirmation' => 'outra-senha-nova',
            'currentPassword' => 'secret',
        ])
        ->call('save')
        ->assertHasFormErrors(['password']);

    expect(Hash::check('secret', $librarian->fresh()->password))->toBeTrue();
});

test('the page offers her password and nothing else', function () {
    // Filament's own profile page edits the name and the email too. The email
    // is the login, this installation has no reset flow and no verification on
    // a change, and the operator's list of accounts is the record of who holds
    // a library's. A typo there is a lockout only the operator can undo.
    Livewire::actingAs(librarian('lib-furg'))
        ->test(EditProfile::class)
        ->assertFormFieldExists('password')
        ->assertFormFieldExists('passwordConfirmation')
        ->assertFormFieldExists('currentPassword')
        ->assertFormFieldDoesNotExist('email')
        ->assertFormFieldDoesNotExist('name');
});

/*
 * NOT TESTED HERE, AND ON PURPOSE: that changing the password does not sign her
 * out.
 *
 * This panel runs AuthenticateSession, which keeps the password hash in the
 * session and ends the session once the stored one stops matching. Filament's
 * `save()` carries the new hash forward in the same request, and without that a
 * successful change would log her out on the very next click -- which looks
 * exactly like the change having failed.
 *
 * It cannot be asserted from here. Filament guards that write with
 * `request()->hasSession()`, and a Livewire component test's request has no
 * session at all -- checked three ways, including `withSession([])`, which
 * fills the store without binding it to the request. So an assertion written at
 * this level would read the value the *test* put there, pass, and go on passing
 * if the carry-over were deleted. The note is worth more than the green tick;
 * the render sweep covers the page over real HTTP.
 */

test('one librarian cannot reach another one s password page', function () {
    // Not a scoping question -- the page has no record to address. It reads the
    // signed-in user and only ever that, which is the property worth pinning.
    $mine = librarian('lib-furg');
    $hers = librarian2('lib-furg');

    Livewire::actingAs($mine)
        ->test(EditProfile::class)
        ->fillForm([
            'password' => 'uma-senha-nova',
            'passwordConfirmation' => 'uma-senha-nova',
            'currentPassword' => 'secret',
        ])
        ->call('save')
        ->assertHasNoFormErrors();

    expect(Hash::check('uma-senha-nova', $mine->fresh()->password))->toBeTrue()
        ->and(Hash::check('secret', User::find($hers->id)->password))->toBeTrue();
});
