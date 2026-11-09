<?php

namespace App\Filament\Library\Pages;

use Filament\Auth\Pages\EditProfile as FilamentEditProfile;
use Filament\Schemas\Components\Component;
use Filament\Schemas\Components\Utilities\Get;
use Filament\Schemas\Schema;

/**
 * The librarian changing her own password.
 *
 * Until now the only way to change it was to ask the operator, who would set a
 * new one through the accounts form and then have to tell her what it is —
 * which means the password travels through whatever channel that conversation
 * happens in, and the operator knows it afterwards. A password only its owner
 * has ever typed is a different thing from one that was handed over, and this
 * page is the difference.
 *
 * **Only the password.** Filament's own profile page also edits the name and
 * the email address, and both are dropped here. The email is the login: this
 * installation has no password-reset flow and no verification step on an email
 * change, so a typo in that field would lock a librarian out of her own panel
 * with the operator as the only way back in. It is also the operator's record
 * of who holds a library's account, and it should not drift without her
 * knowing. Neither is what was asked for, and both are one-way doors.
 *
 * Everything that makes this safe is Filament's and is inherited rather than
 * rewritten: the current password is verified against the guard before any
 * change is accepted, the new one is confirmed twice, `Password::default()`
 * applies, saving is rate-limited to five attempts, and the session's password
 * hash is updated in step — without that last part `AuthenticateSession`, which
 * this panel does run, would sign her out the instant she succeeded.
 */
class EditProfile extends FilamentEditProfile
{
    public function form(Schema $schema): Schema
    {
        return $schema->components([
            $this->getPasswordFormComponent(),
            $this->getPasswordConfirmationFormComponent(),
            $this->getCurrentPasswordFormComponent(),
        ]);
    }

    /**
     * Asked for only when a new password has been typed.
     *
     * The inherited condition also fires when the email field differs from the
     * stored address — and with no email field on this form that comparison
     * reads `null !== 'ela@biblioteca.br'`, which is true forever. The result
     * would be a "senha atual" box demanding to be filled on a page where
     * nothing else can be submitted.
     */
    protected function getCurrentPasswordFormComponent(): Component
    {
        return parent::getCurrentPasswordFormComponent()
            ->visible(fn (Get $get): bool => filled($get('password')));
    }

    /** Static: this is also the label of the item in the user menu. */
    public static function getLabel(): string
    {
        return __('portal.profile.title');
    }

    public function getTitle(): string
    {
        return __('portal.profile.title');
    }

    public function getHeading(): string
    {
        return __('portal.profile.title');
    }

    public function getSubheading(): ?string
    {
        return __('portal.profile.help');
    }
}
