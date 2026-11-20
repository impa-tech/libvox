<?php

namespace App\Enums;

/**
 * The ILS types a driver exists for.
 *
 * Paired with the CHECK on tenant_ils.ils_type and with
 * libvox_sync.drivers.ILS_TYPES, and constrained for the same reason: offering
 * a system here before its driver exists would let an operator configure a
 * library that can never sync, and the failure would arrive much later than
 * the mistake.
 */
enum IlsType: string
{
    case Koha = 'koha';
    case Evergreen = 'evergreen';

    public function label(): string
    {
        return match ($this) {
            self::Koha => 'Koha',
            self::Evergreen => 'Evergreen',
        };
    }

    /**
     * Whether the library signs in with a staff username and password rather
     * than an API key.
     *
     * Evergreen has no API-key screen. The stored columns are the same either
     * way -- client_id and an encrypted client_secret -- so this decides only
     * what the form calls them, not how they are kept.
     */
    public function signsInWithPassword(): bool
    {
        return $this === self::Evergreen;
    }
}
