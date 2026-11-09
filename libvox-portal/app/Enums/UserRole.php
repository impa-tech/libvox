<?php

namespace App\Enums;

/**
 * The two kinds of person who sign in, as Shield role names.
 *
 * An enum rather than loose strings because the role decides which panel a
 * request may reach, and a typo in a string that gates a panel is a typo that
 * silently opens or closes one.
 */
enum UserRole: string
{
    /** LibVox staff. Registers libraries; belongs to none. */
    case Operator = 'operator';

    /** A library's own staff. Belongs to exactly one, and only ever sees it. */
    case Librarian = 'librarian';

    public function label(): string
    {
        return __('portal.role.'.$this->value);
    }
}
