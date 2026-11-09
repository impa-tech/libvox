<?php

namespace App\Enums;

/**
 * The ILS types a driver exists for.
 *
 * Paired with the CHECK on tenant_ils.ils_type, and constrained for the same
 * reason: offering FOLIO here before /libvox-driver-folio exists would let an
 * operator configure a library that can never sync, and the failure would
 * arrive much later than the mistake.
 */
enum IlsType: string
{
    case Koha = 'koha';

    public function label(): string
    {
        return match ($this) {
            self::Koha => 'Koha',
        };
    }
}
